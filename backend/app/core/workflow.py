"""Workflow engine: state machines, guarded transitions, entity timelines.

Every transition: validate → persist entity status → append timeline node →
emit event → audit. Illegal transitions raise WorkflowError.
"""
from typing import Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import WorkflowError


class StateMachine:
    def __init__(self, name: str, transitions: Dict[str, List[str]], initial: str):
        self.name = name
        self.transitions = transitions
        self.initial = initial

    def can(self, from_state: str, to_state: str) -> bool:
        return to_state in self.transitions.get(from_state, [])

    def next_states(self, from_state: str) -> List[str]:
        return self.transitions.get(from_state, [])


# ------------------------------------------------------------------ registry
MACHINES: Dict[str, StateMachine] = {m.name: m for m in [
    StateMachine(
        "vendor",
        {
            "REGISTERED": ["UNDER_REVIEW", "RETIRED"],
            "UNDER_REVIEW": ["QA_QUALIFICATION", "COMMERCIAL_REVIEW", "APPROVED",
                             "BLOCKED"],
            "QA_QUALIFICATION": ["COMMERCIAL_REVIEW", "UNDER_REVIEW"],
            "COMMERCIAL_REVIEW": ["APPROVED", "QA_QUALIFICATION", "UNDER_REVIEW"],
            "APPROVED": ["SUSPENDED", "BLOCKED", "RETIRED"],
            "SUSPENDED": ["APPROVED", "BLOCKED", "BLACKLISTED"],
            "BLOCKED": ["APPROVED", "BLACKLISTED"],
            "BLACKLISTED": [],
            "RETIRED": [],
        },
        "REGISTERED",
    ),
    StateMachine(
        "purchase_order",
        {
            "DRAFT": ["PENDING_APPROVAL", "CANCELLED"],
            "PENDING_APPROVAL": ["APPROVED", "REJECTED", "CANCELLED"],
            "APPROVED": ["SENT", "CANCELLED"],
            "SENT": ["ACKNOWLEDGED", "CANCELLED"],
            "ACKNOWLEDGED": ["PARTIALLY_RECEIVED", "RECEIVED", "CLOSED", "CANCELLED"],
            "PARTIALLY_RECEIVED": ["RECEIVED", "CLOSED"],
            "RECEIVED": ["CLOSED"],
            "CLOSED": [],
            "REJECTED": [],
            "CANCELLED": [],
        },
        "DRAFT",
    ),
    StateMachine(
        "purchase_requisition",
        {
            "DRAFT": ["PENDING_APPROVAL", "CANCELLED"],
            "PENDING_APPROVAL": ["APPROVED", "REJECTED", "CANCELLED"],
            "APPROVED": ["CONVERTED", "CANCELLED"],
            "CONVERTED": [],
            "REJECTED": [],
            "CANCELLED": [],
        },
        "DRAFT",
    ),
    StateMachine(
        "raw_material_batch",
        {
            "EXPECTED": ["RECEIVED", "CANCELLED"],
            "RECEIVED": ["QUARANTINE"],
            "QUARANTINE": ["SAMPLING", "RELEASED", "QUALITY_HOLD"],
            "SAMPLING": ["QC_TESTING"],
            "QC_TESTING": ["QA_REVIEW", "QUALITY_HOLD"],
            "QA_REVIEW": ["RELEASED", "REJECTED", "QUALITY_HOLD"],
            "QUALITY_HOLD": ["SAMPLING", "QA_REVIEW", "REJECTED"],
            "RELEASED": [],
            "REJECTED": ["RTV", "DISPOSAL"],
            "RTV": [],
            "DISPOSAL": [],
        },
        "EXPECTED",
    ),
    StateMachine(
        "sourcing_event",
        {
            "DRAFT": ["PUBLISHED", "CANCELLED"],
            "PUBLISHED": ["BIDDING", "CANCELLED"],
            "BIDDING": ["EVALUATION", "CANCELLED"],
            "EVALUATION": ["RECOMMENDATION", "AWARDED", "CANCELLED"],
            "RECOMMENDATION": ["AWARDED", "CANCELLED"],
            "AWARDED": ["CONTRACTED", "CANCELLED"],
            "CONTRACTED": [],
            "CANCELLED": [],
        },
        "DRAFT",
    ),
    StateMachine(
        "auction",
        {
            "SCHEDULED": ["LIVE", "CANCELLED"],
            "LIVE": ["COMPLETED", "CANCELLED"],
            "COMPLETED": [],
            "CANCELLED": [],
        },
        "SCHEDULED",
    ),
    StateMachine(
        "sales_order",
        {
            "DRAFT": ["CONFIRMED", "CANCELLED"],
            "PENDING_RX": ["RX_APPROVED", "RX_REJECTED", "CANCELLED"],
            "RX_REJECTED": ["CANCELLED"],
            "RX_APPROVED": ["CONFIRMED"],
            "CONFIRMED": ["ALLOCATED", "CANCELLED"],
            "ALLOCATED": ["PICKING", "CANCELLED"],
            "PICKING": ["PACKED"],
            "PACKED": ["DISPATCHED"],
            "DISPATCHED": ["DELIVERED"],
            "DELIVERED": ["INVOICED"],
            "INVOICED": ["CLOSED"],
            "CLOSED": [],
            "CANCELLED": [],
        },
        "DRAFT",
    ),
    StateMachine(
        "prescription",
        {
            "UPLOADED": ["EXTRACTED", "REJECTED"],
            "EXTRACTED": ["VALIDATED", "PHARMACIST_REVIEW", "REJECTED"],
            "VALIDATED": ["PHARMACIST_REVIEW", "REJECTED"],
            "PHARMACIST_REVIEW": ["APPROVED", "REJECTED", "CLARIFICATION"],
            "CLARIFICATION": ["PHARMACIST_REVIEW", "REJECTED"],
            "APPROVED": ["DISPENSED"],
            "REJECTED": [],
            "DISPENSED": [],
        },
        "UPLOADED",
    ),
    StateMachine(
        "production_order",
        {
            "PLANNED": ["RELEASED", "CANCELLED"],
            "RELEASED": ["DISPENSING", "ON_HOLD", "CANCELLED"],
            "DISPENSING": ["IN_PROCESS", "ON_HOLD"],
            "IN_PROCESS": ["PACKAGING", "ON_HOLD"],
            "PACKAGING": ["COMPLETED", "ON_HOLD"],
            "COMPLETED": ["FG_QUARANTINE"],
            "FG_QUARANTINE": ["QC_COMPLETE", "ON_HOLD"],
            "QC_COMPLETE": ["QA_REVIEW"],
            "QA_REVIEW": ["BATCH_RELEASED", "REJECTED", "ON_HOLD"],
            "BATCH_RELEASED": [],
            "REJECTED": [],
            "ON_HOLD": ["DISPENSING", "IN_PROCESS", "PACKAGING", "CANCELLED"],
            "CANCELLED": [],
        },
        "PLANNED",
    ),
    StateMachine(
        "qc_sample",
        {
            "REGISTERED": ["SAMPLING", "CANCELLED"],
            "SAMPLING": ["TESTING"],
            "TESTING": ["RESULTS_ENTERED"],
            "RESULTS_ENTERED": ["REVIEW", "OOS_INVESTIGATION"],
            "OOS_INVESTIGATION": ["REVIEW"],
            "REVIEW": ["COMPLETED"],
            "COMPLETED": [],
            "CANCELLED": [],
        },
        "REGISTERED",
    ),
    StateMachine(
        "grn",
        {
            "DRAFT": ["POSTED", "CANCELLED"],
            "POSTED": ["QC_PENDING", "PUTAWAY_DONE"],
            "QC_PENDING": ["QC_PASSED", "QC_PARTIAL", "QC_FAILED"],
            "QC_PASSED": ["PUTAWAY_DONE"],
            "QC_PARTIAL": ["PUTAWAY_DONE"],
            "QC_FAILED": ["RTV", "DISPOSAL", "CLOSED"],
            "PUTAWAY_DONE": ["CLOSED"],
            "RTV": [],
            "DISPOSAL": [],
            "CLOSED": [],
            "CANCELLED": [],
        },
        "DRAFT",
    ),
    StateMachine(
        "supplier_invoice",
        {
            "RECEIVED": ["EXTRACTED", "DISPUTED", "CANCELLED"],
            "EXTRACTED": ["IN_MATCHING"],
            "IN_MATCHING": ["MATCHED", "MATCH_FAILED"],
            "MATCH_FAILED": ["IN_MATCHING", "DISPUTED", "WRITTEN_OFF"],
            "DISPUTED": ["IN_MATCHING", "WRITTEN_OFF", "CANCELLED"],
            "MATCHED": ["APPROVED"],
            "APPROVED": ["SCHEDULED"],
            "SCHEDULED": ["PAID"],
            "PAID": [],
            "WRITTEN_OFF": [],
            "CANCELLED": [],
        },
        "RECEIVED",
    ),
    StateMachine(
        "customer_invoice",
        {
            "DRAFT": ["ISSUED", "CANCELLED"],
            "ISSUED": ["PARTIALLY_PAID", "PAID", "CREDIT_NOTE_ISSUED"],
            "PARTIALLY_PAID": ["PAID", "CREDIT_NOTE_ISSUED"],
            "PAID": [],
            "CREDIT_NOTE_ISSUED": ["WRITTEN_OFF"],
            "WRITTEN_OFF": [],
            "CANCELLED": [],
        },
        "DRAFT",
    ),
    StateMachine(
        "payment",
        {
            "PROPOSED": ["AUTHORIZED", "REJECTED"],
            "AUTHORIZED": ["PAID", "FAILED"],
            "PAID": [],
            "FAILED": ["AUTHORIZED"],
            "REJECTED": [],
        },
        "PROPOSED",
    ),
    StateMachine(
        "shipment",
        {
            "PLANNED": ["LOADING", "CANCELLED"],
            "LOADING": ["DISPATCHED", "EXCEPTION", "CANCELLED"],
            "DISPATCHED": ["IN_TRANSIT", "EXCEPTION"],
            "IN_TRANSIT": ["DELIVERED", "EXCEPTION"],
            "DELIVERED": ["CLOSED"],
            "CLOSED": [],
            "EXCEPTION": ["PLANNED", "IN_TRANSIT", "CANCELLED"],
            "CANCELLED": [],
        },
        "PLANNED",
    ),
    StateMachine(
        "return_request",
        {
            "REQUESTED": ["RMA_APPROVED", "REJECTED"],
            "RMA_APPROVED": ["PICKUP_SCHEDULED"],
            "PICKUP_SCHEDULED": ["PICKED"],
            "PICKED": ["RECEIVED"],
            "RECEIVED": ["RETURN_QUARANTINE"],
            "RETURN_QUARANTINE": ["INSPECTED"],
            "INSPECTED": ["DISPOSITIONED"],
            "DISPOSITIONED": ["RESTOCKED", "RTV", "DESTROYED", "QUALITY_HOLD", "CLOSED"],
            "RESTOCKED": ["CLOSED"],
            "RTV": ["CLOSED"],
            "DESTROYED": ["CLOSED"],
            "QUALITY_HOLD": ["CLOSED"],
            "CLOSED": [],
            "REJECTED": [],
        },
        "REQUESTED",
    ),
    StateMachine(
        "recall",
        {
            "NOTIFIED": ["IN_PROGRESS", "CANCELLED"],
            "IN_PROGRESS": ["CONTAINED"],
            "CONTAINED": ["RECONCILED"],
            "RECONCILED": ["CLOSED"],
            "CLOSED": [],
            "CANCELLED": [],
        },
        "NOTIFIED",
    ),
    StateMachine(
        "deviation",
        {
            "OPEN": ["INVESTIGATION", "CLOSED"],
            "INVESTIGATION": ["APPROVED", "CLOSED"],
            "APPROVED": ["CLOSED"],
            "CLOSED": [],
        },
        "OPEN",
    ),
    StateMachine(
        "capa",
        {
            "IDENTIFIED": ["PLANNED", "CANCELLED"],
            "PLANNED": ["IMPLEMENTATION", "CANCELLED"],
            "IMPLEMENTATION": ["VERIFICATION"],
            "VERIFICATION": ["CLOSED"],
            "CLOSED": [],
            "CANCELLED": [],
        },
        "IDENTIFIED",
    ),
    StateMachine(
        "safety_case",
        {
            "NEW": ["TRIAGE"],
            "TRIAGE": ["DUPLICATE_CHECK"],
            "DUPLICATE_CHECK": ["MEDICAL_REVIEW"],
            "MEDICAL_REVIEW": ["FOLLOW_UP", "REPORTABLE", "CLOSED"],
            "FOLLOW_UP": ["REPORTABLE", "MEDICAL_REVIEW"],
            "REPORTABLE": ["CLOSED"],
            "CLOSED": [],
        },
        "NEW",
    ),
    StateMachine(
        "approval",
        {
            "PENDING": ["APPROVED", "REJECTED", "HELD", "CLARIFIED", "EXPIRED"],
            "HELD": ["PENDING", "APPROVED", "REJECTED"],
            "APPROVED": [],
            "REJECTED": [],
            "CLARIFIED": [],
            "EXPIRED": [],
        },
        "PENDING",
    ),
]}


def get_machine(name: str) -> StateMachine:
    if name not in MACHINES:
        raise WorkflowError(f"Unknown state machine: {name}")
    return MACHINES[name]


async def transition(
    entity_type: str,
    entity_id: str,
    collection: str,
    id_field: str,
    to_state: str,
    actor: Optional[dict] = None,
    reason: Optional[str] = None,
    extra_update: Optional[dict] = None,
    event_name: Optional[str] = None,
    event_payload: Optional[dict] = None,
) -> dict:
    """Validate + apply a state transition with timeline, event and audit."""
    machine = get_machine(entity_type)
    entity = await db.db[collection].find_one({id_field: entity_id})
    if not entity:
        raise WorkflowError(f"{entity_type} {entity_id} not found")
    from_state = entity.get("status")
    if not machine.can(from_state, to_state):
        raise WorkflowError(
            f"Invalid transition {entity_type} {entity_id}: {from_state} → {to_state} "
            f"(allowed: {machine.next_states(from_state)})"
        )
    update = {
        "$set": {"status": to_state, "updated_at": now_iso()},
        "$inc": {"version": 1},
        "$push": {
            "timeline": {
                "state": to_state,
                "previous_state": from_state,
                "actor": actor or {"type": "SYSTEM", "id": "platform"},
                "reason": reason,
                "at": now_iso(),
            }
        },
    }
    if extra_update:
        for k, v in extra_update.get("$set", {}).items():
            update["$set"][k] = v
        for k, v in extra_update.get("$inc", {}).items():
            update["$inc"][k] = update["$inc"].get(k, 0) + v
    await db.db[collection].update_one({id_field: entity_id}, update)
    await bus_publish(event_name or f"{entity_type}.{to_state.lower()}",
                      event_payload or {"entity_type": entity_type, "id": entity_id,
                                        "from": from_state, "to": to_state},
                      actor)
    await audit(
        entity_type=entity_type, entity_id=entity_id, action=to_state,
        actor=actor, previous_state=from_state, new_state=to_state, reason=reason,
    )
    return await db.db[collection].find_one({id_field: entity_id})


async def bus_publish(name: str, payload: dict, actor: Optional[dict] = None):
    from app.core.events import bus

    await bus.publish(name, payload, actor)


async def get_timeline(entity_type: str, entity_id: str) -> dict:
    entity = await db.db.workflows.find_one(
        {"entity_type": entity_type, "entity_id": entity_id}
    )
    if entity:
        return entity
    return {"entity_type": entity_type, "entity_id": entity_id, "nodes": []}


async def record_node(
    entity_type: str,
    entity_id: str,
    node: str,
    label: str,
    status: str,
    actor: Optional[dict] = None,
    detail: Optional[str] = None,
    documents: Optional[List[str]] = None,
):
    """Append/replace a node in the canonical workflow viewer timeline."""
    await db.db.workflows.update_one(
        {"entity_type": entity_type, "entity_id": entity_id},
        {
            "$set": {"updated_at": now_iso()},
            "$push": {
                "nodes": {
                    "node": node,
                    "label": label,
                    "status": status,
                    "actor": actor or {"type": "SYSTEM", "id": "platform"},
                    "detail": detail,
                    "documents": documents or [],
                    "at": now_iso(),
                }
            },
        },
        upsert=True,
    )
