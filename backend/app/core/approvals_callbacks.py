"""Callbacks executed when Human Decision Queue items are approved/rejected.

Registered by name so approval creation stays decoupled from domain services.
Every callback receives the original approval payload and the human actor who
decided — the audit trail records the human authority, not the agent.
"""
from typing import Any, Callable, Coroutine, Dict

from app.core.errors import DomainError

_REGISTRY: Dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}


def callback(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn

    return deco


async def run_callback(name: str, payload: Dict[str, Any], actor: Dict[str, Any]):
    fn = _REGISTRY.get(name)
    if not fn:
        raise DomainError(f"Approval callback '{name}' not registered")
    return await fn(payload=payload, actor=actor)


def get_callback(name: str):
    return _REGISTRY.get(name)


# ----------------------------------------------------------- callback registry
# Imported for side-effect registration. Kept at the bottom so domain imports
# never circularly import this module at load time.
def _register_all():
    from app.core.database import db, now_iso
    from app.core.events import bus
    from app.core.workflow import transition
    from app.core.audit import audit

    # ---- Vendors -----------------------------------------------------------
    from app.domains.vendors import service as vendors

    @callback("vendor_approve")
    async def vendor_approve(payload: dict, actor: dict):
        return await vendors._do_approve(payload, actor)

    @callback("vendor_bank_change")
    async def vendor_bank_change(payload: dict, actor: dict):
        vid = payload["vendor_id"]
        await db.db.vendors.update_one(
            {"vendor_id": vid},
            {"$set": {"bank_details": payload.get("bank_details"),
                      "bank_details_approved_by": actor,
                      "bank_details_approved_at": now_iso(),
                      "updated_at": now_iso()}},
        )
        await audit("VENDOR", vid, "BANK_DETAILS_APPROVED", actor,
                    details={"via": "human_decision_queue"})
        return await vendors.get_vendor(vid)

    # ---- Sales -------------------------------------------------------------
    from app.domains.sales import service as sales

    @callback("quotation_approve")
    async def quotation_approve(payload: dict, actor: dict):
        return await sales._do_send_quote(payload, actor)

    # ---- Sourcing ----------------------------------------------------------
    from app.domains.sourcing import service as sourcing

    @callback("sourcing_award_approve")
    async def sourcing_award_approve(payload: dict, actor: dict):
        return await sourcing._do_award(payload, actor)

    # ---- Procurement -------------------------------------------------------
    from app.domains.procurement import service as procurement

    @callback("pr_approve")
    async def pr_approve(payload: dict, actor: dict):
        pr_id = payload["pr_id"]
        await transition("purchase_requisition", pr_id, "purchase_requisitions",
                         "pr_id", "APPROVED", actor,
                         reason="Approved from Human Decision Queue")
        await bus.publish("pr.approved", {"pr_id": pr_id, "auto": False}, actor)
        return await procurement._get_pr(pr_id)

    @callback("po_approve")
    async def po_approve(payload: dict, actor: dict):
        return await procurement._approve_po(
            payload["po_id"], actor,
            reason="Approved from Human Decision Queue")

    # ---- Finance -----------------------------------------------------------
    from app.domains.finance import service as finance

    @callback("invoice_match_accept")
    async def invoice_match_accept(payload: dict, actor: dict):
        """Human accepted the residual variance after agent investigation."""
        invoice_id = payload["invoice_id"]
        await transition("supplier_invoice", invoice_id, "supplier_invoices",
                         "invoice_id", "MATCHED", actor,
                         reason="Variance accepted by finance authority")
        await db.db.supplier_invoices.update_one(
            {"invoice_id": invoice_id},
            {"$set": {"match_variance_accepted_by": actor,
                      "updated_at": now_iso()}},
        )
        return await finance._get_inv(invoice_id)

    @callback("payment_authorize")
    async def payment_authorize(payload: dict, actor: dict):
        """Human authorized the payment → PROPOSED → AUTHORIZED."""
        payment_id = payload["payment_id"]
        await transition("payment", payment_id, "payments", "payment_id",
                         "AUTHORIZED", actor,
                         reason="Authorized from Human Decision Queue")
        await audit("PAYMENT", payment_id, "HUMAN_AUTHORIZED", actor)
        return await db.db.payments.find_one({"payment_id": payment_id})


_register_all()
