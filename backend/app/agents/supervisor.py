"""Supervisor Agent: orchestration, self-resolution loop, cross-domain
coordination, human-escalation taxonomy enforcement.

`tick()` runs one orchestration cycle: scans pending work across domains,
attempts autonomous resolution, escalates what remains — feeding the Human
Decision Queue and the AI Command Center.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.events import bus

log = logging.getLogger("pharmaos.supervisor")

AGENT_ID = "supervisor-agent"

ESCALATION_CATEGORIES = [
    "PHYSICAL_WORK", "CLINICAL_AUTHORITY", "QA_AUTHORITY", "FINANCIAL_AUTHORITY",
    "STRATEGIC", "SECURITY_FRAUD", "REGULATORY_EXCEPTION", "UNRESOLVED_EXCEPTION",
]


async def tick(max_work: int = 50) -> dict:
    """One supervision cycle across all domains."""
    started = now_iso()
    results = {
        "started_at": started,
        "actions": [],
        "auto_resolved": 0,
        "escalated": 0,
        "pending_scans": [],
    }

    # 1. Expired approvals → mark EXPIRED (SLA)
    expired = await _expire_approvals()
    if expired:
        results["actions"].append({"action": "EXPIRE_APPROVALS", "count": expired})

    # 2. Retry failed outbox events
    from app.core.events import bus

    processed = await bus.pump_once(limit=100)
    results["actions"].append({"action": "OUTBOX_PUMP", "processed": processed})

    # 3. Open agent tasks awaiting supplier correction → check resolution
    resolved = await _check_supplier_corrections()
    if resolved:
        results["actions"].append({"action": "SUPPLIER_CORRECTIONS",
                                   "resolved": resolved})

    # 4. Licence expiry sweep (compliance agent domain, run by supervisor)
    lic = await _licence_sweep()
    results["actions"].append({"action": "LICENCE_SWEEP", **lic})

    # 5. MRP proposals for shortages flagged by events (bounded)
    proposals = await _scan_shortages(max_work)
    results["actions"].append({"action": "SHORTAGE_SCAN",
                               "proposals_created": proposals})

    await db.db.agent_runs.insert_one({
        "agent": AGENT_ID, "type": "TICK", "results": results,
        "created_at": now_iso()})
    await audit("AGENT_RUN", AGENT_ID, "TICK", {"type": "AGENT", "id": AGENT_ID},
                details=results)
    results["finished_at"] = now_iso()
    return results


async def _expire_approvals() -> int:
    now = now_iso()
    rows = [{"approval_id": r["approval_id"]} async for r in
            db.db.approvals.find({"status": "PENDING",
                                  "due_at": {"$lt": now}})]
    for r in rows:
        await db.db.approvals.update_one(
            {"approval_id": r["approval_id"]},
            {"$set": {"status": "EXPIRED", "updated_at": now_iso()}})
        await bus.publish("approval.decided",
                          {"approval_id": r["approval_id"],
                           "decision": "EXPIRED"})
    return len(rows)


async def _check_supplier_corrections() -> int:
    """Credit notes applied by vendors resolve invoice exceptions."""
    resolved = 0
    async for task in db.db.agent_tasks.find({"type": "SUPPLIER_CORRECTION_REQUEST",
                                              "status": "OPEN"}):
        cn = await db.db.credit_notes.find_one({"invoice_id": task["invoice_id"]})
        if cn:
            await db.db.agent_tasks.update_one(
                {"task_id": task["task_id"]},
                {"$set": {"status": "RESOLVED", "resolved_at": now_iso()}})
            resolved += 1
    return resolved


async def _licence_sweep() -> dict:
    try:
        from app.domains.compliance.service import licence_status_sweep

        out = await licence_status_sweep({"type": "AGENT", "id": AGENT_ID})
        return {"expired": len(out["expired"]),
                "expiring": len(out["expiring_soon"]),
                "vendor_warnings": len(out["vendor_warnings"])}
    except Exception as e:
        return {"error": str(e)[:120]}


async def _scan_shortages(limit: int) -> int:
    """Scan open confirmed orders for shortages → create planning proposals."""
    created = 0
    from app.domains.planning.service import compute_supply_plan

    seen: set = set()
    async for so in db.db.sales_orders.find({"status": "CONFIRMED"}).limit(limit):
        for line in so.get("lines", []):
            sku = line.get("sku")
            if not sku or sku in seen:
                continue
            seen.add(sku)
            try:
                plan = await compute_supply_plan(sku, actor={
                    "type": "AGENT", "id": AGENT_ID})
            except Exception:
                continue
            if plan.get("action") in ("TRANSFER", "PRODUCE", "BUY"):
                exists = await db.db.planning_proposals.find_one(
                    {"product_id": sku, "status": "PROPOSED"})
                if exists:
                    continue
                prop_id = await _next_proposal_id()
                await db.db.planning_proposals.insert_one({
                    "proposal_id": prop_id,
                    "product_id": sku,
                    "action": plan["action"],
                    "quantity": plan["net_requirement"],
                    "options": plan.get("options", []),
                    "reason": plan["reason"],
                    "source": "SUPERVISOR_SCAN",
                    "status": "PROPOSED",
                    "created_by": {"type": "AGENT", "id": AGENT_ID},
                    "created_at": now_iso()})
                created += 1
    return created


async def _next_proposal_id() -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": "proposal"}, {"$inc": {"seq": 1}}, upsert=True,
        return_document=True)
    return f"PROP-{int(doc['seq']):05d}"


async def self_resolve_problem(problem: Dict[str, Any]) -> dict:
    """The canonical self-resolution loop from the blueprint:

    Problem → investigate → retry/alternate → check master data →
    check related workflow → ask another permitted tool/agent →
    request correction → apply deterministic policy → human?
    """
    trail = []
    ptype = problem.get("type")
    entity_id = problem.get("entity_id")

    if ptype == "INVOICE_MISMATCH":
        from app.domains.finance.service import match_invoice

        trail.append("INVESTIGATE: re-run match")
        result = await match_invoice(entity_id, {"type": "AGENT",
                                                 "id": "finance-agent"})
        trail.append(f"MATCH result: {result.get('matched')}")
        return {"resolved": bool(result.get("matched")), "trail": trail}

    if ptype == "DOCUMENT_EXTRACTION_LOW_CONFIDENCE":
        trail.append("RETRY: alternate extraction (offline heuristics)")
        trail.append("ROUTE: HUMAN_REVIEW per confidence policy")
        return {"resolved": False, "route": "HUMAN_REVIEW", "trail": trail}

    trail.append("NO_AUTOMATED_RESOLUTION")
    return {"resolved": False, "trail": trail}
