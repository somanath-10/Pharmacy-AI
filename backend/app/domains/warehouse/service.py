"""Warehouse/WMS: receiving (GRN), quarantine, QC sampling trigger, putaway,
picking, packing, staging, dispatch, cycle counts, transfers, health."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.workflow import record_node
from app.domains.inventory import service as inventory


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


# ------------------------------------------------------------------ GRN receipt
async def create_grn(payload: dict, actor: dict,
                     idempotency_key: Optional[str] = None) -> dict:
    """Receive against an ASN/PO: batches land in QUARANTINE, QC sampling starts."""
    if not payload.get("asn_id") and not payload.get("po_id"):
        raise ValidationFailed("GRN needs asn_id or po_id")
    async with idempotent("GRN", idempotency_key) as gate:
        if not gate["first_time"]:
            return gate["result"]

        asn = None
        po = None
        if payload.get("asn_id"):
            asn = await db.db.asns.find_one({"asn_id": payload["asn_id"]})
            if not asn:
                raise NotFound(f"ASN {payload['asn_id']} not found")
            po = await db.db.purchase_orders.find_one({"po_id": asn["po_id"]})
        else:
            po = await db.db.purchase_orders.find_one({"po_id": payload["po_id"]})
            if not po:
                raise NotFound(f"PO {payload['po_id']} not found")
        warehouse_id = payload.get("warehouse_id") or po.get("warehouse_id")
        if not warehouse_id:
            raise ValidationFailed("warehouse_id required")

        grn_id = await _next_id("grn", "GRN")
        lines = []
        for rl in payload["lines"]:
            line_no = int(rl.get("line_no") or 0)
            po_line = next((l for l in po["lines"] if l["line_no"] == line_no), None)
            if not po_line:
                raise ValidationFailed(f"PO line {line_no} not found")
            qty = float(rl["quantity"])
            batch_id = rl.get("batch_id") or await _next_id("batch", "B")
            expiry = rl.get("expiry_date")
            mfg = rl.get("mfg_date")
            await inventory.ensure_batch(po_line["sku"], batch_id, expiry, mfg,
                                         supplier_id=po["vendor_id"])
            await db.db.batches.update_one(
                {"batch_id": batch_id},
                {"$set": {"qa_status": "QUARANTINE", "warehouse_id": warehouse_id,
                          "blocked": True, "block_reason": "QC_PENDING",
                          "updated_at": now_iso()}},
            )
            # Ledger: PURCHASE_RECEIPT lands in quarantine (blocked batch until QA release)
            await inventory.record_movement(
                movement_type="PURCHASE_RECEIPT",
                product_id=po_line["sku"],
                warehouse_id=warehouse_id,
                quantity=qty,
                uom=po_line.get("uom", "BOX"),
                batch_id=batch_id,
                reference_type="GRN",
                reference_id=grn_id,
                performed_by=actor,
                unit_cost=po_line.get("unit_price"),
            )
            lines.append({
                "line_no": line_no,
                "sku": po_line["sku"],
                "received_qty": qty,
                "quantity": qty,
                "uom": po_line.get("uom", "BOX"),
                "batch_id": batch_id,
                "expiry_date": expiry,
                "mfg_date": mfg,
                "accepted_qty": 0.0,
                "rejected_qty": 0.0,
                "qc_sample_id": None,
                "location_id": None,
                "stock_status": "QUARANTINE",
                "status": "QUARANTINE",
            })

        doc = {
            "grn_id": grn_id,
            "asn_id": asn["asn_id"] if asn else None,
            "po_id": po["po_id"],
            "vendor_id": po["vendor_id"],
            "warehouse_id": warehouse_id,
            "lines": lines,
            "status": "POSTED",
            "received_at": now_iso(),
            "received_by": actor,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "POSTED", "actor": actor, "at": now_iso()}],
        }
        await db.db.grns.insert_one(doc)

        # PO receiving update
        from app.domains.procurement.service import receive_on_grn

        await receive_on_grn(po["po_id"],
                             [{"line_no": l["line_no"], "quantity": l["received_qty"]}
                              for l in lines])

        # QC sampling for each line (auto workflow)
        from app.domains.qc.service import register_sample

        spec_map = {}
        for line in lines:
            try:
                sample = await register_sample({
                    "product_id": line["sku"],
                    "batch_id": line["batch_id"],
                    "ref_type": "GRN_LINE",
                    "ref_id": f"{grn_id}:{line['line_no']}",
                    "stage": "RAW_MATERIAL",
                }, {"type": "AGENT", "id": "warehouse-agent"})
                line["qc_sample_id"] = sample["sample_id"]
                spec_map[line["line_no"]] = sample["sample_id"]
            except Exception as e:  # noqa: BLE001 — QC optional for non-drug lines
                import logging

                logging.getLogger("pharmaos.warehouse").warning(
                    "QC sample registration failed for %s/%s: %s",
                    line.get("sku"), line.get("batch_id"), e)
                line["qc_sample_id"] = None
        await db.db.grns.update_one({"grn_id": grn_id},
                                    {"$set": {"lines": lines,
                                              "status": "QC_PENDING"}})
        doc["status"] = "QC_PENDING"
        doc["lines"] = lines
        await bus.publish("receipt.created",
                          {"grn_id": grn_id, "po_id": po["po_id"],
                           "asn_id": asn["asn_id"] if asn else None,
                           "lines": len(lines)}, actor)
        await bus.publish("inventory.quarantined",
                          {"grn_id": grn_id,
                           "batches": [l["batch_id"] for l in lines]},
                          actor)
        await bus.publish("grn.created",
                          {"grn_id": grn_id, "po_id": po["po_id"],
                           "vendor_id": po["vendor_id"],
                           "quarantine": True}, actor)
        await audit("GRN", grn_id, "POSTED", actor, new_state="QC_PENDING",
                    details={"po_id": po["po_id"], "lines": len(lines)})
        await record_node("purchase_order", po["po_id"], "grn", "GRN Posted",
                          "DONE", actor)
        result = _clean(doc)
        gate.store(result)
    return result


async def putaway(grn_id: str, actor: dict, locations: Optional[List[dict]] = None) -> dict:
    """Move accepted quantities from quarantine into bins (FEFO-aware suggestion)."""
    grn = await _get_grn(grn_id)
    if grn["status"] not in ("QC_PASSED", "QC_PARTIAL", "POSTED"):
        raise ConflictError(f"Putaway not allowed in status {grn['status']}")
    warehouse = await db.db.warehouses.find_one({"code": grn["warehouse_id"]})
    locs = {l["line_no"]: l for l in (locations or [])}
    for line in grn["lines"]:
        accepted = float(line.get("accepted_qty") or 0)
        if accepted <= 0:
            continue
        if line["line_no"] in locs:
            location_id = locs[line["line_no"]]["location_id"]
        else:
            location_id = await _suggest_location(warehouse, line)
        line["location_id"] = location_id
        line["status"] = "PUTAWAY"
        # re-point batch balance to the bin (metadata only; qty never touched —
        # direct quantity modification is not allowed on the projection)
        await db.db.inventory_balances.update_one(
            {"product_id": line["sku"], "warehouse_id": grn["warehouse_id"],
             "batch_id": line["batch_id"]},
            {"$set": {"location_id": location_id}},
        )
    await db.db.grns.update_one({"grn_id": grn_id},
                                {"$set": {"lines": grn["lines"],
                                          "status": "PUTAWAY_DONE",
                                          "updated_at": now_iso()}})
    await bus.publish("inventory.putaway.completed",
                      {"grn_id": grn_id,
                       "locations": {str(l["line_no"]): l.get("location_id")
                                     for l in grn["lines"] if l.get("location_id")}},
                      actor)
    await audit("GRN", grn_id, "PUTAWAY_DONE", actor)
    po_id = grn.get("po_id")
    if po_id:
        await record_node("purchase_order", po_id, "putaway", "Putaway Complete",
                          "DONE", actor)
    return await _get_grn(grn_id)


# ------------------------------------------------------- warehouse structure
async def create_location(payload: dict, actor: dict) -> dict:
    """Warehouse structure: warehouse > zone > aisle > rack > shelf > bin.
    Supports cold/hazardous/quarantine zones, capacity and pick-frequency
    data for system-driven putaway (Part 1/5)."""
    for k in ("warehouse_id", "location_id", "zone"):
        if not payload.get(k):
            raise ValidationFailed(f"Location needs {k}")
    doc = {
        "warehouse_id": payload["warehouse_id"],
        "location_id": payload["location_id"],
        "barcode": payload.get("barcode") or payload["location_id"],
        "zone": payload["zone"],            # RECEIVING, QUARANTINE, AMBIENT, COLD, HAZMAT, RETURNS, DISPATCH
        "aisle": payload.get("aisle"),
        "rack": payload.get("rack"),
        "shelf": payload.get("shelf"),
        "bin": payload.get("bin"),
        "temperature_min": payload.get("temperature_min"),
        "temperature_max": payload.get("temperature_max"),
        "hazardous": bool(payload.get("hazardous")),
        "capacity": payload.get("capacity"),
        "pick_frequency": payload.get("pick_frequency", 0),  # velocity class
        "active": True,
        "created_at": now_iso(),
    }
    await db.db.warehouse_locations.update_one(
        {"warehouse_id": doc["warehouse_id"], "location_id": doc["location_id"]},
        {"$set": doc}, upsert=True)
    await audit("WAREHOUSE_LOCATION", doc["location_id"], "CREATED", actor,
                details={"zone": doc["zone"]})
    return _clean(doc)


async def list_locations(warehouse_id: str, zone: Optional[str] = None) -> List[dict]:
    q: Dict[str, Any] = {"warehouse_id": warehouse_id, "active": True}
    if zone:
        q["zone"] = zone
    return [_clean(dict(r)) async for r in
            db.db.warehouse_locations.find(q).sort("location_id", 1)]


# ------------------------------------------------------- gate / dock / unload
async def gate_entry(payload: dict, actor: dict) -> dict:
    """Inbound gate entry + dock assignment (Part 1). ASN-linked when given."""
    asn_id = payload.get("asn_id")
    if asn_id:
        asn = await db.db.asns.find_one({"asn_id": asn_id})
        if not asn:
            raise NotFound(f"ASN {asn_id} not found")
    ge_id = await _next_id("gate", "GATE")
    doc = {
        "gate_entry_id": ge_id,
        "asn_id": asn_id,
        "po_id": payload.get("po_id"),
        "warehouse_id": payload.get("warehouse_id"),
        "vehicle_no": payload.get("vehicle_no"),
        "driver": payload.get("driver"),
        "dock": payload.get("dock"),
        "type": "INBOUND",
        "status": "CHECKED_IN",
        "created_by": actor,
        "created_at": now_iso(),
    }
    await db.db.gate_entries.insert_one(doc)
    if asn_id:
        await db.db.asns.update_one({"asn_id": asn_id},
                                    {"$set": {"status": "ARRIVED",
                                              "gate_entry_id": ge_id}})
    await bus.publish("shipment.arrived", {"gate_entry_id": ge_id,
                                           "asn_id": asn_id}, actor)
    await audit("GATE_ENTRY", ge_id, "CHECKED_IN", actor,
                details={"dock": doc["dock"], "vehicle": doc["vehicle_no"]})
    return _clean(doc)


async def confirm_unload(gate_entry_id: str, payload: dict, actor: dict) -> dict:
    """Unload confirmation: pallets/LPNs scanned at the dock (Part 1)."""
    ge = await db.db.gate_entries.find_one({"gate_entry_id": gate_entry_id})
    if not ge:
        raise NotFound(f"Gate entry {gate_entry_id} not found")
    if ge["status"] != "CHECKED_IN":
        raise ConflictError(f"Gate entry not CHECKED_IN: {ge['status']}")
    lpns = payload.get("lpns", [])
    await db.db.gate_entries.update_one(
        {"gate_entry_id": gate_entry_id},
        {"$set": {"status": "UNLOADED", "unloaded_at": now_iso(),
                  "lpns": lpns, "pallet_count": payload.get("pallet_count")}})
    await audit("GATE_ENTRY", gate_entry_id, "UNLOADED", actor,
                details={"lpns": len(lpns)})
    return {"gate_entry_id": gate_entry_id, "status": "UNLOADED",
            "lpns": lpns}


async def _suggest_location(warehouse: Optional[dict], line: dict) -> str:
    """System-driven putaway (Part 5): zone rules from product storage needs,
    hazmat flags, cold chain, capacity and pick frequency. Warehouse Agent /
    rules pick the bin — never the worker."""
    product = await db.db.products.find_one({"sku": line.get("sku")}) or {}
    temp_req = product.get("storage_condition") or line.get("storage_condition") or "AMBIENT"
    hazmat = bool(product.get("hazardous"))
    zones_by_temp = {"COLD": ["COLD"], "AMBIENT": ["AMBIENT", "MAIN", "GOODS"],
                     "FROZEN": ["COLD"]}
    wanted = zones_by_temp.get(str(temp_req).upper(), ["AMBIENT", "MAIN", "GOODS"])
    if hazmat:
        wanted = ["HAZMAT"]
    locs = await db.db.warehouse_locations.find(
        {"warehouse_id": (warehouse or {}).get("code"), "active": True}
    ).to_list(500)
    if not locs and warehouse and warehouse.get("locations"):
        return warehouse["locations"][0].get("location_id",
                                             f"{(warehouse or {}).get('code', 'WH')}-DEFAULT-001")
    if not locs:
        return f"{(warehouse or {}).get('code', 'WH')}-DEFAULT-001"

    def score(loc: dict) -> tuple:
        zone_ok = loc.get("zone") in wanted
        quarantine_pen = 0 if loc.get("zone") != "QUARANTINE" else -100
        hazmat_ok = (not hazmat) or bool(loc.get("hazardous"))
        # prefer empty-ish bins, fast-pick zones slightly ahead
        cap = loc.get("capacity")
        _ = 0 if cap is None else 0
        return (zone_ok, hazmat_ok, quarantine_pen,
                -int(loc.get("pick_frequency") or 0) * 0.01, loc["location_id"])

    eligible = [l for l in locs
                if l.get("zone") not in ("RECEIVING", "DISPATCH")]
    eligible.sort(key=score, reverse=True)
    return eligible[0]["location_id"] if eligible else \
        f"{(warehouse or {}).get('code', 'WH')}-DEFAULT-001"


# ------------------------------------------------------------- replenishment
async def create_replenishment_task(payload: dict, actor: dict) -> dict:
    """Pick-face low → find source stock → replenishment task → worker moves
    + confirms scan (Part 5). System decides source and destination."""
    for k in ("warehouse_id", "product_id", "quantity"):
        if not payload.get(k):
            raise ValidationFailed(f"Replenishment needs {k}")
    wh = payload["warehouse_id"]
    qty = float(payload["quantity"])
    # source: deepest reserve bin with AVAILABLE stock (reserve area feeds pick face)
    src = await db.db.warehouse_locations.find_one(
        {"warehouse_id": wh, "zone": {"$in": ["RESERVE", "AMBIENT", "MAIN"]},
         "active": True}, sort=[("pick_frequency", 1)])
    dst = await db.db.warehouse_locations.find_one(
        {"warehouse_id": wh, "zone": {"$in": ["PICK_FACE", "AMBIENT", "MAIN"]},
         "active": True}, sort=[("pick_frequency", -1)])
    # system picks the batch too (Part 18): FEFO earliest-expiry batch holding
    # enough AVAILABLE stock in the source bin; falls back to any stock there.
    batch_id = payload.get("batch_id")
    if not batch_id and src:
        candidates = [_clean(dict(r)) async for r in
                      db.db.inventory_balances.find(
                          {"warehouse_id": wh, "product_id": payload["product_id"],
                           "stock_status": "AVAILABLE",
                           "location_id": src["location_id"],
                           "quantity": {"$gt": 0}}).sort("expiry_date", 1)]
        full = next((c for c in candidates
                     if float(c["quantity"]) >= qty - 1e-9), None)
        pick = full or (candidates[0] if candidates else None)
        batch_id = (pick or {}).get("batch_id")
    task_id = await _next_id("repl", "RPL")
    doc = {
        "task_id": task_id,
        "warehouse_id": wh,
        "product_id": payload["product_id"],
        "batch_id": batch_id,
        "quantity": qty,
        "uom": payload.get("uom", "BOX"),
        "from_location": (src or {}).get("location_id"),
        "to_location": (dst or {}).get("location_id"),
        "status": "PENDING",
        "created_by": actor,
        "created_at": now_iso(),
    }
    await db.db.replenishment_tasks.insert_one(doc)
    await audit("REPLENISHMENT", task_id, "CREATED", actor,
                details={"from": doc["from_location"], "to": doc["to_location"]})
    return _clean(doc)


async def confirm_replenishment(task_id: str, actor: dict,
                                scanned_location: Optional[str] = None) -> dict:
    """Worker confirms the move with a scan; ledger books the bin move."""
    t = await db.db.replenishment_tasks.find_one({"task_id": task_id})
    if not t:
        raise NotFound(f"Replenishment task {task_id} not found")
    if t["status"] != "PENDING":
        raise ConflictError("Replenishment task already processed")
    if scanned_location and scanned_location != t["to_location"]:
        raise ValidationFailed(
            f"Scanned location {scanned_location} != task destination {t['to_location']}")
    await inventory.move_location(
        t["product_id"], t["warehouse_id"], t.get("batch_id"),
        t.get("from_location"), t["to_location"], float(t["quantity"]),
        actor, stock_status="AVAILABLE", uom=t.get("uom", "BOX"),
        reference_type="REPLENISHMENT", reference_id=task_id)
    await db.db.replenishment_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"status": "COMPLETED", "completed_by": actor,
                  "completed_at": now_iso()}})
    await audit("REPLENISHMENT", task_id, "COMPLETED", actor)
    return _clean(dict(await db.db.replenishment_tasks.find_one({"task_id": task_id})))


# -------------------------------------------------------------- outbound picking
async def pick(payload: dict, actor: dict) -> dict:
    """Pick a sales order from FEFO allocation; ledger SALE happens on pack/ship
    config — default: on pick confirmation (SALE movement, ref SALES_ORDER)."""
    order_id = payload.get("sales_order_id")
    if not order_id:
        raise ValidationFailed("pick needs sales_order_id")
    so = await db.db.sales_orders.find_one({"order_id": order_id})
    if not so:
        raise NotFound(f"Sales order {order_id} not found")
    if so["status"] != "ALLOCATED":
        raise ConflictError(f"Order not ALLOCATED: {so['status']}")
    _ = payload.get("warehouse_id") or so.get("warehouse_id")

    pick_tasks = []
    for line in so["lines"]:
        resv = await db.db.reservations.find_one(
            {"reference_type": "SALES_ORDER", "reference_id": order_id,
             "product_id": line["sku"], "status": "ACTIVE"})
        if not resv:
            raise ConflictError(f"No active reservation for {line['sku']}")
        for alloc in resv["allocation"]:
            task_id = await _next_id("pick", "PICK")
            t = {
                "task_id": task_id,
                "sales_order_id": order_id,
                "product_id": line["sku"],
                "batch_id": alloc["batch_id"],
                "warehouse_id": alloc["warehouse_id"],
                "quantity": alloc["allocate"],
                "status": "PENDING",
                "created_at": now_iso(),
            }
            await db.db.pick_tasks.insert_one(t)
            pick_tasks.append(t)

    await db.db.sales_orders.update_one(
        {"order_id": order_id}, {"$set": {"status": "PICKING"}})
    await bus.publish("pick.created",
                      {"sales_order_id": order_id,
                       "tasks": [t["task_id"] for t in pick_tasks],
                       "mode": "SYSTEM_FEFO"}, actor)
    await audit("PICK_WAVE", order_id, "CREATED", actor,
                details={"tasks": [t["task_id"] for t in pick_tasks]})
    return {"sales_order_id": order_id, "tasks": pick_tasks}


async def confirm_pick(task_id: str, actor: dict,
                       scanned_batch_id: Optional[str] = None) -> dict:
    task = await db.db.pick_tasks.find_one({"task_id": task_id})
    if not task:
        raise NotFound(f"Pick task {task_id} not found")
    if task["status"] != "PENDING":
        raise ConflictError("Pick task already processed")
    if scanned_batch_id and scanned_batch_id != task["batch_id"]:
        raise ValidationFailed(
            f"Scanned batch {scanned_batch_id} != assigned {task['batch_id']}")
    if task["batch_id"] != "UNBATCHED":
        b = await db.db.batches.find_one({"batch_id": task["batch_id"]})
        if b and b.get("blocked"):
            raise ConflictError(f"Batch {task['batch_id']} blocked: {b['block_reason']}")
    # Consume ONLY this task's allocation (P0 9.4): the previous order-level
    # consume let the first pick task drain reservations of every other
    # product/line on the same order.
    await inventory.consume_reservation_allocation(
        task["product_id"], "SALES_ORDER", task["sales_order_id"], "SALE",
        batch_id=task["batch_id"], warehouse_id=task["warehouse_id"],
        quantity=float(task["quantity"]), actor=actor)
    await db.db.pick_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"status": "PICKED", "picked_by": actor, "picked_at": now_iso()}})

    remaining = await db.db.pick_tasks.count_documents(
        {"sales_order_id": task["sales_order_id"], "status": "PENDING"})
    if remaining == 0:
        await db.db.sales_orders.update_one(
            {"order_id": task["sales_order_id"], "status": "PICKING"},
            {"$set": {"status": "PACKED"}})
        await bus.publish("order.picked",
                          {"sales_order_id": task["sales_order_id"]}, actor)
    result = _clean(dict(await db.db.pick_tasks.find_one({"task_id": task_id})))
    await bus.publish("pick.completed",
                      {"task_id": task_id,
                       "sales_order_id": task["sales_order_id"]}, actor)
    return result


async def short_pick(task_id: str, actor: dict, reason: str) -> dict:
    """Short/partial pick (Part 8): release the un-picked remainder back to
    AVAILABLE via reservation release; the order line is marked short and the
    balance becomes a backorder decision for sales/planning."""
    task = await db.db.pick_tasks.find_one({"task_id": task_id})
    if not task:
        raise NotFound(f"Pick task {task_id} not found")
    if task["status"] != "PENDING":
        raise ConflictError("Pick task already processed")
    picked_qty = float(task.get("picked_qty") or 0)
    short_qty = float(task["quantity"]) - picked_qty
    if short_qty <= 0:
        raise ValidationFailed("No shortfall recorded on this task")
    if not reason:
        raise ValidationFailed("Short pick requires a reason")
    # release the remainder from the reservation (kept as un-picked)
    await inventory.release_reservation_partial(
        "SALES_ORDER", task["sales_order_id"], task["product_id"],
        short_qty, actor=actor, reason=f"short pick: {reason}")
    await db.db.pick_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"status": "SHORT_PICKED", "short_qty": short_qty,
                  "short_reason": reason, "closed_by": actor,
                  "closed_at": now_iso()}})
    await audit("PICK_TASK", task_id, "SHORT_PICKED", actor,
                details={"short": short_qty, "reason": reason})
    await bus.publish("pick.short", {"task_id": task_id,
                                     "short_qty": short_qty}, actor)
    return _clean(dict(await db.db.pick_tasks.find_one({"task_id": task_id})))


async def stage_order(payload: dict, actor: dict) -> dict:
    """Stage packed orders at the dock for dispatch (Part 8).
    Creates a staging record; loading/dispatch happens in logistics."""
    order_id = payload.get("sales_order_id")
    if not order_id:
        raise ValidationFailed("stage needs sales_order_id")
    so = await db.db.sales_orders.find_one({"order_id": order_id})
    if not so or so["status"] not in ("PACKED", "PICKING"):
        raise ConflictError("Order not ready for staging")
    stage_id = await _next_id("stage", "STG")
    doc = {"stage_id": stage_id, "sales_order_id": order_id,
           "warehouse_id": payload.get("warehouse_id") or so.get("warehouse_id"),
           "dock": payload.get("dock"), "lane": payload.get("lane"),
           "status": "STAGED", "staged_by": actor, "staged_at": now_iso()}
    await db.db.staging_records.insert_one(doc)
    await audit("STAGING", stage_id, "STAGED", actor,
                details={"sales_order_id": order_id, "dock": doc["dock"]})
    await bus.publish("order.staged", {"sales_order_id": order_id,
                                       "stage_id": stage_id}, actor)
    return doc


async def pack(payload: dict, actor: dict) -> dict:
    order_id = payload.get("sales_order_id")
    so = await db.db.sales_orders.find_one({"order_id": order_id})
    if not so:
        raise NotFound(f"Sales order {order_id} not found")
    if so["status"] != "PACKED":
        raise ConflictError(f"Order not PACKED: {so['status']}")
    pack_id = await _next_id("pack", "PK")
    doc = {
        "pack_id": pack_id,
        "sales_order_id": order_id,
        "packages": payload.get("packages", []),
        "weight": payload.get("weight"),
        "packed_by": actor,
        "packed_at": now_iso(),
    }
    await db.db.pack_tasks.insert_one(doc)
    await bus.publish("order.packed", {"sales_order_id": order_id}, actor)
    await audit("PACK", pack_id, "COMPLETED", actor,
                details={"sales_order_id": order_id})
    return doc


# ----------------------------------------------------------- counts & transfers
async def create_transfer_order(payload: dict, actor: dict) -> dict:
    """Warehouse/branch transfer (Part 6): TRANSFER_REQUESTED → APPROVED →
    PICKED → DISPATCHED (stock becomes IN_TRANSIT) → RECEIVED → CLOSED."""
    if not payload.get("from_warehouse") or not payload.get("to_warehouse"):
        raise ValidationFailed("transfer needs from/to warehouse")
    if not payload.get("lines"):
        raise ValidationFailed("transfer needs lines")
    to_id = await _next_id("transfer", "TRF")
    lines = []
    for l in payload["lines"]:
        lines.append({"sku": l["sku"],
                      "quantity": float(l["quantity"]),
                      "batch_id": l.get("batch_id"),
                      "uom": l.get("uom", "BOX"),
                      "picked": False})
    doc = {
        "transfer_id": to_id,
        "from_warehouse": payload["from_warehouse"],
        "to_warehouse": payload["to_warehouse"],
        "lines": lines,
        "status": "TRANSFER_REQUESTED",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "timeline": [{"state": "TRANSFER_REQUESTED", "actor": actor,
                      "at": now_iso()}],
    }
    await db.db.transfer_orders.insert_one(doc)
    await audit("TRANSFER_ORDER", to_id, "TRANSFER_REQUESTED", actor)
    return doc


async def list_transfers(status: Optional[str] = None,
                         from_warehouse: Optional[str] = None,
                         to_warehouse: Optional[str] = None) -> List[dict]:
    """List transfer orders with optional filters."""
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    if from_warehouse:
        q["from_warehouse"] = from_warehouse
    if to_warehouse:
        q["to_warehouse"] = to_warehouse
    rows = []
    async for r in db.db.transfer_orders.find(q).sort("created_at", -1).limit(500):
        rows.append(_clean(dict(r)))
    return rows


async def _transfer_set_status(transfer_id: str, from_state: str, to_state: str,
                               actor: dict, extra: Optional[dict] = None) -> dict:
    trf = await db.db.transfer_orders.find_one({"transfer_id": transfer_id})
    if not trf:
        raise NotFound(f"Transfer {transfer_id} not found")
    if trf["status"] != from_state:
        raise ConflictError(
            f"Transfer is {trf['status']}, expected {from_state}")
    upd = {"$set": {"status": to_state, "updated_at": now_iso()},
           "$push": {"timeline": {"state": to_state, "previous_state": from_state,
                                  "actor": actor, "at": now_iso()}}}
    if extra:
        upd["$set"].update(extra)
    await db.db.transfer_orders.update_one({"transfer_id": transfer_id}, upd)
    await audit("TRANSFER_ORDER", transfer_id, to_state, actor,
                previous_state=from_state, new_state=to_state)
    return await db.db.transfer_orders.find_one({"transfer_id": transfer_id})


async def approve_transfer(transfer_id: str, actor: dict) -> dict:
    return await _transfer_set_status(transfer_id, "TRANSFER_REQUESTED",
                                      "APPROVED", actor)


async def pick_transfer(transfer_id: str, actor: dict) -> dict:
    """FEFO-selects batches (system decides — Part 18) and LOCKS the stock via
    a race-safe reservation: two transfers cannot pick the same last units.
    Stock stays AVAILABLE→RESERVED at the source until dispatch."""
    trf = await db.db.transfer_orders.find_one({"transfer_id": transfer_id})
    if not trf or trf["status"] != "APPROVED":
        raise ConflictError("Transfer not APPROVED")
    for line in trf["lines"]:
        # idempotent per (TRANSFER_ORDER, transfer_id, sku): re-pick reuses the
        # existing reservation instead of double-locking stock
        plan = await inventory.reserve(
            line["sku"], float(line["quantity"]),
            reference_type="TRANSFER_ORDER", reference_id=transfer_id,
            warehouse_id=trf["from_warehouse"], actor=actor)
        line["allocation"] = plan["allocation"]
        line["picked"] = True
    await db.db.transfer_orders.update_one(
        {"transfer_id": transfer_id},
        {"$set": {"lines": trf["lines"], "updated_at": now_iso()}})
    return await _transfer_set_status(transfer_id, "APPROVED", "PICKED", actor)


async def dispatch_transfer(transfer_id: str, actor: dict) -> dict:
    """Consume the pick reservation (RESERVED → out at source, exact bin/batch)
    and book the stock IN_TRANSIT at the destination warehouse — visible and
    non-allocatable until receiving confirmation (Part 6)."""
    trf = await db.db.transfer_orders.find_one({"transfer_id": transfer_id})
    if not trf or trf["status"] != "PICKED":
        raise ConflictError("Transfer not PICKED")
    for line in trf["lines"]:
        # consume the reservation: ledger-explained RESERVED outflow per bin
        await inventory.consume_reservation(
            "TRANSFER_ORDER", transfer_id, "TRANSFER_OUT",
            trf["from_warehouse"], actor=actor)
        for alloc in line.get("allocation", []):
            batch_key = None if alloc["batch_id"] == "UNBATCHED" else alloc["batch_id"]
            # stock is now physically on the road: land it as IN_TRANSIT at
            # the destination so it stays visible but cannot be allocated
            await inventory.record_movement(
                movement_type="TRANSFER_IN", product_id=line["sku"],
                warehouse_id=trf["to_warehouse"],
                quantity=float(alloc["allocate"]), uom=alloc.get("uom", "BOX"),
                batch_id=batch_key, stock_status="IN_TRANSIT",
                reference_type="TRANSFER_ORDER",
                reference_id=transfer_id, performed_by=actor,
                note="in transit from " + trf["from_warehouse"])
    await bus.publish("transfer.in_transit",
                      {"transfer_id": transfer_id,
                       "from": trf["from_warehouse"], "to": trf["to_warehouse"],
                       "lines": len(trf["lines"])}, actor)
    return await _transfer_set_status(transfer_id, "PICKED", "DISPATCHED", actor,
                                      extra={"dispatched_at": now_iso()})


async def receive_transfer(transfer_id: str, actor: dict) -> dict:
    """Destination confirmation: the IN_TRANSIT stock booked at dispatch flips
    to AVAILABLE (inter-branch QA gate can hold it in QUARANTINE)."""
    trf = await db.db.transfer_orders.find_one({"transfer_id": transfer_id})
    if not trf or trf["status"] != "DISPATCHED":
        raise ConflictError("Transfer not DISPATCHED")
    for line in trf["lines"]:
        for alloc in line.get("allocation", []):
            batch_key = None if alloc["batch_id"] == "UNBATCHED" else alloc["batch_id"]
            await inventory.change_stock_status(
                product_id=line["sku"],
                warehouse_id=trf["to_warehouse"],
                batch_id=batch_key,
                from_status="IN_TRANSIT", to_status="AVAILABLE",
                quantity=float(alloc["allocate"]), uom=alloc.get("uom", "BOX"),
                actor=actor, reference_type="TRANSFER_ORDER",
                reference_id=transfer_id)
    await bus.publish("transfer.received",
                      {"transfer_id": transfer_id,
                       "to": trf["to_warehouse"]}, actor)
    return await _transfer_set_status(transfer_id, "DISPATCHED", "CLOSED", actor,
                                      extra={"received_at": now_iso()})


async def execute_transfer(transfer_id: str, actor: dict) -> dict:
    """Legacy one-shot path kept for existing callers: walk the full chain."""
    await approve_transfer(transfer_id, actor)
    await pick_transfer(transfer_id, actor)
    await dispatch_transfer(transfer_id, actor)
    return await receive_transfer(transfer_id, actor)


# ------------------------------------------------------------------ health KPIs
async def warehouse_health() -> dict:
    total_skus = await db.db.inventory_balances.count_documents({"quantity": {"$gt": 0}})
    quarantine = await db.db.batches.count_documents({"qa_status": "QUARANTINE"})
    blocked = await db.db.batches.count_documents({"blocked": True})
    near_expiry = len(await inventory.batches_near_expiry(90))
    pending_picks = await db.db.pick_tasks.count_documents({"status": "PENDING"})
    pending_putaway = await db.db.grns.count_documents({"status": "QC_PENDING"})
    return {"skus_in_stock": total_skus, "quarantine_batches": quarantine,
            "blocked_batches": blocked, "near_expiry_batches": near_expiry,
            "pending_pick_tasks": pending_picks,
            "grns_awaiting_qc": pending_putaway,
            "score": max(0, 100 - quarantine * 2 - blocked * 5 - near_expiry)}


async def _get_grn(grn_id: str) -> dict:
    doc = await db.db.grns.find_one({"grn_id": grn_id})
    if not doc:
        raise NotFound(f"GRN {grn_id} not found")
    return doc


async def get_grn(grn_id: str) -> dict:
    return await _get_grn(grn_id)


async def list_grns(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.grns.find(q).sort("created_at", -1).limit(200)]


async def list_pick_tasks(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in db.db.pick_tasks.find(q).limit(200)]
