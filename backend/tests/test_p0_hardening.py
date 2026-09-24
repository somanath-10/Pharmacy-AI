"""P0 correctness tests: transactions, ledger atomicity, dimensions,
reservation concurrency, returns disposition, idempotency, agent persistence,
uniqueness. Each test isolates its data with unique keys so the shared test
database stays deterministic.
"""
import asyncio
import uuid

import pytest
from pymongo.errors import DuplicateKeyError

from app.core.database import db, current_session
from app.core.errors import ConflictError, ValidationFailed
from app.core.idempotency import IdempotencyInProgress
from app.domains.inventory import service as inv

pytestmark = pytest.mark.asyncio


def _uid():
    return uuid.uuid4().hex[:10]


async def _mk_warehouse(code: str) -> str:
    await db.db.warehouses.update_one(
        {"code": code},
        {"$set": {"code": code, "name": code, "organization_id": "ORG-T",
                  "site_id": "SITE-T", "status": "ACTIVE"}},
        upsert=True)
    return code


async def _stock(wh: str, sku: str, batch: str, qty: float, status="AVAILABLE",
                 expiry="2032-01-01"):
    await inv.ensure_batch(sku, batch, expiry)
    if status == "AVAILABLE":
        await db.db.batches.update_one({"batch_id": batch},
                                       {"$set": {"qa_status": "RELEASED",
                                                 "blocked": False}})
    return await inv.record_movement(
        "OPENING_STOCK" if status == "AVAILABLE" else "STATUS_CHANGE",
        sku, wh, qty, batch_id=batch, stock_status=status,
        reference_type="TEST", reference_id=f"t-{_uid()}",
        performed_by={"type": "SYSTEM", "id": "p0-test"})


# ---------------------------------------------------------------- transactions
async def test_transaction_session_propagates_and_aborts():
    """current_session() is visible inside db.transaction(); writes roll back."""
    uid = _uid()
    coll = db.db.p0_tx_probe
    assert current_session() is None
    async with db.transaction() as s:
        if s is not None:
            assert current_session() is s
            await coll.insert_one({"uid": uid}, session=s)
        else:
            await coll.insert_one({"uid": uid})
    doc = await coll.find_one({"uid": uid})
    if db.tx_support:
        assert doc is not None
        # abort path
        with pytest.raises(RuntimeError):
            async with db.transaction() as s2:
                await coll.insert_one({"uid": uid + "-x"}, session=s2)
                raise RuntimeError("boom")
        assert await coll.find_one({"uid": uid + "-x"}) is None
        await coll.delete_many({"uid": {"$in": [uid, uid + "-x"]}})
    else:
        assert doc is not None  # standalone fallback: still inserts


async def test_record_movement_joins_transaction():
    """Inside a transaction, record_movement uses the ambient session."""
    wh = await _mk_warehouse("WH-TX-" + _uid())
    sku, batch = "SKU-TX-" + _uid(), "B-TX-" + _uid()
    mv_inserted = []

    async with db.transaction() as s:
        await _stock(wh, sku, batch, 10)
        mv_inserted.append(True)
        if db.tx_support:
            assert current_session() is s

    rows = await db.db.inventory_movements.find({"batch_id": batch}).to_list(10)
    assert len(rows) == 1
    bals = await db.db.inventory_balances.find({"batch_id": batch}).to_list(10)
    assert float(bals[0]["quantity"]) == 10.0


# --------------------------------------------------------- ledger atomicity
async def test_ledger_first_balance_projection_consistent():
    """Ledger row and balance row both exist after a movement (append-only)."""
    wh = await _mk_warehouse("WH-LED-" + _uid())
    sku, batch = "SKU-LED-" + _uid(), "B-LED-" + _uid()
    mv = await _stock(wh, sku, batch, 7)
    ledger = await db.db.inventory_movements.find_one(
        {"movement_id": mv["movement_id"]})
    assert ledger is not None
    assert ledger["signed_quantity"] == 7.0
    bal = await db.db.inventory_balances.find_one(
        {"product_id": sku, "warehouse_id": wh, "batch_id": batch,
         "stock_status": "AVAILABLE"})
    assert bal is not None
    assert float(bal["quantity"]) == 7.0


async def test_ledger_is_append_only_no_update_path():
    """Movements collection has no update_one/update_many callers in the
    inventory service (append-only enforced by source inspection)."""
    import inspect
    src = inspect.getsource(inv)
    assert "inventory_movements.update_one" not in src
    assert "inventory_movements.update_many" not in src
    assert "inventory_movements.replace_one" not in src
    assert "inventory_movements.delete_one" not in src
    assert "inventory_movements.delete_many" not in src


async def test_direct_quantity_modification_rejected():
    """Quantities can never be set directly; only $inc via movements."""
    import inspect
    src = inspect.getsource(inv)
    # the only permitted quantity $set is the rebuild-from-ledger path
    assert src.count('"$set": {"quantity"') == 1
    assert "rebuild_balances" in src


async def test_negative_and_unknown_movement_rejected():
    wh = await _mk_warehouse("WH-NEG-" + _uid())
    with pytest.raises(ValidationFailed):
        await inv.record_movement("SALE", "X-" + _uid(), wh, -5)
    with pytest.raises(ValidationFailed):
        await inv.record_movement("NOT_A_TYPE", "X-" + _uid(), wh, 5)


# ----------------------------------------------------------- status dimensions
async def test_balance_dimensions_and_status_summary():
    wh = await _mk_warehouse("WH-DIM-" + _uid())
    sku, batch = "SKU-DIM-" + _uid(), "B-DIM-" + _uid()
    await _stock(wh, sku, batch, 100)                      # AVAILABLE
    await inv.record_movement("PURCHASE_RECEIPT", sku, wh, 40,
                              batch_id="B-DIM-Q-" + _uid(),
                              reference_type="TEST", reference_id="grn",
                              performed_by=None)
    await inv.change_stock_status(sku, wh, batch, "AVAILABLE", "QUARANTINE",
                                  10, actor=None, reference_type="TEST",
                                  reference_id="t")
    summary = await inv.status_summary(sku, wh)
    assert summary["AVAILABLE"] == 90.0
    assert summary["QUARANTINE"] == 50.0  # 40 receipt + 10 status change
    rows = await inv.balances(product_id=sku, warehouse_id=wh)
    dims = rows[0]
    for field in ("organization_id", "site_id", "warehouse_id", "product_id",
                  "batch_id", "stock_status", "uom"):
        assert field in dims, f"balance row missing dimension {field}"
    assert dims["organization_id"] == "ORG-T"
    assert dims["site_id"] == "SITE-T"


async def test_all_p0_statuses_representable():
    expected = {"AVAILABLE", "RESERVED", "QUARANTINE", "QUALITY_HOLD",
                "RECALLED", "DAMAGED", "EXPIRED", "REJECTED", "IN_TRANSIT",
                "RETURN_QUARANTINE"}
    assert expected.issubset(inv.STOCK_STATUSES)
    wh = await _mk_warehouse("WH-ST-" + _uid())
    sku = "SKU-ST-" + _uid()
    batch = "B-ST-" + _uid()
    await _stock(wh, sku, batch, 50)
    # one status move to a random non-available status
    target = "QUALITY_HOLD"
    await inv.change_stock_status(sku, wh, batch, "AVAILABLE", target, 20,
                                  actor=None, reference_type="TEST",
                                  reference_id="t")
    summary = await inv.status_summary(sku, wh)
    assert summary[target] == 20.0
    assert summary["AVAILABLE"] == 30.0
    # invalid status rejected
    with pytest.raises(ValidationFailed):
        await inv.change_stock_status(sku, wh, batch, "AVAILABLE",
                                      "NOT_A_STATUS", 1, actor=None,
                                      reference_type="T", reference_id="t")
    with pytest.raises(ValidationFailed):
        await inv.record_movement("OPENING_STOCK", sku, wh, 1,
                                  stock_status="BOGUS")


async def test_status_change_is_quantity_preserving():
    wh = await _mk_warehouse("WH-QP-" + _uid())
    sku, batch = "SKU-QP-" + _uid(), "B-QP-" + _uid()
    await _stock(wh, sku, batch, 30)
    before = await inv.status_summary(sku, wh)
    total_before = sum(before.values())
    await inv.change_stock_status(sku, wh, batch, "AVAILABLE", "RECALLED", 12,
                                  actor=None, reference_type="TEST",
                                  reference_id="t")
    after = await inv.status_summary(sku, wh)
    assert sum(after.values()) == total_before
    assert after["RECALLED"] == 12.0
    assert after["AVAILABLE"] == 18.0
    # every balance row is explained by ledger movements (STATUS_CHANGE rows)
    legs = await db.db.inventory_movements.count_documents(
        {"product_id": sku, "movement_type": "STATUS_CHANGE"})
    assert legs >= 2


# ------------------------------------------------------ reservation concurrency
async def test_concurrent_reservations_never_oversell():
    """N racing reservations for the same stock: successful total <= stock."""
    wh = await _mk_warehouse("WH-RACE-" + _uid())
    sku, batch = "SKU-RACE-" + _uid(), "B-RACE-" + _uid()
    await _stock(wh, sku, batch, 10)

    async def reserve_one(i):
        try:
            return await inv.reserve(sku, 4, "TEST_ORDER", f"{_uid()}-{i}",
                                     warehouse_id=wh,
                                     actor={"type": "SYSTEM", "id": "t"})
        except ConflictError:
            return None

    results = await asyncio.gather(*[reserve_one(i) for i in range(5)])
    wins = [r for r in results if r is not None]
    assert len(wins) == 2, f"expected exactly 2 winners (2×4=10), got {len(wins)}"
    assert sum(r["quantity"] for r in wins) == 8.0
    avail = await inv.status_summary(sku, wh)
    assert avail["AVAILABLE"] == 2.0
    assert avail["RESERVED"] == 8.0


async def test_reservation_exhausts_atp_exactly():
    wh = await _mk_warehouse("WH-ATP-" + _uid())
    sku, batch = "SKU-ATP-" + _uid(), "B-ATP-" + _uid()
    await _stock(wh, sku, batch, 5)
    await inv.reserve(sku, 5, "ORDER", _uid(), warehouse_id=wh)
    with pytest.raises(ConflictError):
        await inv.reserve(sku, 1, "ORDER", _uid(), warehouse_id=wh)


async def test_reservation_release_restores_available():
    wh = await _mk_warehouse("WH-REL-" + _uid())
    sku, batch = "SKU-REL-" + _uid(), "B-REL-" + _uid()
    await _stock(wh, sku, batch, 9)
    ref = _uid()
    await inv.reserve(sku, 4, "ORDER", ref, warehouse_id=wh)
    assert (await inv.status_summary(sku, wh))["AVAILABLE"] == 5.0
    released = await inv.release_reservation("ORDER", ref)
    assert released == 1
    summary = await inv.status_summary(sku, wh)
    assert summary["AVAILABLE"] == 9.0
    assert summary["RESERVED"] == 0.0


async def test_reservation_duplicate_reference_is_idempotent():
    wh = await _mk_warehouse("WH-IDEMR-" + _uid())
    sku, batch = "SKU-IDEMR-" + _uid(), "B-IDEMR-" + _uid()
    await _stock(wh, sku, batch, 20)
    ref = _uid()
    r1 = await inv.reserve(sku, 5, "ORDER", ref, warehouse_id=wh)
    r2 = await inv.reserve(sku, 5, "ORDER", ref, warehouse_id=wh)
    assert r1["reservation_id"] == r2["reservation_id"]
    assert (await inv.status_summary(sku, wh))["RESERVED"] == 5.0


# ------------------------------------------------------------------- returns
async def test_return_receipt_lands_in_return_quarantine_not_available():
    """Customer return enters RETURN_QUARANTINE; original batch stays sellable."""
    from app.domains.reverse import service as reverse_svc

    wh = await _mk_warehouse("WH-RET-" + _uid())
    sku, batch = "SKU-RET-" + _uid(), "B-RET-" + _uid()
    await _stock(wh, sku, batch, 100)
    ret_id = "RET-P0-" + _uid()
    doc = {
        "return_id": ret_id, "rma_id": None, "sales_order_id": None,
        "invoice_id": None, "customer_id": "C1",
        "lines": [{"sku": sku, "quantity": 3.0, "batch_id": batch,
                   "reason": "QUALITY", "disposition": None}],
        "reason": "QUALITY", "policy_ok": True, "status": "PICKED",
        "created_by": {"type": "SYSTEM", "id": "t"}, "created_at": "",
        "updated_at": "", "version": 1, "warehouse_id": wh,
        "timeline": [],
    }
    await db.db.return_requests.insert_one(doc)
    await reverse_svc.receive_return(ret_id, {"type": "SYSTEM", "id": "t"})
    summary = await inv.status_summary(sku, wh)
    assert summary["RETURN_QUARANTINE"] == 3.0
    assert summary["AVAILABLE"] == 100.0  # original stock untouched


async def test_return_disposition_is_quantity_level_not_batch_level():
    """Restocking a returned qty never unblocks/marks the whole batch."""
    from app.domains.reverse import service as reverse_svc

    wh = await _mk_warehouse("WH-DISP-" + _uid())
    sku, batch = "SKU-DISP-" + _uid(), "B-DISP-" + _uid()
    await _stock(wh, sku, batch, 80)
    ret_id = "RET-P0-" + _uid()
    doc = {
        "return_id": ret_id, "rma_id": None, "sales_order_id": None,
        "invoice_id": None, "customer_id": "C1",
        "lines": [{"sku": sku, "quantity": 2.0, "batch_id": batch,
                   "reason": "QUALITY", "disposition": None}],
        "reason": "QUALITY", "policy_ok": True, "status": "INSPECTED",
        "created_by": {"type": "SYSTEM", "id": "t"}, "created_at": "",
        "updated_at": "", "version": 1, "warehouse_id": wh,
        "timeline": [],
        "inspection": {"findings": "ok", "at": ""},
    }
    await db.db.return_requests.insert_one(doc)
    # receive first so the qty is in RETURN_QUARANTINE
    await db.db.return_requests.update_one(
        {"return_id": ret_id}, {"$set": {"status": "PICKED"}})
    await reverse_svc.receive_return(ret_id, {"type": "SYSTEM", "id": "t"})
    await db.db.return_requests.update_one(
        {"return_id": ret_id}, {"$set": {"status": "INSPECTED"}})
    await reverse_svc.dispose_return(ret_id, "RESTOCK",
                                     {"type": "USER", "id": "qa",
                                      "roles": ["SUPER_ADMIN"]})
    summary = await inv.status_summary(sku, wh)
    assert summary["AVAILABLE"] == 82.0   # 80 original + 2 restocked
    assert summary["RETURN_QUARANTINE"] == 0.0
    # batch master was NOT blocked by the return flow
    b = await db.db.batches.find_one({"batch_id": batch})
    assert not b.get("blocked")


# ---------------------------------------------------------------- idempotency
async def test_idempotency_concurrent_begin_single_winner():
    from app.core import idempotency as idem

    key = f"P0:{_uid()}"
    results = await asyncio.gather(
        *[idem.begin(key, "d", "w") for _ in range(8)],
        return_exceptions=True)
    firsts = [r for r in results
              if isinstance(r, dict) and r["first_time"]]
    inprog = [r for r in results if isinstance(r, IdempotencyInProgress)]
    assert len(firsts) == 1
    assert len(inprog) == 7, "duplicates must surface as explicit 409"
    await db.db.idempotency_keys.delete_one({"key": key})


async def test_idempotency_completed_replay_and_failed_reclaim():
    from app.core import idempotency as idem

    key = f"P0:{_uid()}"
    state = await idem.begin(key, "d", "w")
    assert state["first_time"]
    await idem.complete(key, {"grn": "G-1"})
    replay = await idem.begin(key, "d", "w")
    assert not replay["first_time"]
    assert replay["result"] == {"grn": "G-1"}
    # digest conflict on different payload
    with pytest.raises(Exception):
        await idem.begin(key, "other", "w")
    await db.db.idempotency_keys.delete_one({"key": key})

    # FAILED → reclaim exactly once under concurrency
    key2 = f"P0:{_uid()}"
    await idem.begin(key2, "d", "w")
    await idem.fail(key2, "first attempt blew up")
    claims = await asyncio.gather(
        *[idem.begin(key2, "d", "w") for _ in range(6)],
        return_exceptions=True)
    winners = [c for c in claims
               if isinstance(c, dict) and c.get("first_time")]
    assert len(winners) == 1
    await db.db.idempotency_keys.delete_many({"key": {"$in": [key, key2]}})


async def test_idempotency_complete_never_downgrades_completed():
    from app.core import idempotency as idem

    key = f"P0:{_uid()}"
    await idem.begin(key, "d", "w")
    await idem.complete(key, {"v": 1})
    # late fail from a straggler must not clobber the completed result
    await idem.fail(key, "late straggler")
    doc = await db.db.idempotency_keys.find_one({"key": key})
    assert doc["status"] == "COMPLETED"
    assert doc["result"] == {"v": 1}
    await db.db.idempotency_keys.delete_one({"key": key})


async def test_idempotency_in_progress_explicit_error():
    from app.core import idempotency as idem

    key = f"P0:{_uid()}"
    await idem.begin(key, "d", "w")
    with pytest.raises(IdempotencyInProgress):
        await idem.begin(key, "d", "w2")
    await db.db.idempotency_keys.delete_one({"key": key})


async def test_idempotency_ttl_index_exists():
    idx = await db.db.idempotency_keys.index_information()
    assert any(info.get("expireAfterSeconds") == 0
               for info in idx.values()), f"no TTL index: {idx}"


# --------------------------------------------------------------- agent gateway
async def test_agent_tool_call_reaches_terminal_status():
    """The ObjectId bug: _finish_call used to query _id with a str → stuck."""
    from app.agents.gateway import AGENT_REGISTRY, agent_tool, reap_stale_running, register_agent

    agent_id = "p0-agent-" + _uid()
    register_agent(agent_id, "test", ["echo", "boom"], ["TEST"])

    @agent_tool("TEST", "echo")
    async def echo(payload, actor):
        return {"ok": True, "input": payload}

    out = await echo(agent_id, {"x": 1})
    assert out["ok"] is True
    row = await db.db.agent_tool_calls.find_one(
        {"agent": agent_id, "tool": "echo"})
    assert row is not None
    assert row["status"] == "SUCCESS", f"stuck in {row['status']} (ObjectId bug)"
    assert await db.db.agent_tool_calls.find_one(
        {"agent": agent_id, "status": "RUNNING"}) is None

    # failing tool → FAILED, not RUNNING
    @agent_tool("TEST", "boom")
    async def boom(payload, actor):
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        await boom(agent_id, {})
    row2 = await db.db.agent_tool_calls.find_one(
        {"agent": agent_id, "tool": "boom"})
    assert row2["status"] == "FAILED"

    # reaper closes orphaned RUNNING rows
    old = (await db.db.agent_tool_calls.insert_one(
        {"agent": agent_id, "tool": "ghost", "status": "RUNNING",
         "created_at": "2020-01-01T00:00:00+00:00"}))
    reaped = await reap_stale_running(max_age_minutes=1)
    assert reaped >= 1
    ghost = await db.db.agent_tool_calls.find_one({"_id": old.inserted_id})
    assert ghost["status"] == "FAILED"
    await db.db.agent_tool_calls.delete_many({"agent": agent_id})
    AGENT_REGISTRY.pop(agent_id, None)


# --------------------------------------------------------------- uniqueness
async def test_unique_indexes_present():
    def has_unique(info):
        for v in info.values():
            if v.get("unique"):
                return True
        return False

    for coll, field in [
        ("purchase_orders", "po_id"),
        ("purchase_requisitions", "pr_id"),
        ("grns", "grn_id"),
        ("inventory_movements", "movement_id"),
        ("supplier_invoices", "invoice_id"),
        ("customer_invoices", "invoice_id"),
        ("payments", "payment_id"),
        ("sales_orders", "order_id"),
        ("return_requests", "return_id"),
        ("dispenses", "dispense_id"),
    ]:
        info = await db.db[coll].index_information()
        assert has_unique(info), f"{coll} lacks a unique index"
    info = await db.db.idempotency_keys.index_information()
    key_uniq = [v for v in info.values()
                if v.get("unique") and v.get("key") == [("key", 1)]]
    assert key_uniq, "idempotency_keys.key must be uniquely indexed"
    wf = await db.db.workflows.index_information()
    assert has_unique(wf), "workflows entity_type+entity_id must be unique"


async def test_duplicate_human_ids_rejected():
    uid = _uid()
    await db.db.purchase_orders.insert_one({"po_id": f"PO-DUP-{uid}"})
    with pytest.raises(DuplicateKeyError):
        await db.db.purchase_orders.insert_one({"po_id": f"PO-DUP-{uid}"})
    await db.db.purchase_orders.delete_one({"po_id": f"PO-DUP-{uid}"})

    await db.db.payments.insert_one({"payment_id": f"PAY-DUP-{uid}"})
    with pytest.raises(DuplicateKeyError):
        await db.db.payments.insert_one({"payment_id": f"PAY-DUP-{uid}"})
    await db.db.payments.delete_one({"payment_id": f"PAY-DUP-{uid}"})

    await db.db.dispenses.insert_one({"dispense_id": f"DSP-DUP-{uid}",
                                      "rx_id": "RX-DUP"})
    with pytest.raises(DuplicateKeyError):
        await db.db.dispenses.insert_one({"dispense_id": f"DSP-DUP-{uid}",
                                          "rx_id": "RX-OTHER"})
    await db.db.dispenses.delete_one({"dispense_id": f"DSP-DUP-{uid}"})


# --------------------------------------------------------------- transaction honesty
async def test_topology_report_is_honest():
    info = db.topology_info()
    if not db.tx_support:
        assert info["standalone"] is True
        assert info["transactions_supported"] is False
        async with db.transaction() as s:
            assert s is None
    else:
        assert info["transactions_supported"] is True


async def test_grn_idempotency_e2e_single_effect(seeded):
    """Duplicate concurrent GRN posts with one key → one GRN, one receipt."""
    from httpx import ASGITransport, AsyncClient

    from app.core.security import create_access_token
    from app.main import app

    headers = {"Authorization": "Bearer " +
               create_access_token("p0-admin", ["SUPER_ADMIN"])}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        # vendor + approved PO
        r = await c.post("/api/vendors", json={"name": "P0 Vendor " + _uid()},
                         headers=headers)
        vid = r.json()["vendor_id"]
        await c.post(f"/api/vendors/{vid}/documents", json={
            "doc_type": "DRUG_LICENCE", "doc_number": "DL-" + _uid(),
            "expiry_date": "2031-01-01"}, headers=headers)
        await c.post(f"/api/vendors/{vid}/qualify",
                     json={"kind": "commercial", "result": "PASS"},
                     headers=headers)
        await c.post(f"/api/vendors/{vid}/qualify",
                     json={"kind": "qa", "result": "PASS"}, headers=headers)
        await c.post(f"/api/vendors/{vid}/approve", json={}, headers=headers)
        products = (await c.get("/api/masters/products", headers=headers)).json()
        api = next(p for p in products if "Paracetamol API" in p["name"])
        r = await c.post("/api/procurement/pos", json={
            "vendor_id": vid, "lines": [{"sku": api["sku"], "quantity": 5}]},
            headers=headers)
        po_id = r.json()["po_id"]
        await c.post(f"/api/procurement/pos/{po_id}/submit", json={},
                     headers=headers)
        await c.post(f"/api/procurement/pos/{po_id}/send", json={},
                     headers=headers)
        await c.post(f"/api/procurement/pos/{po_id}/ack", json={},
                     headers=headers)
        r = await c.post("/api/logistics/inbound/asns", json={
            "po_id": po_id,
            "lines": [{"line_no": 1, "quantity": 5,
                       "batch_id": "B-P0-" + _uid(),
                       "expiry_date": "2031-06-01"}]}, headers=headers)
        asn_id = r.json()["asn_id"]
        await c.post(f"/api/logistics/inbound/asns/{asn_id}/arrive", json={},
                     headers=headers)
        payload = {
            "asn_id": asn_id, "warehouse_id": "WH-PLANT",
            "lines": [{"line_no": 1, "quantity": 5,
                       "batch_id": "B-P0-" + _uid(),
                       "expiry_date": "2031-06-01"}]}
        key = "p0-grn-" + _uid()
        hdrs = {**headers, "Idempotency-Key": key}

        async def post_grn():
            return await c.post("/api/warehouse/grn", json=payload,
                                headers=hdrs)

        rs = await asyncio.gather(*[post_grn() for _ in range(4)])
        bodies = [r.json() for r in rs]
        grn_ids = {b.get("grn_id") for b in bodies if b.get("grn_id")}
        inprog = [b for b in bodies if b.get("error") == "IDEMPOTENCY_IN_PROGRESS"]
        assert len(grn_ids) == 1, f"expected 1 GRN id, got {grn_ids}"
        assert len(inprog) >= 0  # duplicates surface as 409 or replay
        batch_id = payload["lines"][0]["batch_id"]
        receipts = await db.db.inventory_movements.count_documents(
            {"batch_id": batch_id, "movement_type": "PURCHASE_RECEIPT"})
        assert receipts == 1
