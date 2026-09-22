"""Policy Engine: deterministic business rules & thresholds.

Policies are evaluated BEFORE persistence (used by services and the agent
gateway). Rules live in `policy_rules` collection so limits can be tuned
without deploys; defaults fall back to settings.
"""
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.database import db, now_iso


async def get_rule(name: str, default: Any) -> Any:
    doc = await db.db.policy_rules.find_one({"name": name})
    if doc:
        return doc.get("value", default)
    return default


async def evaluate_po_approval(po: Dict[str, Any], actor: dict) -> Dict[str, Any]:
    """Auto-approval policy for purchase orders.

    Returns {decision: AUTO_APPROVED|NEEDS_APPROVAL, policy, reasons[], evidence}
    """
    limit = float(await get_rule("po_auto_approval_limit", settings.PO_AUTO_APPROVAL_LIMIT))
    reasons = []
    auto = True

    vendor = await db.db.vendors.find_one({"vendor_id": po.get("vendor_id")})
    VENDOR_OK_STATES = ("APPROVED", "ACTIVE")
    if not vendor or vendor.get("status") not in VENDOR_OK_STATES:
        auto = False
        reasons.append(
            f"Vendor status {vendor.get('status') if vendor else 'UNKNOWN'} "
            f"not in {VENDOR_OK_STATES}")

    if po.get("contract_id"):
        contract = await db.db.contracts.find_one({"contract_id": po.get("contract_id")})
        if not contract:
            auto = False
            reasons.append("Referenced contract missing")
        else:
            reasons.append(f"Contract price release ({contract['contract_id']})")
    else:
        reasons.append("Spot buy (no contract)")

    total = float(po.get("total_amount") or 0)
    if total > limit:
        auto = False
        reasons.append(f"Amount {total} exceeds auto-approval limit {limit}")

    if vendor and vendor.get("risk_level") == "HIGH":
        auto = False
        reasons.append("High-risk vendor requires human approval")

    policy_id = "PO_AUTO_APPROVAL_V1"
    if auto:
        reasons.append("Within policy: approved vendor, valid pricing, within limit")
    return {
        "decision": "AUTO_APPROVED" if auto else "NEEDS_APPROVAL",
        "policy": policy_id,
        "reasons": reasons,
        "evidence": {
            "vendor_status": vendor.get("status") if vendor else None,
            "vendor_risk": vendor.get("risk_level") if vendor else None,
            "amount": total,
            "limit": limit,
        },
    }


async def evaluate_pr_approval(pr: Dict[str, Any], actor: dict) -> Dict[str, Any]:
    limit = float(await get_rule("pr_auto_approval_limit", settings.PO_AUTO_APPROVAL_LIMIT))
    total = float(pr.get("estimated_amount") or 0)
    if total <= limit:
        return {
            "decision": "AUTO_APPROVED",
            "policy": "PR_AUTO_APPROVAL_V1",
            "reasons": [f"Estimated {total} within limit {limit}"],
        }
    return {
        "decision": "NEEDS_APPROVAL",
        "policy": "PR_AUTO_APPROVAL_V1",
        "reasons": [f"Estimated {total} exceeds limit {limit}"],
    }


async def evaluate_payment_authorization(payment: Dict[str, Any], actor: dict) -> Dict[str, Any]:
    limit = float(
        await get_rule("payment_authorization_limit", settings.PAYMENT_AUTHORIZATION_LIMIT)
    )
    amount = float(payment.get("amount") or 0)
    if amount <= limit:
        return {"decision": "AUTO_APPROVED", "policy": "PAYMENT_AUTHORIZATION_V1",
                "reasons": [f"{amount} within limit {limit}"]}
    return {"decision": "NEEDS_APPROVAL", "policy": "PAYMENT_AUTHORIZATION_V1",
            "reasons": [f"{amount} exceeds limit {limit}"]}


async def evaluate_quotation_discount(quotation: Dict[str, Any], actor: dict) -> Dict[str, Any]:
    max_disc = float(await get_rule("quotation_max_discount_pct", 10.0))
    disc = float(quotation.get("discount_pct") or 0)
    if disc <= max_disc:
        return {"decision": "AUTO_APPROVED", "policy": "QUOTATION_DISCOUNT_V1",
                "reasons": [f"Discount {disc}% within {max_disc}%"]}
    return {"decision": "NEEDS_APPROVAL", "policy": "QUOTATION_DISCOUNT_V1",
            "reasons": [f"Discount {disc}% exceeds {max_disc}%"]}


async def save_rule(name: str, value: Any, actor: Optional[dict] = None):
    await db.db.policy_rules.update_one(
        {"name": name},
        {"$set": {"value": value, "updated_at": now_iso(), "updated_by": actor}},
        upsert=True,
    )
