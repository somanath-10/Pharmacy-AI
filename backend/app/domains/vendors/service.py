"""Vendor Management: lifecycle, documents, qualifications, risk, performance, Vendor 360."""
import re
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso, utcnow
from app.core.errors import ConflictError, NotFound, PermissionDenied, ValidationFailed
from app.core.events import bus
from app.core.workflow import transition, record_node

STATUSES = ["REQUESTED", "REGISTERED", "DOCS_PENDING", "UNDER_REVIEW",
            "QA_QUALIFICATION", "COMMERCIAL_REVIEW", "APPROVED",
            "SUSPENDED", "BLOCKED", "BLACKLISTED", "RETIRED"]

VENDOR_DOC_TYPES = ["COMPANY_REGISTRATION", "TAX_CERTIFICATE", "DRUG_LICENCE",
                    "BANK_DETAILS", "MANUFACTURER_AUTHORIZATION", "ISO_CERTIFICATE",
                    "GMP_CERTIFICATE", "INSURANCE", "QUALITY_AGREEMENT", "OTHER"]


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def create_vendor(payload: dict, actor: dict) -> dict:
    required = ["name"]
    for k in required:
        if not payload.get(k):
            raise ValidationFailed(f"Missing field {k}")
    vendor_id = payload.get("vendor_id") or await _next_vendor_id()
    doc = {
        "vendor_id": vendor_id,
        "name": payload["name"],
        "legal_name": payload.get("legal_name"),
        "vendor_type": payload.get("vendor_type", "MANUFACTURER"),
        "contact": payload.get("contact", {}),
        "address": payload.get("address", ""),
        "tax_id": payload.get("tax_id"),
        "drug_licence_no": payload.get("drug_licence_no"),
        "bank_details": payload.get("bank_details", {}),
        "bank_details_locked": False,
        "payment_terms_days": payload.get("payment_terms_days", 30),
        "currency": payload.get("currency", "INR"),
        "status": "REGISTERED",
        "risk_level": None,
        "risk_score": None,
        "qualification": {"commercial": None, "qa": None},
        "performance": {"otd_pct": None, "rejection_pct": None, "score": None},
        "strategic": payload.get("strategic", False),
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "REGISTERED", "actor": actor, "at": now_iso()}],
    }
    await db.db.vendors.insert_one(doc)
    await bus.publish("vendor.registered", {"vendor_id": vendor_id}, actor)
    await audit("VENDOR", vendor_id, "REGISTERED", actor, new_state="REGISTERED")
    await record_node("vendor", vendor_id, "registration", "Vendor Registration",
                      "DONE", actor)
    return _clean(doc)


async def _next_vendor_id() -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": "vendor"}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"VDR-{int(doc['seq']):05d}"


async def add_document(vendor_id: str, payload: dict, actor: dict) -> dict:
    if payload.get("doc_type") not in VENDOR_DOC_TYPES:
        raise ValidationFailed(f"doc_type must be in {VENDOR_DOC_TYPES}")
    vendor = await get_vendor(vendor_id)
    doc = {
        "vendor_id": vendor_id,
        "doc_type": payload["doc_type"],
        "doc_number": payload.get("doc_number"),
        "issue_date": payload.get("issue_date"),
        "expiry_date": payload.get("expiry_date"),
        "file_ref": payload.get("file_ref"),
        "document_id": payload.get("document_id"),
        "verified": False,
        "status": "SUBMITTED",
        "created_at": now_iso(),
    }
    res = await db.db.vendor_documents.insert_one(doc)
    doc["doc_ref_id"] = str(res.inserted_id)
    await db.db.vendor_documents.update_one(
        {"_id": res.inserted_id}, {"$set": {"doc_ref_id": doc["doc_ref_id"]}}
    )
    # move lifecycle: REGISTERED → DOCS_PENDING → UNDER_REVIEW once licence present
    if payload["doc_type"] == "DRUG_LICENCE":
        if vendor["status"] in ("REGISTERED", "DOCS_PENDING"):
            await transition("vendor", vendor_id, "vendors", "vendor_id",
                             "UNDER_REVIEW", actor,
                             reason="Drug licence submitted")
    await audit("VENDOR_DOCUMENT", vendor_id, "SUBMITTED", actor,
                details={"doc_type": payload["doc_type"]})
    return _clean(doc)


async def qualify(vendor_id: str, kind: str, payload: dict, actor: dict) -> dict:
    """Commercial / QA qualification step."""
    if kind not in ("commercial", "qa"):
        raise ValidationFailed("kind must be commercial|qa")
    vendor = await get_vendor(vendor_id)
    upd = {"$set": {f"qualification.{kind}": {
        "result": payload.get("result", "PASS"),
        "score": payload.get("score"),
        "notes": payload.get("notes"),
        "by": actor, "at": now_iso()},
        "updated_at": now_iso()}}
    await db.db.vendors.update_one({"vendor_id": vendor_id}, upd)
    nxt = {"commercial": "QA_QUALIFICATION", "qa": "COMMERCIAL_REVIEW"}
    if vendor["status"] == "UNDER_REVIEW" and kind == "commercial":
        await transition("vendor", vendor_id, "vendors", "vendor_id",
                         "QA_QUALIFICATION", actor)
    elif vendor["status"] == "QA_QUALIFICATION" and kind == "qa":
        await transition("vendor", vendor_id, "vendors", "vendor_id",
                         "COMMERCIAL_REVIEW", actor)
    await audit("VENDOR", vendor_id, f"{kind.upper()}_QUALIFIED", actor,
                details=payload)
    return await get_vendor(vendor_id)


async def assess_risk(vendor_id: str, actor: dict) -> dict:
    vendor = await get_vendor(vendor_id)
    score = 0
    docs = [_clean(dict(d)) async for d in
            db.db.vendor_documents.find({"vendor_id": vendor_id})]
    has_licence = any(d["doc_type"] == "DRUG_LICENCE" for d in docs)
    has_gmp = any(d["doc_type"] in ("GMP_CERTIFICATE", "ISO_CERTIFICATE") for d in docs)
    score += 30 if has_licence else 0
    score += 20 if has_gmp else 0
    score += 20 if vendor.get("qualification", {}).get("commercial", {}).get("result") == "PASS" else 0
    score += 20 if vendor.get("qualification", {}).get("qa", {}).get("result") == "PASS" else 0
    score += 10 if vendor.get("performance", {}).get("otd_pct") and vendor["performance"]["otd_pct"] >= 90 else 0
    risk = "LOW" if score >= 80 else ("MEDIUM" if score >= 50 else "HIGH")
    await db.db.vendors.update_one(
        {"vendor_id": vendor_id},
        {"$set": {"risk_score": score, "risk_level": risk, "updated_at": now_iso()}},
    )
    await audit("VENDOR", vendor_id, "RISK_ASSESSED", actor,
                details={"score": score, "risk": risk})
    return await get_vendor(vendor_id)


async def approve_vendor(vendor_id: str, actor: dict, reason: str = "") -> dict:
    vendor = await get_vendor(vendor_id)
    if vendor.get("risk_level") is None:
        vendor = await assess_risk(vendor_id, actor)

    docs = [_clean(dict(d)) async for d in
            db.db.vendor_documents.find({"vendor_id": vendor_id})]
    has_licence = any(d["doc_type"] == "DRUG_LICENCE" for d in docs)
    if not has_licence:
        raise ValidationFailed("Cannot approve vendor without drug licence")

    # Strategic vendors or HIGH risk need human approval in the queue
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    if vendor.get("strategic") or vendor.get("risk_level") == "HIGH":
        from app.core.approvals import create_approval

        approval_id = await create_approval(
            category="STRATEGIC" if vendor.get("strategic") else "REGULATORY_EXCEPTION",
            title=f"Vendor approval: {vendor['name']} ({vendor_id})",
            entity_type="VENDOR", entity_id=vendor_id, requested_by=actor,
            evidence={"risk": vendor.get("risk_level"),
                      "score": vendor.get("risk_score"),
                      "documents": [d["doc_type"] for d in docs],
                      "qualification": vendor.get("qualification")},
            options=["APPROVE", "REJECT"],
            authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
            on_approve="vendor_approve", payload={"vendor_id": vendor_id},
        )
        await transition("vendor", vendor_id, "vendors", "vendor_id",
                         "COMMERCIAL_REVIEW", actor,
                         reason=f"Pending approval {approval_id}")
        return {"approval_id": approval_id, "status": "PENDING_APPROVAL"}

    return await _do_approve({"vendor_id": vendor_id}, actor)


async def _do_approve(payload: dict, actor: dict) -> dict:
    vendor_id = payload["vendor_id"]
    vendor = await get_vendor(vendor_id)
    if vendor["status"] == "APPROVED":
        return vendor
    await transition("vendor", vendor_id, "vendors", "vendor_id", "APPROVED", actor,
                     reason="All qualifications passed")
    await bus.publish("vendor.approved", {"vendor_id": vendor_id}, actor)
    await record_node("vendor", vendor_id, "approval", "Vendor Approved", "DONE", actor)
    from app.core.notifications import notify

    await notify("VENDOR_REMINDER_CREATED",
                 f"Welcome {vendor['name']}",
                 "Your vendor account is approved. You can now view RFQs and POs.",
                 [vendor.get("contact", {}).get("email", "vendor@example.com")],
                 "VENDOR", vendor_id)
    return await get_vendor(vendor_id)


async def suspend_vendor(vendor_id: str, actor: dict, reason: str) -> dict:
    vendor = await get_vendor(vendor_id)
    if vendor["status"] not in ("APPROVED", "SUSPENDED"):
        raise ConflictError(f"Cannot suspend vendor in status {vendor['status']}")
    new_status = "BLOCKED" if reason.upper().startswith("COMPLIANCE") else "SUSPENDED"
    await transition("vendor", vendor_id, "vendors", "vendor_id", new_status, actor,
                     reason=reason)
    await bus.publish("vendor.suspended", {"vendor_id": vendor_id, "reason": reason}, actor)
    return await get_vendor(vendor_id)


async def update_bank_details(vendor_id: str, payload: dict, actor: dict) -> dict:
    """Bank-detail change is a SECURITY_FRAUD queue item — never direct."""
    vendor = await get_vendor(vendor_id)
    from app.core.approvals import create_approval
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    approval_id = await create_approval(
        category="SECURITY_FRAUD",
        title=f"Vendor bank details change: {vendor['name']}",
        entity_type="VENDOR", entity_id=vendor_id, requested_by=actor,
        evidence={"old": vendor.get("bank_details"), "new": payload.get("bank_details")},
        options=["APPROVE", "REJECT"],
        authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
        on_approve="vendor_bank_change", payload={"vendor_id": vendor_id,
                                                  "bank_details": payload.get("bank_details")},
    )
    await audit("VENDOR", vendor_id, "BANK_CHANGE_REQUESTED", actor,
                details={"approval_id": approval_id})
    return {"approval_id": approval_id}


async def get_vendor(vendor_id: str) -> dict:
    doc = await db.db.vendors.find_one({"vendor_id": vendor_id})
    if not doc:
        raise NotFound(f"Vendor {vendor_id} not found")
    return _clean(doc)


async def list_vendors(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in db.db.vendors.find(q).limit(500)]


async def vendor_360(vendor_id: str) -> dict:
    vendor = await get_vendor(vendor_id)
    docs = [_clean(dict(d)) async for d in
            db.db.vendor_documents.find({"vendor_id": vendor_id})]
    contracts = [_clean(dict(c)) async for c in
                 db.db.contracts.find({"vendor_id": vendor_id})]
    pos = [_clean(dict(p)) async for p in
           db.db.purchase_orders.find({"vendor_id": vendor_id}).limit(50)]
    invoices = [_clean(dict(i)) async for i in
                db.db.supplier_invoices.find({"vendor_id": vendor_id}).limit(50)]
    grns = [_clean(dict(g)) async for g in db.db.grns.find({"vendor_id": vendor_id}).limit(50)]
    perf = await performance(vendor_id)
    return {"vendor": vendor, "documents": docs, "contracts": contracts,
            "purchase_orders": pos, "invoices": invoices, "receipts": grns,
            "performance": perf}


async def performance(vendor_id: str) -> dict:
    """OTD% and QC rejection% from receipts + QC results."""
    grns = [_clean(dict(g)) async for g in
            db.db.grns.find({"vendor_id": vendor_id})]
    pos = {_clean(dict(p))["po_id"]: p async for p in
           db.db.purchase_orders.find({"vendor_id": vendor_id})}
    on_time = 0
    total_deliveries = 0
    for g in grns:
        po = pos.get(g.get("po_id"))
        if not po:
            continue
        total_deliveries += 1
        asn = await db.db.asns.find_one({"asn_id": g.get("asn_id")})
        if asn and asn.get("eta") and g.get("received_at"):
            # on-time = received within ETA + 1 day grace
            try:
                from datetime import datetime

                eta = datetime.fromisoformat(str(asn["eta"])[:19])
                recv = datetime.fromisoformat(str(g["received_at"])[:19])
                if recv <= eta.replace(hour=23, minute=59):
                    on_time += 1
            except ValueError:
                pass
    otd = round(100.0 * on_time / total_deliveries, 1) if total_deliveries else None

    accepted = rejected = 0
    async for g in db.db.grns.find({"vendor_id": vendor_id}):
        for line in g.get("lines", []):
            accepted += float(line.get("accepted_qty") or 0)
            rejected += float(line.get("rejected_qty") or 0)
    rej_pct = round(100.0 * rejected / (accepted + rejected), 2) if (accepted + rejected) else None

    score = 100.0
    if otd is not None:
        score -= max(0, (95 - otd)) * 0.5
    if rej_pct is not None:
        score -= min(rej_pct * 2, 30)
    score = round(max(score, 0), 1)
    out = {"deliveries": total_deliveries, "otd_pct": otd, "rejection_pct": rej_pct,
           "score": score}
    await db.db.vendors.update_one(
        {"vendor_id": vendor_id}, {"$set": {"performance": out, "updated_at": now_iso()}}
    )
    return out


async def licence_expiry_sweep(days: int = 60) -> List[dict]:
    """Compliance agent tool: warn before licence expiry."""
    from datetime import timedelta

    cutoff = (utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
    warnings = []
    async for d in db.db.vendor_documents.find({
            "doc_type": "DRUG_LICENCE",
            "expiry_date": {"$lte": cutoff, "$ne": None}}):
        vendor = await db.db.vendors.find_one({"vendor_id": d["vendor_id"]})
        warnings.append({"vendor_id": d["vendor_id"],
                         "vendor_name": vendor["name"] if vendor else None,
                         "expiry_date": d["expiry_date"]})
        from app.core.notifications import notify

        await notify(
            "LICENCE_EXPIRY_NOTICE_CREATED",
            f"Drug licence expiring: {vendor['name'] if vendor else d['vendor_id']}",
            f"Licence {d.get('doc_number')} expires {d['expiry_date']}. "
            "Renewal required to keep vendor active.",
            ["compliance@pharmaos.local"],
            "VENDOR", d["vendor_id"],
        )
        await bus.publish("vendor.licence_expiry_warning",
                          {"vendor_id": d["vendor_id"],
                           "expiry_date": d["expiry_date"]})
    return warnings


# ----------------------------------------------------------------- vendor portal
async def portal_context(principal: dict) -> dict:
    vendor_id = principal.get("vendor_id")
    if not vendor_id:
        raise PermissionDenied("Not a supplier portal identity")
    vendor = await get_vendor(vendor_id)
    return vendor


async def portal_submit_invoice(vendor_id: str, payload: dict, actor: dict) -> dict:
    from app.domains.finance.service import receive_supplier_invoice

    payload = {**payload, "vendor_id": vendor_id, "channel": "VENDOR_PORTAL"}
    return await receive_supplier_invoice(payload, actor)


async def portal_create_asn(vendor_id: str, payload: dict, actor: dict) -> dict:
    from app.domains.logistics.service import create_asn

    payload = {**payload, "vendor_id": vendor_id}
    return await create_asn(payload, actor)


async def portal_ack_po(vendor_id: str, po_id: str, payload: dict, actor: dict) -> dict:
    from app.domains.procurement.service import acknowledge_po

    po = await db.db.purchase_orders.find_one({"po_id": po_id})
    if not po or po.get("vendor_id") != vendor_id:
        raise NotFound(f"PO {po_id} not found for vendor")
    return await acknowledge_po(po_id, payload, actor)


async def portal_submit_bid(vendor_id: str, event_id: str, payload: dict, actor: dict) -> dict:
    from app.domains.sourcing.service import submit_bid

    return await submit_bid(event_id, {**payload, "vendor_id": vendor_id}, actor)
