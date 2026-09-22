"""Notification Service — V1 email is SIMULATED (records only, no SMTP)."""
from typing import Any, Dict, List, Optional

from app.core.database import db, now_iso

TYPES = [
    "PO_NOTIFICATION_CREATED", "VENDOR_REMINDER_CREATED", "QUALITY_HOLD_NOTICE_CREATED",
    "PAYMENT_NOTICE_CREATED", "ORDER_CONFIRMATION_CREATED", "SHIPMENT_NOTICE_CREATED",
    "RX_STATUS_NOTICE_CREATED", "RECALL_NOTICE_CREATED", "INVOICE_EXCEPTION_CREATED",
    "APPROVAL_REQUEST_NOTICE_CREATED", "LICENCE_EXPIRY_NOTICE_CREATED",
]


async def notify(
    notif_type: str,
    subject: str,
    body: str,
    recipients: List[str],
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    if notif_type not in TYPES:
        notif_type = "APPROVAL_REQUEST_NOTICE_CREATED"
    doc = {
        "type": notif_type,
        "subject": subject,
        "body": body,
        "recipients": recipients,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "meta": meta or {},
        "status": "CREATED",
        "created_at": now_iso(),
        "sent_at": None,
    }
    res = await db.db.notifications.insert_one(doc)
    # Simulated send: no SMTP in V1
    await db.db.notifications.update_one(
        {"_id": res.inserted_id},
        {"$set": {"status": "SIMULATED_SENT", "sent_at": now_iso()}},
    )
    return str(res.inserted_id)


async def list_notifications(limit: int = 100):
    cur = db.db.notifications.find().sort("created_at", -1).limit(limit)
    return [clean(r) async for r in cur]


def clean(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc
