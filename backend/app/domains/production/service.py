"""Plant / Production (MES): production orders, eBMR, material issue, equipment
gates, in-process controls, yield reconciliation, FG quarantine."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.workflow import transition, record_node
from app.domains.inventory import service as inventory
from app.domains.masters.service import equipment_ready, get_active_bom


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


async def create_production_order(payload: dict, actor: dict,
                                  idempotency_key: Optional[str] = None) -> dict:
    if not payload.get("product_id") or not payload.get("batch_size"):
        raise ValidationFailed("production order needs product_id + batch_size")
    bom = await get_active_bom(payload["product_id"])
    order_id = await _next_id("production_order", "MPO")

    async with idempotent("MPO", idempotency_key) as gate:
        if not gate["first_time"]:
            return gate["result"]
        batch_id = payload.get("batch_id") or await _next_id("batch", "B")
        await inventory.ensure_batch(payload["product_id"], batch_id,
                                     payload.get("expiry_date"),
                                     payload.get("mfg_date"))
        await db.db.batches.update_one(
            {"batch_id": batch_id},
            {"$set": {"qa_status": "EXPECTED", "manufacturer": "SELF",
                      "updated_at": now_iso()}})
        doc = {
            "order_id": order_id,
            "product_id": payload["product_id"],
            "batch_id": batch_id,
            "bom_version": bom["version"],
            "batch_size": float(payload["batch_size"]),
            "uom": payload.get("uom", bom.get("uom", "BOX")),
            "site_id": payload.get("site_id"),
            "equipment_codes": payload.get("equipment_codes", []),
            "planned_start": payload.get("planned_start"),
            "planned_end": payload.get("planned_end"),
            "min_yield_pct": payload.get("min_yield_pct", 95.0),
            "status": "PLANNED",
            "materials_reserved": False,
            "issued": False,
            "line_cleared": False,
            "yield_pct": None,
            "batch_record_completed": False,
            "ebmr_steps": [],
            "created_by": actor,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "PLANNED", "actor": actor, "at": now_iso()}],
        }
        await db.db.production_orders.insert_one(doc)
        await audit("PRODUCTION_ORDER", order_id, "CREATED", actor,
                    details={"batch": batch_id, "bom": bom["version"]})
        result = _clean(doc)
        gate.store(result)
    return result


async def release_order(order_id: str, actor: dict) -> dict:
    await transition("production_order", order_id, "production_orders", "order_id",
                     "RELEASED", actor, reason="Released to shop floor")
    await record_node("production_order", order_id, "released", "Order Released",
                      "DONE", actor)
    return await _get(order_id)


async def reserve_materials(order_id: str, actor: dict) -> dict:
    """Reserve BOM components (FEFO) for this batch."""
    order = await _get(order_id)
    if order["status"] != "RELEASED":
        raise ConflictError(f"Order not RELEASED: {order['status']}")
    bom = await db.db.boms.find_one(
        {"product_id": order["product_id"], "version": order["bom_version"]})
    if not bom:
        raise NotFound("BOM missing")
    reservations = []
    for comp in bom["components"]:
        qty = float(comp["qty_per_batch"]) * (float(order["batch_size"]) /
                                              float(bom["batch_size"]))
        r = await inventory.reserve(comp["sku"], qty, "PRODUCTION_ORDER",
                                    order_id, actor=actor)
        reservations.append({"sku": comp["sku"], "quantity": qty,
                             "reservation_id": r["reservation_id"],
                             "allocation": r["allocation"]})
    await db.db.production_orders.update_one(
        {"order_id": order_id},
        {"$set": {"materials_reserved": True, "reservations": reservations,
                  "updated_at": now_iso()}})
    await transition("production_order", order_id, "production_orders", "order_id",
                     "DISPENSING", actor, reason="Materials reserved (FEFO)")
    return await _get(order_id)


async def line_clearance(order_id: str, payload: dict, actor: dict) -> dict:
    order = await _get(order_id)
    if order["status"] != "DISPENSING":
        raise ConflictError("Line clearance after dispensing stage")
    checks = payload.get("checks", {})
    required = ["area_clean", "previous_materials_removed", "labels_ready",
                "documented"]
    missing = [c for c in required if not checks.get(c)]
    if missing:
        raise ValidationFailed(f"Line clearance failed: {missing}")
    await db.db.production_orders.update_one(
        {"order_id": order_id},
        {"$set": {"line_cleared": True, "line_clearance": {
            "checks": checks, "by": actor, "at": now_iso()}}})
    await audit("PRODUCTION_ORDER", order_id, "LINE_CLEARED", actor)
    return await _get(order_id)


async def issue_materials(order_id: str, actor: dict) -> dict:
    """Consume reservations → MATERIAL_ISSUE ledger movements (inventory → WIP)."""
    order = await _get(order_id)
    if not order.get("line_cleared"):
        raise ValidationFailed("Line clearance required before material issue")
    if order["status"] not in ("DISPENSING", "IN_PROCESS"):
        raise ConflictError(f"Cannot issue in status {order['status']}")
    for r in order.get("reservations", []):
        await inventory.record_movement(
            movement_type="MATERIAL_ISSUE",
            product_id=r["sku"],
            warehouse_id=r["allocation"][0]["warehouse_id"] if r["allocation"]
            else order.get("site_id", "WH-MAIN"),
            quantity=r["quantity"],
            batch_id=(r["allocation"][0]["batch_id"]
                      if r["allocation"] and r["allocation"][0]["batch_id"] != "UNBATCHED"
                      else None),
            reference_type="PRODUCTION_ORDER",
            reference_id=order_id,
            performed_by=actor,
        )
    await db.db.production_orders.update_one(
        {"order_id": order_id}, {"$set": {"issued": True, "updated_at": now_iso()}})
    await transition("production_order", order_id, "production_orders", "order_id",
                     "IN_PROCESS", actor, reason="Materials issued to line")
    from app.domains.finance.service import post_event

    await post_event("material_issued", {"order_id": order_id,
                                         "product_id": order["product_id"],
                                         "batch_size": order["batch_size"]})
    return await _get(order_id)


async def equipment_gate(order_id: str, actor: dict) -> dict:
    """GMP gate: all assigned equipment must be calibration/maintenance/cleaning ready."""
    order = await _get(order_id)
    blockers = []
    for code in order.get("equipment_codes", []):
        eq = await db.db.equipment.find_one({"code": code})
        if not eq:
            blockers.append(f"{code}: not found")
            continue
        ok, why = equipment_ready(eq)
        if not ok:
            blockers.append(f"{code}: {', '.join(why)}")
    if blockers:
        await audit("PRODUCTION_ORDER", order_id, "EQUIPMENT_GATE_BLOCKED", actor,
                    details={"blockers": blockers})
        raise ConflictError(f"Equipment not ready: {blockers}")
    await audit("PRODUCTION_ORDER", order_id, "EQUIPMENT_GATE_PASSED", actor)
    return {"ready": True, "checked_at": now_iso()}


async def start_batch(order_id: str, actor: dict) -> dict:
    order = await _get(order_id)
    if order["status"] == "DISPENSING":
        # batch start IS the dispensing → in-process transition
        await transition("production_order", order_id, "production_orders",
                         "order_id", "IN_PROCESS", actor, reason="Batch start")
    order = await _get(order_id)
    if order["status"] != "IN_PROCESS":
        raise ConflictError(f"Order not IN_PROCESS: {order['status']}")
    await equipment_gate(order_id, actor)
    await _ebmr_step(order_id, "BATCH_START", "Batch started", actor)
    await bus.publish("production.started", {"order_id": order_id}, actor)
    return await _get(order_id)


async def complete_step(order_id: str, step: str, params: Optional[dict],
                        actor: dict) -> dict:
    """Record an eBMR process step (immutable append)."""
    order = await _get(order_id)
    if order["status"] not in ("IN_PROCESS", "PACKAGING"):
        raise ConflictError(f"Order not in execution: {order['status']}")
    await _ebmr_step(order_id, f"STEP_{step}", params or {}, actor)
    if step.upper() == "PACKAGING":
        await transition("production_order", order_id, "production_orders",
                         "order_id", "PACKAGING", actor)
    return await _get(order_id)


async def in_process_control(order_id: str, control: dict, actor: dict) -> dict:
    """IPC result (e.g., weight variation) — fail blocks progression."""
    order = await _get(order_id)
    if control.get("verdict") == "FAIL":
        from app.domains.qa.service import apply_quality_hold

        await apply_quality_hold(
            "PRODUCTION_ORDER", order_id,
            f"IPC failure: {control.get('name')}", actor)
    await _ebmr_step(order_id, f"IPC_{control.get('name', 'CHECK').upper()}",
                     control, actor)
    return await _get(order_id)


async def complete_production(order_id: str, actual_yield: float, actor: dict) -> dict:
    order = await _get(order_id)
    if order["status"] != "IN_PROCESS":
        raise ConflictError(f"Order not IN_PROCESS: {order['status']}")
    yield_pct = round(100.0 * float(actual_yield) / float(order["batch_size"]), 2)
    await db.db.production_orders.update_one(
        {"order_id": order_id},
        {"$set": {"actual_yield": float(actual_yield), "yield_pct": yield_pct}})
    await _ebmr_step(order_id, "YIELD_RECONCILIATION",
                     {"planned": order["batch_size"], "actual": actual_yield,
                      "yield_pct": yield_pct}, actor)
    # walk IN_PROCESS → PACKAGING (implicit for single-stage) → COMPLETED
    if order["status"] == "IN_PROCESS":
        await transition("production_order", order_id, "production_orders",
                         "order_id", "PACKAGING", actor,
                         reason="Single-stage completion")
    await transition("production_order", order_id, "production_orders", "order_id",
                     "COMPLETED", actor, reason=f"Yield {yield_pct}%")
    await transition("production_order", order_id, "production_orders", "order_id",
                     "FG_QUARANTINE", actor, reason="FG awaiting QC/QA")
    # FG receipt into quarantine (batch blocked until QA release)
    wh = order.get("site_id") or "WH-MAIN"
    await inventory.record_movement(
        movement_type="PRODUCTION_RECEIPT",
        product_id=order["product_id"],
        warehouse_id=wh,
        quantity=float(actual_yield),
        batch_id=order["batch_id"],
        reference_type="PRODUCTION_ORDER",
        reference_id=order_id,
        performed_by=actor,
        note=f"FG receipt yield {yield_pct}%",
    )
    await db.db.batches.update_one(
        {"batch_id": order["batch_id"]},
        {"$set": {"qa_status": "QUARANTINE", "blocked": True,
                  "block_reason": "FG quarantine — awaiting QA release",
                  "updated_at": now_iso()}})
    from app.domains.finance.service import post_event

    await post_event("production_completed",
                     {"order_id": order_id, "product_id": order["product_id"],
                      "batch_id": order["batch_id"], "quantity": actual_yield,
                      "yield_pct": yield_pct})
    await bus.publish("production.completed",
                      {"order_id": order_id, "batch_id": order["batch_id"]}, actor)
    return await _get(order_id)


async def complete_packaging(order_id: str, actor: dict) -> dict:
    order = await _get(order_id)
    if order["status"] != "PACKAGING":
        raise ConflictError(f"Order not PACKAGING: {order['status']}")
    await _ebmr_step(order_id, "PACKAGING_COMPLETE", {}, actor)
    await db.db.production_orders.update_one(
        {"order_id": order_id},
        {"$set": {"batch_record_completed": True}})
    await transition("production_order", order_id, "production_orders", "order_id",
                     "COMPLETED", actor, reason="Packaging complete")
    await transition("production_order", order_id, "production_orders", "order_id",
                     "FG_QUARANTINE", actor)
    return await _get(order_id)


async def submit_for_qc(order_id: str, actor: dict) -> dict:
    """FG quarantine → QC sampling."""
    order = await _get(order_id)
    if order["status"] != "FG_QUARANTINE":
        raise ConflictError(f"Order not FG_QUARANTINE: {order['status']}")
    from app.domains.qc.service import register_sample

    sample = await register_sample({
        "product_id": order["product_id"],
        "batch_id": order["batch_id"],
        "ref_type": "PRODUCTION_ORDER",
        "ref_id": order_id,
        "stage": "FINISHED_PRODUCT",
    }, actor)
    await transition("production_order", order_id, "production_orders", "order_id",
                     "QC_COMPLETE", actor, reason=f"QC sample {sample['sample_id']}")
    return {"sample_id": sample["sample_id"]}


async def submit_for_qa_review(order_id: str, actor: dict) -> dict:
    await transition("production_order", order_id, "production_orders", "order_id",
                     "QA_REVIEW", actor, reason="QC complete; QA review requested")
    from app.domains.qa.service import batch_release_review

    dossier = await batch_release_review(order_id, actor)
    return {"dossier": dossier}


async def _ebmr_step(order_id: str, step: str, data: Any, actor: dict):
    """Append-only eBMR step."""
    await db.db.production_orders.update_one(
        {"order_id": order_id},
        {"$push": {"ebmr_steps": {"step": step, "data": data, "actor": actor,
                                  "at": now_iso()}}})
    await audit("EBMR", order_id, step, actor, details=data if isinstance(data, dict) else None)


async def batch_record(order_id: str) -> dict:
    order = await _get(order_id)
    bmr = {
        "order_id": order_id,
        "product_id": order["product_id"],
        "batch_id": order["batch_id"],
        "bom_version": order["bom_version"],
        "batch_size": order["batch_size"],
        "status": order["status"],
        "line_clearance": order.get("line_clearance"),
        "reservations": order.get("reservations"),
        "steps": order.get("ebmr_steps", []),
        "yield": {"planned": order["batch_size"], "actual": order.get("actual_yield"),
                  "pct": order.get("yield_pct")},
        "batch_record_completed": order.get("batch_record_completed", False),
    }
    await db.db.batch_records.update_one(
        {"order_id": order_id}, {"$set": bmr}, upsert=True)
    return bmr


async def _get(order_id: str) -> dict:
    doc = await db.db.production_orders.find_one({"order_id": order_id})
    if not doc:
        raise NotFound(f"Production order {order_id} not found")
    return doc


async def get_order(order_id: str) -> dict:
    return await _get(order_id)


async def list_orders(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.production_orders.find(q).sort("created_at", -1).limit(200)]


async def plant_readiness() -> dict:
    equipment = [_clean(dict(e)) async for e in db.db.equipment.find({})]
    ready = sum(1 for e in equipment if equipment_ready(e)[0])
    orders_in_process = await db.db.production_orders.count_documents(
        {"status": {"$in": ["RELEASED", "DISPENSING", "IN_PROCESS", "PACKAGING"]}})
    fg_quarantine = await db.db.production_orders.count_documents(
        {"status": "FG_QUARANTINE"})
    pct = round(100.0 * ready / len(equipment), 0) if equipment else 100
    return {"equipment_total": len(equipment), "equipment_ready": ready,
            "readiness_pct": pct, "orders_in_process": orders_in_process,
            "fg_quarantine": fg_quarantine}
