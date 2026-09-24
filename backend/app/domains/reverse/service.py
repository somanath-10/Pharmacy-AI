"""Reverse Logistics & Recall: returns/RMA, return quarantine, inspection,
dispositions (restock/RTV/destruction), recalls with global batch block."""
from typing import Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.workflow import transition
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
RETURN_REASONS = ["DAMAGED", "WRONG_ITEM", "QUALITY", "EXPIRY", "RECALL",
                  "DELIVERY_FAILURE"]

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
        # BUG-4: a return can never exceed what was actually shipped/dispensed,
        # net of quantity already returned on prior accepted returns.
        if so:
            shipped = sum(float(sl["quantity"])
                          for sl in so.get("lines", []) if sl["sku"] == l["sku"])
            already_returned = 0.0
            async for prev in db.db.return_requests.find({
                    "sales_order_id": so["order_id"],
                    "status": {"$nin": ["REJECTED", "CANCELLED"]}}):
                for pl in prev.get("lines", []):
                    if pl.get("sku") == l["sku"]:
                        already_returned += float(pl.get("quantity") or 0)
            max_returnable = shipped - already_returned
            if float(l["quantity"]) > max_returnable + 1e-9:
                raise ValidationFailed(
                    f"Return of {l['quantity']} {l['sku']} exceeds remaining "
                    f"returnable quantity (shipped {shipped}, already returned "
                    f"{already_returned}) on order {so['order_id']}")
        else:
            outflow = 0.0
            async for mv in db.db.inventory_movements.find({
                    "product_id": l["sku"],
                    "movement_type": {"$in": ["SALE", "DISPENSE"]}}):
                outflow += abs(float(mv.get("quantity") or 0))
            already_returned = 0.0
            async for mv in db.db.inventory_movements.find({
                    "product_id": l["sku"],
                    "movement_type": "RETURN_RECEIPT"}):
                already_returned += abs(float(mv.get("quantity") or 0))
            if outflow > 0 and float(l["quantity"]) > outflow - already_returned + 1e-9:
                raise ValidationFailed(
                    f"Return of {l['quantity']} {l['sku']} exceeds net returnable "
                    f"quantity (shipped {outflow}, already returned {already_returned})")

    ret_id = await _next_id("return", "RET")
    rma_id = None
    policy_ok = payload.get("reason") in ("DAMAGED", "WRONG_ITEM", "QUALITY",
                                          "EXPIRY", "RECALL", "DELIVERY_FAILURE")
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
        "status": "REQUESTED" if policy_ok else "PENDING_APPROVAL",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": doc_status(policy_ok), "actor": actor,
                      "at": now_iso()}],
    }
    await db.db.return_requests.insert_one(doc)
    if not policy_ok:
        # non-policy returns (e.g. goodwill / delivery failure beyond policy)
        # require human return approval before an RMA is cut
        from app.core.approvals import create_approval

        approval_id = await create_approval(
            category="FINANCIAL_AUTHORITY",
            title=f"Return approval (non-policy): {ret_id} "
                  f"reason {payload.get('reason', 'QUALITY')}",
            entity_type="RETURN_REQUEST", entity_id=ret_id,
            requested_by=actor,
            evidence={"lines": doc["lines"],
                      "order": payload.get("sales_order_id")},
            options=["APPROVE", "REJECT"],
            on_approve="return_approve", payload={"return_id": ret_id},
        )
        doc["approval_id"] = approval_id
        await db.db.return_requests.update_one(
            {"return_id": ret_id},
            {"$set": {"approval_id": approval_id}})
    await bus.publish("return.requested",
                      {"return_id": ret_id, "rma": rma_id}, actor)
    await audit("RETURN", ret_id, "REQUESTED", actor,
                details={"policy_ok": policy_ok})
    return _clean(doc)


async def approve_return(return_id: str, actor: dict) -> dict:
    """Manual approval path for non-policy returns (RMA issued on approval)."""
    ret = await _get_return(return_id)
    if ret["status"] != "PENDING_APPROVAL":
        raise ConflictError(f"Return not PENDING_APPROVAL: {ret['status']}")
    from app.core.rbac import FINANCE_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & FINANCE_AUTHORITY_ROLES):
        raise ValidationFailed("Return approval requires finance authority")
    rma_id = await _next_id("rma", "RMA")
    await db.db.return_requests.update_one(
        {"return_id": return_id},
        {"$set": {"status": "REQUESTED", "rma_id": rma_id, "policy_ok": True,
                  "approved_by": actor, "updated_at": now_iso()}})
    await transition("return_request", return_id, "return_requests",
                     "return_id", "REQUESTED", actor,
                     reason=f"Approved by finance authority; RMA {rma_id}")
    await audit("RETURN", return_id, "APPROVED", actor,
                details={"rma": rma_id})
    return await _get_return(return_id)


def doc_status(policy_ok: bool) -> str:
    return "REQUESTED" if policy_ok else "PENDING_APPROVAL"


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


async def confirm_pickup(return_id: str, actor: dict) -> dict:
    """Courier/warehouse collected the return: PICKUP_SCHEDULED → PICKED.

    Without this step the return machine dead-locks: receive_return demands
    PICKED but nothing performed the transition (tests/seed had to hack the
    status with direct DB writes).
    """
    ret = await _get_return(return_id)
    if ret["status"] != "PICKUP_SCHEDULED":
        raise ConflictError(f"Return not PICKUP_SCHEDULED: {ret['status']}")
    await transition("return_request", return_id, "return_requests",
                     "return_id", "PICKED", actor, reason="Return collected")
    await bus.publish("return.picked", {"return_id": return_id}, actor)
    await audit("RETURN", return_id, "PICKED", actor,
                previous_state="PICKUP_SCHEDULED", new_state="PICKED")
    return await _get_return(return_id)


async def receive_return(return_id: str, actor: dict) -> dict:
    """Physical receipt → RETURN_QUARANTINE (quantity-level, never AVAILABLE).

    P0 fix: the returned quantity is posted into the RETURN_QUARANTINE stock
    status via ledger movements. The ORIGINAL batch is never wholesale-blocked
    by a single customer's return — only the returned quantity is held and
    later dispositioned individually. Full-batch blocking is reserved for
    recalls (block_batch at recall scope).
    """
    ret = await _get_return(return_id)
    if ret["status"] != "PICKED":
        raise ConflictError(f"Return not PICKED: {ret['status']}")
    await transition("return_request", return_id, "return_requests", "return_id",
                     "RECEIVED", actor)
    await transition("return_request", return_id, "return_requests", "return_id",
                     "RETURN_QUARANTINE", actor,
                     reason="Awaiting inspection")
    warehouse = await _return_warehouse(ret)
    for line in ret["lines"]:
        batch_id = line.get("batch_id")
        if not batch_id:
            # unknown lot: quarantine under a return-lot id (traceable to RMA)
            batch_id = f"RET-{return_id}-{line['sku']}"
            await inventory.ensure_batch(line["sku"], batch_id, None)
            line["batch_id"] = batch_id
        # quantity-level intake: RETURN_RECEIPT movement → RETURN_QUARANTINE row
        mv = await inventory.record_movement(
            movement_type="RETURN_RECEIPT",
            product_id=line["sku"],
            warehouse_id=warehouse,
            quantity=float(line["quantity"]),
            uom=line.get("uom", "BOX"),
            batch_id=batch_id,
            reference_type="RETURN",
            reference_id=return_id,
            performed_by=actor,
            note=f"Customer return intake {return_id}",
        )
        line["movement_id"] = mv["movement_id"]
        line["stock_status"] = "RETURN_QUARANTINE"
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

    warehouse = await _return_warehouse(ret)
    for line in ret["lines"]:
        batch_id = line.get("batch_id")
        if disposition == "RESTOCK":
            # quantity-level release: RETURN_QUARANTINE → AVAILABLE. The rest
            # of the original batch was never blocked, so nothing to unblock.
            await inventory.change_stock_status(
                product_id=line["sku"], warehouse_id=warehouse,
                batch_id=batch_id, from_status="RETURN_QUARANTINE",
                to_status="AVAILABLE", quantity=float(line["quantity"]),
                actor=actor, reference_type="RETURN_RESTOCK",
                reference_id=return_id, uom=line.get("uom", "BOX"))
            line["disposition"] = "RESTOCK"
            line["stock_status"] = "AVAILABLE"
        elif disposition == "RTV":
            if batch_id:
                # RETURN_QUARANTINE → REJECTED row, then RTV ledger movement
                await inventory.change_stock_status(
                    product_id=line["sku"], warehouse_id=warehouse,
                    batch_id=batch_id, from_status="RETURN_QUARANTINE",
                    to_status="REJECTED", quantity=float(line["quantity"]),
                    actor=actor, reference_type="RETURN_RTV",
                    reference_id=return_id, uom=line.get("uom", "BOX"))
                await inventory.record_movement(
                    "RTV", line["sku"], warehouse, float(line["quantity"]),
                    batch_id=batch_id, reference_type="RETURN",
                    reference_id=return_id, performed_by=actor,
                    note="Return to vendor")
            line["disposition"] = "RTV"
        elif disposition == "DESTRUCTION":
            if batch_id:
                await inventory.change_stock_status(
                    product_id=line["sku"], warehouse_id=warehouse,
                    batch_id=batch_id, from_status="RETURN_QUARANTINE",
                    to_status="DAMAGED", quantity=float(line["quantity"]),
                    actor=actor, reference_type="RETURN_DESTROY",
                    reference_id=return_id, uom=line.get("uom", "BOX"))
                await inventory.record_movement(
                    "DESTRUCTION", line["sku"], warehouse,
                    float(line["quantity"]),
                    batch_id=batch_id, reference_type="RETURN",
                    reference_id=return_id, performed_by=actor)
                dest_id = await _next_id("destruction", "DST")
                await db.db.destructions.insert_one({
                    "destruction_id": dest_id, "return_id": return_id,
                    "lines": [line], "witnessed_by": actor,
                    "created_at": now_iso()})
            line["disposition"] = "DESTRUCTION"
        elif disposition == "QUALITY_HOLD":
            await inventory.change_stock_status(
                product_id=line["sku"], warehouse_id=warehouse,
                batch_id=batch_id, from_status="RETURN_QUARANTINE",
                to_status="QUALITY_HOLD", quantity=float(line["quantity"]),
                actor=actor, reference_type="RETURN_HOLD",
                reference_id=return_id, uom=line.get("uom", "BOX"))
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
        # customer credit note computed from the ORIGINAL invoice line prices
        # (never a fake constant) — falls back to price lists / MRP when the
        # invoice line cannot be resolved
        credit_total = 0.0
        from app.domains.masters.service import get_price

        for l in ret["lines"]:
            unit = None
            if ret.get("invoice_id"):
                inv = await db.db.customer_invoices.find_one(
                    {"invoice_id": ret["invoice_id"]})
                for il in (inv or {}).get("lines", []):
                    if il.get("sku") == l["sku"]:
                        unit = float(il.get("unit_price") or 0)
                        break
            if not unit:
                unit = await get_price(l["sku"], "STANDARD") or 0
            credit_total += unit * float(l["quantity"])
        credit_total = round(credit_total, 2)
        if ret.get("invoice_id") and credit_total > 0:
            inv = await db.db.customer_invoices.find_one(
                {"invoice_id": ret["invoice_id"]})
            if inv:
                await db.db.credit_notes.insert_one({
                    "note_id": await _next_id("credit_note", "CN"),
                    "invoice_id": ret["invoice_id"],
                    "type": "CREDIT",
                    "amount": credit_total,
                    "reason": f"Customer return {return_id} restocked",
                    "direction": "CUSTOMER",
                    "return_id": return_id,
                    "created_at": now_iso(),
                    "created_by": actor,
                })
                await db.db.customer_invoices.update_one(
                    {"invoice_id": ret["invoice_id"]},
                    {"$set": {"balance_amount": round(
                        float(inv.get("balance_amount") or
                              inv.get("total_amount") or 0) - credit_total, 2)}})
        await bus.publish("return.dispositioned",
                          {"return_id": return_id, "disposition": disposition,
                           "credit_amount": credit_total}, actor)
    await audit("RETURN", return_id, f"DISPOSITION_{disposition}", actor)
    return await _get_return(return_id)


async def _return_warehouse(ret: dict) -> str:
    return ret.get("warehouse_id") or "WH-MAIN"


# ---------------------------------------------------------------------- recall
async def create_recall(payload: dict, actor: dict,
                        idempotency_key: Optional[str] = None) -> dict:
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
        recall_qty_by_order: Dict[str, Dict[str, float]] = {}
        async for mv in db.db.inventory_movements.find({
                "batch_id": {"$in": batch_ids},
                "movement_type": {"$in": ["SALE", "DISPENSE"]}}):
            so_ids.add(mv.get("reference_id"))
            if mv.get("reference_id") and mv.get("product_id"):
                per = recall_qty_by_order.setdefault(mv["reference_id"], {})
                per[mv["product_id"]] = round(per.get(mv["product_id"], 0) +
                                              abs(float(mv.get("quantity") or 0)), 3)
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

        # 3. Create quarantine tasks (physical work queue) — only for balances
        # that actually hold stock; zero-quantity projection rows (e.g. spent
        # RESERVED legs) would create tasks whose completion always fails.
        for b in balances:
            if not float(b.get("quantity") or 0) > 0:
                continue
            task_id = await _next_id("task", "TSK")
            await db.db.recall_tasks.insert_one({
                "task_id": task_id, "recall_id": recall_id,
                "type": "QUARANTINE_STOCK",
                "warehouse_id": b["warehouse_id"],
                "batch_id": b["batch_id"],
                "quantity": b["quantity"],
                # exact balance dimensions: retrieval must decrement the SAME
                # row that was located (bin/uom/status), not a guessed one
                "location_id": b.get("location_id"),
                "uom": b.get("uom"),
                "stock_status": b.get("stock_status"),
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

        # 3b. Customer retrieval: return tasks + auto-raised RECALL returns per
        # affected order. Physical retrieval is human work; the system creates
        # the work queue and the pre-filled returns.
        customer_returns = []
        agent_actor = {"type": "AGENT", "id": "compliance-agent"}
        for c in affected_customers:
            task_id = await _next_id("task", "TSK")
            await db.db.recall_tasks.insert_one({
                "task_id": task_id, "recall_id": recall_id,
                "type": "CUSTOMER_RETURN", "order_id": c["order_id"],
                "customer_id": c.get("customer_id"),
                "status": "OPEN", "created_at": now_iso()})
            doc["quarantine_tasks"].append(task_id)
            lines = [{"sku": sku, "quantity": qty,
                      "batch_id": (batch_ids[0] if len(batch_ids) == 1 else None)}
                     for sku, qty in recall_qty_by_order.get(c["order_id"], {}).items()]
            if not lines:
                continue
            try:
                ret = await create_return({
                    "sales_order_id": c["order_id"],
                    "customer_id": c.get("customer_id"),
                    "reason": "RECALL", "lines": lines}, agent_actor)
                customer_returns.append({"return_id": ret["return_id"],
                                         "order_id": c["order_id"]})
            except Exception as e:  # order already fully returned, etc.
                customer_returns.append({"order_id": c["order_id"],
                                         "error": str(e)[:150]})
        doc["located"]["customer_returns"] = customer_returns
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
        gate.store(result)
    return result


async def complete_quarantine_task(task_id: str, actor: dict) -> dict:
    task = await db.db.recall_tasks.find_one({"task_id": task_id})
    if not task:
        raise NotFound(f"Task {task_id} not found")
    await db.db.recall_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"status": "DONE", "done_by": actor, "done_at": now_iso()}})
    if task.get("batch_id") and float(task.get("quantity") or 0) > 0:
        await inventory.record_movement(
            "NEGATIVE_ADJUSTMENT",
            (await inventory._get_batch(task["batch_id"]))["product_id"],
            task["warehouse_id"], float(task["quantity"]),
            batch_id=task["batch_id"],
            uom=task.get("uom") or "BOX",
            location_id=task.get("location_id"),
            stock_status=task.get("stock_status") or "AVAILABLE",
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
    # Reconciliation: what was located vs retrieved vs returned by customers
    located_qty = sum(float(b.get("quantity") or 0)
                      for b in (rec.get("located", {}).get("balances") or []))
    reconciliation = {
        "located_warehouse_qty": located_qty,
        "retrieved_warehouse_qty": float(rec.get("retrieved") or 0),
        "destroyed_qty": float(rec.get("destroyed") or 0),
        "customer_return_requests": rec.get("located", {}).get(
            "customer_returns", []),
        "reconciled_at": now_iso(),
    }
    await db.db.recalls.update_one(
        {"recall_id": recall_id},
        {"$set": {"reconciliation": reconciliation}})
    await transition("recall", recall_id, "recalls", "recall_id",
                     "RECONCILED", actor)
    await transition("recall", recall_id, "recalls", "recall_id",
                     "CLOSED", actor, reason=payload.get("summary"))
    await bus.publish("recall.closed", {"recall_id": recall_id,
                                        "reconciliation": reconciliation}, actor)
    await audit("RECALL", recall_id, "CLOSED", actor,
                details={**payload, "reconciliation": reconciliation})
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


async def recall_reconciliation(recall_id: str, actor: dict) -> dict:
    """Pre-closure reconciliation snapshot: located vs retrieved vs returned.

    Read-only QA aid before close_recall — shows what is still outstanding
    (open tasks, unreturned customer stock) so nothing is closed silently.
    """
    rec = await db.db.recalls.find_one({"recall_id": recall_id})
    if not rec:
        raise NotFound(f"Recall {recall_id} not found")
    tasks = [_clean(dict(t)) async for t in
             db.db.recall_tasks.find({"recall_id": recall_id})]
    located_qty = sum(float(b.get("quantity") or 0)
                      for b in (rec.get("located", {}).get("balances") or []))
    return {
        "recall_id": recall_id,
        "status": rec.get("status"),
        "batches": rec.get("batch_ids"),
        "located_warehouse_qty": located_qty,
        "retrieved_qty": float(rec.get("retrieved") or 0),
        "destroyed_qty": float(rec.get("destroyed") or 0),
        "tasks": {"total": len(tasks),
                  "open": sum(1 for t in tasks if t.get("status") == "OPEN"),
                  "by_type": {t: sum(1 for x in tasks
                                     if x.get("type") == t)
                              for t in {x.get("type") for x in tasks}}},
        "customer_returns": rec.get("located", {}).get("customer_returns", []),
        "in_transit_shipments": rec.get("located", {}).get(
            "in_transit_shipments", []),
        "outstanding": bool(any(t.get("status") == "OPEN" for t in tasks)),
    }
