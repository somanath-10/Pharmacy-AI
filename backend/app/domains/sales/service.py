"""Sales/OMS: quotations, customer PO intake (Document AI), sales orders,
allocation (FEFO), pick/pack hooks, invoicing, POS, partial shipments."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.config import settings
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.policies import evaluate_quotation_discount
from app.core.workflow import transition, record_node
from app.domains.inventory import service as inventory


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str, width: int = 5) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):0{width}d}"


# -------------------------------------------------------------------- quotation
async def create_quotation(payload: dict, actor: dict) -> dict:
    if not payload.get("customer_id"):
        raise ValidationFailed("Quotation needs customer_id")
    if not payload.get("lines"):
        raise ValidationFailed("Quotation needs lines")
    from app.domains.masters.service import get_price

    lines = []
    for l in payload["lines"]:
        price = l.get("unit_price") or await get_price(l["sku"], "STANDARD") or 0
        qty = float(l.get("quantity") or 0)
        lines.append({"sku": l["sku"], "quantity": qty,
                      "uom": l.get("uom", "BOX"),
                      "unit_price": float(price),
                      "amount": round(qty * float(price), 2)})
    subtotal = sum(l["amount"] for l in lines)
    quote_id = await _next_id("quote", "QTN")
    doc = {
        "quote_id": quote_id,
        "customer_id": payload["customer_id"],
        "inquiry_id": payload.get("inquiry_id"),
        "opp_id": payload.get("opp_id"),
        "lines": lines,
        "subtotal": round(subtotal, 2),
        "discount_pct": float(payload.get("discount_pct") or 0),
        "tax_pct": float(payload.get("tax_pct") or 0),
        "validity_days": int(payload.get("validity_days", 30)),
        "status": "DRAFT",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "DRAFT", "actor": actor, "at": now_iso()}],
    }
    await db.db.quotations.insert_one(doc)
    await audit("QUOTATION", quote_id, "CREATED", actor)
    return doc


async def send_quotation(quote_id: str, actor: dict) -> dict:
    q = await _get_quote(quote_id)
    if q["status"] != "DRAFT":
        raise ConflictError(f"Quotation not DRAFT: {q['status']}")
    policy = await evaluate_quotation_discount(q, actor)
    if policy["decision"] != "AUTO_APPROVED":
        from app.core.approvals import create_approval
        from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

        approval_id = await create_approval(
            category="STRATEGIC",
            title=f"Quotation discount approval: {quote_id} ({q['discount_pct']}%)",
            entity_type="QUOTATION", entity_id=quote_id, requested_by=actor,
            evidence={"policy": policy, "lines": q["lines"]},
            options=["APPROVE", "REJECT"],
            authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
            on_approve="quotation_approve", payload={"quote_id": quote_id},
        )
        return {"approval_id": approval_id, "status": "PENDING_APPROVAL"}
    return await _do_send_quote({"quote_id": quote_id}, actor)


async def _do_send_quote(payload: dict, actor: dict) -> dict:
    quote_id = payload["quote_id"]
    await transition("sales_order", quote_id, "quotations", "quote_id", "SENT", actor,
                     reason="Quotation sent to customer")
    q = await _get_quote(quote_id)
    from app.core.notifications import notify

    cust = await db.db.customers.find_one({"code": q["customer_id"]})
    await notify("ORDER_CONFIRMATION_CREATED",
                 f"Quotation {quote_id}",
                 f"Please find quotation {quote_id} for "
                 f"{q['subtotal']} (valid {q['validity_days']} days).",
                 [cust.get("contact", {}).get("email", "customer@example.com")] if cust else [],
                 "QUOTATION", quote_id)
    return await _get_quote(quote_id)


# ------------------------------------------------- customer PO intake (Doc AI)
async def intake_customer_po(payload: dict, actor: dict,
                             idempotency_key: Optional[str] = None) -> dict:
    """Customer PO arrives (file/text) → Document AI → draft sales order."""
    document_id = payload.get("document_id")
    raw_text = payload.get("raw_text")
    extraction: Dict[str, Any] = {}
    if document_id:
        from app.core.docai import classify, extract, match_to_masters

        doc_type = await classify(document_id)
        if doc_type != "customer_po":
            raise ValidationFailed(f"Document classified as {doc_type}, not customer_po")
        extraction = await extract(document_id) or {}
        await match_to_masters(document_id)
    elif raw_text:
        from app.core.ai_gateway import offline_extract_invoice

        extraction = offline_extract_invoice(raw_text)
    else:
        raise ValidationFailed("customer PO intake needs document_id or raw_text")

    cpo_id = await _next_id("cpo", "CPO")
    doc = {
        "cpo_id": cpo_id,
        "customer_id": payload.get("customer_id"),
        "document_id": document_id,
        "raw_text": raw_text,
        "extraction": extraction,
        "po_number": extraction.get("po_number") or extraction.get("invoice_number"),
        "status": "INTAKE",
        "sales_order_id": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.customer_pos.insert_one(doc)
    await audit("CUSTOMER_PO", cpo_id, "INTAKE", actor,
                details={"po_number": doc["po_number"]})
    return doc


# ----------------------------------------------------------------- sales orders
async def create_sales_order(payload: dict, actor: dict,
                             idempotency_key: Optional[str] = None) -> dict:
    if not payload.get("customer_id"):
        raise ValidationFailed("Sales order needs customer_id")
    if not payload.get("lines"):
        raise ValidationFailed("Sales order needs lines")
    customer = await db.db.customers.find_one({"code": payload["customer_id"]})
    if not customer:
        raise NotFound(f"Customer {payload['customer_id']} not found")

    from app.domains.masters.service import get_price

    lines = []
    rx_required = False
    for l in payload["lines"]:
        prod = await db.db.products.find_one({"sku": l["sku"]})
        if not prod:
            raise NotFound(f"Product {l['sku']} not found")
        if prod.get("is_prescription"):
            rx_required = True
        price = l.get("unit_price") or await get_price(l["sku"],
                                                       customer.get("pricing_tier",
                                                                    "STANDARD")) or 0
        qty = float(l.get("quantity") or 0)
        lines.append({"line_no": len(lines) + 1, "sku": l["sku"],
                      "quantity": qty, "uom": l.get("uom", prod.get("uom", "BOX")),
                      "unit_price": float(price),
                      "amount": round(qty * float(price), 2),
                      "shipped_qty": 0.0, "invoiced_qty": 0.0})

    subtotal = sum(l["amount"] for l in lines)
    tax_pct = float(payload.get("tax_pct") or 0)
    total = round(subtotal * (1 + tax_pct / 100), 2)

    async with idempotent("SO", idempotency_key) as gate:
        if not gate["first_time"]:
            return gate["result"]
        order_id = payload.get("order_id") or await _next_id("order", "SO")
        # credit check (deterministic)
        credit = await _credit_check(customer, total)
        initial = "PENDING_RX" if rx_required else "DRAFT"
        doc = {
            "order_id": order_id,
            "customer_id": payload["customer_id"],
            "cpo_id": payload.get("cpo_id"),
            "channel": payload.get("channel", "B2B"),
            "warehouse_id": payload.get("warehouse_id"),
            "lines": lines,
            "subtotal": round(subtotal, 2),
            "tax_pct": tax_pct,
            "total_amount": total,
            "credit_check": credit,
            "rx_required": rx_required,
            "prescription_id": None,
            "status": initial,
            "created_by": actor,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": initial, "actor": actor, "at": now_iso()}],
        }
        await db.db.sales_orders.insert_one(doc)
        await audit("SALES_ORDER", order_id, "CREATED", actor, new_state=initial)
        result = _clean(doc)

    if payload.get("cpo_id"):
        await db.db.customer_pos.update_one(
            {"cpo_id": payload["cpo_id"]},
            {"$set": {"sales_order_id": order_id, "status": "CONVERTED"}})
        await record_node("sales_order", order_id, "po_intake",
                          "Customer PO (Doc AI)", "DONE", actor)
    return result


async def _credit_check(customer: dict, order_amount: float) -> dict:
    """Deterministic credit validation against open AR."""
    open_ar = 0.0
    async for inv in db.db.customer_invoices.find({
            "customer_id": customer["code"],
            "status": {"$in": ["ISSUED", "PARTIALLY_PAID"]}}):
        open_ar += float(inv.get("balance_amount") or 0)
    limit = float(customer.get("credit_limit") or 0)
    ok = (limit == 0) or (open_ar + order_amount <= limit)
    return {"ok": ok, "open_ar": round(open_ar, 2), "credit_limit": limit,
            "order_amount": order_amount,
            "reason": None if ok else f"Credit limit {limit} exceeded "
                                      f"(open AR {open_ar})"}


async def confirm_order(order_id: str, actor: dict) -> dict:
    so = await _get_order(order_id)
    if so["status"] == "PENDING_RX":
        raise ConflictError("Order needs prescription approval first")
    if so["status"] != "DRAFT":
        raise ConflictError(f"Order not DRAFT: {so['status']}")
    if not so["credit_check"]["ok"]:
        raise ConflictError(f"Credit check failed: {so['credit_check']['reason']}")
    await transition("sales_order", order_id, "sales_orders", "order_id",
                     "CONFIRMED", actor, reason="Order confirmed")
    await bus.publish("sales.order_confirmed",
                      {"order_id": order_id,
                       "customer_id": so["customer_id"],
                       "total": so["total_amount"]}, actor)
    from app.core.notifications import notify

    cust = await db.db.customers.find_one({"code": so["customer_id"]})
    await notify("ORDER_CONFIRMATION_CREATED",
                 f"Order confirmed {order_id}",
                 f"Your order {order_id} is confirmed and being prepared.",
                 [cust.get("contact", {}).get("email", "customer@example.com")] if cust else [],
                 "SALES_ORDER", order_id)
    await record_node("sales_order", order_id, "confirmation", "Order Confirmed",
                      "DONE", actor)
    return await _get_order(order_id)


async def attach_prescription(order_id: str, prescription_id: str, actor: dict) -> dict:
    so = await _get_order(order_id)
    if not so.get("rx_required"):
        raise ValidationFailed("Order does not require prescription")
    await db.db.sales_orders.update_one(
        {"order_id": order_id},
        {"$set": {"prescription_id": prescription_id}})
    if so["status"] == "DRAFT":
        await transition("sales_order", order_id, "sales_orders", "order_id",
                         "PENDING_RX", actor)
    return await _get_order(order_id)


async def rx_approved(order_id: str, actor: dict) -> dict:
    await transition("sales_order", order_id, "sales_orders", "order_id",
                     "RX_APPROVED", actor, reason="Pharmacist approved prescription")
    return await confirm_order(order_id, actor)


async def allocate_order(order_id: str, actor: dict) -> dict:
    """FEFO allocation + reservations (QA-released stock only)."""
    so = await _get_order(order_id)
    if so["status"] != "CONFIRMED":
        raise ConflictError(f"Order not CONFIRMED: {so['status']}")
    allocations = []
    for line in so["lines"]:
        plan = await inventory.fefo_batches(line["sku"],
                                            so.get("warehouse_id"),
                                            line["quantity"])
        # ensure only QA-released batches
        safe_plan = []
        for p in plan:
            if p["batch_id"] == "UNBATCHED":
                safe_plan.append(p)
                continue
            b = await db.db.batches.find_one({"batch_id": p["batch_id"]})
            if b and b.get("qa_status") == "RELEASED" and not b.get("blocked"):
                safe_plan.append(p)
        if sum(p["allocate"] for p in safe_plan) < line["quantity"]:
            raise ConflictError(
                f"Insufficient QA-released stock for {line['sku']}")
        await inventory.reserve(line["sku"], line["quantity"], "SALES_ORDER",
                                order_id, so.get("warehouse_id"), actor)
        allocations.extend(safe_plan)
    await transition("sales_order", order_id, "sales_orders", "order_id",
                     "ALLOCATED", actor, reason="FEFO allocation complete")
    return {"order_id": order_id, "allocations": allocations}


async def mark_shipped(order_id: str, shipment_id: str, actor: dict) -> dict:
    so = await _get_order(order_id)
    if so["status"] not in ("PACKED", "DISPATCHED"):
        raise ConflictError(f"Order not shippable: {so['status']}")
    if so["status"] == "PACKED":
        await transition("sales_order", order_id, "sales_orders", "order_id",
                         "DISPATCHED", actor, reason=f"Shipment {shipment_id}")
    await db.db.sales_orders.update_one(
        {"order_id": order_id},
        {"$push": {"shipments": shipment_id}})
    return await _get_order(order_id)


async def mark_delivered(order_id: str, actor: dict) -> dict:
    so = await _get_order(order_id)
    if so["status"] != "DISPATCHED":
        raise ConflictError(f"Order not DISPATCHED: {so['status']}")
    await transition("sales_order", order_id, "sales_orders", "order_id",
                     "DELIVERED", actor, reason="Delivered (POD pending)")
    return await _get_order(order_id)


async def invoice_order(order_id: str, actor: dict,
                        idempotency_key: Optional[str] = None) -> dict:
    so = await _get_order(order_id)
    if so["status"] != "DELIVERED":
        raise ConflictError(f"Order not DELIVERED: {so['status']}")
    async with idempotent("CUST_INVOICE", idempotency_key or order_id) as gate:
        if not gate["first_time"]:
            return gate["result"]
        invoice_id = await _next_id("cust_invoice", "INV")
        doc = {
            "invoice_id": invoice_id,
            "sales_order_id": order_id,
            "customer_id": so["customer_id"],
            "lines": so["lines"],
            "subtotal": so["subtotal"],
            "tax_pct": so["tax_pct"],
            "total_amount": so["total_amount"],
            "balance_amount": so["total_amount"],
            "status": "ISSUED",
            "issued_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "ISSUED", "actor": actor, "at": now_iso()}],
        }
        await db.db.customer_invoices.insert_one(doc)
        await transition("sales_order", order_id, "sales_orders", "order_id",
                         "INVOICED", actor, reason=f"Invoice {invoice_id}")
        from app.domains.finance.service import post_event

        await post_event("customer_invoiced",
                         {"invoice_id": invoice_id, "order_id": order_id,
                          "customer_id": so["customer_id"],
                          "amount": so["total_amount"]})
        await bus.publish("invoice.issued",
                          {"invoice_id": invoice_id, "order_id": order_id}, actor)
        result = _clean(doc)
    return result


# ------------------------------------------------------------------------ POS
async def pos_sale(payload: dict, actor: dict,
                   idempotency_key: Optional[str] = None) -> dict:
    """Retail counter sale (walk-in): pay & dispense immediately."""
    if not payload.get("lines"):
        raise ValidationFailed("POS sale needs lines")
    async with idempotent("POS", idempotency_key) as gate:
        if not gate["first_time"]:
            return gate["result"]
        customer_code = payload.get("customer_id") or "WALKIN"
        lines = []
        rx_ids = payload.get("prescription_ids", [])
        for i, l in enumerate(payload["lines"], 1):
            prod = await db.db.products.find_one({"sku": l["sku"]})
            if not prod:
                raise NotFound(f"Product {l['sku']} not found")
            lines.append({"line_no": i, "sku": l["sku"],
                          "quantity": float(l["quantity"]),
                          "uom": prod.get("uom", "BOX"),
                          "unit_price": float(l.get("unit_price") or prod.get("mrp") or 0),
                          "amount": round(float(l["quantity"]) *
                                          float(l.get("unit_price") or prod.get("mrp") or 0), 2),
                          "shipped_qty": float(l["quantity"]),
                          "invoiced_qty": float(l["quantity"])})
        total = sum(l["amount"] for l in lines)
        order_id = await _next_id("order", "POS")
        doc = {
            "order_id": order_id,
            "customer_id": customer_code,
            "channel": "POS",
            "lines": lines,
            "subtotal": round(total, 2),
            "tax_pct": 0,
            "total_amount": round(total, 2),
            "payment": {"method": payload.get("payment_method", "CASH"),
                        "reference": payload.get("payment_reference"),
                        "paid": True},
            "rx_required": bool(rx_ids),
            "prescription_id": rx_ids[0] if rx_ids else None,
            "status": "CLOSED",
            "created_by": actor,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "CLOSED", "actor": actor, "at": now_iso()}],
        }
        await db.db.sales_orders.insert_one(doc)
        # dispense from stock now
        for l in lines:
            plan = await inventory.fefo_batches(l["sku"], payload.get("warehouse_id"),
                                                l["quantity"])
            for p in plan:
                await inventory.record_movement(
                    "DISPENSE", l["sku"], p["warehouse_id"], p["allocate"],
                    batch_id=p["batch_id"] if p["batch_id"] != "UNBATCHED" else None,
                    reference_type="POS_SALE", reference_id=order_id,
                    performed_by=actor)
        from app.domains.finance.service import post_event

        await post_event("sale_completed",
                         {"order_id": order_id, "amount": doc["total_amount"],
                          "customer_id": customer_code})
        await audit("POS_SALE", order_id, "COMPLETED", actor,
                    details={"total": doc["total_amount"]})
        result = _clean(doc)
    return result


async def _get_order(order_id: str) -> dict:
    doc = await db.db.sales_orders.find_one({"order_id": order_id})
    if not doc:
        raise NotFound(f"Sales order {order_id} not found")
    return doc


async def get_order(order_id: str) -> dict:
    return await _get_order(order_id)


async def list_orders(status: Optional[str] = None,
                      customer_id: Optional[str] = None) -> List[dict]:
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    if customer_id:
        q["customer_id"] = customer_id
    return [_clean(dict(r)) async for r in
            db.db.sales_orders.find(q).sort("created_at", -1).limit(200)]


async def _get_quote(quote_id: str) -> dict:
    doc = await db.db.quotations.find_one({"quote_id": quote_id})
    if not doc:
        raise NotFound(f"Quotation {quote_id} not found")
    return doc
