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

    Auto-approve only when: approved vendor, valid licence, within budget,
    price within tolerance, quantity within tolerance, no policy violation,
    value under the configured threshold. Everything else routes to humans.
    """
    limit = float(await get_rule("po_auto_approval_limit", settings.PO_AUTO_APPROVAL_LIMIT))
    price_tol = float(await get_rule("po_price_tolerance_pct", 2.0))
    qty_tol = float(await get_rule("po_quantity_tolerance_pct", 5.0))
    _ = float(await get_rule("demand_anomaly_factor",
                                          settings.DEMAND_ANOMALY_FACTOR))
    reasons = []
    auto = True

    vendor = await db.db.vendors.find_one({"vendor_id": po.get("vendor_id")})
    VENDOR_OK_STATES = ("APPROVED", "ACTIVE")
    if not vendor or vendor.get("status") not in VENDOR_OK_STATES:
        auto = False
        reasons.append(
            f"Vendor status {vendor.get('status') if vendor else 'UNKNOWN'} "
            f"not in {VENDOR_OK_STATES}")
    if vendor and vendor.get("strategic"):
        auto = False
        reasons.append("Strategic supplier requires management approval")
    if vendor and not _licence_valid(vendor):
        auto = False
        reasons.append("Vendor drug licence missing/expired")

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

    # price deviation vs contract/master price (major deviation → human)
    for line in po.get("lines", []):
        ref = await _reference_price(po, line)
        if ref and float(line.get("unit_price") or 0) > 0:
            dev = 100.0 * (float(line["unit_price"]) - float(ref)) / float(ref)
            if abs(dev) > price_tol:
                auto = False
                reasons.append(
                    f"Line {line.get('line_no')} price deviates {round(dev, 2)}% "
                    f"from reference {ref} (tolerance {price_tol}%)")
                break

    # abnormal quantity vs linked PR lines (quantity tolerance)
    if po.get("pr_id"):
        pr = await db.db.purchase_requisitions.find_one({"pr_id": po["pr_id"]})
        if pr:
            pr_q = {l.get("sku"): float(l.get("quantity") or 0)
                    for l in pr.get("lines", [])}
            for line in po.get("lines", []):
                base = pr_q.get(line.get("sku"))
                if base and float(line.get("quantity") or 0) > \
                        base * (1 + qty_tol / 100.0):
                    auto = False
                    reasons.append(
                        f"Line {line.get('line_no')} quantity exceeds PR by more "
                        f"than {qty_tol}%")
                    break

    # budget validation (policy: budget exceeded → human)
    budget = await check_budget(po.get("department") or "PROCUREMENT", total)
    if budget is not None and not budget["ok"]:
        auto = False
        reasons.append(f"Budget exceeded: {budget['detail']}")

    policy_id = "PO_AUTO_APPROVAL_V1"
    if auto:
        reasons.append("Within policy: approved vendor, valid licence, within budget, "
                       "price/quantity within tolerance, within limit")
    return {
        "decision": "AUTO_APPROVED" if auto else "NEEDS_APPROVAL",
        "policy": policy_id,
        "reasons": reasons,
        "evidence": {
            "vendor_status": vendor.get("status") if vendor else None,
            "vendor_risk": vendor.get("risk_level") if vendor else None,
            "vendor_strategic": vendor.get("strategic") if vendor else None,
            "amount": total,
            "limit": limit,
            "price_tolerance_pct": price_tol,
            "quantity_tolerance_pct": qty_tol,
        },
    }


def _licence_valid(vendor: dict) -> bool:
    """Vendor-level licence check (document-level checked in vendor service)."""
    exp = vendor.get("licence_valid_to")
    if not exp:
        return True  # legacy vendors without licence metadata
    try:
        from datetime import datetime, timezone

        return str(exp)[:10] >= datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        return True


async def _reference_price(po: dict, line: dict) -> Optional[float]:
    """Reference price for tolerance checks: contract price → master price."""
    if po.get("contract_id"):
        contract = await db.db.contracts.find_one({"contract_id": po["contract_id"]})
        for item in (contract or {}).get("price_items", []):
            if item.get("product_id") == line.get("sku"):
                return float(item.get("price") or 0) or None
    from app.domains.masters.service import get_price

    return await get_price(line.get("sku"), "STANDARD")


async def check_budget(department: str, amount: float) -> Optional[dict]:
    """Budget validation against `budgets` collection.
    No budget row → None (no budget control configured)."""
    b = await db.db.budgets.find_one({"department": department})
    if not b:
        return None
    remaining = float(b.get("amount") or 0) - float(b.get("spent") or 0)
    ok = amount <= remaining
    return {"ok": ok, "department": department, "remaining": remaining,
            "detail": f"requested {amount}, remaining {remaining}"}


async def evaluate_pr_approval(pr: Dict[str, Any], actor: dict) -> Dict[str, Any]:
    """PR approval: value threshold + budget validation."""
    limit = float(await get_rule("pr_auto_approval_limit", settings.PO_AUTO_APPROVAL_LIMIT))
    total = float(pr.get("estimated_amount") or 0)
    reasons = []
    if total > limit:
        reasons.append(f"Estimated {total} exceeds limit {limit}")
    budget = await check_budget(pr.get("department") or "GENERAL", total)
    if budget is not None and not budget["ok"]:
        reasons.append(f"Budget exceeded: {budget['detail']}")
    if reasons:
        return {
            "decision": "NEEDS_APPROVAL",
            "policy": "PR_AUTO_APPROVAL_V1",
            "reasons": reasons,
        }
    return {
        "decision": "AUTO_APPROVED",
        "policy": "PR_AUTO_APPROVAL_V1",
        "reasons": [f"Estimated {total} within limit {limit} and budget"],
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
