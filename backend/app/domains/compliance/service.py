"""Compliance: licences (expiry monitoring), policy rules, SoD reporting,
regulatory submissions, audit queries."""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso, utcnow
from app.core.errors import NotFound, ValidationFailed


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


LICENCE_TYPES = ["DRUG_LICENCE_20B", "DRUG_LICENCE_21B", "DRUG_LICENCE_20BB",
                 "MANUFACTURING_LICENSE", "GST_REGISTRATION", "IMPORT_EXPORT",
                 "NARCOTICS_LICENSE", "FSSAI"]

async def register_licence(payload: dict, actor: dict) -> dict:
    if payload.get("licence_type") not in LICENCE_TYPES:
        raise ValidationFailed(f"licence_type must be in {LICENCE_TYPES}")
    lic_id = await _next_id("licence", "LIC")
    doc = {
        "licence_id": lic_id,
        "licence_type": payload["licence_type"],
        "licence_number": payload.get("licence_number"),
        "owner_type": payload.get("owner_type", "ORG"),  # ORG | VENDOR
        "owner_id": payload.get("owner_id"),
        "issue_date": payload.get("issue_date"),
        "expiry_date": payload.get("expiry_date"),
        "document_id": payload.get("document_id"),
        "status": "ACTIVE",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.licences.insert_one(doc)
    await audit("LICENCE", lic_id, "REGISTERED", actor)
    return doc


async def licence_status_sweep(actor: Optional[dict] = None) -> dict:
    """Compliance agent tool: expire outdated licences, warn on upcoming."""
    today = utcnow().strftime("%Y-%m-%d")
    warn_cutoff = (utcnow() + timedelta(days=60)).strftime("%Y-%m-%d")
    expired = []
    expiring = []
    async for lic in db.db.licences.find({"status": "ACTIVE"}):
        exp = lic.get("expiry_date")
        if not exp:
            continue
        if str(exp)[:10] < today:
            await db.db.licences.update_one(
                {"licence_id": lic["licence_id"]},
                {"$set": {"status": "EXPIRED", "updated_at": now_iso()}})
            expired.append(lic["licence_id"])
            await audit("LICENCE", lic["licence_id"], "EXPIRED", actor)
        elif str(exp)[:10] <= warn_cutoff:
            expiring.append({"licence_id": lic["licence_id"],
                             "expiry_date": str(exp)[:10]})
    from app.domains.vendors.service import licence_expiry_sweep

    vendor_warnings = await licence_expiry_sweep()
    return {"expired": expired, "expiring_soon": expiring,
            "vendor_warnings": vendor_warnings}


async def list_licences(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in db.db.licences.find(q).limit(200)]


async def sod_report(limit: int = 100) -> dict:
    """Detect SoD conflicts from audit history per identity."""
    pipeline = [
        {"$match": {"actor.type": {"$in": ["USER", "AGENT"]},
                    "action": {"$in": ["vendor:create", "vendor:bank_change",
                                       "po:raise", "invoice:approve",
                                       "payment:authorize", "CREATED",
                                       "APPROVED", "PAID"]}}},
        {"$group": {"_id": {"identity": "$actor.id", "type": "$actor.type",
                            "entity": "$entity_key"},
                    "actions": {"$addToSet": "$action"}}},
        {"$limit": limit},
    ]
    conflicts = []
    chain_sets = [
        {"CREATED", "APPROVED", "PAID"},
    ]
    async for row in db.db.audit_events.aggregate(pipeline):
        acts = set(row["actions"])
        for cs in chain_sets:
            overlap = acts & cs
            if len(overlap) >= 2:
                conflicts.append({
                    "identity": row["_id"]["identity"],
                    "type": row["_id"]["type"],
                    "entity": row["_id"]["entity"],
                    "conflicting_actions": sorted(overlap),
                })
                break
    return {"conflicts": conflicts, "scanned": limit}


async def regulatory_submission(payload: dict, actor: dict) -> dict:
    sub_id = await _next_id("reg_sub", "REG")
    doc = {
        "submission_id": sub_id,
        "type": payload.get("type", "PSUR"),
        "authority": payload.get("authority", "CDSCO"),
        "product_id": payload.get("product_id"),
        "due_date": payload.get("due_date"),
        "status": "PLANNED",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.regulatory_submissions.insert_one(doc)
    await audit("REGULATORY_SUBMISSION", sub_id, "PLANNED", actor)
    return doc


async def audit_query(entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                      actor_id: Optional[str] = None, limit: int = 200) -> List[dict]:
    q: Dict[str, Any] = {}
    if entity_type:
        q["entity_type"] = entity_type
    if entity_id:
        q["entity_id"] = entity_id
    if actor_id:
        q["actor.id"] = actor_id
    cur = db.db.audit_events.find(q).sort("timestamp", -1).limit(limit)
    return [_clean(dict(r)) async for r in cur]
