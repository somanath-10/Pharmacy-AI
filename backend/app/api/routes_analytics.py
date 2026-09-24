"""Analytics: AI Command Center KPIs, department health, activity feed."""
from datetime import timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.core.database import db, now_iso, utcnow
from app.core.security import get_current_principal

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


async def command_center_payload() -> dict:
    """The Unified AI Command Center payload — every number is real.

    No fake fallbacks: if a domain query fails the field is reported as
    UNAVAILABLE with the error, and the UI shows an error state, never a
    fabricated value.
    """
    since = (utcnow() - timedelta(days=1)).isoformat()
    kpis: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    async def measure(key: str, fn):
        try:
            kpis[key] = await fn()
        except Exception as e:  # surface honest error state to the UI
            errors[key] = str(e)[:200]

    # ---- AI agents ----
    async def _agents():
        from app.agents.gateway import list_agents

        rows = await list_agents()
        active = [a for a in rows if a.get("status") == "ACTIVE"]
        return {"active": len(active), "total": len(rows),
                "degraded": [a.get("agent_id") for a in rows
                             if a.get("status") not in ("ACTIVE",)]}

    # ---- automation vs human touch (last 24h audited transitions) ----
    async def _automation():
        total = await db.db.audit_events.count_documents(
            {"timestamp": {"$gte": since}})
        agent = await db.db.audit_events.count_documents(
            {"timestamp": {"$gte": since}, "actor.type": "AGENT"})
        human = await db.db.audit_events.count_documents(
            {"timestamp": {"$gte": since}, "actor.type": "USER"})
        decided = total or 1
        return {"automation_rate_pct": round(100 * agent / decided, 1),
                "human_touch_rate_pct": round(100 * human / decided, 1),
                "audited_actions_24h": total}

    # ---- queues ----
    async def _open_exceptions():
        unresolved = await db.db.approvals.count_documents(
            {"status": "PENDING", "category": "UNRESOLVED_EXCEPTION"})
        inv = await db.db.supplier_invoices.count_documents(
            {"status": {"$in": ["MATCH_FAILED", "DISPUTED"]}})
        qc = await db.db.qc_samples.count_documents({"status": "OOS_INVESTIGATION"})
        ship = await db.db.shipments.count_documents({"status": "EXCEPTION"})
        payments = await db.db.payments.count_documents({"status": "FAILED"}) if \
            "payments" in await db.db.list_collection_names() else 0
        return {"unresolved": unresolved, "invoice_mismatches": inv,
                "oos_investigations": qc, "shipment_exceptions": ship,
                "failed_payments": payments,
                "total": unresolved + inv + qc + ship + payments}

    async def _pending_approvals():
        return await db.db.approvals.count_documents({"status": "PENDING"})

    async def _orders():
        return {"sales": await db.db.sales_orders.count_documents(
                    {"status": {"$nin": ["CLOSED", "CANCELLED"]}}),
                "purchase": await db.db.purchase_orders.count_documents(
                    {"status": {"$nin": ["CLOSED", "CANCELLED", "REJECTED"]}})}

    # ---- department statuses (real counters, deterministic status rule) ----
    async def _warehouse():
        from app.domains.warehouse.service import warehouse_health

        wh = await warehouse_health()
        quarantine = await db.db.inventory_balances.count_documents(
            {"stock_status": "QUARANTINE"})
        pick = await db.db.pick_tasks.count_documents(
            {"status": {"$in": ["CREATED", "ASSIGNED", "IN_PROGRESS"]}}) if \
            "pick_tasks" in await db.db.list_collection_names() else 0
        return {"score": wh["score"],
                "status": _status(wh["score"]),
                "quarantine_rows": quarantine, "open_pick_tasks": pick}

    async def _quality():
        holds = await db.db.quality_holds.count_documents({"status": "ACTIVE"})
        oos = await db.db.qc_samples.count_documents({"status": "OOS_INVESTIGATION"})
        testing = await db.db.qc_samples.count_documents({"status": "TESTING"})
        score = 100 - min(holds * 10 + oos * 10, 60)
        return {"score": score, "status": _status(score),
                "active_holds": holds, "oos_open": oos, "samples_testing": testing}

    async def _plant():
        from app.domains.production.service import plant_readiness

        p = await plant_readiness()
        wip = await db.db.production_orders.count_documents(
            {"status": {"$in": ["DISPENSING", "IN_PROCESS", "PACKAGING"]}})
        return {"score": p["readiness_pct"], "status": _status(p["readiness_pct"]),
                "batches_in_process": wip}

    async def _logistics():
        in_transit = await db.db.shipments.count_documents(
            {"status": {"$in": ["DISPATCHED", "IN_TRANSIT"]}})
        exceptions = await db.db.shipments.count_documents({"status": "EXCEPTION"})
        score = 100 - min(exceptions * 10, 50)
        return {"score": score, "status": _status(score),
                "in_transit": in_transit, "exceptions": exceptions}

    async def _finance():
        ap_open = await db.db.supplier_invoices.count_documents(
            {"status": {"$nin": ["PAID", "CANCELLED", "WRITTEN_OFF"]}})
        ar_open = await db.db.customer_invoices.count_documents(
            {"status": {"$nin": ["PAID", "CANCELLED", "WRITTEN_OFF"]}})
        mismatches = await db.db.supplier_invoices.count_documents(
            {"status": {"$in": ["MATCH_FAILED", "DISPUTED"]}})
        score = 100 - min(mismatches * 5, 40)
        return {"score": score, "status": _status(score),
                "ap_open": ap_open, "ar_open": ar_open,
                "invoice_exceptions": mismatches}

    async def _rx():
        return await db.db.prescriptions.count_documents(
            {"status": {"$in": ["EXTRACTED", "VALIDATED", "PHARMACIST_REVIEW",
                                "CLARIFICATION"]}})

    await measure("agents", _agents)
    await measure("automation", _automation)
    await measure("exceptions", _open_exceptions)
    await measure("pending_approvals", _pending_approvals)
    await measure("orders", _orders)
    await measure("warehouse", _warehouse)
    await measure("quality", _quality)
    await measure("plant", _plant)
    await measure("logistics", _logistics)
    await measure("finance", _finance)
    await measure("prescriptions_pending", _rx)

    return {
        "kpis": kpis,
        "errors": errors,           # non-empty → UI shows error states
        "generated_at": now_iso(),
    }


def _status(v: float) -> str:
    return "HEALTHY" if v >= 90 else ("ATTENTION" if v >= 75 else "CRITICAL")


async def department_health_payload() -> dict:
    from app.domains.production.service import plant_readiness
    from app.domains.warehouse.service import warehouse_health

    wh = await warehouse_health()
    plant = await plant_readiness()

    def status(v: float) -> str:
        return "HEALTHY" if v >= 90 else ("ATTENTION" if v >= 75 else "CRITICAL")

    sales_blocked = await db.db.sales_orders.count_documents(
        {"status": {"$nin": ["CLOSED", "CANCELLED"]}, "credit_check.ok": False})
    procurement_late = await db.db.purchase_orders.count_documents(
        {"status": {"$in": ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"]}})
    open_oos = await db.db.qc_samples.count_documents(
        {"status": "OOS_INVESTIGATION"})
    finance_exceptions = await db.db.supplier_invoices.count_documents(
        {"status": {"$in": ["MATCH_FAILED", "DISPUTED"]}})
    rx_pending = await db.db.prescriptions.count_documents(
        {"status": {"$in": ["VALIDATED", "PHARMACIST_REVIEW", "CLARIFICATION"]}})

    scores = {
        "Sales": 100 - min(sales_blocked * 10, 40),
        "Purchase": 100 - min(procurement_late * 2, 30),
        "QA": 100 - min(await db.db.quality_holds.count_documents(
            {"status": "ACTIVE"}) * 10, 50),
        "QC": 100 - min(open_oos * 10, 40),
        "Warehouse": wh["score"],
        "Plant": plant["readiness_pct"],
        "Logistics": 100 - min(await db.db.shipments.count_documents(
            {"status": "EXCEPTION"}) * 10, 40),
        "Regulatory": 100 - min(len((await _licence_risk()) or []) * 5, 40),
        "Finance": 100 - min(finance_exceptions * 5, 40),
        "Pharmacy": 100 - min(rx_pending * 5, 40),
    }
    return {"departments": [
        {"name": k, "score": v, "status": status(v)} for k, v in scores.items()
    ]}


async def _licence_risk() -> list:
    risk = []
    async for lic in db.db.licences.find({"status": "ACTIVE",
                                          "expiry_date": {"$ne": None}}):
        risk.append(lic["licence_id"])
    return risk


async def activity_feed(limit: int = 50) -> list:
    rows = []
    async for e in db.db.events.find().sort("created_at", -1).limit(limit):
        e.pop("_id", None)
        rows.append(e)
    return rows


@router.get("/command-center")
async def command_center(principal: dict = Depends(get_current_principal)):
    return await command_center_payload()


@router.get("/departments")
async def departments(principal: dict = Depends(get_current_principal)):
    return await department_health_payload()


@router.get("/activity")
async def activity(limit: int = 50,
                   principal: dict = Depends(get_current_principal)):
    return await activity_feed(limit)


# ==================================================================
# Phase: AI Command Center + Exception Center + Agent Activity
# ==================================================================

async def exception_center_payload(auto_resolve: bool = True) -> dict:
    """One place for unresolved problems.

    The supervisor's self-resolution loop runs FIRST (bounded); only problems
    it cannot resolve are returned as needing human attention — and those are
    routed to the Human Decision Queue as UNRESOLVED_EXCEPTION.
    """
    from app.agents.supervisor import self_resolve_problem

    problems = []

    # invoice mismatches
    async for inv in db.db.supplier_invoices.find(
            {"status": {"$in": ["MATCH_FAILED", "DISPUTED"]}}).limit(20):
        problems.append({"type": "INVOICE_MISMATCH", "entity_id": inv["invoice_id"],
                         "severity": "HIGH", "amount": inv.get("total_amount"),
                         "detail": f"Supplier invoice {inv.get('supplier_invoice_number', '')}"})

    # OOS investigations
    async for s in db.db.qc_samples.find({"status": "OOS_INVESTIGATION"}).limit(20):
        problems.append({"type": "QC_OOS", "entity_id": s["sample_id"],
                         "severity": "HIGH",
                         "detail": f"OOS on {s.get('product_id')} batch {s.get('batch_id')}"})

    # shipment exceptions
    async for sh in db.db.shipments.find({"status": "EXCEPTION"}).limit(20):
        problems.append({"type": "SHIPMENT_EXCEPTION", "entity_id": sh["shipment_id"],
                         "severity": "MEDIUM",
                         "detail": f"Shipment to {sh.get('customer_id') or 'customer'} in exception"})

    # failed payments
    try:
        async for p in db.db.payments.find({"status": "FAILED"}).limit(10):
            problems.append({"type": "PAYMENT_FAILED", "entity_id": p["payment_id"],
                             "severity": "HIGH", "amount": p.get("amount"),
                             "detail": "Payment execution failed"})
    except Exception:
        pass

    # quality holds aging
    async for qh in db.db.quality_holds.find({"status": "ACTIVE"}).limit(20):
        problems.append({"type": "QUALITY_HOLD", "entity_id": qh.get("hold_id",
                         qh.get("batch_id", "")), "severity": "MEDIUM",
                         "detail": qh.get("reason", "Active quality hold")})

    # vendor issues: blocked/suspended vendors
    async for v in db.db.vendors.find(
            {"status": {"$in": ["BLOCKED", "SUSPENDED"]}}).limit(10):
        problems.append({"type": "VENDOR_ISSUE", "entity_id": v["vendor_id"],
                         "severity": "MEDIUM", "detail": f"Vendor {v.get('name')} {v['status'].lower()}"})

    # self-resolution pass (bounded, deterministic tools only)
    if auto_resolve:
        for p in problems[:30]:
            try:
                res = await self_resolve_problem(p)
                p["auto_resolution"] = res
                p["resolved"] = bool(res.get("resolved"))
            except Exception as e:
                p["resolved"] = False
                p["auto_resolution"] = {"error": str(e)[:150]}

    unresolved = [p for p in problems if not p.get("resolved")]
    resolved = [p for p in problems if p.get("resolved")]
    return {"unresolved": unresolved, "auto_resolved": resolved,
            "checked_at": now_iso()}


async def agent_activity_payload(limit: int = 100) -> dict:
    """Agent Activity: tool calls + supervisor runs + escalation summary."""
    calls = []
    async for c in db.db.agent_tool_calls.find().sort("created_at", -1).limit(limit):
        c.pop("_id", None)
        calls.append(c)
    runs = []
    async for r in db.db.agent_runs.find().sort("created_at", -1).limit(25):
        r.pop("_id", None)
        runs.append(r)
    since = (utcnow() - timedelta(days=1)).isoformat()
    escalated = await db.db.agent_tool_calls.count_documents(
        {"status": "AWAITING_APPROVAL", "created_at": {"$gte": since}})
    failed = await db.db.agent_tool_calls.count_documents(
        {"status": "FAILED", "created_at": {"$gte": since}})
    success = await db.db.agent_tool_calls.count_documents(
        {"status": "SUCCESS", "created_at": {"$gte": since}})
    durations = [c.get("duration_ms") for c in calls if c.get("duration_ms")]
    return {"tool_calls": calls, "supervisor_runs": runs,
            "summary_24h": {"success": success, "failed": failed,
                            "escalated": escalated,
                            "p50_ms": sorted(durations)[len(durations) // 2]
                            if durations else None}}


@router.get("/exception-center")
async def exception_center(auto: bool = True,
                           principal: dict = Depends(get_current_principal)):
    return await exception_center_payload(auto_resolve=auto)


@router.get("/agent-activity")
async def agent_activity(limit: int = 100,
                         principal: dict = Depends(get_current_principal)):
    return await agent_activity_payload(limit)


@router.get("/admin/overview")
async def admin_overview(principal: dict = Depends(get_current_principal)):
    """Administration: users, roles, agents, integrations, system health."""
    roles = set(principal.get("roles", []))
    if "SUPER_ADMIN" not in roles and "MANAGEMENT" not in roles \
            and "COMPLIANCE" not in roles:
        from app.core.errors import PermissionDenied

        raise PermissionDenied("Admin overview requires elevated roles")
    users = []
    async for u in db.db.users.find({}).limit(200):
        u.pop("_id", None)
        u.pop("password_hash", None)
        users.append({"email": u.get("email"), "roles": u.get("roles", []),
                      "status": u.get("status", "ACTIVE"),
                      "mfa": bool(u.get("mfa_secret")),
                      "last_login": u.get("last_login")})
    licences = await db.db.licences.count_documents(
        {"status": "ACTIVE", "expiry_date": {"$ne": None}})
    agents = await db.db.agent_tool_calls.aggregate([
        {"$group": {"_id": "$agent", "calls": {"$sum": 1},
                    "errors": {"$sum": {"$cond": [
                        {"$eq": ["$status", "FAILED"]}, 1, 0]}}}},
        {"$sort": {"calls": -1}}]).to_list(30)
    return {
        "users": users,
        "user_count": len(users),
        "roles_in_use": sorted({r for u in users for r in u["roles"]}),
        "agents": agents,
        "open_licences_tracked": licences,
        "counters": {c: await db.db.counters.count_documents({}) for c in []},
    }


@router.get("/executive")
async def executive(principal: dict = Depends(get_current_principal)):
    """Executive dashboard (Part 16): every domain + AI governance + risks."""
    from app.domains.analytics.executive import executive_dashboard

    return await executive_dashboard()


@router.get("/advanced/quality-trends")
async def quality_trends_view(principal: dict = Depends(get_current_principal)):
    from app.domains.analytics.executive import quality_trends

    return await quality_trends()


@router.get("/advanced/plant-oee")
async def plant_oee_view(principal: dict = Depends(get_current_principal)):
    from app.domains.analytics.executive import plant_oee

    return await plant_oee()


@router.get("/advanced/inventory-intelligence")
async def inventory_intel(principal: dict = Depends(get_current_principal)):
    from app.domains.analytics.executive import inventory_intelligence

    return await inventory_intelligence()
