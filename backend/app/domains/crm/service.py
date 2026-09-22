"""CRM: markets, campaigns, leads (capture/enrich/score), opportunities, inquiries."""
import re
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.ai_gateway import ai
from app.core.database import db, now_iso
from app.core.errors import NotFound, ValidationFailed
from app.core.events import bus
from app.core.config import settings


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
        fb = False
    else:
        fb = True
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
