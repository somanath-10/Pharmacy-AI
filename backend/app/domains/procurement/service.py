"""Procurement: PRs (policy-approved) and POs (approval matrix, SoD, acks)."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, DomainError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.policies import evaluate_po_approval, evaluate_pr_approval
from app.core.workflow import transition, record_node


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str, width: int = 5) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


# ------------------------------------------------------------------------- PR
async def create_pr(payload: dict, actor: dict, idempotency_key: Optional[str] = None) -> dict:
    if not payload.get("lines"):
        raise ValidationFailed("PR needs lines")
    async with idempotent("PR", idempotency_key) as gate:
        if not gate["first_time"]:
            return gate["result"]
        pr_id = await _next_id("pr", "PR")
        estimated = sum(float(l.get("estimated_amount") or
                              (float(l.get("quantity") or 0) * float(l.get("unit_price") or 0)))
                        for l in payload["lines"])
        doc = {
            "pr_id": pr_id,
            "title": payload.get("title", pr_id),
            "requester": actor,
            "department": payload.get("department"),
            "site_id": payload.get("site_id"),
            "needed_by": payload.get("needed_by"),
            "lines": payload["lines"],
            "estimated_amount": estimated,
            "suggested_vendor_id": payload.get("suggested_vendor_id"),
            "status": "DRAFT",
            "po_id": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "DRAFT", "actor": actor, "at": now_iso()}],
        }
        await db.db.purchase_requisitions.insert_one(doc)
        await audit("PR", pr_id, "CREATED", actor, new_state="DRAFT")
        gate.store(_clean(doc))
        return _clean(doc)


async def submit_pr(pr_id: str, actor: dict) -> dict:
    pr = await _get_pr(pr_id)
    if pr["status"] != "DRAFT":
        raise ConflictError(f"PR not in DRAFT: {pr['status']}")
    await transition("purchase_requisition", pr_id, "purchase_requisitions", "pr_id",
                     "PENDING_APPROVAL", actor)
    policy = await evaluate_pr_approval(pr, actor)
    if policy["decision"] == "AUTO_APPROVED":
        await transition("purchase_requisition", pr_id, "purchase_requisitions", "pr_id",
                         "APPROVED", {"type": "AGENT", "id": "policy-engine"},
                         reason="; ".join(policy["reasons"]))
        await bus.publish("pr.approved", {"pr_id": pr_id, "auto": True}, actor)
        return await _get_pr(pr_id)
    from app.core.approvals import create_approval
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    approval_id = await create_approval(
        category="FINANCIAL_AUTHORITY",
        title=f"PR approval: {pr_id} (amount {pr['estimated_amount']})",
        entity_type="PR", entity_id=pr_id, requested_by=actor,
        evidence={"policy": policy, "lines": pr["lines"]},
        options=["APPROVE", "REJECT"],
        authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
        on_approve="pr_approve", payload={"pr_id": pr_id},
    )
    await audit("PR", pr_id, "HUMAN_APPROVAL_REQUESTED", actor,
                details={"approval_id": approval_id})
    return await _get_pr(pr_id)


# ------------------------------------------------------------------------- PO
async def create_po(payload: dict, actor: dict, idempotency_key: Optional[str] = None) -> dict:
    if not payload.get("lines"):
        raise ValidationFailed("PO needs lines")
    if not payload.get("vendor_id"):
        raise ValidationFailed("PO needs vendor_id")
    vendor = await db.db.vendors.find_one({"vendor_id": payload["vendor_id"]})
    if not vendor:
        raise NotFound(f"Vendor {payload['vendor_id']} not found")

    # contract price enforcement
    contract = None
    if payload.get("contract_id"):
        contract = await db.db.contracts.find_one({"contract_id": payload["contract_id"]})
        if not contract or contract["status"] != "ACTIVE":
            raise ValidationFailed("Contract not active")

    lines = []
    for l in payload["lines"]:
        if not l.get("sku") or not l.get("quantity"):
            raise ValidationFailed("Line needs sku + quantity")
        price = l.get("unit_price")
        if contract:
            item = next((p for p in contract.get("price_items", [])
                         if p.get("product_id") == l["sku"]), None)
            if item:
                price = item["price"]
        if price is None:
            from app.domains.masters.service import get_price

            price = await get_price(l["sku"], "STANDARD") or 0
        lines.append({
            "line_no": len(lines) + 1,
            "sku": l["sku"],
            "description": l.get("description"),
            "quantity": float(l["quantity"]),
            "uom": l.get("uom", "BOX"),
            "unit_price": float(price),
            "amount": float(l["quantity"]) * float(price),
            "tax_pct": float(l.get("tax_pct") or 0),
            "received_qty": 0.0,
            "qc_accepted_qty": 0.0,
            "invoiced_qty": 0.0,
        })
    subtotal = sum(l["amount"] for l in lines)
    tax = sum(l["amount"] * l["tax_pct"] / 100 for l in lines)

    async with idempotent("PO", idempotency_key) as gate:
        if not gate["first_time"]:
            return gate["result"]
        po_id = payload.get("po_id") or await _next_id("po", "PO", width=5)
        doc = {
            "po_id": po_id,
            "vendor_id": payload["vendor_id"],
            "contract_id": payload.get("contract_id"),
            "pr_id": payload.get("pr_id"),
            "site_id": payload.get("site_id"),
            "warehouse_id": payload.get("warehouse_id"),
            "currency": payload.get("currency", "INR"),
            "lines": lines,
            "subtotal": round(subtotal, 2),
            "tax_amount": round(tax, 2),
            "total_amount": round(subtotal + tax, 2),
            "needed_by": payload.get("needed_by"),
            "payment_terms": payload.get("payment_terms",
                                         vendor.get("payment_terms_days", 30)),
            "status": "DRAFT",
            "approval": None,
            "ack": None,
            "received_at": None,
            "created_by": actor,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": 1,
            "timeline": [{"state": "DRAFT", "actor": actor, "at": now_iso()}],
        }
        await db.db.purchase_orders.insert_one(doc)
        await audit("PURCHASE_ORDER", po_id, "CREATED", actor, new_state="DRAFT")
        result = _clean(doc)
        gate.store(result)

    # record node for workflow viewer
    await record_node("purchase_order", po_id, "po_draft", "PO Created", "DONE", actor)
    return result


async def submit_po_for_approval(po_id: str, actor: dict) -> dict:
    po = await _get_po(po_id)
    if po["status"] != "DRAFT":
        raise ConflictError(f"PO not in DRAFT: {po['status']}")
    await transition("purchase_order", po_id, "purchase_orders", "po_id",
                     "PENDING_APPROVAL", actor)
    policy = await evaluate_po_approval(po, actor)
    if policy["decision"] == "AUTO_APPROVED":
        return await _approve_po(po_id,
                                 {"type": "AGENT", "id": "policy-engine"},
                                 reason="; ".join(policy["reasons"]),
                                 policy=policy["policy"])
    from app.core.approvals import create_approval
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    approval_id = await create_approval(
        category="FINANCIAL_AUTHORITY",
        title=f"PO approval: {po_id} (amount {po['total_amount']})",
        entity_type="PURCHASE_ORDER", entity_id=po_id, requested_by=actor,
        evidence={"policy": policy, "lines": po["lines"],
                  "vendor": po["vendor_id"]},
        options=["APPROVE", "REJECT"],
        authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
        on_approve="po_approve", payload={"po_id": po_id},
    )
    await db.db.purchase_orders.update_one(
        {"po_id": po_id}, {"$set": {"approval": {"approval_id": approval_id,
                                                 "type": "HUMAN", "policy": policy}}}
    )
    await audit("PURCHASE_ORDER", po_id, "HUMAN_APPROVAL_REQUESTED", actor,
                details={"approval_id": approval_id, "policy": policy})
    return await _get_po(po_id)


async def _approve_po(po_id: str, actor: dict, reason: str = "",
                      policy: Optional[str] = None) -> dict:
    await transition("purchase_order", po_id, "purchase_orders", "po_id",
                     "APPROVED", actor, reason=reason)
    await db.db.purchase_orders.update_one(
        {"po_id": po_id},
        {"$set": {"approval": {"type": actor.get("type", "USER"),
                               "id": actor.get("id"), "policy": policy,
                               "reason": reason, "at": now_iso()}}},
    )
    await bus.publish("po.approved", {"po_id": po_id}, actor)
    await audit("PURCHASE_ORDER", po_id, "APPROVED", actor,
                previous_state="PENDING_APPROVAL", new_state="APPROVED",
                policy=policy, reason=reason)
    await record_node("purchase_order", po_id, "po_approval", "PO Approved",
                      "DONE", actor, detail=reason)
    return await _get_po(po_id)


async def send_po(po_id: str, actor: dict) -> dict:
    po = await _get_po(po_id)
    if po["status"] != "APPROVED":
        raise ConflictError(f"PO not APPROVED: {po['status']}")
    await transition("purchase_order", po_id, "purchase_orders", "po_id",
                     "SENT", actor, reason="Transmitted to vendor")
    vendor = await db.db.vendors.find_one({"vendor_id": po["vendor_id"]})
    from app.core.notifications import notify

    await notify("PO_NOTIFICATION_CREATED",
                 f"New purchase order {po_id}",
                 f"PO {po_id} for {po['total_amount']} {po['currency']} — please acknowledge "
                 "in the vendor portal.",
                 [vendor.get("contact", {}).get("email", "vendor@example.com")] if vendor else [],
                 "PURCHASE_ORDER", po_id)
    return await _get_po(po_id)


async def acknowledge_po(po_id: str, payload: dict, actor: dict) -> dict:
    po = await _get_po(po_id)
    if po["status"] != "SENT":
        raise ConflictError(f"PO not SENT: {po['status']}")
    ack = {"accepted": payload.get("accepted", True),
           "confirmed_delivery": payload.get("confirmed_delivery"),
           "comments": payload.get("comments"),
           "by": actor, "at": now_iso()}
    await transition("purchase_order", po_id, "purchase_orders", "po_id",
                     "ACKNOWLEDGED", actor, reason="Vendor acknowledged")
    await db.db.purchase_orders.update_one({"po_id": po_id}, {"$set": {"ack": ack}})
    await bus.publish("po.acknowledged", {"po_id": po_id}, actor)
    return await _get_po(po_id)


async def receive_on_grn(po_id: str, received_lines: List[dict]) -> dict:
    """Called by warehouse receiving: update line received quantities."""
    po = await _get_po(po_id)
    for rl in received_lines:
        for line in po["lines"]:
            if line["line_no"] == rl["line_no"]:
                line["received_qty"] = round(
                    float(line["received_qty"]) + float(rl["quantity"]), 3)
    fully = all(float(l["received_qty"]) >= float(l["quantity"]) for l in po["lines"])
    partially = any(0 < float(l["received_qty"]) < float(l["quantity"]) for l in po["lines"])
    if po["status"] in ("ACKNOWLEDGED", "PARTIALLY_RECEIVED"):
        target = "RECEIVED" if fully else ("PARTIALLY_RECEIVED" if partially else None)
        if target and target != po["status"]:
            await transition("purchase_order", po_id, "purchase_orders", "po_id",
                             target, {"type": "SYSTEM", "id": "warehouse-flow"},
                             reason="Goods received")
        else:
            await db.db.purchase_orders.update_one(
                {"po_id": po_id}, {"$set": {"lines": po["lines"],
                                            "updated_at": now_iso()}})
    return await _get_po(po_id)


async def close_po(po_id: str, actor: dict, reason: str = "Completed") -> dict:
    po = await _get_po(po_id)
    if po["status"] not in ("RECEIVED", "PARTIALLY_RECEIVED", "ACKNOWLEDGED"):
        raise ConflictError(f"Cannot close PO in {po['status']}")
    await transition("purchase_order", po_id, "purchase_orders", "po_id",
                     "CLOSED", actor, reason=reason)
    return await _get_po(po_id)


async def resolve_sourcing_for_lines(skus: List[str]) -> Optional[dict]:
    """Interlink: source-to-contract → procurement. Find an ACTIVE contract/BPA
    covering the SKUs, else the last approved vendor that supplied them."""
    async for c in db.db.contracts.find({"status": "ACTIVE",
                                         "type": {"$in": ["BPA", "CONTRACT"]}}):
        covered = {p.get("product_id") for p in c.get("price_items", [])}
        if covered & set(skus):
            return {"vendor_id": c["vendor_id"], "contract_id": c["contract_id"],
                    "source": "CONTRACT"}
    last_grn = await db.db.grns.find_one(
        {"lines.sku": {"$in": skus}, "vendor_id": {"$exists": True, "$ne": None}},
        sort=[("created_at", -1)])
    if last_grn and last_grn.get("vendor_id"):
        v = await db.db.vendors.find_one({"vendor_id": last_grn["vendor_id"],
                                          "status": "APPROVED"})
        if v:
            return {"vendor_id": v["vendor_id"], "contract_id": None,
                    "source": "PRIOR_SUPPLY"}
    return None


async def convert_pr(pr_id: str, payload: dict, actor: dict,
                     idempotency_key: Optional[str] = None) -> dict:
    pr = await _get_pr(pr_id)
    if pr["status"] != "APPROVED":
        raise ConflictError(f"PR not APPROVED: {pr['status']}")
    async with idempotent("PR_CONVERT", idempotency_key or f"{pr_id}") as gate:
        if not gate["first_time"]:
            return gate["result"]
        skus = [l["sku"] for l in pr["lines"]]
        vendor_id = payload.get("vendor_id") or pr.get("suggested_vendor_id")
        contract_id = payload.get("contract_id")
        sourcing_source = None
        if not vendor_id:
            resolved = await resolve_sourcing_for_lines(skus)
            if not resolved:
                raise ValidationFailed(
                    f"No vendor resolvable for PR {pr_id}: pass vendor_id, set "
                    "suggested_vendor_id, hold an active contract, or have prior "
                    "supply history")
            vendor_id = resolved["vendor_id"]
            contract_id = contract_id or resolved["contract_id"]
            sourcing_source = resolved["source"]
        po = await create_po({
            "vendor_id": vendor_id,
            "contract_id": contract_id,
            "pr_id": pr_id,
            "lines": [{"sku": l["sku"], "quantity": l["quantity"],
                       "uom": l.get("uom", "BOX")} for l in pr["lines"]],
            **{k: v for k, v in payload.items()
               if k not in ("lines", "vendor_id", "contract_id")},
        }, actor)
        if sourcing_source:
            await db.db.purchase_orders.update_one(
                {"po_id": po["po_id"]},
                {"$set": {"sourcing_source": sourcing_source}})
            po["sourcing_source"] = sourcing_source
        # routine work is automatic: submit through the approval matrix immediately
        po = await submit_po_for_approval(po["po_id"], actor)
        await transition("purchase_requisition", pr_id, "purchase_requisitions",
                         "pr_id", "CONVERTED", actor, reason=f"Converted to {po['po_id']}")
        await db.db.purchase_requisitions.update_one(
            {"pr_id": pr_id}, {"$set": {"po_id": po["po_id"]}})
        gate.store(po)
    return po


# ------------------------------------------------------------------- queries
async def _get_pr(pr_id: str) -> dict:
    doc = await db.db.purchase_requisitions.find_one({"pr_id": pr_id})
    if not doc:
        raise NotFound(f"PR {pr_id} not found")
    return _clean(doc)


async def _get_po(po_id: str) -> dict:
    doc = await db.db.purchase_orders.find_one({"po_id": po_id})
    if not doc:
        raise NotFound(f"PO {po_id} not found")
    return _clean(doc)


async def get_po(po_id: str) -> dict:
    return await _get_po(po_id)


async def list_prs(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.purchase_requisitions.find(q).sort("created_at", -1).limit(200)]


async def list_pos(status: Optional[str] = None, vendor_id: Optional[str] = None) -> List[dict]:
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    if vendor_id:
        q["vendor_id"] = vendor_id
    return [_clean(dict(r)) async for r in
            db.db.purchase_orders.find(q).sort("created_at", -1).limit(200)]


async def open_pos_for_product(sku: str) -> List[dict]:
    out = []
    async for po in db.db.purchase_orders.find({
            "status": {"$in": ["APPROVED", "SENT", "ACKNOWLEDGED",
                               "PARTIALLY_RECEIVED"]}}):
        for line in po.get("lines", []):
            if line.get("sku") == sku and float(line["quantity"]) > float(line["received_qty"]):
                out.append({"po_id": po["po_id"], "vendor_id": po["vendor_id"],
                            "open_qty": float(line["quantity"]) - float(line["received_qty"]),
                            "needed_by": po.get("needed_by")})
    return out


async def workflow_view(po_id: str) -> dict:
    po = await _get_po(po_id)
    nodes = []
    stage_map = [
        ("requirement", "Requirement"),
        ("pr", "PR"),
        ("rfq", "RFQ"),
        ("bid", "Bid"),
        ("auction", "Auction"),
        ("award", "BRA / Award"),
        ("po", "PO"),
        ("ack", "Supplier Ack"),
        ("asn", "ASN"),
        ("inbound", "Inbound"),
        ("grn", "GRN"),
        ("qc", "QC"),
        ("qa", "QA"),
        ("putaway", "Putaway"),
        ("invoice", "Invoice"),
        ("match", "4-Way Match"),
        ("payment", "Payment"),
    ]
    wf = await db.db.workflows.find_one({"entity_type": "purchase_order",
                                         "entity_id": po_id})
    done_nodes = {n["node"]: n for n in (wf or {}).get("nodes", [])}
    for key, label in stage_map:
        node = done_nodes.get(key)
        nodes.append({
            "node": key, "label": label,
            "status": "DONE" if node else ("PENDING" if key != "payment" else "PENDING"),
            "actor": (node or {}).get("actor"),
            "at": (node or {}).get("at"),
            "detail": (node or {}).get("detail"),
        })
    return {"po": po, "stages": nodes,
            "current_status": po["status"],
            "timeline": po.get("timeline", [])}
