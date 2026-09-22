"""Pharmacovigilance: adverse events → safety cases → medical review →
ICSR/follow-up → signals → PSUR. Quality complaints stay in QMS; can link."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import NotFound, ValidationFailed
from app.core.events import bus
from app.core.workflow import transition


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


SERIOUS_CRITERIA = ["DEATH", "LIFE_THREATENING", "HOSPITALIZATION", "DISABILITY",
                    "CONGENITAL", "OTHER_MEDICALLY_IMPORTANT"]

async def report_adverse_event(payload: dict, actor: Optional[dict] = None) -> dict:
    if not payload.get("suspect_medicine"):
        raise ValidationFailed("AE needs suspect_medicine")
    ae_id = await _next_id("ae", "AE")
    doc = {
        "ae_id": ae_id,
        "reporter": payload.get("reporter", {}),
        "patient": payload.get("patient", {}),
        "suspect_medicine": payload["suspect_medicine"],
        "batch_id": payload.get("batch_id"),
        "reaction": payload.get("reaction"),
        "outcome": payload.get("outcome"),
        "seriousness_criteria": [c for c in payload.get("seriousness_criteria", [])
                                 if c in SERIOUS_CRITERIA],
        "case_id": None,
        "created_at": now_iso(),
    }
    await db.db.adverse_events.insert_one(doc)
    # auto-create safety case
    case = await create_case_from_ae(ae_id, actor)
    doc["case_id"] = case["case_id"]
    await db.db.adverse_events.update_one({"ae_id": ae_id},
                                          {"$set": {"case_id": case["case_id"]}})
    await bus.publish("adverse_event.received",
                      {"ae_id": ae_id, "case_id": case["case_id"],
                       "serious": bool(doc["seriousness_criteria"])}, actor)
    await audit("ADVERSE_EVENT", ae_id, "RECEIVED", actor)
    return doc


async def create_case_from_ae(ae_id: str, actor: Optional[dict]) -> dict:
    case_id = await _next_id("case", "PV")
    doc = {
        "case_id": case_id,
        "ae_ids": [ae_id],
        "product_id": None,
        "batch_id": None,
        "duplicate_of": None,
        "serious": False,
        "expectedness": None,
        "review": None,
        "icsr_id": None,
        "status": "NEW",
        "complaint_id": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "NEW", "actor": actor, "at": now_iso()}],
    }
    await db.db.safety_cases.insert_one(doc)
    return doc


async def triage_case(case_id: str, actor: dict) -> dict:
    case = await _get_case(case_id)
    ae = await db.db.adverse_events.find_one({"case_id": case_id})
    serious = bool((ae or {}).get("seriousness_criteria"))
    await db.db.safety_cases.update_one(
        {"case_id": case_id},
        {"$set": {"serious": serious,
                  "product_id": (ae or {}).get("suspect_medicine"),
                  "batch_id": (ae or {}).get("batch_id")}})
    await transition("safety_case", case_id, "safety_cases", "case_id",
                     "TRIAGE", actor)
    return await _get_case(case_id)


async def duplicate_check(case_id: str, actor: dict) -> dict:
    """Deterministic duplicate detection (patient+product+reaction)."""
    case = await _get_case(case_id)
    ae = await db.db.adverse_events.find_one({"case_id": case_id})
    dup = None
    if ae:
        probe = {"suspect_medicine": ae.get("suspect_medicine"),
                 "reaction": ae.get("reaction")}
        cand = await db.db.adverse_events.find_one({
            **probe, "case_id": {"$ne": case_id},
            "created_at": {"$lt": ae["created_at"]}})
        if cand:
            cand_case = await db.db.safety_cases.find_one(
                {"case_id": cand["case_id"], "status": {"$nin": ["CLOSED"]}})
            if cand_case:
                dup = cand_case["case_id"]
    await db.db.safety_cases.update_one(
        {"case_id": case_id}, {"$set": {"duplicate_of": dup}})
    await transition("safety_case", case_id, "safety_cases", "case_id",
                     "DUPLICATE_CHECK", actor,
                     reason=f"Duplicate of {dup}" if dup else "No duplicate found")
    return await _get_case(case_id)


async def medical_review(case_id: str, payload: dict, actor: dict) -> dict:
    case = await _get_case(case_id)
    if case["status"] != "DUPLICATE_CHECK":
        raise ValidationFailed("Medical review after duplicate check")
    expectedness = payload.get("expectedness", "UNEXPECTED")
    await db.db.safety_cases.update_one(
        {"case_id": case_id},
        {"$set": {"expectedness": expectedness,
                  "review": {"reviewer": actor, "notes": payload.get("notes"),
                             "at": now_iso()}}})
    target = "FOLLOW_UP" if payload.get("needs_follow_up") else "REPORTABLE"
    await transition("safety_case", case_id, "safety_cases", "case_id",
                     "MEDICAL_REVIEW", actor, reason="Review recorded")
    await transition("safety_case", case_id, "safety_cases", "case_id",
                     target, actor, reason=expectedness)
    if target == "REPORTABLE":
        await _generate_icsr(case_id, actor)
    return await _get_case(case_id)


async def complete_follow_up(case_id: str, payload: dict, actor: dict) -> dict:
    await db.db.safety_cases.update_one(
        {"case_id": case_id},
        {"$push": {"follow_ups": {**payload, "by": actor, "at": now_iso()}}})
    await transition("safety_case", case_id, "safety_cases", "case_id",
                     "REPORTABLE", actor, reason="Follow-up complete")
    await _generate_icsr(case_id, actor)
    return await _get_case(case_id)


async def _generate_icsr(case_id: str, actor: dict) -> str:
    case = await _get_case(case_id)
    if case.get("icsr_id"):
        return case["icsr_id"]
    icsr_id = await _next_id("icsr", "ICSR")
    ae = await db.db.adverse_events.find_one({"case_id": case_id})
    await db.db.icsr_reports.insert_one({
        "icsr_id": icsr_id,
        "case_id": case_id,
        "payload": {
            "report_type": "INDIVIDUAL_CASE_SAFETY_REPORT",
            "serious": case.get("serious"),
            "expectedness": case.get("expectedness"),
            "suspect_medicine": (ae or {}).get("suspect_medicine"),
            "batch": (ae or {}).get("batch_id"),
            "reaction": (ae or {}).get("reaction"),
        },
        "generated_by": actor,
        "created_at": now_iso()})
    await db.db.safety_cases.update_one({"case_id": case_id},
                                        {"$set": {"icsr_id": icsr_id}})
    await audit("ICSR", icsr_id, "GENERATED", actor, details={"case": case_id})
    return icsr_id


async def close_case(case_id: str, actor: dict, reason: str = "") -> dict:
    await transition("safety_case", case_id, "safety_cases", "case_id",
                     "CLOSED", actor, reason=reason)
    return await _get_case(case_id)


async def track_signal(payload: dict, actor: dict) -> dict:
    sig_id = await _next_id("signal", "SIG")
    doc = {
        "signal_id": sig_id,
        "product_id": payload.get("product_id"),
        "description": payload.get("description"),
        "case_ids": payload.get("case_ids", []),
        "status": "OPEN",
        "created_by": actor,
        "created_at": now_iso(),
    }
    await db.db.signals.insert_one(doc)
    await audit("SIGNAL", sig_id, "OPENED", actor)
    return doc


async def generate_psur(payload: dict, actor: dict) -> dict:
    """Periodic safety report aggregation."""
    product_id = payload.get("product_id")
    q: Dict[str, Any] = {}
    if product_id:
        q["product_id"] = product_id
    cases = [_clean(dict(c)) async for c in
             db.db.safety_cases.find(q)]
    psur_id = await _next_id("psur", "PSUR")
    doc = {
        "psur_id": psur_id,
        "product_id": product_id,
        "period": payload.get("period"),
        "total_cases": len(cases),
        "serious_cases": sum(1 for c in cases if c.get("serious")),
        "expectedness_summary": _expectedness_summary(cases),
        "created_by": actor,
        "created_at": now_iso(),
    }
    await db.db.psur_reports.insert_one(doc)
    await audit("PSUR", psur_id, "GENERATED", actor,
                details={"cases": len(cases)})
    return doc


def _expectedness_summary(cases: List[dict]) -> dict:
    out: Dict[str, int] = {}
    for c in cases:
        key = c.get("expectedness") or "NOT_REVIEWED"
        out[key] = out.get(key, 0) + 1
    return out


async def _get_case(case_id: str) -> dict:
    doc = await db.db.safety_cases.find_one({"case_id": case_id})
    if not doc:
        raise NotFound(f"Safety case {case_id} not found")
    return doc


async def list_cases(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.safety_cases.find(q).sort("created_at", -1).limit(200)]
