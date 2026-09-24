"""Append-only audit engine. Regulated semantics: never update/delete.

Every important transition records: actor (human/agent/system), previous/new
state, reason, policy, related entity and a correlation ID for tracing one
business thread across services/events (Part 8).
"""
import uuid
import contextvars
from typing import Any, Dict, Optional

from app.core.database import db, now_iso

# Correlation ID contextvar: set per request/task to link audit+events.
correlation_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None)


def new_correlation_id() -> str:
    cid = str(uuid.uuid4())
    correlation_id_ctx.set(cid)
    return cid


def current_correlation_id() -> Optional[str]:
    return correlation_id_ctx.get()


def actor_is_human(actor: Optional[dict]) -> bool:
    if not actor:
        return False
    return actor.get("type") == "USER"


async def audit(
    entity_type: str,
    entity_id: str,
    action: str,
    actor: Dict[str, Any] | None = None,
    previous_state: Optional[str] = None,
    new_state: Optional[str] = None,
    policy: Optional[str] = None,
    reason: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
):
    act = actor or {"type": "SYSTEM", "id": "platform"}
    doc = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_key": f"{entity_type}:{entity_id}",
        "action": action,
        "actor": act,
        "actor_is_human": actor_is_human(act),
        "previous_state": previous_state,
        "new_state": new_state,
        "policy": policy,
        "reason": reason,
        "details": details or {},
        "correlation_id": current_correlation_id() or str(uuid.uuid4()),
        "timestamp": now_iso(),
    }
    await db.db.audit_events.insert_one(doc)
    return doc
