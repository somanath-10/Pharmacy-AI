"""RBAC permission catalog + Segregation-of-Duties engine.

Permissions are `resource:action` strings. SoD chains prevent one identity from
performing conflicting critical actions (vendors/bank/PO/invoice/payment).
Agents are subjects too — an agent identity can never approve what it created.
"""
from typing import Dict, List, Set

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "SUPER_ADMIN": {"*"},
    "MANAGEMENT": {"dashboard:read", "approvals:decide", "reports:read", "audit:read"},
    "PROCUREMENT": {
        "pr:write", "pr:read", "po:write", "po:read", "po:send", "rfq:write",
        "vendor:read", "contract:read", "sourcing:write", "dashboard:read",
    },
    "BUYER": {"pr:write", "pr:read", "po:write", "po:read", "vendor:read", "contract:read"},
    "VENDOR_MANAGER": {
        "vendor:write", "vendor:read", "vendor:approve", "vendor:document:write",
        "vendor:performance:read", "dashboard:read",
    },
    "WAREHOUSE": {
        "grn:write", "grn:read", "putaway:write", "pick:write", "pack:write",
        "inventory:read", "transfer:write", "cycle_count:write", "asn:read",
        "gate:write", "dashboard:read", "dispatch:write",
    },
    "QA": {
        "qa:write", "qa:read", "qa:release", "deviation:write", "capa:write",
        "hold:write", "recall:write", "recall:close", "spec:write", "audit:read",
        "dashboard:read", "complaint:write",
    },
    "QC": {
        "qc:write", "qc:read", "sample:write", "result:write", "oos:write",
        "coa:write", "spec:read", "dashboard:read",
    },
    "PLANT": {
        "production:write", "production:read", "ebmr:write", "equipment:write",
        "line_clearance:write", "material_issue:write", "dashboard:read",
    },
    "PHARMACIST": {
        "rx:review", "rx:read", "dispense:write", "dispense:read",
        "inventory:read", "dashboard:read",
    },
    "SALES": {
        "lead:write", "lead:read", "opportunity:write", "quotation:write",
        "quotation:send", "order:write", "order:read", "customer:write",
        "customer:read", "pos:write", "dashboard:read",
    },
    "FINANCE": {
        "invoice:write", "invoice:read", "match:write", "payment:write",
        "payment:authorize", "reconcile:write", "gl:read", "credit_note:write",
        "budget:read", "audit:read", "dashboard:read",
    },
    "LOGISTICS": {
        "shipment:write", "shipment:read", "carrier:write", "dispatch:write",
        "pod:write", "inbound:write", "dashboard:read",
    },
    "COMPLIANCE": {
        "licence:write", "licence:read", "policy:write", "audit:read",
        "sod:read", "recall:write", "dashboard:read",
    },
    "AUDITOR": {"audit:read", "reports:read", "dashboard:read"},
    "SUPPLIER": {
        "portal:read", "rfq:bid", "po:ack", "asn:write", "invoice:submit",
        "contract:read", "po:read", "payment:status:read",
    },
    "CUSTOMER": {"portal:read", "order:read", "invoice:read"},
}

# SoD: identity cannot hold >1 action from the same chain on the same entity.
SOD_CHAINS: List[dict] = [
    {
        "chain": "vendor_bank_payment",
        "actions": ["vendor:create", "vendor:bank_change", "po:raise", "invoice:approve", "payment:authorize"],
    },
]

# Roles trusted for QA authority and financial authority (used by approvals engine)
QA_AUTHORITY_ROLES = {"QA", "SUPER_ADMIN"}
FINANCE_AUTHORITY_ROLES = {"FINANCE", "SUPER_ADMIN"}
PHARMACIST_AUTHORITY_ROLES = {"PHARMACIST", "SUPER_ADMIN"}
MANAGEMENT_AUTHORITY_ROLES = {"MANAGEMENT", "SUPER_ADMIN"}


def permissions_for(roles: List[str]) -> Set[str]:
    out: Set[str] = set()
    for r in roles:
        out |= ROLE_PERMISSIONS.get(r, set())
    return out


def has_permission(roles: List[str], permission: str) -> bool:
    perms = permissions_for(roles)
    return "*" in perms or permission in perms


async def sod_violation(identity: str, action: str, entity_key: str) -> bool:
    """Async SoD check against audit_events."""
    from app.core.database import db

    chain = next((c for c in SOD_CHAINS if action in c["actions"]), None)
    if not chain:
        return False
    conflicts = [a for a in chain["actions"] if a != action]
    doc = await db.db.audit_events.find_one(
        {"entity_key": entity_key, "actor.id": identity, "action": {"$in": conflicts}}
    )
    return doc is not None


def require_permission(permission: str):
    from fastapi import Depends, HTTPException
    from app.core.security import get_current_principal

    async def _dep(principal: dict = Depends(get_current_principal)) -> dict:
        if not has_permission(principal["roles"], permission):
            raise HTTPException(403, f"Missing permission {permission}")
        return principal

    return _dep
