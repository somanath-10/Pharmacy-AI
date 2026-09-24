"""Concurrency stress tests for surfaces not covered by test_p0_hardening:
payment authorization/approval/decide races, approval dedup, doc-number
counter races under contention, duplicate supplier invoices, and outbox
claim exclusivity. Each test isolates its data with unique keys.
"""
import asyncio
import uuid

import pytest
from pymongo.errors import DuplicateKeyError

from app.core.approvals import create_approval, decide
from app.core.database import db, now_iso
from app.core.errors import ConflictError, DomainError
from app.core.events import bus
from app.domains.finance import service as fin

pytestmark = pytest.mark.asyncio


def _uid():
    return uuid.uuid4().hex[:10]


async def _mk_vendor() -> str:
    vid = f"V-STRESS-{_uid()}"
    await db.db.vendors.insert_one({
        "vendor_id": vid, "name": vid, "status": "ACTIVE",
        "organization_id": "ORG-T", "created_at": now_iso(),
    })
    return vid


async def _mk_approved_invoice(amount: float = 100.0) -> str:
    """One APPROVED supplier invoice, ready for payment proposal."""
    vid = await _mk_vendor()
    po_id = f"PO-STRESS-{_uid()}"
    sup_no = f"SUP-{_uid()}"
    await db.db.purchase_orders.insert_one({
        "po_id": po_id, "vendor_id": vid, "status": "CLOSED",
        "lines": [{"line_no": 1, "quantity": 1, "unit_price": amount}],
        "total_amount": amount, "currency": "INR",
        "created_at": now_iso(),
    })
    invoice_id = f"AP-STRESS-{_uid()}"
    await db.db.supplier_invoices.insert_one({
        "invoice_id": invoice_id, "vendor_id": vid, "po_id": po_id,
        "supplier_invoice_number": sup_no, "lines": [
            {"line_no": 1, "quantity": 1, "unit_price": amount,
             "amount": amount}],
        "total_amount": amount, "currency": "INR", "status": "APPROVED",
        "created_at": now_iso(), "updated_at": now_iso(),
    })
    return invoice_id


FIN_ACTOR = {"type": "USER", "id": "stress-finance",
             "roles": ["FINANCE", "SUPER_ADMIN"]}


# ------------------------------------------------------- payment proposal race
async def test_payment_proposal_concurrent_no_duplicate_invoices():
    """Two concurrent proposals over the same invoice set must not both win:
    the second sees the invoice no longer APPROVED and raises Conflict."""
    iid = await _mk_approved_invoice()
    actor = FIN_ACTOR

    async def propose():
        try:
            return await fin.create_payment_proposal({"invoice_ids": [iid]},
                                                     actor)
        except (ConflictError, DomainError) as e:
            return {"error": str(e)}

    r1, r2 = await asyncio.gather(propose(), propose())
    docs = [r for r in (r1, r2) if "payment_id" in r]
    rejected = [r for r in (r1, r2) if "error" in r]
    assert len(docs) == 1, f"exactly one proposal may claim the invoice: {r1} {r2}"
    assert len(rejected) == 1
    assert len(await db.db.payments.find(
        {"lines.invoice_id": iid}).to_list(None)) == 1


async def test_payment_proposal_is_idempotent():
    iid = await _mk_approved_invoice()
    actor = FIN_ACTOR
    first = await fin.create_payment_proposal({"invoice_ids": [iid]},
                                              actor, idempotency_key="STRESS-PAY-1")
    again = await fin.create_payment_proposal({"invoice_ids": [iid]},
                                              actor, idempotency_key="STRESS-PAY-1")
    assert first["payment_id"] == again["payment_id"]


# ------------------------------------------------------------- approval dedup
async def test_concurrent_approvals_dedup_same_entity_category():
    """Two concurrent create_approval calls for the same (entity, category)
    resolve to one PENDING approval — the atomic claim returns the winner."""
    entity_id = f"PAY-STRESS-{_uid()}"
    actor = FIN_ACTOR
    ids = await asyncio.gather(
        create_approval("FINANCIAL_AUTHORITY", f"Auth {entity_id}",
                        "PAYMENT", entity_id, actor),
        create_approval("FINANCIAL_AUTHORITY", f"Auth {entity_id}",
                        "PAYMENT", entity_id, actor),
    )
    assert ids[0] == ids[1], "same entity+category → same PENDING approval"
    pending = await db.db.approvals.count_documents(
        {"entity_type": "PAYMENT", "entity_id": entity_id,
         "category": "FINANCIAL_AUTHORITY", "status": "PENDING"})
    assert pending == 1


async def test_different_categories_same_entity_both_created():
    entity_id = f"PO-STRESS-{_uid()}"
    actor = FIN_ACTOR
    a1 = await create_approval("FINANCIAL_AUTHORITY", "PO approval",
                               "PURCHASE_ORDER", entity_id, actor)
    a2 = await create_approval("STRATEGIC", "Sourcing award",
                               "PURCHASE_ORDER", entity_id, actor)
    assert a1 != a2


# --------------------------------------------------------------- decide race
async def test_concurrent_decide_only_one_callback_execution():
    """Double-click / racing agents on one approval: exactly one decision
    wins, the on_approve callback runs exactly once."""
    entity_id = f"PAY-DEC-{_uid()}"
    actor = FIN_ACTOR
    approval_id = await create_approval(
        "FINANCIAL_AUTHORITY", f"Authorize {entity_id}",
        "PAYMENT", entity_id, actor,
        on_approve="noop_stress", payload={"entity_id": entity_id})
    await db.db.approvals_callbacks_probe.delete_many({"eid": entity_id})

    from app.core import approvals_callbacks as cbmod
    if not hasattr(cbmod, "_REGISTRY"):
        CALLBACKS = getattr(cbmod, "REGISTRY", None) or {}
    else:
        CALLBACKS = cbmod._REGISTRY

    calls = []

    async def _probe(payload, actor):
        calls.append(payload.get("entity_id"))
        await db.db.approvals_callbacks_probe.insert_one(
            {"eid": payload.get("entity_id")})

    # register probe under a dedicated name for this test
    old = CALLBACKS.get("noop_stress")
    CALLBACKS["noop_stress"] = _probe
    try:
        results = await asyncio.gather(
            decide(approval_id, "APPROVED",
                   {"type": "USER", "id": "mgr-1", "roles": ["MANAGEMENT"]},
                   "ok", return_exception=True)
            if False else _decide_safe(approval_id, "mgr-1"),
            _decide_safe(approval_id, "mgr-2"),
        )
    finally:
        if old is not None:
            CALLBACKS["noop_stress"] = old
        else:
            CALLBACKS.pop("noop_stress", None)

    errors = [r for r in results if isinstance(r, Exception)]
    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 1, f"exactly one decide may win: {results}"
    assert len(errors) == 1
    assert len(calls) == 1, f"callback executed exactly once, got {calls}"


async def _decide_safe(approval_id: str, who: str):
    try:
        return await decide(approval_id, "APPROVED",
                            {"type": "USER", "id": who,
                             "roles": ["MANAGEMENT"]}, "ok")
    except Exception as e:  # noqa: BLE001 — the test asserts on the type
        return e


# ------------------------------------------------- doc-number counter contention
async def test_doc_number_counter_race_unique_sequences():
    """20 concurrent counter consumers each get a distinct increasing seq."""
    from app.core.sequences import next_id

    name = f"stress-counter-{_uid()}"
    results = await asyncio.gather(*[next_id(name) for _ in range(20)])
    seqs = [int(r.rsplit("-", 1)[1]) for r in results]
    assert len(set(seqs)) == 20, f"duplicates in {sorted(seqs)}"


# ------------------------------------------------ duplicate supplier invoice
async def test_duplicate_supplier_invoice_concurrent_surfaces_conflict():
    """Concurrent duplicate supplier invoice: unique index lets one win,
    the loser surfaces a clean ConflictError (409), never a 500."""
    vid = await _mk_vendor()
    po_id = f"PO-STRESS-{_uid()}"
    await db.db.purchase_orders.insert_one({
        "po_id": po_id, "vendor_id": vid, "status": "SENT",
        "lines": [{"line_no": 1, "quantity": 5, "unit_price": 10}],
        "total_amount": 50, "currency": "INR", "created_at": now_iso(),
    })
    payload = {"po_id": po_id, "supplier_invoice_number": f"SUP-DUP-{_uid()}",
               "total_amount": 50}
    results = await asyncio.gather(
        _sup_inv_safe(payload, fin), _sup_inv_safe(payload, fin))
    errs = [r for r in results if isinstance(r, Exception)]
    oks = [r for r in results if not isinstance(r, Exception)]
    # exactly one insert wins; the loser either conflicts on insert or was
    # rejected by the pre-check (both are valid race outcomes)
    dup_errors = [e for e in errs if isinstance(e, ConflictError)]
    dup_errors_500 = [e for e in errs if not isinstance(e, ConflictError)]
    assert not dup_errors_500, f"loser must not raise a non-conflict error: {dup_errors_500}"
    assert len(oks) + len(dup_errors) == 2
    count = await db.db.supplier_invoices.count_documents(
        {"po_id": po_id, "supplier_invoice_number": payload["supplier_invoice_number"]})
    assert count == 1


async def _sup_inv_safe(payload, fin):
    try:
        return await fin.receive_supplier_invoice(payload, FIN_ACTOR)
    except Exception as e:  # noqa: BLE001 — asserted by the caller
        return e


# ---------------------------------------------------------------- outbox claims
async def test_outbox_parallel_pump_no_double_dispatch():
    """Two pumps racing on the same outbox must never both dispatch a row."""
    handled = []

    async def handler(payload, actor=None):
        handled.append(payload["n"])

    bus.subscribe("stress.exclusive", handler)
    for i in range(12):
        await db.db.outbox_events.insert_one({
            "name": "stress.exclusive", "payload": {"n": i},
            "status": "PENDING", "attempts": 0,
            "created_at": now_iso(),
        })
    await asyncio.gather(bus.pump_once(), bus.pump_once())
    assert sorted(handled) == list(range(12)), \
        f"each event dispatched exactly once: {sorted(handled)}"
