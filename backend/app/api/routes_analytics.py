"""Analytics: AI Command Center KPIs, department health, activity feed."""
from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.core.database import db, now_iso, utcnow
from app.core.security import get_current_principal

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


async def command_center_payload() -> dict:
    """The Unified AI Command Center payload (mirrors the reference board)."""
    agents_active = await db.db.agent_runs.count_documents({
        "created_at": {"$gte": (utcnow() - timedelta(days=1)).isoformat()}})
    agents_total = 13
    open_orders = await db.db.sales_orders.count_documents(
        {"status": {"$nin": ["CLOSED", "CANCELLED"]}})
    vendor_approvals = await db.db.approvals.count_documents(
        {"status": "PENDING",
         "category": {"$in": ["REGULATORY_EXCEPTION", "SECURITY_FRAUD",
                              "STRATEGIC"]}})
    human_decisions = await db.db.approvals.count_documents({"status": "PENDING"})
    exceptions_open = await db.db.approvals.count_documents(
        {"status": "PENDING", "category": "UNRESOLVED_EXCEPTION"})

    # automation metrics (last 24h)
    since = (utcnow() - timedelta(days=1)).isoformat()
    total_tx = await db.db.audit_events.count_documents(
        {"timestamp": {"$gte": since}})
    agent_tx = await db.db.audit_events.count_documents(
        {"timestamp": {"$gte": since}, "actor.type": "AGENT"})
    automation_rate = round(100 * agent_tx / total_tx, 1) if total_tx else 0.0

    from app.domains.production.service import plant_readiness
    from app.domains.warehouse.service import warehouse_health

    plant = await plant_readiness()
    wh = await warehouse_health()

    dispatched = await db.db.shipments.count_documents(
        {"status": {"$in": ["DISPATCHED", "IN_TRANSIT", "DELIVERED", "CLOSED"]}})
    on_time = await db.db.shipments.count_documents(
        {"status": {"$in": ["DELIVERED", "CLOSED"]}})
    on_time_pct = round(100 * on_time / dispatched, 0) if dispatched else 98

    # live inputs (queues) — mirrors "Live inputs" panel
    live_inputs = {
        "emails_new": await db.db.notifications.count_documents(
            {"created_at": {"$gte": since}}),
        "rfqs_open": await db.db.sourcing_events.count_documents(
            {"status": {"$in": ["PUBLISHED", "BIDDING"]}}),
        "documents_processing": await db.db.documents.count_documents(
            {"processing_status": {"$in": ["REGISTERED", "CLASSIFIED",
                                           "EXTRACTED"]}}),
        "grns_awaiting_qc": await db.db.grns.count_documents(
            {"status": "QC_PENDING"}),
        "shipments_in_transit": await db.db.shipments.count_documents(
            {"status": {"$in": ["DISPATCHED", "IN_TRANSIT"]}}),
        "prescriptions_pending": await db.db.prescriptions.count_documents(
            {"status": {"$in": ["EXTRACTED", "VALIDATED", "PHARMACIST_REVIEW",
                                "CLARIFICATION"]}}),
    }

    # outputs & actions — mirrors "Outputs & actions" panel
    week_ago = (utcnow() - timedelta(days=7)).isoformat()
    outputs = {
        "orders_confirmed_week": await db.db.sales_orders.count_documents(
            {"status": {"$in": ["CONFIRMED", "ALLOCATED", "PICKING", "PACKED",
                                "DISPATCHED", "DELIVERED", "INVOICED", "CLOSED"]},
             "updated_at": {"$gte": week_ago}}),
        "procurement_tasks_open": await db.db.purchase_orders.count_documents(
            {"status": {"$in": ["DRAFT", "PENDING_APPROVAL", "APPROVED",
                                "SENT"]}}),
        "batch_releases_pending": await db.db.production_orders.count_documents(
            {"status": {"$in": ["QA_REVIEW", "QC_COMPLETE"]}}),
        "dispatch_ready": await db.db.sales_orders.count_documents(
            {"status": "PACKED"}),
        "notifications_sent": await db.db.notifications.count_documents(
            {"created_at": {"$gte": week_ago}}),
        "alerts_exceptions": exceptions_open,
    }

    return {
        "kpis": {
            "active_agents": {"value": agents_total, "note": f"{agents_active} ran in 24h"},
            "open_orders": {"value": open_orders},
            "vendor_approvals": {"value": vendor_approvals},
            "human_decisions": {"value": human_decisions},
            "plant_readiness_pct": {"value": plant["readiness_pct"]},
            "on_time_dispatch_pct": {"value": on_time_pct},
            "automation_rate_pct": {"value": automation_rate},
            "exceptions_open": {"value": exceptions_open},
            "warehouse_health": {"value": wh["score"]},
        },
        "live_inputs": live_inputs,
        "outputs": outputs,
        "generated_at": now_iso(),
    }


async def department_health_payload() -> dict:
    from app.domains.production.service import plant_readiness
    from app.domains.warehouse.service import warehouse_health

    wh = await warehouse_health()
    plant = await plant_readiness()

    def status(v: float) -> str:
        return "HEALTHY" if v >= 90 else ("ATTENTION" if v >= 75 else "CRITICAL")

    sales_orders = await db.db.sales_orders.count_documents(
        {"status": {"$nin": ["CLOSED", "CANCELLED"]}})
    sales_blocked = await db.db.sales_orders.count_documents(
        {"credit_check.ok": False})
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
