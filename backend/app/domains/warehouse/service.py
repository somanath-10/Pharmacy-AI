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
            mv = await inventory.record_movement(
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
                "uom": po_line.get("uom", "BOX"),
                "batch_id": batch_id,
                "expiry_date": expiry,
                "mfg_date": mfg,
                "accepted_qty": 0.0,
                "rejected_qty": 0.0,
                "qc_sample_id": None,
                "location_id": None,
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
        # re-point batch balance to the bin (ledger note only; qty unchanged)
        await db.db.inventory_balances.update_one(
            {"product_id": line["sku"], "warehouse_id": grn["warehouse_id"],
             "batch_id": line["batch_id"]},
            {"$set": {"location_id": location_id}},
        )
    await db.db.grns.update_one({"grn_id": grn_id},
                                {"$set": {"lines": grn["lines"],
                                          "status": "PUTAWAY_DONE",
                                          "updated_at": now_iso()}})
    await audit("GRN", grn_id, "PUTAWAY_DONE", actor)
    po_id = grn.get("po_id")
    if po_id:
        await record_node("purchase_order", po_id, "putaway", "Putaway Complete",
                          "DONE", actor)
    return await _get_grn(grn_id)


async def _suggest_location(warehouse: Optional[dict], line: dict) -> str:
    if warehouse and warehouse.get("locations"):
        for loc in warehouse["locations"]:
            if loc.get("zone") in ("GOODS", "MAIN", "AMBIENT", "COLD"):
                return loc["location_id"]
        return warehouse["locations"][0]["location_id"]
    return f"{warehouse['code'] if warehouse else 'WH'}-DEFAULT-001"


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
    warehouse_id = payload.get("warehouse_id") or so.get("warehouse_id")

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
    await inventory.record_movement(
        movement_type="SALE",
        product_id=task["product_id"],
        warehouse_id=task["warehouse_id"],
        quantity=task["quantity"],
        batch_id=task["batch_id"] if task["batch_id"] != "UNBATCHED" else None,
        reference_type="SALES_ORDER",
        reference_id=task["sales_order_id"],
        performed_by=actor,
    )
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
    return _clean(dict(await db.db.pick_tasks.find_one({"task_id": task_id})))


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
    if not payload.get("from_warehouse") or not payload.get("to_warehouse"):
        raise ValidationFailed("transfer needs from/to warehouse")
    to_id = await _next_id("transfer", "TRF")
    doc = {
        "transfer_id": to_id,
        "from_warehouse": payload["from_warehouse"],
        "to_warehouse": payload["to_warehouse"],
        "lines": payload["lines"],
        "status": "DRAFT",
        "created_by": actor,
        "created_at": now_iso(),
    }
    await db.db.transfer_orders.insert_one(doc)
    return doc


async def execute_transfer(transfer_id: str, actor: dict) -> dict:
    trf = await db.db.transfer_orders.find_one({"transfer_id": transfer_id})
    if not trf:
        raise NotFound(f"Transfer {transfer_id} not found")
    if trf["status"] != "DRAFT":
        raise ConflictError("Transfer already executed")
    for line in trf["lines"]:
        await inventory.transfer(
            trf["from_warehouse"], trf["to_warehouse"], line["sku"],
            float(line["quantity"]), line.get("batch_id"), actor,
            reference=f"TRANSFER_ORDER:{transfer_id}")
    await db.db.transfer_orders.update_one(
        {"transfer_id": transfer_id},
        {"$set": {"status": "EXECUTED", "executed_at": now_iso()}})
    await audit("TRANSFER_ORDER", transfer_id, "EXECUTED", actor)
    return await db.db.transfer_orders.find_one({"transfer_id": transfer_id})


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
