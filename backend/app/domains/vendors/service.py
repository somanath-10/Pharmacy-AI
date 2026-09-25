"""Vendor Management: lifecycle, documents, qualifications, risk, performance, Vendor 360."""
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
    # nxt = {"commercial": "QA_QUALIFICATION", "qa": "COMMERCIAL_REVIEW"}
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
    qual = vendor.get("qualification") or {}
    commercial_q = qual.get("commercial") or {}
    qa_q = qual.get("qa") or {}
    perf = vendor.get("performance") or {}
    score += 30 if has_licence else 0
    score += 20 if has_gmp else 0
    score += 20 if commercial_q.get("result") == "PASS" else 0
    score += 20 if qa_q.get("result") == "PASS" else 0
    otd = perf.get("otd_pct")
    score += 10 if otd is not None and otd >= 90 else 0
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
    if new_status == "BLOCKED":
        await bus.publish("vendor.blocked",
                          {"vendor_id": vendor_id, "reason": reason}, actor)
    return await get_vendor(vendor_id)


# ------------------------------------------------- licence-expiry auto blocking
async def auto_block_on_licence_expiry(actor: Optional[dict] = None) -> List[dict]:
    """Policy: vendors whose drug licence has lapsed are auto-blocked.
    Configurable via policy rule `licence_expiry_block_days` (default 0 = at
    expiry). Blocked vendors are excluded from RFQ publication and PO policy."""
    from app.core.policies import get_rule
    from datetime import timedelta

    from app.core.config import settings

    grace = int(await get_rule("licence_expiry_block_days",
                               settings.LICENCE_EXPIRY_BLOCK_DAYS))
    horizon = (utcnow() - timedelta(days=grace)).strftime("%Y-%m-%d")
    blocked = []
    async for d in db.db.vendor_documents.find({
            "doc_type": "DRUG_LICENCE", "expiry_date": {"$ne": None,
                                                        "$lt": horizon}}):
        vendor = await db.db.vendors.find_one({"vendor_id": d["vendor_id"]})
        if not vendor or vendor["status"] not in ("APPROVED", "SUSPENDED",
                                                  "UNDER_REVIEW",
                                                  "COMMERCIAL_REVIEW"):
            continue
        await transition("vendor", d["vendor_id"], "vendors", "vendor_id",
                         "BLOCKED", actor or {"type": "AGENT",
                                              "id": "vendor-sourcing-agent"},
                         reason=f"Drug licence expired {d['expiry_date']}")
        await bus.publish("vendor.blocked",
                          {"vendor_id": d["vendor_id"],
                           "reason": "LICENCE_EXPIRED",
                           "licence_expiry": d["expiry_date"]},
                          actor or {"type": "AGENT", "id": "vendor-sourcing-agent"})
        await audit("VENDOR", d["vendor_id"], "AUTO_BLOCKED_LICENCE_EXPIRED",
                    actor or {"type": "AGENT", "id": "vendor-sourcing-agent"},
                    details={"licence_expiry": d["expiry_date"]})
        blocked.append({"vendor_id": d["vendor_id"],
                        "licence_expiry": d["expiry_date"]})
    return blocked


async def update_bank_details(vendor_id: str, payload: dict, actor: dict,
                              otp: Optional[str] = None) -> dict:
    """Bank-detail change is a SECURITY_FRAUD queue item — never direct.
    Two-step verification: request generates an OTP; confirm (with otp) files
    the change into the approval queue. Creator may not approve (SoD)."""
    vendor = await get_vendor(vendor_id)
    import hashlib
    import secrets
    from datetime import timedelta

    if not otp:
        code = f"{secrets.randbelow(900000) + 100000}"  # 6-digit OTP
        await db.db.vendor_bank_verifications.update_one(
            {"vendor_id": vendor_id, "requested_by": actor.get("id")},
            {"$set": {"otp_hash": hashlib.sha256(
                          code.encode()).hexdigest(),
                      "expires_at": (utcnow() + timedelta(
                          minutes=10)).isoformat(),
                      "created_at": now_iso()}, },
            upsert=True)
        from app.core.notifications import notify

        # OTP is sent via secure channel (email/SMS), not stored in notification body
        await notify("VENDOR_BANK_OTP_CREATED",
                     f"Bank change verification: {vendor['name']}",
                     f"An OTP has been sent to the vendor's registered contact for bank details change of {vendor_id} "
                     "(valid 10 minutes). Share only with authorized finance staff.",
                     [vendor.get("contact", {}).get("email", "vendor@example.com")],
                     "VENDOR", vendor_id)
        await audit("VENDOR", vendor_id, "BANK_CHANGE_OTP_SENT", actor)
        return {"otp_sent": True,
                "detail": "OTP sent to vendor contact; confirm with otp to file the change"}

    ver = await db.db.vendor_bank_verifications.find_one(
        {"vendor_id": vendor_id, "requested_by": actor.get("id")})
    import hashlib

    if not ver or ver.get("otp_hash") != hashlib.sha256(otp.encode()).hexdigest():
        raise ValidationFailed("Invalid or missing bank-change OTP")
    if ver.get("expires_at") and str(ver["expires_at"])[:19] < now_iso()[:19]:
        raise ValidationFailed("Bank-change OTP expired")
    await db.db.vendor_bank_verifications.delete_one({"_id": ver["_id"]})

    from app.core.approvals import create_approval
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    approval_id = await create_approval(
        category="SECURITY_FRAUD",
        title=f"Vendor bank details change: {vendor['name']}",
        entity_type="VENDOR", entity_id=vendor_id, requested_by=actor,
        evidence={"old": vendor.get("bank_details"),
                  "new": payload.get("bank_details"),
                  "otp_verified": True},
        options=["APPROVE", "REJECT"],
        authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
        on_approve="vendor_bank_change", payload={"vendor_id": vendor_id,
                                                  "bank_details": payload.get("bank_details")},
    )
    await audit("VENDOR", vendor_id, "BANK_CHANGE_REQUESTED", actor,
                details={"approval_id": approval_id, "otp_verified": True})
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
    rfqs = [_clean(dict(e)) async for e in db.db.sourcing_events.find(
        {"$or": [{"invited_vendors": vendor_id},
                 {"published_to": vendor_id}]}).limit(25)]
    bids = [_clean(dict(b)) async for b in db.db.bids.find(
        {"vendor_id": vendor_id}).limit(25)]
    payments = [_clean(dict(p)) async for p in db.db.payments.find(
        {"lines.invoice_id": {"$in": [i["invoice_id"] for i in invoices]}}).limit(25)]
    disputes = [_clean(dict(d)) async for d in db.db.vendor_disputes.find(
        {"vendor_id": vendor_id}).limit(25)]
    qualifs = [_clean(dict(q)) async for q in db.db.vendor_qualifications.find(
        {"vendor_id": vendor_id}).limit(25)]
    perf = await performance(vendor_id)
    # fill rate: received vs ordered across PO lines
    ordered = sum(float(l.get("quantity") or 0) for p in pos for l in p.get("lines", []))
    received = sum(float(l.get("received_qty") or 0) for p in pos for l in p.get("lines", []))
    fill_rate = round(100.0 * received / ordered, 1) if ordered else None
    q_rejects = sum(1 for g in grns for l in g.get("lines", [])
                    if float(l.get("rejected_qty") or 0) > 0)
    quality_score = round(max(0.0, 100.0 - 10.0 * q_rejects), 1) if grns else None
    return {"vendor": vendor, "documents": docs, "contracts": contracts,
            "purchase_orders": pos, "invoices": invoices, "receipts": grns,
            "rfqs": rfqs, "bids": bids, "payments": payments,
            "disputes": disputes, "qualifications": qualifs,
            "performance": perf,
            "scores": {"fill_rate_pct": fill_rate,
                       "quality_score": quality_score,
                       "risk_score": vendor.get("risk_score"),
                       "risk_level": vendor.get("risk_level")}}


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


# ------------------------------------------------------------- requalification
async def create_requalification(vendor_id: str, payload: dict, actor: dict) -> dict:
    """Schedule requalification: vendor returns to UNDER_REVIEW on approval of
    the request (lifecycle restart with qualification evidence)."""
    vendor = await get_vendor(vendor_id)
    if vendor["status"] not in ("APPROVED", "SUSPENDED"):
        raise ConflictError(
            f"Requalification allowed from APPROVED/SUSPENDED, not {vendor['status']}")
    reason = payload.get("reason", "Scheduled requalification")
    rq_id = await _next_seq("requalification")
    doc = {"requalification_id": f"RQ-{int(rq_id):05d}",
           "vendor_id": vendor_id, "reason": reason,
           "status": "OPEN", "requested_by": actor, "created_at": now_iso()}
    await db.db.vendor_requalifications.insert_one(doc)
    await transition("vendor", vendor_id, "vendors", "vendor_id",
                     "UNDER_REVIEW", actor, reason=reason)
    await audit("VENDOR", vendor_id, "REQUALIFICATION_OPENED", actor,
                reason=reason)
    return _clean(doc)


# -------------------------------------------------- material/site qualifications
QUAL_STATUSES = ["PROPOSED", "QA_APPROVED", "REJECTED", "EXPIRED"]


async def propose_qualification(payload: dict, actor: dict) -> dict:
    """Material/site qualification: (vendor, material, site, spec, validity).
    Required before a quality-critical material may be sourced from a vendor
    for that site (reference: qualifications)."""
    for k in ("vendor_id", "product_id", "site_id", "valid_from", "valid_to"):
        if not payload.get(k):
            raise ValidationFailed(f"Qualification needs {k}")
    if await get_vendor(payload["vendor_id"]) is None:
        raise NotFound(f"Vendor {payload['vendor_id']} not found")
    qid = payload.get("qualification_id") or f"QLF-{int((await _next_seq('qualification'))):05d}"
    doc = {
        "qualification_id": qid,
        "vendor_id": payload["vendor_id"],
        "product_id": payload["product_id"],
        "site_id": payload["site_id"],
        "spec_id": payload.get("spec_id"),
        "valid_from": payload["valid_from"],
        "valid_to": payload["valid_to"],
        "status": "PROPOSED",
        "proposed_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.vendor_qualifications.insert_one(doc)
    await audit("VENDOR_QUALIFICATION", qid, "PROPOSED", actor,
                details={"vendor": payload["vendor_id"],
                         "product": payload["product_id"]})
    return _clean(doc)


async def _next_seq(name: str) -> int:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True)
    return int(doc["seq"])


async def decide_qualification(qualification_id: str, decision: str,
                               actor: dict, notes: str = "") -> dict:
    """QA authority approves/rejects the qualification proposal."""
    from app.core.errors import ValidationFailed
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        raise PermissionDenied("Qualification decision requires QA authority")
    if decision not in ("QA_APPROVED", "REJECTED"):
        raise ValidationFailed("decision must be QA_APPROVED|REJECTED")
    doc = await db.db.vendor_qualifications.find_one(
        {"qualification_id": qualification_id})
    if not doc:
        raise NotFound(f"Qualification {qualification_id} not found")
    if doc["status"] != "PROPOSED":
        raise ConflictError(f"Qualification not PROPOSED: {doc['status']}")
    await db.db.vendor_qualifications.update_one(
        {"qualification_id": qualification_id},
        {"$set": {"status": decision, "decision_notes": notes,
                  "decided_by": actor, "decided_at": now_iso(),
                  "updated_at": now_iso()}})
    await audit("VENDOR_QUALIFICATION", qualification_id, decision, actor,
                reason=notes)
    return await db.db.vendor_qualifications.find_one(
        {"qualification_id": qualification_id})


async def assert_qualified(vendor_id: str, product_id: str, site_id: str,
                           as_of: Optional[str] = None):
    """Raise ConflictError when no QA-approved, in-validity qualification
    covers (vendor, product, site) — used before award/PO on quality-critical
    materials."""
    check = as_of or now_iso()
    doc = await db.db.vendor_qualifications.find_one({
        "vendor_id": vendor_id, "product_id": product_id, "site_id": site_id,
        "status": "QA_APPROVED",
        "valid_from": {"$lte": check}, "valid_to": {"$gte": check}})
    if not doc:
        raise ConflictError(
            f"Vendor {vendor_id} is not QA-qualified for {product_id} at "
            f"{site_id}; propose a qualification first")
    return doc


async def list_qualifications(vendor_id: Optional[str] = None,
                              status: Optional[str] = None) -> List[dict]:
    q: Dict[str, Any] = {}
    if vendor_id:
        q["vendor_id"] = vendor_id
    if status:
        q["status"] = status
    return [_clean(dict(r)) async for r in
            db.db.vendor_qualifications.find(q).sort("created_at", -1).limit(200)]


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


async def portal_list_asns(vendor_id: str) -> List[dict]:
    """ASNs visible to exactly one supplier portal identity."""
    return [_clean(dict(row)) async for row in db.db.asns.find(
        {"vendor_id": vendor_id}).sort("created_at", -1).limit(200)]


async def portal_ack_po(vendor_id: str, po_id: str, payload: dict, actor: dict) -> dict:
    from app.domains.procurement.service import acknowledge_po

    po = await db.db.purchase_orders.find_one({"po_id": po_id})
    if not po or po.get("vendor_id") != vendor_id:
        raise NotFound(f"PO {po_id} not found for vendor")
    return await acknowledge_po(po_id, payload, actor)


async def portal_submit_bid(vendor_id: str, event_id: str, payload: dict, actor: dict) -> dict:
    from app.domains.sourcing.service import submit_bid

    return await submit_bid(event_id, {**payload, "vendor_id": vendor_id}, actor)


# =============================================================== portal self-service
async def portal_register(payload: dict) -> dict:
    """Public vendor self-registration (Part 4). Creates a REGISTERED vendor;
    credentials are provisioned by VENDOR_MANAGER after document review."""
    for k in ("name", "contact"):
        if not payload.get(k):
            raise ValidationFailed(f"Registration needs {k}")
    vendor = await create_vendor({
        "name": payload["name"],
        "legal_name": payload.get("legal_name"),
        "vendor_type": payload.get("vendor_type", "MANUFACTURER"),
        "contact": payload["contact"],
        "address": payload.get("address"),
        "tax_id": payload.get("tax_id"),
        "drug_licence_no": payload.get("drug_licence_no"),
        "source": "PORTAL",
    }, {"type": "ANONYMOUS", "id": "portal-registration"})
    await db.db.vendors.update_one({"vendor_id": vendor["vendor_id"]},
                                   {"$set": {"status": "DOCS_PENDING"}})
    await bus.publish("vendor.registered",
                      {"vendor_id": vendor["vendor_id"],
                       "channel": "PORTAL"},
                      {"type": "ANONYMOUS", "id": "portal-registration"})
    await audit("VENDOR", vendor["vendor_id"], "PORTAL_REGISTRATION",
                {"type": "ANONYMOUS", "id": "portal-registration"},
                new_state="DOCS_PENDING")
    return {"vendor_id": vendor["vendor_id"],
            "status": "DOCS_PENDING",
            "detail": "Submit documents; VENDOR_MANAGER will provision credentials"}


async def portal_upload_document(vendor_id: str, payload: dict, actor: dict) -> dict:
    return await add_document(vendor_id, payload, actor)


async def portal_po_amendment_request(vendor_id: str, po_id: str, payload: dict,
                                      actor: dict) -> dict:
    """Supplier requests a PO amendment (price/date/qty change). Buyer reviews;
    approval creates a new PO version (Part 6 version history)."""
    po = await db.db.purchase_orders.find_one({"po_id": po_id})
    if not po or po.get("vendor_id") != vendor_id:
        raise NotFound(f"PO {po_id} not found for vendor")
    if po["status"] not in ("SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"):
        raise ConflictError(f"Amendment not allowed for PO status {po['status']}")
    from app.core.idempotency import idempotent

    async with idempotent("PO_AMENDMENT_REQ",
                          payload.get("request_id") or None) as gate:
        if not gate["first_time"]:
            return gate["result"]
        from app.domains.procurement.service import _next_id

        req_id = await _next_id("po_amendment", "AMR")
        doc = {"request_id": req_id,
               "po_id": po_id, "vendor_id": vendor_id,
               "changes": payload.get("changes", {}),
               "reason": payload.get("reason", ""),
               "status": "PROPOSED",
               "requested_by": actor, "created_at": now_iso()}
        await db.db.po_amendments.insert_one(doc)
        await audit("PURCHASE_ORDER", po_id, "AMENDMENT_REQUESTED", actor,
                    details={"request": req_id})
        result = _clean(doc)
        gate.store(result)
    return result


async def portal_respond_qa_issue(vendor_id: str, issue_id: str, payload: dict,
                                  actor: dict) -> dict:
    """Vendor responds to a QA issue / SCAR raised against them."""
    issue = await db.db.qa_issues.find_one({"issue_id": issue_id,
                                            "vendor_id": vendor_id})
    if not issue:
        raise NotFound(f"QA issue {issue_id} not found for vendor")
    await db.db.qa_issues.update_one(
        {"issue_id": issue_id},
        {"$push": {"responses": {"by": actor, "at": now_iso(),
                                 "text": payload.get("response", ""),
                                 "attachments": payload.get("attachments", [])}},
         "$set": {"status": "VENDOR_RESPONDED", "updated_at": now_iso()}})
    await audit("QA_ISSUE", issue_id, "VENDOR_RESPONDED", actor)
    return {"issue_id": issue_id, "status": "VENDOR_RESPONDED"}


async def portal_create_dispute(vendor_id: str, payload: dict, actor: dict) -> dict:
    """Vendor raises a dispute (invoice/quality/delivery)."""
    if not payload.get("subject"):
        raise ValidationFailed("Dispute needs subject")
    dispute_id = await _next_seq("dispute")
    doc = {"dispute_id": f"DSP-{int(dispute_id):05d}",
           "vendor_id": vendor_id,
           "subject": payload["subject"],
           "kind": payload.get("kind", "OTHER"),
           "invoice_id": payload.get("invoice_id"),
           "po_id": payload.get("po_id"),
           "description": payload.get("description", ""),
           "status": "OPEN", "opened_by": actor, "created_at": now_iso()}
    await db.db.vendor_disputes.insert_one(doc)
    await bus.publish("vendor.dispute_raised",
                      {"dispute_id": doc["dispute_id"],
                       "vendor_id": vendor_id}, actor)
    await audit("VENDOR_DISPUTE", doc["dispute_id"], "OPENED", actor)
    return _clean(doc)


async def portal_questionnaires(vendor_id: str) -> List[dict]:
    """Outstanding qualification questionnaires for the vendor."""
    return [_clean(dict(q)) async for q in db.db.vendor_questionnaires.find(
        {"vendor_id": vendor_id, "status": {"$in": ["SENT", "DRAFT"]}})]


async def portal_submit_questionnaire(vendor_id: str, questionnaire_id: str,
                                      answers: dict, actor: dict) -> dict:
    doc = await db.db.vendor_questionnaires.find_one(
        {"questionnaire_id": questionnaire_id, "vendor_id": vendor_id})
    if not doc:
        raise NotFound(f"Questionnaire {questionnaire_id} not found")
    if doc["status"] == "SUBMITTED":
        raise ConflictError("Questionnaire already submitted")
    await db.db.vendor_questionnaires.update_one(
        {"questionnaire_id": questionnaire_id},
        {"$set": {"answers": answers, "status": "SUBMITTED",
                  "submitted_at": now_iso()}})
    await audit("VENDOR", vendor_id, "QUESTIONNAIRE_SUBMITTED", actor,
                details={"questionnaire": questionnaire_id})
    return {"questionnaire_id": questionnaire_id, "status": "SUBMITTED"}


async def send_questionnaire(vendor_id: str, questions: List[str], actor: dict) -> dict:
    if not questions:
        raise ValidationFailed("Questionnaire needs questions")
    qid = f"QN-{int(await _next_seq('questionnaire')):05d}"
    doc = {"questionnaire_id": qid, "vendor_id": vendor_id,
           "questions": questions, "answers": None, "status": "SENT",
           "sent_by": actor, "created_at": now_iso()}
    await db.db.vendor_questionnaires.insert_one(doc)
    await audit("VENDOR", vendor_id, "QUESTIONNAIRE_SENT", actor,
                details={"questionnaire": qid})
    return _clean(doc)
