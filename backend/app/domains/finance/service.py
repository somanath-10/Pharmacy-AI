"""Finance: event-driven postings, supplier invoices + 2/3/4-way matching,
credit notes, AP payments + authorization, AR + cash application,
reconciliation, valuation, GL."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.config import settings
from app.core.database import db, now_iso
from app.core.errors import ConflictError, DomainError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.policies import evaluate_payment_authorization, get_rule as _get_rule
from app.core.workflow import transition, record_node


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


# ------------------------------------------------------------------ GL engine
async def post_gl(account: str, debit: float, credit: float, description: str,
                  ref_type: Optional[str] = None, ref_id: Optional[str] = None,
                  actor: Optional[dict] = None):
    """Append-only GL entry."""
    doc = {
        "account": account,
        "debit": round(float(debit), 2),
        "credit": round(float(credit), 2),
        "description": description,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "posted_by": actor or {"type": "SYSTEM", "id": "finance-engine"},
        "created_at": now_iso(),
    }
    await db.db.gl_entries.insert_one(doc)
    return doc


async def post_event(event: str, payload: dict):
    """Financial postings triggered by operational events (idempotent-ish by ref)."""
    if event == "grn_posted":
        pass  # inventory accounting handled at receipt/valuation
    elif event == "material_issued":
        await post_gl("WIP", payload.get("batch_size", 0) * 10, 0,
                      f"Material issued to production {payload.get('order_id')}",
                      "PRODUCTION_ORDER", payload.get("order_id"))
        await post_gl("INVENTORY", 0, payload.get("batch_size", 0) * 10,
                      f"Inventory consumed {payload.get('order_id')}",
                      "PRODUCTION_ORDER", payload.get("order_id"))
    elif event == "production_completed":
        await post_gl("FINISHED_GOODS", payload.get("quantity", 0) * 12, 0,
                      f"FG receipt {payload.get('batch_id')}",
                      "PRODUCTION_ORDER", payload.get("order_id"))
        await post_gl("WIP", 0, payload.get("quantity", 0) * 12,
                      f"WIP to FG {payload.get('order_id')}",
                      "PRODUCTION_ORDER", payload.get("order_id"))
    elif event == "customer_invoiced":
        await post_gl("ACCOUNTS_RECEIVABLE", payload.get("amount", 0), 0,
                      f"Invoice {payload.get('invoice_id')}",
                      "CUSTOMER_INVOICE", payload.get("invoice_id"))
        await post_gl("REVENUE", 0, payload.get("amount", 0),
                      f"Revenue {payload.get('invoice_id')}",
                      "CUSTOMER_INVOICE", payload.get("invoice_id"))
    elif event == "sale_completed":
        await post_gl("CASH", payload.get("amount", 0), 0,
                      f"POS sale {payload.get('order_id')}",
                      "SALES_ORDER", payload.get("order_id"))
        await post_gl("REVENUE", 0, payload.get("amount", 0),
                      f"POS revenue {payload.get('order_id')}",
                      "SALES_ORDER", payload.get("order_id"))
        await post_gl("COGS", payload.get("cogs", payload.get("amount", 0) * 0.6), 0,
                      f"COGS {payload.get('order_id')}",
                      "SALES_ORDER", payload.get("order_id"))
        await post_gl("INVENTORY", 0, payload.get("cogs", payload.get("amount", 0) * 0.6),
                      f"Inventory out {payload.get('order_id')}",
                      "SALES_ORDER", payload.get("order_id"))
    elif event == "return_received":
        await post_gl("SALES_RETURNS", payload.get("amount", 0), 0,
                      f"Return {payload.get('return_id')}",
                      "RETURN", payload.get("return_id"))


# --------------------------------------------------------- supplier invoices AP
async def receive_supplier_invoice(payload: dict, actor: dict,
                                   idempotency_key: Optional[str] = None) -> dict:
    """Supplier invoice arrives (doc AI or manual) → matching pipeline."""
    if not payload.get("po_id"):
        raise ValidationFailed("Supplier invoice needs po_id")
    po = await db.db.purchase_orders.find_one({"po_id": payload["po_id"]})
    if not po:
        raise NotFound(f"PO {payload['po_id']} not found")
    async with idempotent("SUP_INV", idempotency_key) as gate:
        if not gate["first_time"]:
            return gate["result"]
        invoice_id = payload.get("invoice_id") or await _next_id("sup_inv", "AP")
        lines = payload.get("lines") or [
            {"line_no": l["line_no"], "quantity": l["quantity"],
             "unit_price": l["unit_price"],
             "amount": round(float(l["quantity"]) * float(l["unit_price"]), 2)}
            for l in po["lines"]]
        total = float(payload.get("total_amount") or
                      sum(float(l.get("amount") or
                                (float(l.get("quantity") or 0) *
                                 float(l.get("unit_price") or 0)))
                          for l in lines))
        doc = {
            "invoice_id": invoice_id,
            "supplier_invoice_number": payload.get("supplier_invoice_number",
                                                   invoice_id),
            "vendor_id": po["vendor_id"],
            "po_id": po["po_id"],
            "channel": payload.get("channel", "API"),
            "currency": po.get("currency", "INR"),
            "lines": lines,
            "total_amount": round(total, 2),
            "invoice_date": payload.get("invoice_date"),
            "due_date": payload.get("due_date"),
            "document_id": payload.get("document_id"),
            "status": "RECEIVED",
            "match": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "RECEIVED", "actor": actor, "at": now_iso()}],
        }
        await db.db.supplier_invoices.insert_one(doc)
        await audit("SUPPLIER_INVOICE", invoice_id, "RECEIVED", actor,
                    details={"po": po["po_id"], "amount": doc["total_amount"]})
        result = _clean(doc)
    if payload.get("document_id"):
        from app.core.docai import classify, extract

        dt = await classify(payload["document_id"])
        if dt == "supplier_invoice":
            data = await extract(payload["document_id"])
            await db.db.supplier_invoices.update_one(
                {"invoice_id": invoice_id},
                {"$set": {"extraction": data, "status": "EXTRACTED"}})
    return result


async def match_invoice(invoice_id: str, actor: dict) -> dict:
    """2/3/4-way match: PO + GRN + QA-accepted qty + invoice (tolerance policy)."""
    inv = await _get_inv(invoice_id)
    if inv["status"] not in ("RECEIVED", "EXTRACTED", "IN_MATCHING", "MATCH_FAILED",
                             "DISPUTED"):
        raise ConflictError(f"Invoice not matchable: {inv['status']}")
    # walk the state chain to IN_MATCHING before evaluating
    if inv["status"] == "RECEIVED":
        await transition("supplier_invoice", invoice_id, "supplier_invoices",
                         "invoice_id", "EXTRACTED", actor, reason="No document AI payload")
    if inv["status"] in ("RECEIVED", "EXTRACTED"):
        await transition("supplier_invoice", invoice_id, "supplier_invoices",
                         "invoice_id", "IN_MATCHING", actor, reason="Matching started")
    po = await db.db.purchase_orders.find_one({"po_id": inv["po_id"]})
    grns = [_clean(dict(g)) async for g in db.db.grns.find({"po_id": inv["po_id"]})]

    tolerance_pct = float(await _get_rule("match_tolerance_pct",
                                          settings.MATCH_TOLERANCE_PCT))
    line_results = []
    total_variance = 0.0
    for line in inv["lines"]:
        po_line = next((l for l in po["lines"] if l["line_no"] == line["line_no"]), None)
        grn_qty = sum(
            float(gl["received_qty"]) for g in grns
            for gl in g.get("lines", []) if gl["line_no"] == line["line_no"])
        qa_qty = sum(
            float(gl.get("accepted_qty") or 0) for g in grns
            for gl in g.get("lines", []) if gl["line_no"] == line["line_no"])
        inv_qty = float(line.get("quantity") or 0)
        po_qty = float(po_line["quantity"]) if po_line else 0.0
        unit_price = float(line.get("unit_price") or
                           (po_line["unit_price"] if po_line else 0))
        price_var = 0.0
        if po_line and abs(unit_price - float(po_line["unit_price"])) > 0.001:
            price_var = (unit_price - float(po_line["unit_price"])) * inv_qty
        amount_var = price_var
        # 4-way: qty billed vs QA-accepted (not just received)
        qty_var = (inv_qty - qa_qty) if qa_qty > 0 else (inv_qty - grn_qty)
        amount_var += qty_var * unit_price
        total_variance += amount_var
        line_results.append({
            "line_no": line["line_no"],
            "po_qty": po_qty, "grn_qty": grn_qty, "qa_accepted_qty": qa_qty,
            "invoice_qty": inv_qty, "unit_price": unit_price,
            "qty_variance": round(qty_var, 3),
            "price_variance": round(price_var, 2),
            "amount_variance": round(amount_var, 2),
        })
    tol_amount = abs(float(po.get("total_amount") or 0)) * tolerance_pct / 100
    matched = abs(total_variance) <= max(tol_amount, 0.01)
    match_type = "FOUR_WAY" if any(g.get("lines") for g in grns) else "TWO_WAY"

    result = {
        "type": match_type,
        "matched": matched,
        "total_variance": round(total_variance, 2),
        "tolerance_pct": tolerance_pct,
        "lines": line_results,
        "grn_count": len(grns),
        "at": now_iso(),
    }
    new_status = "MATCHED" if matched else "MATCH_FAILED"
    await transition("supplier_invoice", invoice_id, "supplier_invoices",
                     "invoice_id", new_status, actor,
                     reason=f"{match_type} variance {total_variance}")
    await db.db.supplier_invoices.update_one(
        {"invoice_id": invoice_id}, {"$set": {"match": result}})

    if matched:
        await bus.publish("invoice.matched", {"invoice_id": invoice_id}, actor)
        await record_node("purchase_order", inv["po_id"], "match",
                          "4-Way Match", "DONE", actor,
                          detail=f"Variance {total_variance}")
        await post_gl("ACCOUNTS_PAYABLE", 0, inv["total_amount"],
                      f"AP invoice {invoice_id}", "SUPPLIER_INVOICE", invoice_id)
        await post_gl("GR_IR", inv["total_amount"], 0,
                      f"GR/IR clearing {invoice_id}", "SUPPLIER_INVOICE", invoice_id)
    else:
        await bus.publish("invoice.match_failed",
                          {"invoice_id": invoice_id,
                           "variance": total_variance}, actor)
        await record_node("purchase_order", inv["po_id"], "match",
                          "4-Way Match", "EXCEPTION", actor,
                          detail=f"Variance {total_variance}")
        # Self-resolution: Finance Agent investigates BEFORE human escalation
        investigation = await _agent_investigate_mismatch(invoice_id, result, actor)
        result["investigation"] = investigation
    await audit("SUPPLIER_INVOICE", invoice_id, "MATCHED" if matched else "MATCH_FAILED",
                actor, details={"variance": total_variance})
    return result


async def _agent_investigate_mismatch(invoice_id: str, match_result: dict,
                                      actor: dict) -> dict:
    """Finance Agent self-resolution loop (deterministic investigation)."""
    steps = []
    inv = await _get_inv(invoice_id)
    variance = match_result["total_variance"]
    po = await db.db.purchase_orders.find_one({"po_id": inv["po_id"]})

    # 1. Check QA records for rejected quantities explaining the variance
    qa_rejected = 0.0
    grns = [_clean(dict(g)) async for g in db.db.grns.find({"po_id": inv["po_id"]})]
    for g in grns:
        for gl in g.get("lines", []):
            qa_rejected += float(gl.get("rejected_qty") or 0)
    unit_price = float(inv["lines"][0]["unit_price"]) if inv["lines"] else 0
    expected_from_rejection = qa_rejected * unit_price
    steps.append({
        "step": "CHECK_QA_RECORDS",
        "found": qa_rejected,
        "detail": f"QA rejected {qa_rejected} units across {len(grns)} GRNs",
    })
    if qa_rejected > 0 and abs(variance - expected_from_rejection) <= 0.01:
        # 2. Request credit note / corrected invoice from supplier
        task_id = await _next_id("task", "TSK")
        await db.db.agent_tasks.insert_one({
            "task_id": task_id,
            "type": "SUPPLIER_CORRECTION_REQUEST",
            "invoice_id": invoice_id,
            "vendor_id": inv["vendor_id"],
            "requested_amount": round(abs(variance), 2),
            "explanation": (f"Invoice {inv['supplier_invoice_number']} bills full "
                            f"quantity; QA rejected {qa_rejected}. Credit note of "
                            f"{abs(variance)} requested."),
            "status": "OPEN",
            "created_at": now_iso(),
        })
        from app.core.notifications import notify

        vendor = await db.db.vendors.find_one({"vendor_id": inv["vendor_id"]})
        await notify("INVOICE_EXCEPTION_CREATED",
                     f"Invoice exception {inv['supplier_invoice_number']}",
                     f"Credit note of {abs(variance):.2f} requested for rejected "
                     f"quantity ({qa_rejected}).",
                     [vendor.get("contact", {}).get("email", "vendor@example.com")]
                     if vendor else [],
                     "SUPPLIER_INVOICE", invoice_id)
        steps.append({
            "step": "REQUEST_CREDIT_NOTE",
            "task_id": task_id,
            "detail": f"Supplier correction requested for {abs(variance):.2f}",
        })
        return {"resolved": False, "awaiting": "SUPPLIER_CREDIT_NOTE",
                "steps": steps, "human_escalation": False}
    steps.append({"step": "SELF_RESOLUTION_FAILED",
                  "detail": "Variance not explained by QA records"})
    # 3. Human escalation with evidence
    from app.core.approvals import create_approval
    from app.core.rbac import FINANCE_AUTHORITY_ROLES

    approval_id = await create_approval(
        category="UNRESOLVED_EXCEPTION",
        title=f"Invoice mismatch: {invoice_id} (variance {variance})",
        entity_type="SUPPLIER_INVOICE", entity_id=invoice_id,
        requested_by={"type": "AGENT", "id": "finance-agent"},
        evidence={"match": match_result, "po": po["po_id"],
                  "qa_rejected_qty": qa_rejected},
        options=["APPROVE", "REJECT"],
        authority_roles=list(FINANCE_AUTHORITY_ROLES),
        on_approve="invoice_match_accept",
        payload={"invoice_id": invoice_id},
    )
    steps.append({"step": "HUMAN_ESCALATION", "approval_id": approval_id})
    return {"resolved": False, "awaiting": "HUMAN_DECISION",
            "approval_id": approval_id, "steps": steps, "human_escalation": True}


async def apply_credit_note(invoice_id: str, payload: dict, actor: dict) -> dict:
    """Credit note received → adjust invoice → re-run match."""
    inv = await _get_inv(invoice_id)
    amount = float(payload.get("amount") or 0)
    if amount <= 0:
        raise ValidationFailed("Credit note amount must be positive")
    note_id = await _next_id("credit_note", "CN")
    doc = {
        "note_id": note_id,
        "invoice_id": invoice_id,
        "type": payload.get("type", "CREDIT"),
        "amount": amount,
        "reason": payload.get("reason", "Supplier correction"),
        "created_at": now_iso(),
        "created_by": actor,
    }
    await db.db.credit_notes.insert_one(doc)
    await db.db.supplier_invoices.update_one(
        {"invoice_id": invoice_id},
        {"$set": {"total_amount": round(inv["total_amount"] - amount, 2)}})
    await audit("CREDIT_NOTE", note_id, "APPLIED", actor,
                details={"invoice": invoice_id, "amount": amount})
    # re-run the match
    match = await match_invoice(invoice_id, actor)
    return {"credit_note": note_id, "match": match}


async def approve_matched_invoice(invoice_id: str, actor: dict) -> dict:
    inv = await _get_inv(invoice_id)
    if inv["status"] != "MATCHED":
        raise ConflictError(f"Invoice not MATCHED: {inv['status']}")
    await transition("supplier_invoice", invoice_id, "supplier_invoices",
                     "invoice_id", "APPROVED", actor, reason="Matched invoice approved")
    await record_node("purchase_order", inv["po_id"], "invoice",
                      "Invoice Approved", "DONE", actor)
    return await _get_inv(invoice_id)


# ------------------------------------------------------------------ payments AP
async def create_payment_proposal(payload: dict, actor: dict) -> dict:
    """AP payment proposal for approved invoices (optionally filtered by due)."""
    invoice_ids = payload.get("invoice_ids")
    if not invoice_ids:
        cutoff = (datetime.utcnow() + timedelta(days=int(payload.get("due_days", 7)))
                  ).strftime("%Y-%m-%d")
        rows = [_clean(dict(r)) async for r in db.db.supplier_invoices.find({
            "status": "APPROVED"})]
        invoice_ids = [r["invoice_id"] for r in rows]
    lines = []
    total = 0.0
    for iid in invoice_ids:
        inv = await _get_inv(iid)
        if inv["status"] != "APPROVED":
            raise ConflictError(f"Invoice {iid} not APPROVED")
        lines.append({"invoice_id": iid, "vendor_id": inv["vendor_id"],
                      "amount": inv["total_amount"]})
        total += inv["total_amount"]
    payment_id = await _next_id("payment", "PAY")
    doc = {
        "payment_id": payment_id,
        "type": "OUTGOING",
        "lines": lines,
        "amount": round(total, 2),
        "currency": "INR",
        "method": payload.get("method", "BANK_TRANSFER"),
        "status": "PROPOSED",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "PROPOSED", "actor": actor, "at": now_iso()}],
    }
    await db.db.payments.insert_one(doc)
    await audit("PAYMENT", payment_id, "PROPOSED", actor,
                details={"amount": total, "invoices": invoice_ids})
    return doc


async def authorize_payment(payment_id: str, actor: dict) -> dict:
    """Payment authorization: over limit → Human Decision Queue + SoD."""
    pay = await db.db.payments.find_one({"payment_id": payment_id})
    if not pay:
        raise NotFound(f"Payment {payment_id} not found")
    if pay["status"] != "PROPOSED":
        raise ConflictError(f"Payment not PROPOSED: {pay['status']}")
    policy = await evaluate_payment_authorization(pay, actor)
    if policy["decision"] == "AUTO_APPROVED":
        await transition("payment", payment_id, "payments", "payment_id",
                         "AUTHORIZED", {"type": "AGENT", "id": "policy-engine"},
                         reason="; ".join(policy["reasons"]))
        return await db.db.payments.find_one({"payment_id": payment_id})
    from app.core.approvals import create_approval
    from app.core.rbac import FINANCE_AUTHORITY_ROLES

    approval_id = await create_approval(
        category="FINANCIAL_AUTHORITY",
        title=f"Payment authorization: {payment_id} (amount {pay['amount']})",
        entity_type="PAYMENT", entity_id=payment_id, requested_by=actor,
        evidence={"policy": policy, "lines": pay["lines"]},
        options=["APPROVE", "REJECT"],
        authority_roles=list(FINANCE_AUTHORITY_ROLES),
        on_approve="payment_authorize", payload={"payment_id": payment_id},
    )
    await audit("PAYMENT", payment_id, "HUMAN_AUTHORIZATION_REQUESTED", actor,
                details={"approval_id": approval_id})
    return {**pay, "approval_id": approval_id, "status": pay["status"],
            "pending_authorization": True}


async def execute_payment(payment_id: str, payload: dict, actor: dict,
                          idempotency_key: Optional[str] = None) -> dict:
    pay = await db.db.payments.find_one({"payment_id": payment_id})
    if not pay:
        raise NotFound(f"Payment {payment_id} not found")
    if pay["status"] != "AUTHORIZED":
        raise ConflictError(f"Payment not AUTHORIZED: {pay['status']}")
    async with idempotent("PAYMENT_EXEC", idempotency_key or payment_id) as gate:
        if not gate["first_time"]:
            return gate["result"]
        await transition("payment", payment_id, "payments", "payment_id",
                         "PAID", actor, reason=f"Paid via {pay.get('method')}")
        # Apply invoice payments (walk APPROVED → SCHEDULED → PAID)
        for line in pay["lines"]:
            inv = await db.db.supplier_invoices.find_one(
                {"invoice_id": line["invoice_id"]})
            if inv["status"] == "APPROVED":
                await transition("supplier_invoice", line["invoice_id"],
                                 "supplier_invoices", "invoice_id", "SCHEDULED",
                                 actor, reason=f"Scheduled by {payment_id}")
            if inv["status"] in ("APPROVED", "SCHEDULED"):
                await transition("supplier_invoice", line["invoice_id"],
                                 "supplier_invoices", "invoice_id", "PAID", actor,
                                 reason=f"Paid by {payment_id}")
            await post_gl("ACCOUNTS_PAYABLE", line["amount"], 0,
                          f"Payment {payment_id}", "PAYMENT", payment_id)
            await post_gl("BANK", 0, line["amount"],
                          f"Payment {payment_id}", "PAYMENT", payment_id)
        await bus.publish("payment.completed",
                          {"payment_id": payment_id,
                           "amount": pay["amount"]}, actor)
        # close the payment node on every PO workflow in this payment
        for line in pay["lines"]:
            inv = await db.db.supplier_invoices.find_one(
                {"invoice_id": line["invoice_id"]})
            if inv and inv.get("po_id"):
                await record_node("purchase_order", inv["po_id"], "payment",
                                  f"Payment {payment_id}", "DONE", actor)
        result = await db.db.payments.find_one({"payment_id": payment_id})
    return _clean(result)


# --------------------------------------------------------------- AR & cash app
async def apply_cash(payload: dict, actor: dict) -> dict:
    """Customer payment received → cash application on invoices."""
    invoice_id = payload.get("invoice_id")
    amount = float(payload.get("amount") or 0)
    inv = await db.db.customer_invoices.find_one({"invoice_id": invoice_id})
    if not inv:
        raise NotFound(f"Customer invoice {invoice_id} not found")
    if inv["status"] not in ("ISSUED", "PARTIALLY_PAID"):
        raise ConflictError(f"Invoice not open: {inv['status']}")
    balance = float(inv.get("balance_amount") or inv["total_amount"])
    new_balance = round(balance - amount, 2)
    status = "PAID" if new_balance <= 0 else "PARTIALLY_PAID"
    await db.db.customer_invoices.update_one(
        {"invoice_id": invoice_id},
        {"$set": {"balance_amount": max(new_balance, 0), "status": status,
                  "updated_at": now_iso()},
         "$push": {"cash_applications": {"amount": amount, "at": now_iso(),
                                         "reference": payload.get("reference"),
                                         "by": actor}}})
    await post_gl("CASH", amount, 0, f"Cash received {invoice_id}",
                  "CUSTOMER_INVOICE", invoice_id)
    await post_gl("ACCOUNTS_RECEIVABLE", 0, amount,
                  f"AR settled {invoice_id}", "CUSTOMER_INVOICE", invoice_id)
    await audit("CUSTOMER_INVOICE", invoice_id, f"CASH_APPLIED_{status}", actor,
                details={"amount": amount})
    return {"invoice_id": invoice_id, "balance": max(new_balance, 0),
            "status": status}


# ------------------------------------------------------------ reconciliation
async def run_reconciliation(payload: dict, actor: dict) -> dict:
    """Bank reconciliation: match payments & cash to bank statement lines."""
    statement_lines = payload.get("statement_lines", [])
    matched, unmatched = [], []
    for sl in statement_lines:
        ref = sl.get("reference") or ""
        pay = await db.db.payments.find_one({"payment_id": {"$in": [ref]},
                                             "status": "PAID"})
        doc = None
        if pay:
            doc = {"type": "PAYMENT", "id": pay["payment_id"],
                   "amount": pay["amount"]}
        else:
            inv = await db.db.customer_invoices.find_one({
                "invoice_id": ref, "status": {"$in": ["PAID", "PARTIALLY_PAID"]}})
            if inv:
                doc = {"type": "CUSTOMER_INVOICE", "id": inv["invoice_id"],
                       "amount": inv["total_amount"] - float(inv.get("balance_amount") or 0)}
        if doc and abs(float(doc["amount"]) - abs(float(sl.get("amount") or 0))) <= 0.01:
            matched.append({"statement_line": sl, "matched_to": doc})
        else:
            unmatched.append(sl)
    rec_id = await _next_id("recon", "REC")
    out = {
        "rec_id": rec_id,
        "matched": matched,
        "unmatched": unmatched,
        "status": "BALANCED" if not unmatched else "EXCEPTIONS",
        "created_by": actor,
        "created_at": now_iso(),
    }
    await db.db.reconciliations.insert_one(out)
    await audit("RECONCILIATION", rec_id, "COMPLETED", actor,
                details={"matched": len(matched), "unmatched": len(unmatched)})
    return out


# ------------------------------------------------------------------ valuation
async def inventory_valuation(product_id: Optional[str] = None) -> dict:
    q: Dict[str, Any] = {"quantity": {"$gt": 0}}
    if product_id:
        q["product_id"] = product_id
    rows = [_clean(dict(r)) async for r in db.db.inventory_balances.find(q)]
    total_value = 0.0
    for r in rows:
        cost = float(r.get("unit_cost") or 0)
        if not cost:
            # weighted avg from purchase movements
            mv = await db.db.inventory_movements.find_one(
                {"product_id": r["product_id"], "movement_type": "PURCHASE_RECEIPT",
                 "unit_cost": {"$gt": 0}}, sort=[("created_at", -1)])
            cost = float((mv or {}).get("unit_cost") or 0)
        total_value += float(r["quantity"]) * cost
    return {"products": len(rows), "total_value": round(total_value, 2)}


async def write_off(payload: dict, actor: dict) -> dict:
    """Expiry/damage write-off (ledger movement + GL)."""
    from app.domains.inventory.service import record_movement

    mv = await record_movement(
        movement_type="EXPIRY_WRITE_OFF" if payload.get("reason", "EXPIRY")
        .upper() == "EXPIRY" else "DAMAGE",
        product_id=payload["product_id"],
        warehouse_id=payload["warehouse_id"],
        quantity=float(payload["quantity"]),
        batch_id=payload.get("batch_id"),
        reference_type="WRITE_OFF",
        reference_id=payload.get("note", "MANUAL"),
        performed_by=actor,
        note=payload.get("reason"),
    )
    value = float(payload["quantity"]) * float(payload.get("unit_cost") or 10)
    await post_gl("INVENTORY_WRITE_OFF", value, 0,
                  f"Write-off {payload['product_id']}", "MOVEMENT",
                  mv["movement_id"])
    await post_gl("INVENTORY", 0, value, f"Write-off {payload['product_id']}",
                  "MOVEMENT", mv["movement_id"])
    await audit("WRITE_OFF", mv["movement_id"], "POSTED", actor, details=payload)
    return mv


async def _get_inv(invoice_id: str) -> dict:
    doc = await db.db.supplier_invoices.find_one({"invoice_id": invoice_id})
    if not doc:
        raise NotFound(f"Supplier invoice {invoice_id} not found")
    return doc


async def list_supplier_invoices(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.supplier_invoices.find(q).sort("created_at", -1).limit(200)]


async def list_customer_invoices(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.customer_invoices.find(q).sort("created_at", -1).limit(200)]


async def gl_export(account: Optional[str] = None, limit: int = 500) -> List[dict]:
    q = {} if not account else {"account": account}
    return [_clean(dict(r)) async for r in
            db.db.gl_entries.find(q).sort("created_at", -1).limit(limit)]
