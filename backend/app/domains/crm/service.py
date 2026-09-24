"""CRM: markets, campaigns, leads (capture/enrich/score), opportunities, inquiries."""
import re
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.ai_gateway import ai
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, PermissionDenied, ValidationFailed
from app.core.events import bus


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


SOURCES = ["MARKET_RESEARCH", "EXPO", "WEB", "REFERRAL", "CAMPAIGN", "CHATBOT",
           "EMAIL", "PARTNER", "INBOUND_CALL"]
LEAD_STATUSES = ["NEW", "ENRICHED", "SCORED", "QUALIFIED", "DISQUALIFIED", "CONVERTED"]

# ------------------------------------------------------------------- campaigns
CAMPAIGN_STATUSES = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "ACTIVE", "CANCELLED"]


async def create_campaign(payload: dict, actor: dict) -> dict:
    if not payload.get("name"):
        raise ValidationFailed("Campaign needs name")
    cid = await _next_id("campaign", "CMP")
    doc = {
        "campaign_id": cid,
        "name": payload["name"],
        "objective": payload.get("objective"),
        "channel": payload.get("channel", "EMAIL"),
        "budget": float(payload.get("budget") or 0),
        "start_date": payload.get("start_date"),
        "end_date": payload.get("end_date"),
        "target_segments": payload.get("target_segments", []),
        "status": "DRAFT",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
    }
    await db.db.campaigns.insert_one(doc)
    await audit("CAMPAIGN", cid, "CREATED", actor)
    return _clean(doc)


async def submit_campaign(campaign_id: str, actor: dict) -> dict:
    c = await _get_campaign(campaign_id)
    if c["status"] != "DRAFT":
        raise ConflictError(f"Campaign not DRAFT: {c['status']}")
    await db.db.campaigns.update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": "PENDING_APPROVAL", "updated_at": now_iso()}})
    await audit("CAMPAIGN", campaign_id, "SUBMITTED", actor)
    return await _get_campaign(campaign_id)


async def decide_campaign(campaign_id: str, decision: str, actor: dict,
                          reason: str = "") -> dict:
    """Management approves the plan; APPROVED campaigns may be activated."""
    if decision not in ("APPROVED", "REJECTED", "CANCELLED"):
        raise ValidationFailed("decision must be APPROVED|REJECTED|CANCELLED")
    c = await _get_campaign(campaign_id)
    allowed = {
        "PENDING_APPROVAL": {"APPROVED", "REJECTED", "CANCELLED"},
        "DRAFT": {"CANCELLED"},
        "APPROVED": {"CANCELLED"},
    }.get(c["status"], set())
    if decision not in allowed:
        raise ConflictError(
            f"Campaign {campaign_id} {c['status']} cannot become {decision}")
    if decision == "CANCELLED":
        from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

        if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
                and not (set(actor.get("roles", [])) & MANAGEMENT_AUTHORITY_ROLES):
            raise PermissionDenied("Campaign cancellation requires management")
    await db.db.campaigns.update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": decision, "decision_reason": reason,
                  "decided_by": actor, "updated_at": now_iso()}})
    await bus.publish("campaign.decided",
                      {"campaign_id": campaign_id, "decision": decision}, actor)
    await audit("CAMPAIGN", campaign_id, decision, actor, reason=reason)
    return await _get_campaign(campaign_id)


async def activate_campaign(campaign_id: str, actor: dict) -> dict:
    c = await _get_campaign(campaign_id)
    if c["status"] != "APPROVED":
        raise ConflictError(f"Campaign not APPROVED: {c['status']}")
    await db.db.campaigns.update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": "ACTIVE", "updated_at": now_iso()}})
    await audit("CAMPAIGN", campaign_id, "ACTIVATED", actor)
    return await _get_campaign(campaign_id)


async def list_campaigns(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.campaigns.find(q).sort("created_at", -1).limit(200)]


async def _get_campaign(campaign_id: str) -> dict:
    doc = await db.db.campaigns.find_one({"campaign_id": campaign_id})
    if not doc:
        raise NotFound(f"Campaign {campaign_id} not found")
    return doc


# --------------------------------------------------------------- CSV import
MAX_CSV_ROWS = 500


def _parse_csv(content: str) -> List[dict]:
    """Parse lead CSV (company_name,contact_name,contact_email,contact_phone,
    source,notes) with header row; tolerant of extra columns."""
    import csv
    import io

    reader = csv.DictReader(io.StringIO(content))
    rows = []
    for r in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in r.items()
               if k is not None}
        rows.append(row)
    return rows


async def import_leads_csv(content: str, actor: dict,
                           campaign_id: Optional[str] = None) -> dict:
    """Bulk lead import (≤500 rows) with deterministic dedupe rules.

    Dedupe key: exact (company_name lower, contact email lower). Duplicates
    inside the file collapse to the first row; rows matching existing leads
    (or existing customers) are skipped — never re-created.
    """
    rows = _parse_csv(content)
    if len(rows) > MAX_CSV_ROWS:
        raise ValidationFailed(
            f"CSV import limited to {MAX_CSV_ROWS} rows (got {len(rows)})")
    imported, skipped, errors = [], [], []
    seen = set()
    for i, row in enumerate(rows, start=2):  # +1 header, +1 human numbering
        company = row.get("company_name") or ""
        email = (row.get("contact_email") or row.get("email") or "").lower()
        if not company:
            errors.append({"row": i, "error": "missing company_name"})
            continue
        key = (company.lower(), email)
        if key in seen:
            skipped.append({"row": i, "reason": "DUPLICATE_IN_FILE"})
            continue
        seen.add(key)
        existing = await db.db.leads.find_one({
            "company_name": {"$regex": f"^{re.escape(company)}$", "$options": "i"},
            "contact.email": {"$in": [email]}} if email else {
                "company_name": {"$regex": f"^{re.escape(company)}$",
                                 "$options": "i"}})
        cust = await db.db.customers.find_one(
            {"name": {"$regex": f"^{re.escape(company)}$", "$options": "i"}})
        if existing:
            skipped.append({"row": i, "reason": "EXISTS_AS_LEAD",
                            "lead_id": existing["lead_id"]})
            continue
        if cust:
            skipped.append({"row": i, "reason": "EXISTS_AS_CUSTOMER",
                            "code": cust["code"]})
            continue
        source = (row.get("source") or "WEB").upper()
        if source not in SOURCES:
            source = "WEB"
        contact = {"name": row.get("contact_name"), "email": email or None,
                   "phone": row.get("contact_phone") or row.get("phone")}
        lead = await capture_lead({
            "company_name": company, "source": source,
            "campaign_id": campaign_id,
            "contact": {k: v for k, v in contact.items() if v},
            "notes": row.get("notes"),
        }, actor)
        imported.append(lead["lead_id"])
    await audit("LEAD_IMPORT", "batch", "COMPLETED", actor,
                details={"imported": len(imported), "skipped": len(skipped),
                         "errors": len(errors)})
    return {"imported": len(imported), "lead_ids": imported,
            "skipped": skipped, "errors": errors}


async def capture_lead(payload: dict, actor: Optional[dict] = None) -> dict:
    if not payload.get("company_name"):
        raise ValidationFailed("Lead needs company_name")
    if payload.get("source") not in SOURCES:
        raise ValidationFailed(f"source must be in {SOURCES}")
    lead_id = await _next_id("lead", "LEAD")
    doc = {
        "lead_id": lead_id,
        "company_name": payload["company_name"],
        "contact": payload.get("contact", {}),
        "source": payload["source"],
        "campaign_id": payload.get("campaign_id"),
        "market_segment": payload.get("market_segment"),
        "notes": payload.get("notes"),
        "enrichment": {},
        "score": None,
        "status": "NEW",
        "converted_to": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.leads.insert_one(doc)
    await bus.publish("sales.lead_captured",
                      {"lead_id": lead_id, "company": doc["company_name"],
                       "source": doc["source"]}, actor)
    await audit("LEAD", lead_id, "CAPTURED", actor)
    return doc


async def enrich_lead(lead_id: str, actor: Optional[dict] = None) -> dict:
    """AI enrichment (offline fallback: domain heuristics)."""
    lead = await _get_lead(lead_id)
    name = lead["company_name"]
    enrichment: Dict[str, Any] = {}
    segment = "HOSPITAL" if re.search(r"hospital|clinic|medical", name, re.I) else (
        "DISTRIBUTOR" if re.search(r"distrib|traders|agency", name, re.I) else (
            "PHARMACY_CHAIN" if re.search(r"pharmacy|chemist|medicos", name, re.I)
            else "RETAIL"))
    enrichment["predicted_segment"] = segment
    res = await ai.extract_json(
        'Enrich this B2B pharma lead. Return JSON {"predicted_segment",'
        '"estimated_size","intent_signals":[...],"suggested_pitch"}',
        f"Company: {name}. Source: {lead['source']}. Notes: {lead.get('notes')}")
    if res.get("ok") and res.get("data"):
        enrichment.update({k: v for k, v in res["data"].items() if v})
        _ = False
    else:
        _ = True
    await db.db.leads.update_one(
        {"lead_id": lead_id},
        {"$set": {"enrichment": enrichment, "status": "ENRICHED",
                  "updated_at": now_iso()}})
    return await _get_lead(lead_id)


async def score_lead(lead_id: str, actor: Optional[dict] = None) -> dict:
    """Deterministic scoring (AI signals are inputs, never the decider)."""
    lead = await _get_lead(lead_id)
    if lead["status"] == "NEW":
        lead = await enrich_lead(lead_id, actor)
    score = 0
    seg = lead.get("enrichment", {}).get("predicted_segment", "RETAIL")
    score += {"HOSPITAL": 30, "DISTRIBUTOR": 25, "PHARMACY_CHAIN": 20,
              "RETAIL": 10}.get(seg, 5)
    src = lead.get("source")
    score += {"EXPO": 20, "REFERRAL": 15, "MARKET_RESEARCH": 10, "WEB": 10,
              "CAMPAIGN": 8, "CHATBOT": 8, "EMAIL": 5, "PARTNER": 12,
              "INBOUND_CALL": 18}.get(src, 5)
    if lead.get("contact", {}).get("email"):
        score += 10
    if lead.get("contact", {}).get("phone"):
        score += 5
    notes = (lead.get("notes") or "").lower()
    if "urgent" in notes or "immediate" in notes:
        score += 10
    grade = "HOT" if score >= 70 else ("WARM" if score >= 45 else "COLD")
    status = "QUALIFIED" if grade == "HOT" else "SCORED"
    await db.db.leads.update_one(
        {"lead_id": lead_id},
        {"$set": {"score": score, "grade": grade, "status": status,
                  "updated_at": now_iso()}})
    await audit("LEAD", lead_id, "SCORED", actor, details={"score": score,
                                                           "grade": grade})
    return await _get_lead(lead_id)


async def convert_lead(lead_id: str, actor: dict) -> dict:
    """Lead → Opportunity."""
    lead = await _get_lead(lead_id)
    if lead["status"] == "CONVERTED":
        raise ValidationFailed("Lead already converted")
    opp_id = await _next_id("opportunity", "OPP")
    doc = {
        "opp_id": opp_id,
        "lead_id": lead_id,
        "name": f"{lead['company_name']} opportunity",
        "company_name": lead["company_name"],
        "stage": "DISCOVERY",
        "expected_value": None,
        "expected_close": None,
        "customer_id": None,
        "status": "OPEN",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.opportunities.insert_one(doc)
    await db.db.leads.update_one(
        {"lead_id": lead_id},
        {"$set": {"status": "CONVERTED", "converted_to": opp_id,
                  "updated_at": now_iso()}})
    await bus.publish("sales.opportunity_created",
                      {"opp_id": opp_id, "lead_id": lead_id}, actor)
    return doc


async def create_customer_from_opportunity(opp_id: str, payload: dict,
                                           actor: dict) -> dict:
    from app.domains.masters.service import create_customer

    opp = await db.db.opportunities.find_one({"opp_id": opp_id})
    if not opp:
        raise NotFound(f"Opportunity {opp_id} not found")
    customer = await create_customer({
        "name": opp["company_name"], "type": payload.get("type", "RETAIL"),
        **{k: v for k, v in payload.items() if k != "type"},
    })
    await db.db.opportunities.update_one(
        {"opp_id": opp_id},
        {"$set": {"customer_id": customer["code"], "stage": "CUSTOMER",
                  "updated_at": now_iso()}})
    return customer


async def create_inquiry(payload: dict, actor: Optional[dict] = None) -> dict:
    """Customer inquiry / RFQ from a customer or prospect."""
    if not payload.get("customer_id") and not payload.get("prospect_name"):
        raise ValidationFailed("Inquiry needs customer_id or prospect_name")
    inq_id = await _next_id("inquiry", "INQ")
    doc = {
        "inq_id": inq_id,
        "customer_id": payload.get("customer_id"),
        "prospect_name": payload.get("prospect_name"),
        "channel": payload.get("channel", "EMAIL"),
        "raw_text": payload.get("raw_text"),
        "document_id": payload.get("document_id"),
        "understanding": None,
        "lines": payload.get("lines", []),
        "status": "RECEIVED",
        "quote_id": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.inquiries.insert_one(doc)
    await bus.publish("sales.inquiry_received", {"inq_id": inq_id}, actor)
    return doc


async def understand_inquiry(inq_id: str, actor: Optional[dict] = None) -> dict:
    """Document AI / NLU requirement understanding (offline fallback: line heuristics)."""
    inq = await db.db.inquiries.find_one({"inq_id": inq_id})
    if not inq:
        raise NotFound(f"Inquiry {inq_id} not found")
    text = inq.get("raw_text") or ""
    lines = []
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9 -]{2,40}?)\s*[xX*]\s*(\d+)", text):
        lines.append({"product_hint": m.group(1).strip(), "quantity": int(m.group(2))})
    res = await ai.extract_json(
        'Understand this pharma customer inquiry. Return JSON {"intent",'
        '"lines":[{"product","quantity"}],"urgency","notes"}', text[:3000])
    if res.get("ok") and res.get("data", {}).get("lines"):
        lines = res["data"]["lines"]
    await db.db.inquiries.update_one(
        {"inq_id": inq_id},
        {"$set": {"understanding": {"lines": lines, "at": now_iso()},
                  "lines": lines, "status": "UNDERSTOOD"}})
    return await db.db.inquiries.find_one({"inq_id": inq_id})


async def list_leads(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in db.db.leads.find(q).sort(
        "created_at", -1).limit(200)]


async def list_opportunities() -> List[dict]:
    return [_clean(dict(r)) async for r in
            db.db.opportunities.find().sort("created_at", -1).limit(200)]


async def list_inquiries() -> List[dict]:
    return [_clean(dict(r)) async for r in
            db.db.inquiries.find().sort("created_at", -1).limit(200)]


async def _get_lead(lead_id: str) -> dict:
    doc = await db.db.leads.find_one({"lead_id": lead_id})
    if not doc:
        raise NotFound(f"Lead {lead_id} not found")
    return doc


# ==================================================================
# Phase: CRM completion — opportunity→quotation chain, customer 360.
# ==================================================================
async def create_opportunity_rfq(opp_id: str, payload: dict,
                                 actor: Optional[dict] = None) -> dict:
    """Opportunity → formal customer RFQ (an inquiry backed by an opp)."""
    opp = await db.db.opportunities.find_one({"opp_id": opp_id})
    if not opp:
        raise NotFound(f"Opportunity {opp_id} not found")
    if not opp.get("customer_id") and not payload.get("customer_id"):
        raise ValidationFailed("RFQ needs a customer (convert the lead first)")
    lines = payload.get("lines") or []
    if not lines:
        raise ValidationFailed("RFQ needs lines")
    inq_id = await _next_id("inquiry", "INQ")
    doc = {
        "inq_id": inq_id,
        "opp_id": opp_id,
        "customer_id": payload.get("customer_id") or opp.get("customer_id"),
        "prospect_name": None,
        "channel": "RFQ",
        "raw_text": payload.get("raw_text"),
        "document_id": None,
        "understanding": None,
        "lines": [{"sku": l["sku"], "quantity": float(l["quantity"]),
                   "uom": l.get("uom", "BOX")} for l in lines],
        "status": "RECEIVED",
        "quote_id": None,
        "rfq": True,
        "due_date": payload.get("due_date"),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.inquiries.insert_one(doc)
    await db.db.opportunities.update_one(
        {"opp_id": opp_id},
        {"$set": {"stage": "RFQ", "updated_at": now_iso()},
         "$push": {"inquiry_ids": inq_id}})
    await bus.publish("sales.rfq_created",
                      {"inq_id": inq_id, "opp_id": opp_id}, actor)
    await audit("OPPORTUNITY", opp_id, "RFQ_CREATED", actor,
                details={"inq_id": inq_id})
    return _clean(doc)


async def convert_opportunity(opp_id: str, payload: dict, actor: dict) -> dict:
    """Close the opportunity (WON/LOST) with optional customer creation."""
    opp = await db.db.opportunities.find_one({"opp_id": opp_id})
    if not opp:
        raise NotFound(f"Opportunity {opp_id} not found")
    outcome = payload.get("outcome")
    if outcome not in ("WON", "LOST"):
        raise ValidationFailed("outcome must be WON or LOST")
    setd = {"stage": outcome, "status": "CLOSED", "updated_at": now_iso()}
    if outcome == "WON" and payload.get("create_customer") and not opp.get("customer_id"):
        from app.domains.masters.service import create_customer

        customer = await create_customer({
            "name": opp["company_name"], "type": payload.get("type", "RETAIL"),
            **{k: v for k, v in payload.items()
               if k not in ("type", "outcome", "create_customer")}})
        setd["customer_id"] = customer["code"]
    await db.db.opportunities.update_one({"opp_id": opp_id}, {"$set": setd})
    await bus.publish("sales.opportunity_closed",
                      {"opp_id": opp_id, "outcome": outcome}, actor)
    await audit("OPPORTUNITY", opp_id, f"CLOSED_{outcome}", actor)
    return _clean(await db.db.opportunities.find_one({"opp_id": opp_id}))


async def customer_360(customer_id: str) -> dict:
    """Everything about a customer in one call: profile, contacts, commercial
    history, quality interactions and risk signals."""
    cust = await db.db.customers.find_one({"code": customer_id})
    if not cust:
        raise NotFound(f"Customer {customer_id} not found")

    async def rows(col, q, sort_key="created_at", limit=50):
        out = []
        for r in await db.db[col].find(q).sort(sort_key, -1).to_list(limit):
            r.pop("_id", None)
            out.append(r)
        return out

    quotations = await rows("quotations", {"customer_id": customer_id})
    orders = await rows("sales_orders", {"customer_id": customer_id})
    invoices = await rows("customer_invoices", {"customer_id": customer_id})
    returns_ = await rows("return_requests", {"customer_id": customer_id})
    complaints = await rows("complaints", {"customer_id": customer_id}) \
        if "complaints" in await db.db.list_collection_names() else []
    shipments = []
    order_ids = [o["order_id"] for o in orders]
    if order_ids:
        async for sh in db.db.shipments.find({"sales_order_id": {"$in": order_ids}}):
            sh.pop("_id", None)
            shipments.append(sh)
    payments = []
    async for p in db.db.payments.find({"invoice_id": {"$in":
                                         [i["invoice_id"] for i in invoices]}}):
        p.pop("_id", None)
        payments.append(p)

    # derived metrics (never fake)
    open_ar = round(sum(float(i.get("balance_amount") or 0)
                        for i in invoices
                        if i.get("status") in ("ISSUED", "PARTIALLY_PAID")), 2)
    lifetime = round(sum(float(o.get("total_amount") or 0) for o in orders
                         if o.get("status") != "CANCELLED"), 2)
    on_time = 0
    delivered = [s for s in shipments if s.get("status") in ("DELIVERED", "CLOSED")]
    for s in delivered:
        eta, at = s.get("eta"), None
        ev = (s.get("tracking_events") or [])
        for e in ev:
            if e.get("event") == "DELIVERED":
                at = e.get("at")
        if eta and at and str(at) <= str(eta):
            on_time += 1
    otif = round(100.0 * on_time / len(delivered), 1) if delivered else None

    return {
        "profile": _clean(dict(cust)),
        "quotations": quotations, "orders": orders, "invoices": invoices,
        "payments": payments, "returns": returns_, "complaints": complaints,
        "shipments": shipments,
        "metrics": {"lifetime_value": lifetime, "open_ar": open_ar,
                    "orders_count": len(orders),
                    "open_returns": sum(1 for r in returns_
                                        if r.get("status") not in
                                        ("CLOSED", "RESTOCKED", "RTV", "DESTROYED")),
                    "otif_pct": otif},
    }
