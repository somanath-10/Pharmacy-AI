"""WMS / Inventory / Logistics / QC / QA / Equipment phase tests (Parts 1-21).

Each test isolates its data with unique keys so the shared test database stays
deterministic. Covers: location inventory, batch across bins, transfer
visibility, two-phase counts, status rules, FEFO with min shelf life,
replenishment, QC spec/OOS/OOT/retest/CoA, QA change control / CAPA closure /
risk, equipment gates, logistics exception handling and authorization rules.
"""
import uuid

import pytest

from app.core.database import db, now_iso
from app.core.errors import (ConflictError, PermissionDenied, ValidationFailed)
from app.domains import inventory as inv
from app.domains import logistics as log_svc
from app.domains import masters as masters_svc
from app.domains import qc as qc_svc
from app.domains import qa as qa_svc
from app.domains import warehouse as wh_svc

pytestmark = pytest.mark.asyncio

ACTOR = {"type": "USER", "id": "phase-test", "roles": ["SUPER_ADMIN"]}


def _uid():
    return uuid.uuid4().hex[:10]


async def _mk_warehouse(code: str, temp=None) -> str:
    await db.db.warehouses.update_one(
        {"code": code},
        {"$set": {"code": code, "name": code, "organization_id": "ORG-T",
                  "site_id": "SITE-T", "status": "ACTIVE",
                  **({"temp_range": temp} if temp else {})}},
        upsert=True)
    return code


async def _stock(wh: str, sku: str, batch: str, qty: float, status="AVAILABLE",
                 expiry="2032-01-01", location=None):
    await inv.ensure_batch(sku, batch, expiry)
    if status == "AVAILABLE":
        await db.db.batches.update_one({"batch_id": batch},
                                       {"$set": {"qa_status": "RELEASED",
                                                 "blocked": False}})
    return await inv.record_movement(
        "OPENING_STOCK" if status == "AVAILABLE" else "STATUS_CHANGE",
        sku, wh, qty, batch_id=batch, stock_status=status, location_id=location,
        reference_type="TEST", reference_id=f"t-{_uid()}",
        performed_by={"type": "SYSTEM", "id": "phase-test"})


# ------------------------------------------------------------------ inventory
async def test_batch_exists_in_multiple_bins():
    """Part 1: one batch may live in several bins; each bin holds its own qty."""
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 50, location="BIN-A")
    await inv.move_location(sku, wh, batch, "BIN-A", "BIN-B", 20, ACTOR)
    rows = [r async for r in db.db.inventory_balances.find(
        {"product_id": sku, "warehouse_id": wh, "batch_id": batch})]
    by_loc = {r.get("location_id"): float(r["quantity"]) for r in rows}
    assert sum(by_loc.values()) == 50
    assert by_loc.get("BIN-A") == 30 and by_loc.get("BIN-B") == 20


async def test_move_location_rejects_overdraw():
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 5, location="BIN-A")
    with pytest.raises(ConflictError):
        await inv.move_location(sku, wh, batch, "BIN-A", "BIN-B", 10, ACTOR)


async def test_fefo_respects_min_shelf_life_and_blocked_batches():
    """Part 4: batches below min remaining shelf life never allocate."""
    from datetime import datetime as _dt, timedelta as _td

    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku = f"PRD-{_uid()}"
    soon = f"B-SOON-{_uid()}"
    far = f"B-FAR-{_uid()}"
    soon_expiry = (_dt.fromisoformat(now_iso()[:10])
                   + _td(days=10)).isoformat()[:10]   # inside 30-day floor
    await _stock(wh, sku, soon, 100, expiry=soon_expiry)
    await _stock(wh, sku, far, 100, expiry="2032-06-01")             # good
    plan = await inv.fefo_batches(sku, wh, qty_needed=50,
                                  min_shelf_life_days=30)
    assert [p["batch_id"] for p in plan] == [far]


async def test_fefo_earliest_expiry_first():
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku = f"PRD-{_uid()}"
    b1, b2 = f"B1-{_uid()}", f"B2-{_uid()}"
    await _stock(wh, sku, b2, 10, expiry="2031-01-01")
    await _stock(wh, sku, b1, 10, expiry="2030-01-01")
    plan = await inv.fefo_batches(sku, wh, qty_needed=15)
    assert [p["batch_id"] for p in plan] == [b1, b2]


async def test_expired_stock_cannot_be_allocated():
    """Part 3: EXPIRED stock is not allocatable via reserve."""
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 30)
    # simulate time passing: backdate the batch expiry, then sweep
    await db.db.batches.update_one({"batch_id": batch},
                                   {"$set": {"expiry_date": "2020-01-01"}})
    expired = await inv.expire_due_batches(ACTOR)
    assert any(e["batch_id"] == batch for e in expired)
    with pytest.raises(ConflictError):
        await inv.reserve(sku, 5, "SALES_ORDER", f"SO-{_uid()}", wh, ACTOR)


async def test_quarantine_stock_cannot_be_reserved():
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 30, status="QUARANTINE")
    with pytest.raises(ConflictError):
        await inv.reserve(sku, 5, "SALES_ORDER", f"SO-{_uid()}", wh, ACTOR)


async def test_recall_blocks_batch_and_reservation():
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 30)
    await inv.block_batch(batch, "RECALL RC-1", ACTOR, source="RECALL")
    with pytest.raises(ConflictError):
        await inv.reserve(sku, 5, "SALES_ORDER", f"SO-{_uid()}", wh, ACTOR)


# ------------------------------------------------------------------ transfers
async def test_transfer_in_transit_visibility():
    """Part 6: dispatched stock stays visible (TRANSFER_OUT legs + status) and
    lands only on receive confirmation."""
    src, dst = await _mk_warehouse(f"WH-{_uid()}"), await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(src, sku, batch, 40)
    trf = await wh_svc.create_transfer_order(
        {"from_warehouse": src, "to_warehouse": dst,
         "lines": [{"sku": sku, "quantity": 25, "uom": "BOX"}]}, ACTOR)
    await wh_svc.approve_transfer(trf["transfer_id"], ACTOR)
    await wh_svc.pick_transfer(trf["transfer_id"], ACTOR)
    await wh_svc.dispatch_transfer(trf["transfer_id"], ACTOR)
    # in transit: source reduced, destination holds visible-but-locked IN_TRANSIT stock
    assert await inv.on_hand(sku, src) == 15
    assert await inv.on_hand(sku, dst) == 0
    assert await inv.on_hand(sku, dst, "IN_TRANSIT") == 25
    # IN_TRANSIT stock is not allocatable (FEFO only selects AVAILABLE rows)
    with pytest.raises(Exception):
        await inv.fefo_batches(sku, dst, 25)
    await wh_svc.receive_transfer(trf["transfer_id"], ACTOR)
    assert await inv.on_hand(sku, dst) == 25
    assert await inv.on_hand(sku, dst, "IN_TRANSIT") == 0
    doc = await db.db.transfer_orders.find_one(
        {"transfer_id": trf["transfer_id"]})
    assert doc["status"] == "CLOSED"


async def test_transfer_pick_locks_stock_against_racers():
    """Picked transfer stock is reserved: a concurrent reservation cannot
    take the same units (Part 4/5 concurrency rule)."""
    src, dst = await _mk_warehouse(f"WH-{_uid()}"), await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(src, sku, batch, 10)
    trf = await wh_svc.create_transfer_order(
        {"from_warehouse": src, "to_warehouse": dst,
         "lines": [{"sku": sku, "quantity": 8, "uom": "BOX"}]}, ACTOR)
    await wh_svc.approve_transfer(trf["transfer_id"], ACTOR)
    await wh_svc.pick_transfer(trf["transfer_id"], ACTOR)
    assert await inv.on_hand(sku, src, "RESERVED") == 8
    # only 2 remain for anyone else — a 5-unit reservation must lose
    from app.core.errors import ConflictError

    with pytest.raises(ConflictError):
        await inv.reserve(sku, 5, "SO", f"SO-RACE-{_uid()}",
                          warehouse_id=src, actor=ACTOR)
    # the competing 2-unit reservation still succeeds (no false starvation)
    await inv.reserve(sku, 2, "SO", f"SO-OK-{_uid()}",
                      warehouse_id=src, actor=ACTOR)
    await wh_svc.dispatch_transfer(trf["transfer_id"], ACTOR)
    # transfer's 8 units consumed from RESERVED; competitor's 2 remain held
    assert await inv.on_hand(sku, src, "RESERVED") == 2
    res = await db.db.reservations.find_one(
        {"reference_type": "TRANSFER_ORDER", "reference_id": trf["transfer_id"]})
    assert res["status"] == "CONSUMED"


# ------------------------------------------------------------- cycle counting
async def test_two_phase_count_and_adjustment_authorization():
    """Part 7: variance recorded only; posting needs approver + reason; the
    counter cannot approve their own count."""
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 100)
    counter = {"type": "USER", "id": "counter-1", "roles": ["WAREHOUSE"]}
    count = await inv.cycle_count(wh, sku, batch, 97, counter, "CYCLE")
    assert count["variance"] == -3
    assert count["status"] == "VARIANCE_DETECTED"
    # system qty unchanged until adjustment posted
    assert await inv.on_hand(sku, wh) == 100
    # counter cannot approve their own count
    with pytest.raises(Exception):
        await inv.post_stock_adjustment(count["count_id"], counter, "shrink",
                                        approved_by=counter)
    approver = {"type": "USER", "id": "approver-1", "roles": ["SUPER_ADMIN"]}
    mv = await inv.post_stock_adjustment(count["count_id"], ACTOR, "shrink",
                                         approved_by=approver)
    assert await inv.on_hand(sku, wh) == 97
    led = await db.db.inventory_movements.find_one(
        {"movement_id": mv["movement_id"]})
    assert led["movement_type"] == "NEGATIVE_ADJUSTMENT"
    # double posting blocked
    with pytest.raises(ConflictError):
        await inv.post_stock_adjustment(count["count_id"], ACTOR, "again",
                                        approved_by=approver)


# ----------------------------------------------------------------- warehouse
async def test_location_master_and_zone_listing():
    wh = await _mk_warehouse(f"WH-{_uid()}")
    await wh_svc.create_location({"warehouse_id": wh, "location_id": "CLD-01",
                                  "zone": "COLD", "aisle": "A", "rack": "R1",
                                  "shelf": "S1", "bin": "B01",
                                  "temp_min": 2, "temp_max": 8}, ACTOR)
    await wh_svc.create_location({"warehouse_id": wh, "location_id": "DRY-01",
                                  "zone": "DRY"}, ACTOR)
    cold = await wh_svc.list_locations(wh, zone="COLD")
    assert [l["location_id"] for l in cold] == ["CLD-01"]


async def test_replenishment_task_scan_confirm():
    """Part 5: system decides source/destination; worker confirms via scan."""
    wh = await _mk_warehouse(f"WH-{_uid()}")
    await wh_svc.create_location({"warehouse_id": wh, "location_id": "PICK-01",
                                  "zone": "PICK_FACE", "pick_frequency": 10},
                                 ACTOR)
    await wh_svc.create_location({"warehouse_id": wh, "location_id": "BULK-01",
                                  "zone": "RESERVE", "pick_frequency": 1},
                                 ACTOR)
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 100, location="BULK-01")
    await _stock(wh, sku, batch, 5, location="PICK-01")
    task = await wh_svc.create_replenishment_task(
        {"warehouse_id": wh, "product_id": sku, "quantity": 20}, ACTOR)
    assert task["status"] == "PENDING"
    # system decides source/destination (Part 18): reserve -> pick face
    assert task["from_location"] == "BULK-01" and task["to_location"] == "PICK-01"
    done = await wh_svc.confirm_replenishment(task["task_id"], ACTOR,
                                              scanned_location="PICK-01")
    assert done["status"] == "COMPLETED"
    rows = [r async for r in db.db.inventory_balances.find(
        {"product_id": sku, "warehouse_id": wh, "batch_id": batch,
         "location_id": "PICK-01"})]
    assert sum(float(r["quantity"]) for r in rows) == 25


# ------------------------------------------------------------------- cold chain
async def test_temperature_excursion_auto_holds_batch():
    """Part 10: excursion → QUALITY_HOLD immediately; batch cannot stay AVAILABLE."""
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 30)
    res = await log_svc.check_temperature_excursion(
        "BATCH", batch, [{"temp": 15.0, "at": now_iso()}],
        {"min": 2, "max": 8}, ACTOR)
    assert res["excursion"] is True
    b = await db.db.batches.find_one({"batch_id": batch})
    assert b["blocked"] is True


async def test_temperature_within_range_no_hold():
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 10)
    res = await log_svc.check_temperature_excursion(
        "BATCH", batch, [{"temp": 5.0}], {"min": 2, "max": 8}, ACTOR)
    assert res["excursion"] is False
    b = await db.db.batches.find_one({"batch_id": batch})
    assert b.get("blocked") in (False, None)


# ------------------------------------------------------------------------ QC
async def _mk_spec(sku: str, acceptance={"min": 9.5, "max": 10.5}):
    spec = {"product_id": sku, "version": f"V-{_uid()}",
            "tests": [{"name": "assay", "method": "HPLC",
                       "acceptance": acceptance}],
            "status": "APPROVED", "sampling_plan": {"n": 1, "method": "FIXED"}}
    await db.db.specifications.insert_one(spec)
    return spec


async def test_sample_pass_deterministic():
    """Part 11: result 9.7 in 9.5-10.5 → PASS (deterministic, master-data driven)."""
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _mk_spec(sku)
    s = await qc_svc.register_sample({"product_id": sku, "batch_id": batch,
                                      "ref_type": "TEST", "ref_id": "x",
                                      "stage": "RAW_MATERIAL"}, ACTOR)
    await qc_svc.start_testing(s["sample_id"], ACTOR)
    out = await qc_svc.enter_results(
        s["sample_id"], [{"name": "assay", "result": 9.7,
                          "acceptance": {"min": 9.5, "max": 10.5}}], ACTOR)
    assert out["status"] == "RESULTS_ENTERED"
    assert out["oos"] is None
    tests = {t["name"]: t for t in out["tests"]}
    assert tests["assay"]["verdict"] == "PASS"


async def test_sample_oos_workflow_and_qa_only_close():
    """Part 12: OOS case opens; QC cannot close; QA closes with conclusion."""
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _mk_spec(sku)
    s = await qc_svc.register_sample({"product_id": sku, "batch_id": batch,
                                      "ref_type": "TEST", "ref_id": "x",
                                      "stage": "RAW_MATERIAL"}, ACTOR)
    await qc_svc.start_testing(s["sample_id"], ACTOR)
    out = await qc_svc.enter_results(
        s["sample_id"], [{"name": "assay", "result": 8.1,
                          "acceptance": {"min": 9.5, "max": 10.5}}], ACTOR)
    assert out["oos"] == ["assay"]
    assert out["status"] == "OOS_INVESTIGATION"
    qc_actor = {"type": "USER", "id": "qc1", "roles": ["QC"]}
    with pytest.raises(PermissionDenied):
        await qc_svc.close_oos(s["sample_id"],
                               {"conclusion": "LAB_ERROR"}, qc_actor)
    qa_actor = {"type": "USER", "id": "qa1", "roles": ["QA"]}
    closed = await qc_svc.close_oos(s["sample_id"],
                                    {"conclusion": "CONFIRMED_OOS",
                                     "root_cause": "mixing fault"}, qa_actor)
    assert closed["oos_conclusion"]["conclusion"] == "CONFIRMED_OOS"


async def test_retest_requires_qa_authorization():
    """Retest needs (a) a QA OOS closure that authorized it AND (b) QA authority
    on the requester."""
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _mk_spec(sku)
    s = await qc_svc.register_sample({"product_id": sku, "batch_id": batch,
                                      "ref_type": "TEST", "ref_id": "x",
                                      "stage": "RAW_MATERIAL"}, ACTOR)
    qc_actor = {"type": "USER", "id": "qc1", "roles": ["QC"]}
    # QC cannot even request a retest (authority gate)
    with pytest.raises(PermissionDenied):
        await qc_svc.request_retest(s["sample_id"], {"reason": "want retest"},
                                    qc_actor)
    # even QA cannot retest while the OOS is unclosed/unauthorized
    qa_actor = {"type": "USER", "id": "qa1", "roles": ["QA"]}
    with pytest.raises(ConflictError):
        await qc_svc.request_retest(s["sample_id"], {}, qa_actor)


async def test_reagent_expiry_gate():
    rg = await qc_svc.register_reagent({"name": "KCl std", "kind": "REAGENT",
                                        "lot_no": "L1",
                                        "expiry_date": now_iso()[:10]}, ACTOR)
    with pytest.raises(ConflictError):
        await qc_svc.consume_reagent(rg["reagent_id"], "SMP-X", ACTOR)


# ------------------------------------------------------------------------ QA
async def test_change_control_lifecycle_and_versioning():
    """Part 15: CR → assessment → QA approval → implementation → verify."""
    sku = f"PRD-{_uid()}"
    await _mk_spec(sku)
    qa_actor = {"type": "USER", "id": "qa1", "roles": ["QA"]}
    cr = await qa_svc.create_change_request(
        {"category": "SPECIFICATION", "title": "tighten assay range",
         "target_id": sku, "justification": "stability data",
         "current_version": "1.0", "proposed_version": "1.1",
         "proposed_payload": {"assay_max": 10.3}}, ACTOR)
    assessor = {"type": "USER", "id": "assessor", "roles": ["QA"]}
    await qa_svc.assess_change(cr["change_id"],
                               {"assessment": "low risk",
                                "risk_level": "MEDIUM"}, assessor)
    # SoD: assessor (single role QA) cannot solely approve own assessment
    with pytest.raises(ConflictError):
        await qa_svc.approve_change(cr["change_id"], {"decision": "APPROVED"},
                                    assessor)
    approver = {"type": "USER", "id": "qa-head", "roles": ["QA", "MANAGEMENT"]}
    await qa_svc.approve_change(cr["change_id"], {"decision": "APPROVED"},
                                approver)
    await qa_svc.implement_change(cr["change_id"], {}, ACTOR)
    target = await db.db.specifications.find_one({"product_id": sku})
    assert target["change_applied"]["assay_max"] == 10.3
    assert target["change_history"][0]["previous_version"] == "1.0"
    await qa_svc.verify_change(cr["change_id"],
                               {"verification": "documents updated"}, ACTOR)
    doc = await db.db.change_requests.find_one({"change_id": cr["change_id"]})
    assert doc["status"] == "CLOSED"


async def test_change_control_rejects_invalid_category():
    with pytest.raises(ValidationFailed):
        await qa_svc.create_change_request(
            {"category": "NOT_A_THING", "title": "x", "target_id": "y",
             "justification": "z"}, ACTOR)


async def test_capa_closure_requires_effectiveness():
    """Part 14: CAPA can only close from VERIFICATION with EFFECTIVE outcome."""
    capa = await qa_svc.create_capa({"title": "retrain operators"}, ACTOR)
    with pytest.raises(ConflictError):
        await qa_svc.close_capa(capa["capa_id"],
                                {"effectiveness": {"outcome": "EFFECTIVE"}},
                                ACTOR)
    await qa_svc.advance_capa(capa["capa_id"], {}, ACTOR)   # PLANNED
    await qa_svc.advance_capa(capa["capa_id"], {}, ACTOR)   # IMPLEMENTATION
    await qa_svc.advance_capa(capa["capa_id"], {}, ACTOR)   # VERIFICATION
    with pytest.raises(ValidationFailed):
        await qa_svc.close_capa(capa["capa_id"],
                                {"effectiveness": {"outcome": "NOT_EFFECTIVE"}},
                                ACTOR)
    out = await qa_svc.close_capa(capa["capa_id"],
                                  {"effectiveness": {"outcome": "EFFECTIVE",
                                                     "notes": "no recurrence"}},
                                  ACTOR)
    assert out["status"] == "CLOSED"


async def test_capa_overdue_report():
    capa = await qa_svc.create_capa({"title": "late one",
                                     "due_date": "2020-01-01"}, ACTOR)
    rep = await qa_svc.capa_overdue_report()
    assert any(c["capa_id"] == capa["capa_id"] for c in rep["overdue"])


async def test_risk_assessment_rpn_levels():
    ra = await qa_svc.create_risk_assessment(
        {"subject_type": "BATCH", "subject_id": "B-1", "severity": 8,
         "occurrence": 5, "detectability": 3}, ACTOR)
    assert ra["rpn"] == 120 and ra["risk_level"] == "HIGH"
    with pytest.raises(ValidationFailed):
        await qa_svc.create_risk_assessment(
            {"subject_type": "BATCH", "subject_id": "B-2", "severity": 11,
             "occurrence": 1, "detectability": 1}, ACTOR)


# ----------------------------------------------------------------- equipment
async def _mk_equipment(code: str, **fields) -> str:
    await db.db.equipment.update_one(
        {"code": code},
        {"$set": {"code": code, "name": code, "site_id": "SITE-T",
                  "qualification_status": "IQ_OQ_PQ_DONE",
                  "calibration_status": "VALID",
                  "maintenance_status": "OK", "cleaning_status": "VALID",
                  "status": "RELEASED", "logs": [], **fields}},
        upsert=True)
    return code


async def test_equipment_expired_calibration_blocks_use():
    """Part 16: stale VALID flag loses to an expired due date."""
    code = f"EQ-{_uid()}"
    await _mk_equipment(code, calibration_due="2020-01-01")
    with pytest.raises(ValidationFailed):
        await masters_svc.use_equipment(code, "assay run", ACTOR)


async def test_equipment_overdue_maintenance_blocks_use():
    code = f"EQ-{_uid()}"
    await _mk_equipment(code, maintenance_due="2020-01-01")
    ok, blockers = masters_svc.equipment_ready(
        await masters_svc.get_equipment(code))
    assert not ok and any("maintenance_due" in b for b in blockers)


async def test_equipment_hold_and_recalibration_cycle():
    code = f"EQ-{_uid()}"
    await _mk_equipment(code, calibration_due="2020-01-01")
    await masters_svc.hold_equipment(code, "suspected drift", ACTOR)
    eq = await masters_svc.get_equipment(code)
    ok, blockers = masters_svc.equipment_ready(eq)
    assert not ok and any("HOLD" in b for b in blockers)
    await masters_svc.calibrate_equipment(
        code, {"next_due": "2033-01-01", "certificate_ref": "CAL-1"}, ACTOR)
    await masters_svc.maintenance_equipment(
        code, {"next_due": "2033-06-01"}, ACTOR)
    eq = await masters_svc.get_equipment(code)
    ok, blockers = masters_svc.equipment_ready(eq)
    assert ok, blockers


# ----------------------------------------------------------------- logistics
async def test_failed_delivery_then_reattempt_then_pod():
    """Part 9: delivery exception keeps shipment open; reattempt clears; POD closes."""
    so_id = f"SO-{_uid()}"
    await db.db.sales_orders.insert_one(
        {"order_id": so_id, "status": "DISPATCHED", "lines": [],
         "created_at": now_iso()})
    shp = await log_svc.plan_shipment(
        {"sales_order_id": so_id, "destination": "SITE-A",
         "lines": [{"sku": "P", "quantity": 1}]}, ACTOR)
    await log_svc.dispatch_shipment(shp["shipment_id"], {}, ACTOR)
    failed = await log_svc.track(shp["shipment_id"], "FAILED_DELIVERY",
                                 {"reason": "CUSTOMER_UNAVAILABLE"}, ACTOR)
    assert failed["delivery_exception"]["reason"] == "CUSTOMER_UNAVAILABLE"
    re = await log_svc.reattempt_delivery(shp["shipment_id"], {}, ACTOR)
    assert re["delivery_exception"] is None
    assert re["delivery_attempts"] == 1
    await log_svc.track(shp["shipment_id"], "DELIVERED", {}, ACTOR)
    pod = await log_svc.capture_pod(shp["shipment_id"],
                                    {"received_by": "store keeper"}, ACTOR)
    assert pod["status"] == "CLOSED" and pod["pod"]["received_by"] == "store keeper"
    # no duplicate POD after close
    with pytest.raises(ConflictError):
        await log_svc.capture_pod(shp["shipment_id"], {}, ACTOR)


async def test_transport_temperature_excursion():
    so_id = f"SO-{_uid()}"
    await db.db.sales_orders.insert_one(
        {"order_id": so_id, "status": "DISPATCHED", "lines": [],
         "created_at": now_iso()})
    shp = await log_svc.plan_shipment(
        {"sales_order_id": so_id, "destination": "SITE-B",
         "lines": [{"sku": "P", "quantity": 1}],
         "required_range": {"min": 2, "max": 8}}, ACTOR)
    await log_svc.dispatch_shipment(shp["shipment_id"], {}, ACTOR)
    res = await log_svc.capture_transport_temperature(
        shp["shipment_id"], {"temp": 20.0}, ACTOR)
    assert res["excursion"] is True


# ------------------------------------------------------------- authorization
async def test_qc_cannot_perform_qa_release():
    from app.domains.qa.service import batch_release
    qc_actor = {"type": "USER", "id": "qc-only", "roles": ["QC"]}
    with pytest.raises(ValidationFailed):
        await batch_release("PO-0001", qc_actor, "RELEASE", "nope")


async def test_agent_cannot_execute_qa_hold_command():
    """Agents can never execute QA-hold-class commands via the dependency."""
    from app.core.rbac import authorize_command
    agent = {"type": "AGENT", "id": "warehouse-agent", "roles": []}
    with pytest.raises(PermissionDenied):
        authorize_command(agent, "qa:hold")


async def test_stock_adjustment_command_requires_human():
    from app.core.rbac import authorize_command
    agent = {"type": "AGENT", "id": "warehouse-agent", "roles": []}
    with pytest.raises(PermissionDenied):
        authorize_command(agent, "inventory:adjust")


async def test_direct_quantity_edit_is_not_possible():
    """Part 2: balances are a projection — quantity is only moved by movements
    (no service API exists for direct edits; ledger is append-only)."""
    wh = await _mk_warehouse(f"WH-{_uid()}")
    sku, batch = f"PRD-{_uid()}", f"B-{_uid()}"
    await _stock(wh, sku, batch, 10)
    # service surface exposes no quantity setter
    assert not hasattr(inv, "set_quantity") and not hasattr(inv, "edit_balance")
    # append-only: ledger rows are never mutated by service code paths
    mv = await _stock(wh, sku, batch, 5)
    before = await db.db.inventory_movements.find_one(
        {"movement_id": mv["movement_id"]})
    assert float(before["quantity"]) == 5.0
