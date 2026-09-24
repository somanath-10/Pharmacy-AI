"""Approval Engine — the Human Decision Queue.

Everything a human must decide lands here with an evidence bundle (AI summary,
documents, policy evaluation, related entity links). One-click decisions:
APPROVED / REJECTED / HELD / CLARIFIED with mandatory reason.
"""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso, utcnow
from app.core.errors import DomainError, NotFound
from app.core.events import bus

CATEGORIES = [
    "PHYSICAL_WORK",       # tasks for warehouse/plant staff (not decisions, tracked as work)
    "CLINICAL_AUTHORITY",  # pharmacist review
    "QA_AUTHORITY",        # batch release, dispositions
    "FINANCIAL_AUTHORITY", # high-value PO, payments, credit notes
    "STRATEGIC",           # sourcing award, strategic vendor
    "SECURITY_FRAUD",      # bank change, suspicious invoice
    "REGULATORY_EXCEPTION",# licence expiry, blocked vendor
    "UNRESOLVED_EXCEPTION" # self-resolution failed
]


async def create_approval(
    category: str,
    title: str,
    entity_type: str,
    entity_id: str,
    requested_by: Dict[str, Any],
    evidence: Optional[Dict[str, Any]] = None,
    options: Optional[List[str]] = None,
    authority_roles: Optional[List[str]] = None,
    sla_hours: int = 24,
    on_approve: Optional[str] = None,   # domain callback name (see approvals_cb)
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    from datetime import timedelta

    from pymongo.errors import DuplicateKeyError

    if category not in CATEGORIES:
        raise DomainError(f"Unknown approval category {category}")
    # Dedup guard: at most one PENDING approval per (entity, category). Two
    # concurrent payment-authorizations for the same payment would otherwise
    # create duplicate queue items — and approving both could double-execute
    # the callback (e.g. two payment runs). The partial unique index on
    # {entity_type, entity_id, category, status:PENDING} makes the claim
    # atomic; on loss we return the existing PENDING approval instead of
    # creating a duplicate. Different categories on one entity stay distinct.
    existing_pending = await db.db.approvals.find_one(
        {"entity_type": entity_type, "entity_id": entity_id,
         "category": category, "status": "PENDING"}, {"approval_id": True})
    if existing_pending:
        return existing_pending["approval_id"]
    doc = {
        "approval_id": await _next_approval_id(),
        "category": category,
        "title": title,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "status": "PENDING",
        "requested_by": requested_by,
        "evidence": evidence or {},
        "options": options or ["APPROVE", "REJECT"],
        "authority_roles": authority_roles or ["MANAGEMENT"],
        "on_approve": on_approve,
        "payload": payload or {},
        "decision": None,
        "decided_by": None,
        "decided_at": None,
        "decision_reason": None,
        "due_at": (utcnow() + timedelta(hours=sla_hours)).isoformat(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        await db.db.approvals.insert_one(doc)
    except DuplicateKeyError:
        # lost the race — the concurrent winner's approval covers this entity+category
        existing = await db.db.approvals.find_one(
            {"entity_type": entity_type, "entity_id": entity_id,
             "category": category, "status": "PENDING"}, {"approval_id": True})
        if existing:
            return existing["approval_id"]
        raise  # index missing or the winner just decided; retry is safe
    await bus.publish("approval.requested",
                      {"approval_id": doc["approval_id"], "category": category,
                       "entity": f"{entity_type}:{entity_id}", "title": title},
                      requested_by)
    await audit("APPROVAL", doc["approval_id"], "REQUESTED", requested_by,
                new_state="PENDING", details={"title": title, "category": category})
    return doc["approval_id"]


async def decide(
    approval_id: str,
    decision: str,
    decided_by: Dict[str, Any],
    reason: str,
    payload: Optional[Dict[str, Any]] = None,
) -> dict:
    if decision not in {"APPROVED", "REJECTED", "HELD", "CLARIFIED",
                        "ESCALATED", "MODIFIED"}:
        raise DomainError(f"Invalid decision {decision}")
    item = await db.db.approvals.find_one({"approval_id": approval_id})
    if not item:
        raise NotFound(f"Approval {approval_id} not found")
    if item["status"] != "PENDING" and item["status"] != "HELD":
        raise DomainError(f"Approval {approval_id} already decided ({item['status']})")

    # SoD: requester cannot decide their own escalation unless SUPER_ADMIN
    req = item.get("requested_by", {})
    if (req.get("type") == decided_by.get("type") and req.get("id") == decided_by.get("id")
            and "SUPER_ADMIN" not in decided_by.get("roles", [])):
        raise DomainError("Segregation of duties: requester cannot decide own item")

    # Role authority check
    if decided_by.get("type") == "USER":
        roles = set(decided_by.get("roles", []))
        if "SUPER_ADMIN" not in roles and not (roles & set(item.get("authority_roles", []))):
            raise DomainError("Insufficient authority for this decision category")

    # Atomic decision claim: PENDING/HELD → DECIDING (or reclaim a stale
    # DECIDING left by a crashed worker). Two concurrent decides — double-click,
    # racing agents — can otherwise both pass the PENDING check and BOTH run
    # the on_approve callback (double payment run / double stock action).
    from datetime import timedelta as _claim_td

    stale_cutoff = (utcnow() - _claim_td(seconds=60)).isoformat()
    claim = await db.db.approvals.update_one(
        {"approval_id": approval_id,
         "$or": [{"status": {"$in": ["PENDING", "HELD"]}},
                 {"status": "DECIDING", "updated_at": {"$lt": stale_cutoff}}]},
        {"$set": {"status": "DECIDING", "decided_by": decided_by,
                  "decided_at": now_iso(), "decision_reason": reason,
                  "updated_at": now_iso()}})
    if claim.modified_count == 0:
        raise DomainError(
            f"Approval {approval_id} already decided or being decided by another session")

    update = {
        "status": decision,
        "decision": decision,
        "decided_by": decided_by,
        "decided_at": now_iso(),
        "decision_reason": reason,
        "updated_at": now_iso(),
    }
    if decision == "MODIFIED":
        # Human adjusted the terms (e.g. approved a lower amount). The payload
        # the eventual APPROVE consumes is amended; original is kept in history.
        update["payload"] = {**item.get("payload", {}), **(payload or {})}
        update["modification_history"] = (item.get("modification_history") or []) + [{
            "by": decided_by, "at": now_iso(), "reason": reason,
            "changes": payload or {}}]
        update["status"] = "PENDING"   # returns to the queue for final decision
        update["decision"] = None

    try:
        await db.db.approvals.update_one({"approval_id": approval_id}, {"$set": update})

        # Execute domain callback for approval-driven flows
        if decision == "APPROVED" and item.get("on_approve"):
            from app.core.approvals_callbacks import run_callback
            await run_callback(item["on_approve"], {**item.get("payload", {}),
                                                    "approval_id": approval_id},
                               decided_by)
        if decision == "REJECTED" and item.get("on_reject"):
            from app.core.approvals_callbacks import run_callback
            await run_callback(item["on_reject"], {**item.get("payload", {}),
                                                   "approval_id": approval_id},
                               decided_by)
        if decision == "ESCALATED":
            # Escalation widens authority: original authority roles + MANAGEMENT
            # + SUPER_ADMIN, SLA is refreshed. Escalation is itself audited below.
            escalated_roles = sorted(set(item.get("authority_roles", [])) |
                                     {"MANAGEMENT", "SUPER_ADMIN"})
            from datetime import timedelta as _td

            await db.db.approvals.update_one(
                {"approval_id": approval_id},
                {"$set": {"status": "PENDING",
                          "authority_roles": escalated_roles,
                          "escalated": True,
                          "escalated_by": decided_by,
                          "escalated_at": now_iso(),
                          "escalation_reason": reason,
                          "due_at": (utcnow() + _td(hours=48)).isoformat()}})
    except Exception:
        # Decision failed mid-execution (e.g. callback error): release the
        # DECIDING claim so the item returns to the queue and can be retried.
        # The status guard prevents reverting an already-applied terminal state.
        await db.db.approvals.update_one(
            {"approval_id": approval_id, "status": "DECIDING"},
            {"$set": {"status": "PENDING", "last_error": str(reason)[:300],
                      "updated_at": now_iso()}})
        raise

    await bus.publish("approval.decided",
                      {"approval_id": approval_id, "decision": decision,
                       "entity": f"{item['entity_type']}:{item['entity_id']}",
                       "reason": reason},
                      decided_by)
    await audit("APPROVAL", approval_id, decision, decided_by,
                previous_state=item["status"], new_state=decision, reason=reason,
                details={"entity": f"{item['entity_type']}:{item['entity_id']}"})
    return await db.db.approvals.find_one({"approval_id": approval_id})


async def _next_approval_id() -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": "approval"}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"APPR-{utcnow().year}-{int(doc['seq']):05d}"


async def queue_stats() -> dict:
    pipeline = [
        {"$group": {"_id": "$category",
                    "count": {"$sum": 1},
                    "pending": {"$sum": {"$cond": [{"$eq": ["$status", "PENDING"]}, 1, 0]}}}}
    ]
    rows = [r async for r in db.db.approvals.aggregate(pipeline)]
    pending = sum(r["pending"] for r in rows)
    total = sum(r["count"] for r in rows)
    return {"by_category": {r["_id"]: r for r in rows}, "pending": pending, "total": total}
