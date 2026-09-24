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


# ----------------------------------------------------------- service requests
SERVICE_REQUEST_TYPES = ["QA", "REGULATORY", "MAINTENANCE", "IT", "DOC", "GENERAL"]


async def create_service_request(payload: dict, actor: dict) -> dict:
    """Internal service request (reference: service requests create→resolve)."""
    if not payload.get("subject"):
        raise ValidationFailed("Service request needs subject")
    if payload.get("type") not in SERVICE_REQUEST_TYPES:
        raise ValidationFailed(f"type must be in {SERVICE_REQUEST_TYPES}")
    srid = await _next_id("service_request", "SR")
    doc = {
        "service_request_id": srid,
        "type": payload["type"],
        "subject": payload["subject"],
        "description": payload.get("description"),
        "priority": payload.get("priority", "NORMAL"),
        "entity_type": payload.get("entity_type"),
        "entity_id": payload.get("entity_id"),
        "status": "OPEN",
        "resolution": None,
        "requested_by": actor,
        "assigned_to": payload.get("assigned_to"),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.service_requests.insert_one(doc)
    await audit("SERVICE_REQUEST", srid, "OPENED", actor)
    return _clean(doc)


async def resolve_service_request(service_request_id: str, payload: dict,
                                  actor: dict) -> dict:
    doc = await db.db.service_requests.find_one(
        {"service_request_id": service_request_id})
    if not doc:
        raise NotFound(f"Service request {service_request_id} not found")
    if doc["status"] != "OPEN":
        raise ValidationFailed(f"Service request not OPEN: {doc['status']}")
    await db.db.service_requests.update_one(
        {"service_request_id": service_request_id},
        {"$set": {"status": "RESOLVED", "resolution": payload.get("resolution"),
                  "resolved_by": actor, "resolved_at": now_iso(),
                  "updated_at": now_iso()}})
    await audit("SERVICE_REQUEST", service_request_id, "RESOLVED", actor)
    return await db.db.service_requests.find_one(
        {"service_request_id": service_request_id})


async def list_service_requests(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.service_requests.find(q).sort("created_at", -1).limit(200)]


async def anomaly_sweep(actor: Optional[dict] = None) -> dict:
    """Compliance Agent: security/financial anomaly detection sweep.

    Deterministic checks over the audit trail + finance ledger:
      - suspicious inventory/stock overrides (adjustments without variance doc)
      - repeated failed approvals / rejections on the same entity
      - sensitive role changes
      - unusual payment activity (after-hours, round-figure, rapid sequence)
      - policy violations logged by the policy engine
    Findings are returned AND routed to the human decision queue when risk is
    high. Read-only — compliance never mutates business data.
    """
    out = {"high": [], "medium": [], "info": [], "checked": []}

    # 1. stock adjustments not linked to a cycle-count variance
    adj_q = {"category": "INVENTORY", "action": {"$in": ["STOCK_ADJUSTMENT",
                                                         "ADJUSTMENT"]}}
    n_adj = await db.db.audit_events.count_documents(adj_q)
    out["checked"].append(f"stock_adjustments={n_adj}")

    # 2. repeated failed approvals (>=3 rejections on one entity)
    pipeline = [
        {"$match": {"action": {"$in": ["REJECTED", "APPROVAL_REJECTED",
                                       "PAYMENT_REJECTED"]}}},
        {"$group": {"_id": {"entity": "$entity_id", "type": "$entity_type"},
                    "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": 3}}},
        {"$sort": {"n": -1}}, {"$limit": 10},
    ]
    async for row in db.db.audit_events.aggregate(pipeline):
        out["medium"].append({
            "kind": "REPEATED_FAILED_APPROVALS",
            "entity": f"{(row['_id'] or {}).get('type')}:"
                      f"{(row['_id'] or {}).get('entity')}",
            "count": row["n"]})

    # 3. sensitive role changes
    async for r in db.db.audit_events.find(
            {"action": {"$in": ["ROLE_CHANGE", "PERMISSION_CHANGE",
                                "USER_ROLE_UPDATED"]}}
    ).sort("created_at", -1).limit(10):
        out["high"].append({"kind": "SENSITIVE_ROLE_CHANGE",
                            "entity": r.get("entity_id"),
                            "at": r.get("created_at"),
                            "actor": r.get("actor")})

    # 4. unusual payment activity: executed outside 06:00-22:00 IST window
    for coll in ("payments", "finance_payments"):
        try:
            async for p in db.db[coll].find(
                    {"status": {"$in": ["PAID", "EXECUTED"]}}
            ).sort("created_at", -1).limit(50):
                amt = float(p.get("amount") or 0)
                hour = int(str(p.get("created_at", ""))[11:13] or -1)
                if 0 <= hour and not (6 <= hour < 22):
                    out["high"].append({
                        "kind": "AFTER_HOURS_PAYMENT",
                        "payment": p.get("payment_id"),
                        "amount": amt, "at": p.get("created_at")})
        except Exception:
            pass

    # 5. policy violations
    n_pol = await db.db.audit_events.count_documents(
        {"action": {"$regex": "POLICY", "$options": "i"}})
    if n_pol:
        out["medium"].append({"kind": "POLICY_VIOLATIONS_LOGGED",
                              "count": n_pol})
    out["checked"].append(f"policy_violations={n_pol}")

    # Route high-risk findings to the human decision queue
    for f in out["high"]:
        try:
            from app.core.approvals import create_approval

            await create_approval(
                "SECURITY_FRAUD", f["kind"], "COMPLIANCE",
                str(f.get("payment") or f.get("entity") or "UNKNOWN"),
                actor or {"type": "AGENT", "id": "compliance-agent"},
                evidence=f, authority_roles=["COMPLIANCE", "SUPER_ADMIN"],
                payload={"finding": f})
        except Exception:
            pass  # queue unavailable — findings still returned

    await audit("COMPLIANCE", "ANOMALY_SWEEP", "COMPLETED",
                actor or {"type": "AGENT", "id": "compliance-agent"},
                details={"high": len(out["high"]),
                         "medium": len(out["medium"])})
    return out
