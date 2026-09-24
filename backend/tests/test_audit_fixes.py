"""Regression tests for the repository-audit P0 fixes (acceptance scenarios
A/B/F/I/J/M/N/O/Q from the master prompt).

Every test drives real domain services / HTTP handlers — no direct business
mutation — mirroring how production code exercises these paths.
"""
import pytest

from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.domains.inventory import service as inventory
from app.domains.sales import service as sales_svc
from app.domains.warehouse import service as warehouse_svc

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module", autouse=True)
async def _seed_af():
    """These service-level tests run without the HTTP `seeded` fixture —
    ensure baseline masters (warehouses/products/customers) exist."""
    from app.seed.run import _seed_masters, _seed_users

    await _seed_users()
    await _seed_masters()
    yield


ADMIN = {"type": "USER", "id": "af-admin", "roles": ["SUPER_ADMIN"]}
PHARM = {"type": "USER", "id": "af-pharm", "roles": ["PHARMACIST"]}


def _uid():
    import uuid
    return uuid.uuid4().hex[:8]


async def _stock(sku, wh, qty, batch="B-AF", expiry="2032-06-01"):
    await db.db.warehouses.update_one(
        {"code": wh},
        {"$setOnInsert": {"code": wh, "name": wh, "type": "CENTRAL",
                          "status": "ACTIVE", "created_at": now_iso()}},
        upsert=True)
    await inventory.ensure_batch(sku, batch, expiry)
    await db.db.batches.update_one({"batch_id": batch},
                                   {"$set": {"qa_status": "RELEASED",
                                             "blocked": False}})
    return await inventory.record_movement(
        "OPENING_STOCK", sku, wh, qty, batch_id=batch,
        reference_type="TEST", reference_id=f"af-{_uid()}",
        performed_by={"type": "SYSTEM", "id": "af"})


# ================================================== A. reservation / ATP model
async def test_atp_not_double_subtracted():
    """AVAILABLE 100 → reserve 20 → AVAILABLE 80 / ATP 80 (never 60)."""
    sku = f"PRD-{_uid()}"
    from app.domains.masters.service import create_product
    try:
        await create_product({"sku": sku, "name": f"ATP {_uid()}",
                                  "type": "TRADE_ITEM", "uom": "BOX",
                                  "standard_cost": 10, "price": 20})
    except Exception:
        pass
    batch = f"B-ATP-{_uid()}"
    await _stock(sku, "WH-MAIN", 100, batch=batch)
    before = await inventory.availability(sku)
    resv = await inventory.reserve(sku, 20, "TEST", f"R-{_uid()}",
                                   "WH-MAIN", ADMIN)
    after = await inventory.availability(sku)
    assert after["available"] == before["available"] - 20, \
        f"AVAILABLE must drop by exactly the reserved qty: {before} → {after}"
    assert after["available_to_promise"] == after["available"], \
        "ATP must equal AVAILABLE (reservations already physically moved)"
    assert after["reserved"] >= 20
    # ledger reconstructs the same state: AVAILABLE legs net to the projection
    legs = [m async for m in db.db.inventory_movements.find(
        {"product_id": sku, "batch_id": batch, "stock_status": "AVAILABLE",
         "lifecycle": {"$ne": "VOID"}})]
    net = sum(float(m["signed_quantity"]) for m in legs)
    # ledger legs must explain exactly the AVAILABLE drop and RESERVED rise
    # reconstruct rule: POSTED ledger legs for this batch must equal the
    # resulting balance exactly (80 = 100 received − 20 moved to RESERVED)
    assert net == after["available"], \
        "ledger must reconstruct the AVAILABLE balance exactly"


# ====================================================== B. failed outflow / B'
async def test_failed_outflow_leaves_no_postable_movement():
    """AVAILABLE 5, outflow 10 → ConflictError; movement is VOID, never POSTED."""
    sku = f"PRD-{_uid()}"
    from app.domains.masters.service import create_product
    try:
        await create_product({"sku": sku, "name": f"OF {_uid()}",
                                  "type": "TRADE_ITEM", "uom": "BOX",
                                  "standard_cost": 5, "price": 9})
    except Exception:
        pass
    batch = f"B-OF-{_uid()}"
    await _stock(sku, "WH-MAIN", 5, batch=batch)
    ref = f"OF-{_uid()}"
    with pytest.raises(ConflictError):
        await inventory.record_movement(
            "SALE", sku, "WH-MAIN", 10, batch_id=batch,
            reference_type="TEST", reference_id=ref,
            performed_by={"type": "SYSTEM", "id": "af"})
    mv = await db.db.inventory_movements.find_one(
        {"reference_type": "TEST", "reference_id": ref})
    if mv is not None:  # non-transactional fallback VOIDs; txn path aborts pre-insert
        assert mv["lifecycle"] == "VOID", \
            f"rejected movement must be VOIDed, got {mv.get('lifecycle')}"
    bal = await db.db.inventory_balances.find_one(
        {"product_id": sku, "batch_id": batch, "stock_status": "AVAILABLE"})
    assert float(bal["quantity"]) == 5.0, "balance unchanged"


# ================================== F. pick task consumes only its allocation
async def test_pick_task_consumes_only_its_allocation():
    """SO with A(10)+B(5): confirming A's task must not touch B's reservation."""
    from app.domains.masters.service import create_product, create_customer
    cust_code = f"CUS-AF-{_uid()}"
    try:
        await create_customer({"code": cust_code, "name": "AF Cust", "contact": {}}, ADMIN)
    except Exception:
        pass
    c_doc = await db.db.customers.find_one({"code": cust_code}) or await db.db.customers.find_one()
    cust = c_doc["code"]
    a, b = f"PRD-A-{_uid()}", f"PRD-B-{_uid()}"
    for sku in (a, b):
        from app.domains.masters.service import create_product
        try:
            await create_product({"sku": sku, "name": sku, "type": "TRADE_ITEM",
                                  "uom": "BOX", "standard_cost": 1,
                                  "price": 2})
        except Exception:
            pass
        await _stock(sku, "WH-MAIN", 50, batch=f"B-{sku}")
    so = await sales_svc.create_sales_order({
        "customer_id": cust, "lines": [{"sku": a, "quantity": 10},
                                       {"sku": b, "quantity": 5}]}, ADMIN)
    await sales_svc.confirm_order(so["order_id"], ADMIN)
    await sales_svc.allocate_order(so["order_id"], ADMIN)
    wave = await warehouse_svc.pick({"sales_order_id": so["order_id"]}, ADMIN)
    tasks = {t["product_id"]: t for t in wave["tasks"]}
    task_a = tasks[a]
    await warehouse_svc.confirm_pick(task_a["task_id"], ADMIN)

    resv_a = await db.db.reservations.find_one(
        {"reference_type": "SALES_ORDER", "reference_id": so["order_id"],
         "product_id": a})
    resv_b = await db.db.reservations.find_one(
        {"reference_type": "SALES_ORDER", "reference_id": so["order_id"],
         "product_id": b})
    assert resv_a["status"] == "CONSUMED", "A's reservation is fully consumed"
    assert resv_b["status"] == "ACTIVE", \
        f"B's reservation must remain ACTIVE, got {resv_b['status']}"
    picked = await db.db.pick_tasks.find_one({"task_id": task_a["task_id"]})
    assert picked["status"] == "PICKED"
    order = await db.db.sales_orders.find_one({"order_id": so["order_id"]})
    assert order["status"] == "PICKING", "order stays PICKING until B is picked"


# ===================== F'. multi-bin batch: each pick drains its own allocation
async def test_pick_bin_split_batch_consumes_exact_bin():
    """One batch, two bins: FEFO reserves 6@A-01 + 4@B-02. Each pick task must
    consume exactly ITS bin's allocation (previously the task carried no bin,
    so allocation lookup failed for the whole multi-bin pick wave)."""
    from app.domains.masters.service import create_product, create_customer
    cust_code = f"CUS-AF-{_uid()}"
    try:
        await create_customer({"code": cust_code, "name": "AF Cust", "contact": {}}, ADMIN)
    except Exception:
        pass
    c_doc = await db.db.customers.find_one({"code": cust_code}) or \
        await db.db.customers.find_one()
    sku = f"PRD-MB-{_uid()}"
    try:
        await create_product({"sku": sku, "name": sku, "type": "TRADE_ITEM",
                              "uom": "BOX", "standard_cost": 1, "price": 2})
    except Exception:
        pass
    batch = f"B-MB-{_uid()}"
    await inventory.ensure_batch(sku, batch, "2032-06-01")
    await db.db.batches.update_one({"batch_id": batch},
                                   {"$set": {"qa_status": "RELEASED",
                                             "blocked": False}})
    for loc, qty in (("A-01", 6), ("B-02", 4)):
        await inventory.record_movement(
            "PURCHASE_RECEIPT", sku, "WH-MAIN", qty, batch_id=batch,
            location_id=loc, reference_type="TEST",
            reference_id=f"rc-{_uid()}", performed_by=ADMIN)
        await inventory.change_stock_status(
            sku, "WH-MAIN", batch, "QUARANTINE", "AVAILABLE", qty,
            actor=ADMIN, location_id=loc, reference_type="TEST",
            reference_id=f"rel-{_uid()}")
    so = await sales_svc.create_sales_order(
        {"customer_id": c_doc["code"], "lines": [{"sku": sku, "quantity": 10}]},
        ADMIN)
    await sales_svc.confirm_order(so["order_id"], ADMIN)
    await sales_svc.allocate_order(so["order_id"], ADMIN)
    wave = await warehouse_svc.pick({"sales_order_id": so["order_id"]}, ADMIN)
    assert len(wave["tasks"]) == 2, "one task per bin allocation"
    assert all(t.get("location_id") for t in wave["tasks"]), \
        "each task must carry its allocation bin"
    for t in wave["tasks"]:
        await warehouse_svc.confirm_pick(t["task_id"], ADMIN)
    rows = [r async for r in db.db.inventory_balances.find(
        {"product_id": sku, "batch_id": batch, "stock_status": "RESERVED"})]
    for r in rows:
        assert float(r["quantity"]) == 0.0, \
            f"bin {r['location_id']} RESERVED must be 0 after pick, got {r['quantity']}"
    neg = [r async for r in db.db.inventory_balances.find(
        {"product_id": sku, "quantity": {"$lt": 0}})]
    assert not neg, "no negative balances may exist"
    rsv = await db.db.reservations.find_one(
        {"reference_type": "SALES_ORDER", "reference_id": so["order_id"]})
    assert rsv["status"] == "CONSUMED"


# ================= terminal reservation must not wedge re-reservation (A')
async def test_reserve_after_release_supersedes_terminal_reservation():
    """The unique (reference, product) index outlives a released reservation;
    re-reserving the same reference must supersede the terminal row instead of
    failing forever, and balances must stay exact across the cycle."""
    from app.domains.masters.service import create_product
    sku = f"PRD-RS-{_uid()}"
    try:
        await create_product({"sku": sku, "name": sku, "type": "TRADE_ITEM",
                              "uom": "BOX", "standard_cost": 1, "price": 2})
    except Exception:
        pass
    batch = f"B-RS-{_uid()}"
    await inventory.ensure_batch(sku, batch, "2032-06-01")
    await db.db.batches.update_one({"batch_id": batch},
                                   {"$set": {"qa_status": "RELEASED",
                                             "blocked": False}})
    await inventory.record_movement(
        "OPENING_STOCK", sku, "WH-MAIN", 10, batch_id=batch,
        reference_type="TEST", reference_id=f"os-{_uid()}",
        performed_by={"type": "SYSTEM", "id": "af"})
    ref = f"RETRY-{_uid()}"
    await inventory.reserve(sku, 4, "SO", ref, "WH-MAIN", ADMIN)
    await inventory.release_reservation("SO", ref, actor=ADMIN)
    r2 = await inventory.reserve(sku, 4, "SO", ref, "WH-MAIN", ADMIN)
    assert r2["status"] == "ACTIVE" and float(r2["quantity"]) == 4.0
    av = await inventory.availability(sku)
    assert av["available"] == 6.0 and av["reserved"] == 4.0, \
        f"balances must reconstruct exactly across the cycle: {av}"



# ============ concurrent partial releases must not desync the reservation doc
async def test_concurrent_partial_release_stays_consistent():
    """Two interleaved partial releases of the same reservation: the version
    CAS must make the loser re-read its allocation rows, so doc.quantity,
    the allocation sum, and the RESERVED balance always agree."""
    import asyncio
    from app.domains.masters.service import create_product
    sku = f"PRD-CR-{_uid()}"
    try:
        await create_product({"sku": sku, "name": sku, "type": "TRADE_ITEM",
                              "uom": "BOX", "standard_cost": 1, "price": 2})
    except Exception:
        pass
    batch = f"B-CR-{_uid()}"
    await inventory.ensure_batch(sku, batch, "2032-06-01")
    await db.db.batches.update_one({"batch_id": batch},
                                   {"$set": {"qa_status": "RELEASED",
                                             "blocked": False}})
    await inventory.record_movement(
        "OPENING_STOCK", sku, "WH-MAIN", 10, batch_id=batch,
        location_id="C-01", reference_type="TEST", reference_id=f"os-{_uid()}",
        performed_by={"type": "SYSTEM", "id": "af"})
    ref = f"CR-{_uid()}"
    await inventory.reserve(sku, 8, "SO", ref, "WH-MAIN", ADMIN)

    # force the dangerous interleave: the first leg-writes yield control long
    # enough for the other release to claim + read a stale allocation snapshot
    orig_leg = inventory._post_status_leg
    state = {"n": 0}

    async def slow_leg(*a, **kw):
        state["n"] += 1
        n = state["n"]
        await asyncio.sleep(0.05 if n <= 2 else 0)
        return await orig_leg(*a, **kw)

    inventory._post_status_leg = slow_leg
    try:
        results = await asyncio.gather(
            inventory.release_reservation_partial("SO", ref, sku, 3, actor=ADMIN),
            inventory.release_reservation_partial("SO", ref, sku, 3, actor=ADMIN),
            return_exceptions=True)
    finally:
        inventory._post_status_leg = orig_leg
    assert not any(isinstance(r, Exception) for r in results), \
        f"both releases should succeed: {results}"
    doc = await db.db.reservations.find_one({"reference_id": ref})
    alloc_sum = sum(float(a["allocate"]) for a in doc.get("allocation") or [])
    assert abs(alloc_sum - float(doc["quantity"])) <= 1e-9, \
        f"doc.quantity {doc['quantity']} diverged from allocation sum {alloc_sum}"
    bal = await db.db.inventory_balances.find_one(
        {"product_id": sku, "batch_id": batch, "location_id": "C-01",
         "stock_status": "RESERVED"})
    assert float(bal["quantity"]) == float(doc["quantity"]), \
        "RESERVED balance must equal the remaining reservation"






# ============================================= I. prescription sales order path
async def test_rx_order_full_path():
    """Rx-required SO: DRAFT→PENDING_RX→RX_APPROVED→CONFIRMED→allocate."""
    from app.domains.masters.service import create_product, create_customer
    from app.domains.pharmacy import service as pharmacy_svc
    c_doc = await db.db.customers.find_one()
    if not c_doc:
        c_code = f"CUS-RX-{_uid()}"
        await create_customer({"code": c_code, "name": "Rx Cust", "contact": {}}, ADMIN)
        cust = c_code
    else:
        cust = c_doc["code"]
    sku = f"PRD-RX-{_uid()}"
    try:
        await create_product({"sku": sku, "name": f"Rx {_uid()}",
                              "type": "TRADE_ITEM", "uom": "BOX",
                              "standard_cost": 3, "price": 8,
                              "is_prescription": True}, ADMIN)
    except Exception:
        pass
    await _stock(sku, "WH-MAIN", 30, batch=f"B-RXO-{_uid()}")
    so = await sales_svc.create_sales_order({
        "customer_id": cust, "lines": [{"sku": sku, "quantity": 2}]}, ADMIN)
    assert so["status"] == "PENDING_RX", "rx product forces PENDING_RX"

    # pharmacist approval path (upload → match → approve → rx_approved)
    raw = (f"Dr. A. Kumar\npatient: AF {_uid()}\nage: 30\n"
           f"Tab {sku} 250mg 1-0-0 x 2 days")
    rx = await pharmacy_svc.upload_prescription({"raw_text": raw}, ADMIN)
    # drug master may not know this synthetic SKU — attach it manually as the
    # pharmacist-corrected match, then approve through the authority gate
    await db.db.prescriptions.update_one(
        {"rx_id": rx["rx_id"]},
        {"$set": {"matched": [{"raw": {"name": sku, "dispense_qty": 2},
                               "product_id": sku, "matched": True,
                               "issues": []}],
                  "compliance": {"controlled_substance": False,
                                 "issues": [],
                                 "needs_clarification": False}}})
    approved = await pharmacy_svc.pharmacist_review(rx["rx_id"], "APPROVE",
                                                    "ok", PHARM)
    assert approved["status"] == "APPROVED"
    so2 = await sales_svc.rx_approved(so["order_id"], ADMIN)
    assert so2["status"] == "CONFIRMED", \
        f"rx approval must confirm the order, got {so2['status']}"
    allocated = await sales_svc.allocate_order(so["order_id"], ADMIN)
    assert allocated["allocations"], "allocation must produce a FEFO plan"
    so3 = await sales_svc.get_order(so["order_id"])
    assert so3["status"] == "ALLOCATED", f"order must be ALLOCATED, got {so3['status']}"


# =================================================== J. POS Rx enforcement
async def test_pos_rx_product_without_prescription_rejected():
    """POS sale of an Rx product with no approved linked Rx → denied."""
    from app.domains.masters.service import create_product
    sku = f"PRD-POSRX-{_uid()}"
    try:
        await create_product({"sku": sku, "name": f"PosRx {_uid()}",
                                  "type": "TRADE_ITEM", "uom": "BOX",
                                  "standard_cost": 3, "price": 8,
                                  "is_prescription": True})
    except Exception:
        pass
    await _stock(sku, "WH-MAIN", 10, batch=f"B-POSRX-{_uid()}")
    with pytest.raises(ValidationFailed):
        await sales_svc.pos_sale({
            "lines": [{"sku": sku, "quantity": 1, "unit_price": 8}],
            "payment_method": "CASH"}, ADMIN, f"POS-AF-{_uid()}")


# =================================== M. outbox: dispatch-once + handler retry
async def test_outbox_handler_failure_retries_not_done():
    from app.core.events import bus
    calls = {"n": 0}

    async def flaky(payload):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")

    name = f"test.flaky.{_uid()}"
    bus.subscribe(name, flaky)
    try:
        await bus.publish(name, {"x": 1}, ADMIN)
        # inline path failed → row must be back to PENDING for the pump
        row = await db.db.outbox_events.find_one(
            {"name": name, "payload": {"x": 1}})
        assert row is not None
        for _ in range(10):
            if row["status"] == "DONE":
                break
            assert row["status"] == "PENDING", \
                f"failed handler must stay retryable, got {row['status']}"
            await bus.pump_once(limit=5)
            row = await db.db.outbox_events.find_one({"_id": row["_id"]})
        assert row["status"] == "DONE"
        assert calls["n"] == 3, \
            f"handler retried until success exactly: {calls['n']} calls"
        # dispatch-once: pumping again must not re-run the handler
        await bus.pump_once(limit=50)
        assert calls["n"] == 3, "DONE event must never re-dispatch"
    finally:
        bus._handlers.get(name, []).remove(flaky)


async def test_outbox_inline_and_pump_never_double_dispatch():
    from app.core.events import bus
    calls = {"n": 0}

    async def ok_handler(payload):
        calls["n"] += 1

    name = f"test.once.{_uid()}"
    bus.subscribe(name, ok_handler)
    try:
        await bus.publish(name, {"y": 2}, ADMIN)
        await bus.pump_once(limit=50)
        assert calls["n"] == 1, \
            f"event must be dispatched exactly once, got {calls['n']}"
    finally:
        bus._handlers.get(name, []).remove(ok_handler)


# ==================================== N/O. RBAC deny-by-default + role matrix
async def test_unknown_read_kind_denied():
    from app.core.rbac import can_read
    assert can_read(["WAREHOUSE"], "inventory") is True
    assert can_read(["SALES"], "inventory") is True   # ATP visibility
    assert can_read(["CUSTOMER"], "inventory") is False
    assert can_read(["WAREHOUSE"], "never_mapped_kind") is False, \
        "unknown kinds must deny by default"


async def test_department_authorization_matrix():
    from app.core.rbac import has_permission
    assert has_permission(["WAREHOUSE"], "payment:write") is False
    assert has_permission(["FINANCE"], "payment:write") is True
    assert has_permission(["SALES"], "qc:write") is False
    assert has_permission(["QC"], "payment:authorize") is False


# ============================== Q. MFA enrollment enforced for required roles
async def test_mfa_required_role_without_enrollment_gets_enrollment_flow():
    """Role mandated for MFA + no enrolled secret → 403 enrollment required,
    never a token pair."""
    from app.api import routes_auth
    from fastapi import HTTPException
    import app.core.config as cfg
    from app.core.security import hash_password
    original = cfg.settings.MFA_ENFORCE_ROLES
    cfg.settings.MFA_ENFORCE_ROLES = "SUPER_ADMIN"
    try:
        # dedicated user: MFA-required role, no enrolled secret, known password
        email = f"mfa-{_uid()}@af.test"
        await db.db.users.insert_one({
            "user_id": f"mfa-{_uid()}", "email": email,
            "name": "MFA Probe", "roles": ["SUPER_ADMIN"],
            "password_hash": hash_password("Str0ngPass!234"),
            "status": "ACTIVE", "active": True,
            "mfa_secret": None, "mfa_secret_pending": None,
            "failed_logins": 0})
        payload = routes_auth.LoginIn(email=email, password="Str0ngPass!234")
        with pytest.raises(HTTPException) as exc:
            await routes_auth.login(payload)
        assert exc.value.status_code == 403
        assert "MFA_ENROLLMENT_REQUIRED" in str(exc.value.detail)
    finally:
        cfg.settings.MFA_ENFORCE_ROLES = original


# ======================================= supervisor must not write business data
async def test_supervisor_planning_writes_go_through_domain():
    """create_planning_proposal is the only supervisor path for proposals:
    deduped, evented, audited — and the supervisor module has no direct
    planning_proposals/insert path left."""
    import app.agents.supervisor as sup
    import inspect
    src = inspect.getsource(sup)
    assert "planning_proposals.insert_one" not in src
    assert "db.db.approvals.update_one" not in src

    from app.domains.planning.service import create_planning_proposal
    plan = {"reason": "test", "options": []}
    p1 = await create_planning_proposal(f"PRD-{_uid()}", "BUY", 5, plan,
                                        ADMIN, source="TEST")
    p2 = await create_planning_proposal(p1["product_id"], "BUY", 5, plan,
                                        ADMIN, source="TEST")
    assert p1["proposal_id"] == p2["proposal_id"], \
        "one open proposal per product (dedupe)"


# ========================== staged movement lifecycle: ledger reconstruct rule
async def test_void_movements_excluded_from_ledger_reconstruction():
    sku = f"PRD-{_uid()}"
    from app.domains.masters.service import create_product
    try:
        await create_product({"sku": sku, "name": f"VR {_uid()}",
                                  "type": "TRADE_ITEM", "uom": "BOX",
                                  "standard_cost": 2, "price": 4})
    except Exception:
        pass
    batch = f"B-VR-{_uid()}"
    await _stock(sku, "WH-MAIN", 8, batch=batch)
    with pytest.raises(ConflictError):
        await inventory.record_movement(
            "DAMAGE", sku, "WH-MAIN", 9, batch_id=batch,
            reference_type="TEST", reference_id=f"VR-{_uid()}",
            performed_by={"type": "SYSTEM", "id": "af"})
    posted = [m async for m in db.db.inventory_movements.find(
        {"product_id": sku, "batch_id": batch, "lifecycle": "POSTED"})]
    net = sum(float(m["signed_quantity"]) for m in posted)
    bal = await db.db.inventory_balances.find_one(
        {"product_id": sku, "batch_id": batch, "stock_status": "AVAILABLE"})
    assert net == float(bal["quantity"]), \
        f"POSTED ledger must reconstruct the balance: {net} vs {bal['quantity']}"
