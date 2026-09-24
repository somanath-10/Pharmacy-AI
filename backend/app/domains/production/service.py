"""Plant / Production (MES): production orders, eBMR, material issue, equipment
gates, in-process controls, yield reconciliation, FG quarantine."""
from typing import Any, List, Optional

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
    """Consume reservations → MATERIAL_ISSUE ledger movements (inventory → WIP).

    Reservations already decremented AVAILABLE and mirrored into RESERVED at
    reserve_materials time — consumption must draw from the RESERVED rows,
    never a second decrement (double-decrement fix, parity BUG-5)."""
    order = await _get(order_id)
    if not order.get("line_cleared"):
        raise ValidationFailed("Line clearance required before material issue")
    if order["status"] not in ("DISPENSING", "IN_PROCESS"):
        raise ConflictError(f"Cannot issue in status {order['status']}")
    # NOTE: the equipment gate deliberately runs at batch START, not at
    # issue: materials staged to a line are recoverable, manufacturing on
    # out-of-calibration equipment is not. (Test: issue 200, start 409.)
    for r in order.get("reservations", []):
        wh = (r["allocation"][0]["warehouse_id"] if r.get("allocation")
              else (order.get("site_id") or "WH-MAIN"))
        await inventory.consume_reservation(
            "PRODUCTION_ORDER", order_id, "MATERIAL_ISSUE", wh, actor=actor)
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
    # GMP gate BEFORE any state change: a failed equipment gate must never
    # leave the order in IN_PROCESS with no batch actually started.
    await equipment_gate(order_id, actor)
    if order["status"] == "DISPENSING":
        # batch start IS the dispensing → in-process transition
        await transition("production_order", order_id, "production_orders",
                         "order_id", "IN_PROCESS", actor, reason="Batch start")
    order = await _get(order_id)
    if order["status"] != "IN_PROCESS":
        raise ConflictError(f"Order not IN_PROCESS: {order['status']}")
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
    await _get(order_id)
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


# ==================================================================
# Phase: Plant completion — production plan, waste, packing record,
# FG release to warehouse, requirement preview.
# ==================================================================
async def material_requirement(product_id: str, batch_size: float,
                               bom_version: Optional[int] = None) -> dict:
    """Explode BOM into material requirements with live shortage check.

    Read-only: used by planners and the Plant Agent before an order exists.
    """
    if bom_version:
        bom = await db.db.boms.find_one({"product_id": product_id,
                                         "version": bom_version})
    else:
        bom = await get_active_bom(product_id)
    if not bom:
        raise NotFound(f"No approved BOM for {product_id}")
    scale = float(batch_size) / float(bom["batch_size"])
    lines = []
    for comp in bom["components"]:
        qty = float(comp["qty_per_batch"]) * scale
        avail = await inventory.availability(comp["sku"])
        atp = float(avail.get("available_to_promise", 0) or 0)
        lines.append({"sku": comp["sku"], "required": round(qty, 3),
                      "atp": atp, "shortage": round(max(0.0, qty - atp), 3),
                      "uom": comp.get("uom", "BOX")})
    return {"product_id": product_id, "bom_version": bom["version"],
            "batch_size": float(batch_size), "lines": lines,
            "shortages": [l for l in lines if l["shortage"] > 0]}


async def create_production_plan(payload: dict, actor: dict) -> dict:
    """Planning-level production plan (no reservations, no inventory writes).

    Evaluates each requested product against current ATP and flags items
    that need procurement or are blocked by missing BOMs. Executing a plan
    item simply creates a production order — the plan itself stays a draft
    until orders are cut from it.
    """
    items = payload.get("items") or []
    if not items:
        raise ValidationFailed("Plan needs items [{product_id, quantity}]")
    plan_id = await _next_id("production_plan", "PPLAN")
    evaluated = []
    for it in items:
        pid, qty = it["product_id"], float(it["quantity"])
        try:
            req = await material_requirement(pid, qty)
            feasible = not req["shortages"]
        except NotFound:
            req, feasible = {"lines": [], "shortages": [], "bom_version": None}, False
        evaluated.append({"product_id": pid, "quantity": qty,
                          "bom_version": (req or {}).get("bom_version"),
                          "feasible_now": feasible,
                          "shortage_lines": (req or {}).get("shortages", [])})
    doc = {"plan_id": plan_id,
           "name": payload.get("name") or f"Plan {plan_id}",
           "items": evaluated,
           "status": "EVALUATED",
           "orders_cut": [],
           "created_by": actor, "created_at": now_iso()}
    await db.db.production_plans.insert_one(doc)
    await audit("PRODUCTION_PLAN", plan_id, "CREATED", actor,
                details={"items": len(evaluated),
                         "feasible": sum(1 for e in evaluated if e["feasible_now"])})
    await bus.publish("production.plan_created", {"plan_id": plan_id}, actor)
    return _clean(doc)


async def cut_order_from_plan(plan_id: str, product_id: str, actor: dict,
                              idempotency_key: Optional[str] = None) -> dict:
    """Execute one plan item → production order (records the link)."""
    plan = await db.db.production_plans.find_one({"plan_id": plan_id})
    if not plan:
        raise NotFound(f"Plan {plan_id} not found")
    item = next((i for i in plan["items"] if i["product_id"] == product_id), None)
    if not item:
        raise NotFound(f"Product {product_id} not in plan {plan_id}")
    order = await create_production_order(
        {"product_id": product_id, "batch_size": item["quantity"]}, actor,
        idempotency_key=idempotency_key or f"PPLAN:{plan_id}:{product_id}")
    await db.db.production_plans.update_one(
        {"plan_id": plan_id}, {"$addToSet": {"orders_cut": order["order_id"]}})
    return order


async def record_waste(order_id: str, payload: dict, actor: dict) -> dict:
    """Record production waste (append-only; feeds yield reconciliation).

    Waste is tracked per stage with reason codes; it never edits any
    quantity — the eBMR absorbs it as evidence for QA review.
    """
    order = await _get(order_id)
    if order["status"] not in ("IN_PROCESS", "PACKAGING"):
        raise ConflictError(f"Waste only during execution: {order['status']}")
    reason = payload.get("reason")
    if reason not in ("SCRAP", "SAMPLE", "SPILL", "REWORK_LOSS", "PACKAGING_LOSS",
                      "CLEANING_LOSS", "OTHER"):
        raise ValidationFailed("reason must be a waste code (SCRAP/SAMPLE/...)")
    entry = {"stage": payload.get("stage", order["status"]),
             "sku": payload.get("sku"), "quantity": float(payload.get("quantity", 0)),
             "reason": reason, "note": payload.get("note"), "by": actor,
             "at": now_iso()}
    await db.db.production_orders.update_one(
        {"order_id": order_id}, {"$push": {"waste_log": entry}})
    await _ebmr_step(order_id, "WASTE_RECORDED", entry, actor)
    return await _get(order_id)


async def record_packing(order_id: str, payload: dict, actor: dict) -> dict:
    """Batch Packing Record: pack units, labels, batch numbers (append-only)."""
    order = await _get(order_id)
    if order["status"] != "PACKAGING":
        raise ConflictError(f"Order not PACKAGING: {order['status']}")
    entry = {"pack_size": payload.get("pack_size"),
             "packs_produced": int(payload.get("packs_produced", 0)),
             "label_code": payload.get("label_code"),
             "packed_by": actor, "at": now_iso()}
    await db.db.production_orders.update_one(
        {"order_id": order_id}, {"$push": {"packing_records": entry}})
    await _ebmr_step(order_id, "PACKING_RECORD", entry, actor)
    return await _get(order_id)


async def release_fg_to_warehouse(order_id: str, actor: dict) -> dict:
    """QA-released batch → AVAILABLE in the finished-goods warehouse.

    Called after QA release flips the batch to RELEASED. Posts the
    STATUS_CHANGE legs QUARANTINE → AVAILABLE so the FG stock becomes
    sellable ATP. Idempotent per order.
    """
    order = await _get(order_id)
    if order["status"] != "BATCH_RELEASED":
        raise ConflictError(f"Order not BATCH_RELEASED: {order['status']}")
    if order.get("fg_released_to_wh"):
        return order
    # NOTE: QA batch_release already posted the QUARANTINE -> AVAILABLE legs
    # (reference BATCH_RELEASE). This call is the MES-side marker + event that
    # closes the Production -> FG Warehouse link; it never double-moves stock.
    wh = order.get("site_id") or "WH-MAIN"
    qty = float(order.get("actual_yield") or 0)
    await db.db.production_orders.update_one(
        {"order_id": order_id}, {"$set": {"fg_released_to_wh": True,
                                          "updated_at": now_iso()}})
    await _ebmr_step(order_id, "FG_RELEASED_TO_WAREHOUSE",
                     {"warehouse": wh, "quantity": qty}, actor)
    await bus.publish("production.fg_available",
                      {"order_id": order_id, "product_id": order["product_id"],
                       "batch_id": order["batch_id"], "quantity": qty}, actor)
    await audit("PRODUCTION_ORDER", order_id, "FG_RELEASED", actor,
                details={"warehouse": wh, "qty": qty})
    return await _get(order_id)


# ---------------------------------------------------------------- traceability
async def trace_batch(batch_id: str) -> dict:
    """Full forward + backward trace for a production batch (recall-ready).

    Backward:  vendor lots (material issues) → this batch
    Forward:   this batch → sales orders / dispenses → customers
    """
    order = await db.db.production_orders.find_one({"batch_id": batch_id})
    product_id = (order or {}).get("product_id")

    # backward: raw-material lots consumed (MATERIAL_ISSUE ledger rows)
    raw_materials = []
    async for mv in db.db.inventory_movements.find({
            "movement_type": "MATERIAL_ISSUE",
            "reference_id": (order or {}).get("order_id", "__none__")}):
        # trace the vendor lot: find the lot's arrival (PURCHASE_RECEIPT chain)
        lot_in = await db.db.inventory_movements.find_one({
            "movement_type": "PURCHASE_RECEIPT", "product_id": mv["product_id"],
            "batch_id": mv.get("batch_id")}, sort=[("created_at", 1)])
        grn = (lot_in or {}).get("reference_id")
        grn_doc = await db.db.grns.find_one({"grn_id": grn}) if grn else None
        po = await db.db.purchase_orders.find_one(
            {"po_id": (grn_doc or {}).get("po_id")}) if grn_doc else None
        vendor = None
        if po:
            v = await db.db.vendors.find_one({"vendor_id": po.get("vendor_id")})
            vendor = {"vendor_id": po.get("vendor_id"),
                      "name": (v or {}).get("name")}
        raw_materials.append({
            "sku": mv["product_id"], "lot": mv.get("batch_id"),
            "quantity": abs(float(mv.get("quantity") or 0)),
            "grn_id": grn, "po_id": (grn_doc or {}).get("po_id"),
            "vendor": vendor})

    # forward: where did this batch go?
    dispatched = []
    async for mv in db.db.inventory_movements.find({
            "batch_id": batch_id,
            "movement_type": {"$in": ["SALE", "DISPENSE", "TRANSFER_OUT",
                                      "DESTROY", "RTV"]}}):
        row = {"to": mv["movement_type"], "quantity": abs(float(mv.get("quantity") or 0)),
               "warehouse": mv["warehouse_id"], "reference_type": mv.get("reference_type"),
               "reference_id": mv.get("reference_id"), "at": mv.get("created_at")}
        if mv["movement_type"] == "SALE" and mv.get("reference_id"):
            so = await db.db.sales_orders.find_one({"order_id": mv["reference_id"]})
            if so:
                row["customer_id"] = so.get("customer_id")
        if mv["movement_type"] == "DISPENSE" and mv.get("reference_id"):
            rx = await db.db.prescriptions.find_one({"rx_id": mv["reference_id"]})
            if rx:
                row["patient_ref"] = rx.get("patient")
        dispatched.append(row)

    # open reservations holding this batch
    reserved = [_clean(dict(r)) async for r in db.db.reservations.find(
        {"allocation.batch_id": batch_id, "status": "ACTIVE"})]

    # current stock positions of the batch
    balances = [_clean(dict(b)) async for b in db.db.inventory_balances.find(
        {"batch_id": batch_id, "quantity": {"$ne": 0}})]

    return {"batch_id": batch_id,
            "product_id": product_id,
            "production_order_id": (order or {}).get("order_id"),
            "bom_version": (order or {}).get("bom_version"),
            "qa_status": (await db.db.batches.find_one(
                {"batch_id": batch_id}) or {}).get("qa_status"),
            "backward": {"raw_material_lots": raw_materials},
            "forward": {"dispatched": dispatched,
                        "open_reservations": len(reserved)},
            "current_positions": balances,
            "generated_at": now_iso()}


async def yield_anomalies(window: int = 50) -> dict:
    """Recent batches with yield below their plan threshold (agent-ready)."""
    rows = [_clean(dict(r)) async for r in db.db.production_orders.find(
        {"yield_pct": {"$ne": None}}).sort("created_at", -1).limit(window)]
    flagged = [{"order_id": r["order_id"], "product_id": r["product_id"],
                "batch_id": r["batch_id"], "yield_pct": r["yield_pct"],
                "min_yield_pct": r.get("min_yield_pct", 95.0),
                "below_threshold": r["yield_pct"] < float(r.get("min_yield_pct", 95.0))}
               for r in rows]
    return {"checked": len(rows),
            "flagged": [f for f in flagged if f["below_threshold"]]}


async def batch_progress(order_id: str) -> dict:
    """Progress % + expected completion signals for the Plant Agent."""
    order = await _get(order_id)
    stage_order = ["PLANNED", "RELEASED", "DISPENSING", "IN_PROCESS", "PACKAGING",
                   "COMPLETED", "FG_QUARANTINE", "QC_COMPLETE", "QA_REVIEW",
                   "BATCH_RELEASED"]
    idx = stage_order.index(order["status"]) if order["status"] in stage_order else 0
    steps_done = len(order.get("ebmr_steps", []))
    return {"order_id": order_id, "status": order["status"],
            "progress_pct": round(100.0 * idx / (len(stage_order) - 1)),
            "ebmr_steps": steps_done,
            "yield_pct": order.get("yield_pct"),
            "waste_entries": len(order.get("waste_log", [])),
            "packed_records": len(order.get("packing_records", []))}
