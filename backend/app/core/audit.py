"""Append-only audit engine. Regulated semantics: never update/delete."""
from typing import Any, Dict, Optional

from app.core.database import db, now_iso


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
    doc = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_key": f"{entity_type}:{entity_id}",
        "action": action,
        "actor": actor or {"type": "SYSTEM", "id": "platform"},
        "previous_state": previous_state,
        "new_state": new_state,
        "policy": policy,
        "reason": reason,
        "details": details or {},
        "timestamp": now_iso(),
    }
    await db.db.audit_events.insert_one(doc)
    return doc
