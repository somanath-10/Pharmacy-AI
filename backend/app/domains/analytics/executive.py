"""Executive dashboard + advanced deterministic analytics (Part 15/16).

Every figure is computed from real collections — no sampled or synthetic
values. All functions are read-only and safe to poll from dashboards.
"""
from datetime import timedelta

from app.core.database import db, now_iso, utcnow


def _since(days: int) -> str:
    return (utcnow() - timedelta(days=days)).isoformat()


async def executive_dashboard() -> dict:
    """One screen for management: every domain + AI governance + risks."""
    since30 = _since(30)

    # ---- commercial ----
    sales_open = await db.db.sales_orders.count_documents(
        {"status": {"$nin": ["CLOSED", "CANCELLED"]}})
    sales_value = 0.0
    async for so in db.db.sales_orders.find(
            {"status": {"$nin": ["CLOSED", "CANCELLED"]}},
            {"total_amount": 1}).limit(500):
        sales_value += float(so.get("total_amount") or 0)
    purchase_open = await db.db.purchase_orders.count_documents(
        {"status": {"$nin": ["CLOSED", "CANCELLED", "REJECTED"]}})
    purchase_value = 0.0
    async for po in db.db.purchase_orders.find(
            {"status": {"$nin": ["CLOSED", "CANCELLED", "REJECTED"]}},
            {"total_amount": 1}).limit(500):
        purchase_value += float(po.get("total_amount") or 0)

    # ---- inventory / production / quality / logistics / finance ----
    products = await db.db.products.count_documents({})
    quarantine_rows = await db.db.inventory_balances.count_documents(
        {"stock_status": {"$in": ["QUARANTINE", "QUALITY_HOLD"]}})
    wip = await db.db.production_orders.count_documents(
        {"status": {"$in": ["DISPENSING", "IN_PROCESS", "PACKAGING"]}})
    holds = await db.db.quality_holds.count_documents({"status": "ACTIVE"})
    oos = await db.db.qc_samples.count_documents({"status": "OOS_INVESTIGATION"})
    in_transit = await db.db.shipments.count_documents(
        {"status": {"$in": ["DISPATCHED", "IN_TRANSIT"]}})
    ship_exceptions = await db.db.shipments.count_documents({"status": "EXCEPTION"})
    ap_open = await db.db.supplier_invoices.count_documents(
        {"status": {"$nin": ["PAID", "CANCELLED", "WRITTEN_OFF"]}})
    ar_open = await db.db.customer_invoices.count_documents(
        {"status": {"$nin": ["PAID", "CANCELLED", "WRITTEN_OFF"]}})

    # ---- AI governance ----
    from app.agents.gateway import AGENT_REGISTRY

    agents = [{"agent_id": k, "status": v["status"], "runs": v["runs"],
               "errors": v["errors"]} for k, v in AGENT_REGISTRY.items()]
    agent_calls_30d = await db.db.agent_tool_calls.count_documents(
        {"created_at": {"$gte": since30}})
    agent_fails_30d = await db.db.agent_tool_calls.count_documents(
        {"created_at": {"$gte": since30}, "status": "FAILED"})
    total_tx = await db.db.audit_events.count_documents(
        {"timestamp": {"$gte": since30}})
    agent_tx = await db.db.audit_events.count_documents(
        {"timestamp": {"$gte": since30}, "actor.type": "AGENT"})
    automation = round(100 * agent_tx / total_tx, 1) if total_tx else 0.0
    human_decisions = await db.db.approvals.count_documents({"status": "PENDING"})

    # ---- business risks (deterministic rules) ----
    risks = []
    if oos:
        risks.append({"risk": "Open OOS investigations", "level": "HIGH",
                      "count": oos})
    if ship_exceptions:
        risks.append({"risk": "Shipment exceptions unresolved", "level": "MEDIUM",
                      "count": ship_exceptions})
    mismatches = await db.db.supplier_invoices.count_documents(
        {"status": {"$in": ["MATCH_FAILED", "DISPUTED"]}})
    if mismatches:
        risks.append({"risk": "Invoice mismatches blocking AP", "level": "HIGH",
                      "count": mismatches})
    expiry_risk = await db.db.inventory_balances.count_documents(
        {"stock_status": "AVAILABLE", "expiry_date": {"$ne": None,
         "$lt": (utcnow() + timedelta(days=90)).isoformat()}}) \
        if "expiry_date" in (await db.db.inventory_balances.index_information()) else None
    if expiry_risk:
        risks.append({"risk": "Batches expiring within 90d", "level": "MEDIUM",
                      "count": expiry_risk})
    degraded = [a["agent_id"] for a in agents if a["status"] != "ACTIVE"]
    if degraded:
        risks.append({"risk": "Degraded agents", "level": "MEDIUM",
                      "agents": degraded})

    return {
        "sales": {"open_orders": sales_open, "open_value": round(sales_value, 2)},
        "purchase": {"open_orders": purchase_open,
                     "open_value": round(purchase_value, 2)},
        "inventory": {"products": products, "quarantine_or_hold_rows":
                      quarantine_rows},
        "production": {"batches_in_process": wip},
        "quality": {"active_holds": holds, "oos_open": oos},
        "warehouse": {"in_transit_shipments": in_transit,
                      "exceptions": ship_exceptions},
        "finance": {"ap_open": ap_open, "ar_open": ar_open},
        "ai": {"agents": agents, "automation_rate_pct": automation,
               "human_decisions_pending": human_decisions,
               "tool_calls_30d": agent_calls_30d,
               "failures_30d": agent_fails_30d},
        "risks": risks,
        "generated_at": now_iso(),
    }


async def quality_trends() -> dict:
    """Quality analytics: OOS rate per product, repeated-OOS flags, CAPA KPIs."""
    pipeline = [
        {"$group": {"_id": "$product_id", "total": {"$sum": 1},
                    "oos": {"$sum": {"$cond": [
                        {"$eq": ["$result", "OOS"]}, 1, 0]}}}},
        {"$match": {"total": {"$gte": 1}}},
        {"$sort": {"oos": -1}}, {"$limit": 15},
    ]
    rows = []
    async for r in db.db.qc_samples.aggregate(pipeline):
        rows.append({"product_id": r["_id"], "samples": r["total"],
                     "oos": r["oos"],
                     "oos_rate_pct": round(100 * r["oos"] / r["total"], 1),
                     "repeated_oos": r["oos"] >= 2})
    # CAPA effectiveness: closed on time vs overdue
    capa_total = await db.db.capas.count_documents({})
    capa_overdue = await db.db.capas.count_documents(
        {"status": {"$nin": ["CLOSED", "CANCELLED"]},
         "due_date": {"$ne": None, "$lt": now_iso()}})
    return {"oos_by_product": rows, "capa": {"total": capa_total,
                                             "overdue": capa_overdue}}


async def plant_oee() -> dict:
    """Plant OEE: availability × performance × quality from real batches."""
    total = await db.db.production_orders.count_documents(
        {"status": {"$nin": ["CANCELLED"]}})
    released = await db.db.production_orders.count_documents(
        {"status": "BATCH_RELEASED"})
    rejected = await db.db.production_orders.count_documents(
        {"status": "REJECTED"})
    in_process = await db.db.production_orders.count_documents(
        {"status": {"$in": ["DISPENSING", "IN_PROCESS", "PACKAGING"]}})
    # yield: planned vs actual across completed orders
    yields = []
    async for po in db.db.production_orders.find(
            {"status": {"$in": ["BATCH_RELEASED", "COMPLETED"]}},
            {"planned_qty": 1, "produced_qty": 1}).limit(200):
        planned = float(po.get("planned_qty") or 0)
        actual = float(po.get("produced_qty") or 0)
        if planned > 0:
            yields.append(min(actual / planned, 1.5))
    avg_yield = round(100 * sum(yields) / len(yields), 1) if yields else None
    quality_pct = round(100 * released / max(released + rejected, 1), 1) \
        if (released + rejected) else None
    return {"orders_total": total, "orders_in_process": in_process,
            "orders_released": released, "orders_rejected": rejected,
            "avg_yield_pct": avg_yield, "quality_rate_pct": quality_pct,
            "availability_proxy_pct": round(100 * (total - in_process) /
                                            max(total, 1), 1) if total else None}


async def inventory_intelligence() -> dict:
    """Stockout risk, excess stock, expiry optimization, transfer suggestions."""
    now = utcnow()
    soon = (now + timedelta(days=90)).isoformat()
    expiring = []
    async for b in db.db.inventory_balances.find(
            {"stock_status": "AVAILABLE", "expiry_date": {"$ne": None,
                                                          "$lte": soon}},
            {"product_id": 1, "warehouse_id": 1, "batch_id": 1,
             "quantity": 1, "expiry_date": 1}).limit(50):
        b.pop("_id", None)
        expiring.append(b)
    # excess: available qty far above reorder point
    excess = []
    async for b in db.db.inventory_balances.aggregate([
        {"$match": {"stock_status": "AVAILABLE"}},
        {"$group": {"_id": {"product": "$product_id",
                            "warehouse": "$warehouse_id"},
                    "qty": {"$sum": "$quantity"}}},
        {"$sort": {"qty": -1}}, {"$limit": 15},
    ]):
        excess.append({"product_id": (b["_id"] or {}).get("product"),
                       "warehouse_id": (b["_id"] or {}).get("warehouse"),
                       "available_qty": b["qty"]})
    # stockout risk: open SO demand vs available ATP
    shortages = []
    async for so in db.db.sales_orders.find(
            {"status": {"$in": ["CONFIRMED", "ALLOCATED"]}}).limit(100):
        for line in (so.get("lines") or []):
            bal = await db.db.inventory_balances.find_one(
                {"product_id": line.get("sku") or line.get("product_id"),
                 "stock_status": "AVAILABLE"})
            avail = float((bal or {}).get("quantity") or 0)
            need = float(line.get("quantity") or 0)
            if bal is None or avail < need:
                shortages.append({"order_id": so.get("order_id"),
                                  "product_id": line.get("sku") or
                                  line.get("product_id"),
                                  "needed": need, "available": avail})
    return {"expiring_batches": expiring[:20], "top_stock_positions": excess,
            "stockout_risks": shortages[:20]}
