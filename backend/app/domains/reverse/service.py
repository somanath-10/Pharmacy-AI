"""Reverse Logistics & Recall: returns/RMA, return quarantine, inspection,
dispositions (restock/RTV/destruction), recalls with global batch block."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.workflow import transition, record_node
from app.domains.inventory import service as inventory


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


# --------------------------------------------------------------------- returns
DISPOSITIONS = ["RESTOCK", "RTV", "QUALITY_HOLD", "RECALL_HOLD", "DESTRUCTION",
                "REJECT_RETURN"]

async def create_return(payload: dict, actor: dict) -> dict:
    """Return request → policy validation → RMA."""
    if not payload.get("sales_order_id") and not payload.get("invoice_id"):
        raise ValidationFailed("Return needs sales_order_id or invoice_id")
    lines = payload.get("lines") or []
    if not lines:
        raise ValidationFailed("Return needs lines")

    # deterministic return policy validation
    so = None
    if payload.get("sales_order_id"):
        so = await db.db.sales_orders.find_one({"order_id": payload["sales_order_id"]})
        if not so:
            raise NotFound(f"Sales order {payload['sales_order_id']} not found")
    for l in lines:
        prod = await db.db.products.find_one({"sku": l["sku"]})
        if not prod:
            raise NotFound(f"Product {l['sku']} not found")
        if prod.get("is_controlled"):
            raise ValidationFailed(
                f"Controlled substance {l['sku']} not returnable per policy")

    ret_id = await _next_id("return", "RET")
    rma_id = None
    policy_ok = payload.get("reason") in ("DAMAGED", "WRONG_ITEM", "QUALITY",
                                          "EXPIRY", "RECALL")
    if policy_ok:
        rma_id = await _next_id("rma", "RMA")
    doc = {
        "return_id": ret_id,
        "rma_id": rma_id,
        "sales_order_id": payload.get("sales_order_id"),
        "invoice_id": payload.get("invoice_id"),
        "customer_id": payload.get("customer_id") or (so or {}).get("customer_id"),
        "lines": [{"sku": l["sku"], "quantity": float(l["quantity"]),
                   "batch_id": l.get("batch_id"),
                   "reason": payload.get("reason", "QUALITY"),
                   "disposition": None} for l in lines],
        "reason": payload.get("reason", "QUALITY"),
        "policy_ok": policy_ok,
        "status": "REQUESTED" if policy_ok else "REJECTED",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": doc_status(policy_ok), "actor": actor,
                      "at": now_iso()}],
    }
    await db.db.return_requests.insert_one(doc)
    await bus.publish("return.requested",
                      {"return_id": ret_id, "rma": rma_id}, actor)
    await audit("RETURN", ret_id, "REQUESTED", actor,
                details={"policy_ok": policy_ok})
    return _clean(doc)


def doc_status(policy_ok: bool) -> str:
    return "REQUESTED" if policy_ok else "REJECTED"


async def schedule_pickup(return_id: str, payload: dict, actor: dict) -> dict:
    ret = await _get_return(return_id)
    if ret["status"] != "REQUESTED":
        raise ConflictError(f"Return not REQUESTED: {ret['status']}")
    await transition("return_request", return_id, "return_requests", "return_id",
                     "RMA_APPROVED", actor)
    await transition("return_request", return_id, "return_requests", "return_id",
                     "PICKUP_SCHEDULED", actor,
                     reason=f"Pickup {payload.get('slot', 'asap')}")
    return await _get_return(return_id)


async def receive_return(return_id: str, actor: dict) -> dict:
    """Physical receipt → RETURN_QUARANTINE (never straight to stock)."""
    ret = await _get_return(return_id)
    if ret["status"] != "PICKED":
        raise ConflictError(f"Return not PICKED: {ret['status']}")
    await transition("return_request", return_id, "return_requests", "return_id",
                     "RECEIVED", actor)
    await transition("return_request", return_id, "return_requests", "return_id",
                     "RETURN_QUARANTINE", actor,
                     reason="Awaiting inspection")
    # batches go to blocked quarantine state
    for line in ret["lines"]:
        batch_id = line.get("batch_id")
        if batch_id:
            await inventory.block_batch(batch_id,
                                        f"RETURN_QUARANTINE {return_id}",
                                        actor, source="RETURNS")
        else:
            batch_id = await _next_id("batch", "B")
            await inventory.ensure_batch(line["sku"], batch_id, None)
            await inventory.block_batch(batch_id,
                                        f"RETURN_QUARANTINE {return_id}",
                                        actor, source="RETURNS")
            line["batch_id"] = batch_id
    await db.db.return_requests.update_one(
        {"return_id": return_id}, {"$set": {"lines": ret["lines"]}})
    await bus.publish("return.received", {"return_id": return_id}, actor)
    return await _get_return(return_id)


async def inspect_return(return_id: str, payload: dict, actor: dict) -> dict:
    ret = await _get_return(return_id)
    if ret["status"] != "RETURN_QUARANTINE":
        raise ConflictError("Return not in quarantine")
    await transition("return_request", return_id, "return_requests", "return_id",
                     "INSPECTED", actor, reason=payload.get("findings"))
    await db.db.return_requests.update_one(
        {"return_id": return_id},
        {"$set": {"inspection": {"findings": payload.get("findings"),
                                 "inspector": actor, "at": now_iso()}}})
    return await _get_return(return_id)


async def dispose_return(return_id: str, disposition: str, actor: dict) -> dict:
    """Final disposition: RESTOCK / RTV / DESTRUCTION / QUALITY_HOLD / REJECT."""
    if disposition not in DISPOSITIONS:
        raise ValidationFailed(f"disposition must be in {DISPOSITIONS}")
    ret = await _get_return(return_id)
    if ret["status"] != "INSPECTED":
        raise ConflictError("Inspect before disposition")
    # QA authority required for restock decisions
    if disposition == "RESTOCK":
        from app.core.rbac import QA_AUTHORITY_ROLES

        if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
                and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
            raise ValidationFailed("Restock disposition requires QA authority")

    for line in ret["lines"]:
        batch_id = line.get("batch_id")
        if disposition == "RESTOCK" and batch_id:
            await inventory.unblock_batch(batch_id, actor, "Return restocked")
            line["disposition"] = "RESTOCK"
        elif disposition == "RTV":
            if batch_id:
                await inventory.record_movement(
                    "RTV", line["sku"],
                    (await _return_warehouse(ret)), float(line["quantity"]),
                    batch_id=batch_id, reference_type="RETURN",
                    reference_id=return_id, performed_by=actor,
                    note="Return to vendor")
                await inventory.block_batch(batch_id, "RTV pending pickup",
                                            actor, source="RETURNS")
            line["disposition"] = "RTV"
        elif disposition == "DESTRUCTION":
            if batch_id:
                await inventory.record_movement(
                    "DESTRUCTION", line["sku"],
                    (await _return_warehouse(ret)), float(line["quantity"]),
                    batch_id=batch_id, reference_type="RETURN",
                    reference_id=return_id, performed_by=actor)
                dest_id = await _next_id("destruction", "DST")
                await db.db.destructions.insert_one({
                    "destruction_id": dest_id, "return_id": return_id,
                    "lines": [line], "witnessed_by": actor,
                    "created_at": now_iso()})
            line["disposition"] = "DESTRUCTION"
        elif disposition == "QUALITY_HOLD":
            line["disposition"] = "QUALITY_HOLD"
        else:
            line["disposition"] = disposition

    await transition("return_request", return_id, "return_requests", "return_id",
                     "DISPOSITIONED", actor, reason=disposition)
    target = {"RESTOCK": "RESTOCKED", "RTV": "RTV", "DESTRUCTION": "DESTROYED",
              "QUALITY_HOLD": "QUALITY_HOLD", "REJECT_RETURN": "CLOSED",
              "RECALL_HOLD": "CLOSED"}[disposition]
    await transition("return_request", return_id, "return_requests", "return_id",
                     target, actor)

    if disposition == "RESTOCK":
        from app.domains.finance.service import post_event

        await post_event("return_received",
                         {"return_id": return_id,
                          "amount": sum(float(l["quantity"]) * 10
                                        for l in ret["lines"])})
        # credit note to customer (finance event)
        await bus.publish("return.dispositioned",
                          {"return_id": return_id, "disposition": disposition}, actor)
    await audit("RETURN", return_id, f"DISPOSITION_{disposition}", actor)
    return await _get_return(return_id)


async def _return_warehouse(ret: dict) -> str:
    return ret.get("warehouse_id") or "WH-MAIN"


# ---------------------------------------------------------------------- recall
async def create_recall(payload: dict, actor: dict) -> dict:
    """Recall notice → immediate global block + locate everywhere."""
    if not payload.get("product_id") and not payload.get("batch_ids"):
        raise ValidationFailed("Recall needs product_id or batch_ids")
    async with idempotent("RECALL", payload.get("recall_id")) as gate:
        if not gate["first_time"]:
            return gate["result"]
        recall_id = await _next_id("recall", "RCL")
        product_id = payload.get("product_id")
        batch_ids = list(payload.get("batch_ids") or [])
        if product_id and not batch_ids:
            batch_ids = [b["batch_id"] async for b in
                         db.db.batches.find({"product_id": product_id})]

        # 1. GLOBAL BLOCK — stop reservation, picking, dispensing immediately
        for bid in batch_ids:
            await inventory.block_batch(bid,
                                        f"RECALL {recall_id}",
                                        {"type": "AGENT", "id": "compliance-agent"},
                                        source="RECALL")
            await bus.publish("batch.blocked",
                              {"batch_id": bid, "reason": f"RECALL {recall_id}"})

        # 2. LOCATE: warehouses, in-transit, production, customers
        balances = [_clean(dict(r)) async for r in
                    db.db.inventory_balances.find({"batch_id": {"$in": batch_ids}})]
        in_transit_shipments = [_clean(dict(s)) async for s in
                                db.db.shipments.find({
                                    "status": {"$in": ["PLANNED", "LOADING",
                                                       "DISPATCHED", "IN_TRANSIT"]}})]
        affected_customers = []
        so_ids = set()
        async for mv in db.db.inventory_movements.find({
                "batch_id": {"$in": batch_ids},
                "movement_type": {"$in": ["SALE", "DISPENSE"]}}):
            so_ids.add(mv.get("reference_id"))
        for so_id in so_ids:
            if not so_id:
                continue
            so = await db.db.sales_orders.find_one({"order_id": so_id})
            if so:
                affected_customers.append({
                    "order_id": so_id, "customer_id": so.get("customer_id")})

        doc = {
            "recall_id": recall_id,
            "product_id": product_id,
            "batch_ids": batch_ids,
            "class": payload.get("class", "CLASS_II"),
            "reason": payload.get("reason", "Quality issue"),
            "source_ref": payload.get("source_ref"),
            "status": "IN_PROGRESS",
            "located": {
                "balances": balances,
                "in_transit_shipments": [s["shipment_id"] for s in in_transit_shipments],
                "affected_customers": affected_customers,
            },
            "quarantine_tasks": [],
            "retrieved": 0.0,
            "destroyed": 0.0,
            "created_by": actor,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "IN_PROGRESS", "actor": actor, "at": now_iso()}],
        }
        await db.db.recalls.insert_one(doc)

        # 3. Create quarantine tasks (physical work queue)
        for b in balances:
            task_id = await _next_id("task", "TSK")
            await db.db.recall_tasks.insert_one({
                "task_id": task_id, "recall_id": recall_id,
                "type": "QUARANTINE_STOCK",
                "warehouse_id": b["warehouse_id"],
                "batch_id": b["batch_id"],
                "quantity": b["quantity"],
                "status": "OPEN",
                "created_at": now_iso()})
            doc["quarantine_tasks"].append(task_id)
        for s in in_transit_shipments:
            task_id = await _next_id("task", "TSK")
            await db.db.recall_tasks.insert_one({
                "task_id": task_id, "recall_id": recall_id,
                "type": "STOP_SHIPMENT",
                "shipment_id": s["shipment_id"],
                "status": "OPEN",
                "created_at": now_iso()})
            doc["quarantine_tasks"].append(task_id)
        await db.db.recalls.update_one(
            {"recall_id": recall_id},
            {"$set": {"quarantine_tasks": doc["quarantine_tasks"]}})

        # 4. Notifications
        from app.core.notifications import notify

        await notify("RECALL_NOTICE_CREATED",
                     f"RECALL {recall_id}: {payload.get('reason')}",
                     f"Product {product_id}, batches {batch_ids}. "
                     f"Global block applied; {len(doc['quarantine_tasks'])} tasks created.",
                     ["qa@pharmaos.local", "warehouse@pharmaos.local",
                      "compliance@pharmaos.local"],
                     "RECALL", recall_id)
        await bus.publish("batch.recalled",
                          {"recall_id": recall_id,
                           "batches": batch_ids,
                           "customers": len(affected_customers)}, actor)
        await audit("RECALL", recall_id, "INITIATED", actor,
                    details={"batches": batch_ids})
        result = _clean(doc)
    return result


async def complete_quarantine_task(task_id: str, actor: dict) -> dict:
    task = await db.db.recall_tasks.find_one({"task_id": task_id})
    if not task:
        raise NotFound(f"Task {task_id} not found")
    await db.db.recall_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"status": "DONE", "done_by": actor, "done_at": now_iso()}})
    if task.get("batch_id"):
        await inventory.record_movement(
            "NEGATIVE_ADJUSTMENT",
            (await inventory._get_batch(task["batch_id"]))["product_id"],
            task["warehouse_id"], float(task["quantity"]),
            batch_id=task["batch_id"],
            reference_type="RECALL_TASK", reference_id=task_id,
            performed_by=actor, note="Recall retrieval")
    open_count = await db.db.recall_tasks.count_documents(
        {"recall_id": task["recall_id"], "status": "OPEN"})
    if open_count == 0:
        await transition("recall", task["recall_id"], "recalls", "recall_id",
                         "CONTAINED", actor, reason="All quarantine tasks done")
    return {"task_id": task_id, "open_tasks_remaining": open_count}


async def close_recall(recall_id: str, payload: dict, actor: dict) -> dict:
    """QA authority closes recall after reconciliation."""
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        raise ValidationFailed("Recall closure requires QA authority")
    rec = await db.db.recalls.find_one({"recall_id": recall_id})
    if not rec:
        raise NotFound(f"Recall {recall_id} not found")
    open_tasks = await db.db.recall_tasks.count_documents(
        {"recall_id": recall_id, "status": "OPEN"})
    if open_tasks:
        raise ConflictError(f"{open_tasks} tasks still open")
    await transition("recall", recall_id, "recalls", "recall_id",
                     "RECONCILED", actor)
    await transition("recall", recall_id, "recalls", "recall_id",
                     "CLOSED", actor, reason=payload.get("summary"))
    await bus.publish("recall.closed", {"recall_id": recall_id}, actor)
    await audit("RECALL", recall_id, "CLOSED", actor,
                details=payload)
    return await db.db.recalls.find_one({"recall_id": recall_id})


async def _get_return(return_id: str) -> dict:
    doc = await db.db.return_requests.find_one({"return_id": return_id})
    if not doc:
        raise NotFound(f"Return {return_id} not found")
    return doc


async def list_returns(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.return_requests.find(q).sort("created_at", -1).limit(200)]


async def list_recalls(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.recalls.find(q).sort("created_at", -1).limit(200)]
