"""RBAC permission catalog + Segregation-of-Duties engine.

Permissions are `resource:action` strings. SoD chains prevent one identity from
performing conflicting critical actions (vendors/bank/PO/invoice/payment).
Agents are subjects too — an agent identity can never approve what it created.
"""
from typing import Dict, List, Optional, Set

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
    "PLANNING": {
        "plan:write", "plan:read", "forecast:write", "forecast:read",
        "pr:write", "pr:read", "inventory:read", "dashboard:read",
        "proposal:read", "proposal:accept",
    },
    "SUPPLIER": {
        "portal:read", "rfq:bid", "po:ack", "asn:write", "invoice:submit",
        "contract:read", "po:read", "payment:status:read", "dispute:write",
    },
    "CUSTOMER": {"portal:read", "order:read", "invoice:read"},
}

# SoD: identity cannot hold >1 action from the same chain on the same entity.
# Actions are matched against audit_events `action` values for the entity key.
SOD_CHAINS: List[dict] = [
    {
        "chain": "vendor_bank_payment",
        "actions": ["vendor:create", "vendor:bank_change", "po:raise", "invoice:approve", "payment:authorize"],
    },
    {
        "chain": "vendor_creation_vs_approval",
        "actions": ["REGISTERED", "APPROVED"],  # vendor entity: creator vs approver
    },
    {
        "chain": "po_creation_vs_approval",
        "actions": ["CREATED", "APPROVED"],      # purchase_order entity
    },
    {
        "chain": "invoice_vs_payment",
        "actions": ["REGISTERED", "AUTHORIZED"],  # supplier_invoice / payment entity
    },
    {
        "chain": "qc_vs_qa_release",
        "actions": ["RESULTS_ENTERED", "BATCH_RELEASED"],  # production_order entity
    },
]

# ---------------------------------------------------------------- read matrix
# Who may READ which record kind (reference: departmentReadKinds). Absence from
# a kind's set means no read grant. SUPER_ADMIN implicitly reads everything.
READ_KINDS: Dict[str, Set[str]] = {
    "vendor": {"PROCUREMENT", "BUYER", "VENDOR_MANAGER", "FINANCE", "QA", "QC",
               "WAREHOUSE", "LOGISTICS", "MANAGEMENT", "COMPLIANCE", "AUDITOR",
               "SUPPLIER"},
    "purchase_order": {"PROCUREMENT", "BUYER", "FINANCE", "WAREHOUSE", "LOGISTICS",
                       "MANAGEMENT", "QA", "AUDITOR", "SUPPLIER"},
    "purchase_requisition": {"PROCUREMENT", "BUYER", "MANAGEMENT", "AUDITOR",
                             "PLANT", "WAREHOUSE"},
    "grn": {"WAREHOUSE", "QC", "QA", "FINANCE", "PROCUREMENT", "LOGISTICS",
            "MANAGEMENT", "AUDITOR", "SUPPLIER"},
    "invoice": {"FINANCE", "MANAGEMENT", "AUDITOR", "PROCUREMENT", "SALES",
                "SUPPLIER", "CUSTOMER"},
    "inventory": {"WAREHOUSE", "QC", "QA", "FINANCE", "PROCUREMENT", "LOGISTICS",
                  "MANAGEMENT", "SALES", "PLANT", "AUDITOR"},
    "payment": {"FINANCE", "MANAGEMENT", "AUDITOR", "SUPPLIER"},
    "gl": {"FINANCE", "MANAGEMENT", "AUDITOR"},
    "sales_order": {"SALES", "FINANCE", "WAREHOUSE", "LOGISTICS", "MANAGEMENT",
                    "AUDITOR", "CUSTOMER"},
    "production": {"PLANT", "QA", "QC", "MANAGEMENT", "AUDITOR"},
    "qc": {"QC", "QA", "PLANT", "MANAGEMENT", "AUDITOR"},
    "recall": {"QA", "COMPLIANCE", "WAREHOUSE", "LOGISTICS", "MANAGEMENT",
               "SALES", "AUDITOR"},
    "audit": {"COMPLIANCE", "AUDITOR", "MANAGEMENT", "SUPER_ADMIN"} | set(),
    "document": {"QA", "QC", "FINANCE", "PROCUREMENT", "WAREHOUSE", "SALES",
                 "LOGISTICS", "COMPLIANCE", "MANAGEMENT", "AUDITOR", "PLANT",
                 "SUPPLIER", "CUSTOMER"},
    "user": {"SUPER_ADMIN"},
}


def can_read(roles: List[str], kind: str) -> bool:
    if "SUPER_ADMIN" in roles:
        return True
    allowed = READ_KINDS.get(kind)
    if allowed is None:
        # DENY BY DEFAULT (P0 4.2): an unmapped resource kind must never be
        # implicitly readable. Every new read surface needs an explicit policy.
        return False
    return bool(allowed & set(roles))


def require_read(kind: str):
    """FastAPI dependency: principal must hold the read grant for `kind`."""
    from fastapi import Depends, HTTPException
    from app.core.security import get_current_principal

    async def _dep(principal: dict = Depends(get_current_principal)) -> dict:
        if not can_read(principal.get("roles", []), kind):
            raise HTTPException(403, f"No read grant for {kind}")
        return principal

    return _dep


# ------------------------------------------------------------- command matrix
# Who may EXECUTE which sensitive command (reference: commandRoles).
# agent_ok: an AGENT principal may execute it autonomously; everything else
# requires a human with the role (management/admin visibility ≠ authority).
COMMAND_ROLES: Dict[str, dict] = {
    "grn:create": {"roles": {"WAREHOUSE", "SUPER_ADMIN"}, "agent_ok": True},
    "putaway:execute": {"roles": {"WAREHOUSE", "SUPER_ADMIN"}, "agent_ok": True},
    "inventory:adjust": {"roles": {"WAREHOUSE", "SUPER_ADMIN"}, "agent_ok": False},
    "pr:approve": {"roles": {"MANAGEMENT", "SUPER_ADMIN"}, "agent_ok": False},
    "po:approve": {"roles": {"MANAGEMENT", "PROCUREMENT", "SUPER_ADMIN"},
                   "agent_ok": False},
    "vendor:approve": {"roles": {"MANAGEMENT", "VENDOR_MANAGER", "SUPER_ADMIN"},
                       "agent_ok": False},
    "invoice:match_accept": {"roles": {"FINANCE", "SUPER_ADMIN"}, "agent_ok": False},
    "payment:authorize": {"roles": {"FINANCE", "SUPER_ADMIN"}, "agent_ok": False},
    "qa:release": {"roles": {"QA", "SUPER_ADMIN"}, "agent_ok": False},
    "qa:hold": {"roles": {"QA", "SUPER_ADMIN"}, "agent_ok": False},
    "equipment:use": {"roles": {"QC", "PLANT", "SUPER_ADMIN"}, "agent_ok": True},
    "rx:approve": {"roles": {"PHARMACIST", "SUPER_ADMIN"}, "agent_ok": False},
    "dispense:write": {"roles": {"PHARMACIST", "SUPER_ADMIN"}, "agent_ok": False},
    "recall:create": {"roles": {"QA", "COMPLIANCE", "SUPER_ADMIN"}, "agent_ok": True},
    "recall:close": {"roles": {"QA", "COMPLIANCE", "SUPER_ADMIN"}, "agent_ok": False},
    "return:restock": {"roles": {"QA", "SUPER_ADMIN"}, "agent_ok": False},
    "user:manage": {"roles": {"SUPER_ADMIN"}, "agent_ok": False},
}


def authorize_command(principal: dict, command: str):
    """Raise PermissionDenied unless the principal may execute `command`."""
    from app.core.errors import PermissionDenied

    rule = COMMAND_ROLES.get(command)
    if rule is None:
        return
    if principal.get("type") == "AGENT":
        if rule.get("agent_ok"):
            return
        raise PermissionDenied(
            f"Agent identities may not execute {command}; requires human authority")
    roles = set(principal.get("roles", []))
    if "SUPER_ADMIN" in roles or (roles & rule["roles"]):
        return
    raise PermissionDenied(
        f"Command {command} requires one of {sorted(rule['roles'])}")


def require_command(command: str):
    """FastAPI dependency form of authorize_command for sensitive commands."""
    from fastapi import Depends
    from app.core.security import get_current_principal

    async def _dep(principal: dict = Depends(get_current_principal)) -> dict:
        authorize_command(principal, command)
        return principal

    return _dep


# --------------------------------------------------------------- site scoping
def site_scope(principal: dict):
    """Site ids this principal may see, or None for unrestricted access."""
    if "SUPER_ADMIN" in principal.get("roles", []):
        return None
    return principal.get("site_ids") or None


def assert_site(principal: dict, site_id: Optional[str]):
    from app.core.errors import PermissionDenied

    scope = site_scope(principal)
    if scope is not None and site_id and site_id not in scope:
        raise PermissionDenied(f"Site {site_id} outside your access scope")


async def warehouses_in_scope(site_ids) -> List[str]:
    """Warehouse codes visible for a site scope (empty list ⇒ nothing)."""
    from app.core.database import db

    if site_ids is None:
        return []  # caller treats None-scope differently; not used directly
    rows = db.db.warehouses.find({"site_id": {"$in": list(site_ids)}})
    return [r["code"] async for r in rows]


def assert_party(principal: dict, vendor_id: Optional[str] = None,
                 customer_id: Optional[str] = None):
    """External-party isolation: supplier/customer identities only see their own."""
    from app.core.errors import PermissionDenied

    roles = set(principal.get("roles", []))
    if "SUPER_ADMIN" in roles:
        return
    if vendor_id is not None and "SUPPLIER" in roles:
        if principal.get("vendor_id") != vendor_id:
            raise PermissionDenied("Vendor record outside your party scope")
    if customer_id is not None and "CUSTOMER" in roles:
        if principal.get("customer_id") != customer_id:
            raise PermissionDenied("Customer record outside your party scope")


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


async def enforce_sod(actor: dict, action: str, entity_type: str, entity_id: str):
    """Raise SoDError when `actor` already performed a conflicting action on
    this exact entity (Part 1 segregation of duties). SUPER_ADMIN exempt.

    Applies to humans AND agent identities alike (an agent must not approve
    what it created); agent *authority* is separately governed by the
    COMMAND_ROLES matrix (agent_ok). Internal pseudo-actors (policy-engine,
    system flows) pass because they never performed the conflicting action.
    """
    from app.core.errors import SoDError

    identity = actor.get("id")
    if not identity or "SUPER_ADMIN" in actor.get("roles", []):
        return
    entity_key = f"{entity_type}:{entity_id}"
    if await sod_violation(identity, action, entity_key):
        raise SoDError(
            f"Segregation of duties: {identity} already acted on {entity_key}; "
            f"cannot also perform {action}")


def require_permission(permission: str):
    from fastapi import Depends, HTTPException
    from app.core.security import get_current_principal

    async def _dep(principal: dict = Depends(get_current_principal)) -> dict:
        if not has_permission(principal["roles"], permission):
            raise HTTPException(403, f"Missing permission {permission}")
        return principal

    return _dep
