"""Supply Planning: demand aggregation, MRP run, transfer-vs-produce-vs-buy,
safety stock & expiry optimization."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.config import settings
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.policies import get_rule
from app.domains.inventory import service as inventory


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


async def create_planning_proposal(product_id: str, action: str, quantity: float,
                                   plan: dict, actor: Optional[dict],
                                   source: str = "AGENT") -> dict:
    """Single governed entry point for creating a planning proposal (P0 6):
    agents/supervisor call this instead of writing planning_proposals (a
    business collection) or touching the counters directly."""
    if action not in ("TRANSFER", "PRODUCE", "BUY"):
        raise ValidationFailed(f"Invalid proposal action {action}")
    existing = await db.db.planning_proposals.find_one(
        {"product_id": product_id, "status": "PROPOSED"})
    if existing:
        return _clean(dict(existing))  # dedupe: one open proposal per product
    prop = {
        "proposal_id": await _next_id("proposal", "PROP"),
        "product_id": product_id,
        "action": action,
        "quantity": quantity,
        "options": plan.get("options", []),
        "reason": plan.get("reason"),
        "source": source,
        "status": "PROPOSED",
        "created_by": actor or {"type": "SYSTEM", "id": "planning"},
        "created_at": now_iso(),
    }
    await db.db.planning_proposals.insert_one(prop)
    await bus.publish(f"planning.{action.lower()}_proposed",
                      {"proposal_id": prop["proposal_id"],
                       "product_id": product_id, "quantity": quantity}, actor)
    await audit("PLANNING_PROPOSAL", prop["proposal_id"], "CREATED", actor,
                details={"action": action, "qty": quantity, "source": source})
    return _clean(prop)


async def record_demand_history(product_id: str, quantity: float,
                                period: str, source: str = "SALES") -> dict:
    """Upsert monthly demand history used by the moving-average forecast."""
    await db.db.demand_history.update_one(
        {"product_id": product_id, "period": period},
        {"$set": {"quantity": float(quantity), "source": source,
                  "updated_at": now_iso()}},
        upsert=True)
    return {"product_id": product_id, "period": period, "quantity": quantity}


async def forecast_for(product_id: str, months: int = 3) -> dict:
    """Deterministic 3-month moving average from demand_history; falls back to
    the product master forecast_qty when no history exists."""
    rows = [_clean(dict(r)) async for r in db.db.demand_history.find(
        {"product_id": product_id}).sort("period", -1).limit(6)]
    product = await db.db.products.find_one({"sku": product_id}) or {}
    if len(rows) >= 2:
        avg = sum(float(r["quantity"]) for r in rows[:3]) / min(len(rows), 3)
        method = "MOVING_AVERAGE_3M"
        confidence = "HIGH" if len(rows) >= 3 else "MEDIUM"
    else:
        avg = float(product.get("forecast_qty") or 0)
        method = "MASTER_FORECAST"
        confidence = "LOW"
    return {"product_id": product_id, "method": method, "confidence": confidence,
            "monthly": round(avg, 2), "horizon_months": months,
            "forecast_qty": round(avg * months, 2)}


async def compute_supply_plan(product_id: str, horizon_days: int = 30,
                              actor: Optional[dict] = None) -> dict:
    """Demand vs supply for one product across the horizon.

    Decision ladder (Part 2):
      1. existing stock satisfies demand?                → NONE
      2. can another warehouse transfer?                 → TRANSFER
      3. can production satisfy (BOM)?                   → PRODUCE
      4. otherwise purchase required                     → BUY
    Anomaly detection flags demand spikes (forecast factor) for humans.
    """
    product = await db.db.products.find_one({"sku": product_id})
    if not product:
        raise NotFound(f"Product {product_id} not found")

    # ---- demand: confirmed orders (horizon) + forecast + safety stock top-up
    demand = 0.0
    demand_sources = []
    async for so in db.db.sales_orders.find({
            "status": {"$in": ["DRAFT", "PENDING_RX", "RX_APPROVED", "CONFIRMED",
                               "ALLOCATED", "PICKING", "PACKED"]}}):
        for line in so.get("lines", []):
            if line.get("sku") == product_id:
                demand += float(line["quantity"])
                demand_sources.append({"order_id": so["order_id"],
                                       "qty": float(line["quantity"])})
    fc = await forecast_for(product_id)
    forecast = fc["forecast_qty"]
    safety_stock = float(product.get("safety_stock") or 0)

    # ---- supply: on-hand (unblocked) + in-transit + open production + open POs
    avail = await inventory.availability(product_id)
    on_hand = avail["on_hand"]
    in_transit = avail["in_transit"]
    open_production = 0.0
    async for mpo in db.db.production_orders.find({
            "product_id": product_id,
            "status": {"$in": ["PLANNED", "RELEASED", "DISPENSING", "IN_PROCESS",
                               "PACKAGING"]}}):
        open_production += float(mpo.get("batch_size") or 0) - \
            float(mpo.get("actual_yield") or 0)
    open_po = 0.0
    async for po in db.db.purchase_orders.find({
            "lines.sku": product_id,
            "status": {"$in": ["APPROVED", "SENT", "ACKNOWLEDGED",
                               "PARTIALLY_RECEIVED"]}}):
        for line in po.get("lines", []):
            if line.get("sku") == product_id:
                open_po += float(line["quantity"]) - float(line.get("received_qty") or 0)

    # Forecast is a first-class demand component (Part 2): historical forecast
    # feeds the supply requirement exactly like confirmed orders do. It is NOT
    # double-counted when a forecast-driven order is already confirmed — the
    # forecast row represents expected demand not yet converted to orders.
    net_requirement = max(demand + forecast + safety_stock - on_hand -
                          in_transit - open_production - open_po, 0)

    # anomaly detection: demand far above forecast → human decision required
    anomaly_factor = float(await get_rule("demand_anomaly_factor",
                                          settings.DEMAND_ANOMALY_FACTOR))
    anomaly = None
    if fc["monthly"] > 0 and demand > fc["monthly"] * anomaly_factor:
        anomaly = {"type": "DEMAND_SPIKE", "demand": demand,
                   "expected": round(fc["monthly"] * anomaly_factor, 2),
                   "factor": anomaly_factor}

    decision = {
        "product_id": product_id,
        "horizon_days": horizon_days,
        "forecast": fc,
        "demand": {"confirmed": demand, "forecast": forecast,
                   "safety_stock": safety_stock, "sources": demand_sources[:20]},
        "supply": {"on_hand": on_hand, "reserved": avail["reserved"],
                   "in_transit": in_transit, "open_production": open_production,
                   "open_po": open_po},
        "net_requirement": net_requirement,
        "anomaly": anomaly,
    }

    if net_requirement <= 0:
        decision["action"] = "NONE"
        decision["reason"] = "Stock + inbound + open POs cover demand & safety stock"
    else:
        # transfer check: other warehouses with excess
        excess = await _find_excess(product_id, net_requirement)
        if excess:
            decision["action"] = "TRANSFER"
            decision["options"] = excess
            decision["reason"] = "Another warehouse holds excess stock"
        elif product.get("make_to_order", True) and await _has_bom(product_id):
            decision["action"] = "PRODUCE"
            decision["reason"] = "Production capacity preferred (make item)"
        else:
            decision["action"] = "BUY"
            decision["reason"] = "Procurement required (buy item)"

    # expiry-risk optimization note
    near_expiry = await inventory.batches_near_expiry(90)
    risk_batches = [b for b in near_expiry if b["product_id"] == product_id]
    if risk_batches:
        decision["expiry_risk"] = {
            "batches": [{"batch_id": b["batch_id"], "expiry": b["expiry_date"]}
                        for b in risk_batches],
            "recommendation": "Prioritize FEFO allocation & promotion before expiry",
        }
    return decision


async def _find_excess(product_id: str, needed: float) -> List[dict]:
    """Warehouses with unreserved on-hand above safety stock."""
    rows = [_clean(dict(r)) async for r in db.db.inventory_balances.find(
        {"product_id": product_id, "quantity": {"$gt": 0}, "batch_id": None})]
    product = await db.db.products.find_one({"sku": product_id}) or {}
    safety = float(product.get("safety_stock") or 0)
    reserved = {}
    async for r in db.db.reservations.find({"product_id": product_id,
                                            "status": "ACTIVE"}):
        reserved[r.get("warehouse_id") or "ANY"] = reserved.get(
            r.get("warehouse_id") or "ANY", 0) + float(r["quantity"])
    out = []
    for r in rows:
        free = float(r["quantity"]) - reserved.get(r["warehouse_id"], 0) - safety
        if free > 0:
            out.append({"warehouse_id": r["warehouse_id"],
                        "free_qty": round(min(free, needed), 2)})
    return out


async def _has_bom(product_id: str) -> bool:
    doc = await db.db.boms.find_one({"product_id": product_id,
                                     "status": "APPROVED"})
    return bool(doc)


async def run_mrp(payload: dict, actor: Optional[dict] = None) -> dict:
    """MRP across active products → proposals with routine auto-execution.

    Part 2: routine planning needs no human work — proposals under the
    auto-execution limit execute immediately (transfer/produce/PR); anomalies
    and large/strategic values escalate to the Human Decision Queue.
    """
    auto_limit = float(await get_rule("planning_auto_execution_limit",
                                      settings.PLANNING_AUTO_EXECUTION_LIMIT))
    skus = payload.get("skus")
    if not skus:
        skus = [p["sku"] async for p in db.db.products.find(
            {"status": "ACTIVE", "type": {"$in": ["FINISHED_GOOD", "TRADE_ITEM"]}},
            {"sku": 1}).limit(200)]
    proposals = []
    auto_executed = []
    escalated = []
    run_id = await _next_id("mrp_run", "MRP")
    for sku in skus:
        try:
            plan = await compute_supply_plan(sku, actor=actor)
        except NotFound:
            continue
        if plan["action"] in ("TRANSFER", "PRODUCE", "BUY"):
            prop = {
                "proposal_id": await _next_id("proposal", "PROP"),
                "run_id": run_id,
                "product_id": sku,
                "action": plan["action"],
                "quantity": plan["net_requirement"],
                "options": plan.get("options", []),
                "reason": plan["reason"],
                "anomaly": plan.get("anomaly"),
                "status": "PROPOSED",
                "created_at": now_iso(),
                "created_by": actor or {"type": "AGENT", "id": "supply-chain-agent"},
            }
            await db.db.planning_proposals.insert_one(prop)
            proposals.append(prop)
            await bus.publish(f"planning.{plan['action'].lower()}_proposed",
                              {"proposal_id": prop["proposal_id"],
                               "product_id": sku,
                               "quantity": plan["net_requirement"]}, actor)
            # ---- routine auto-execution vs human escalation
            needs_human = bool(plan.get("anomaly")) \
                or prop["quantity"] > auto_limit \
                or prop["action"] == "BUY" and await _is_strategic_product(sku)
            if needs_human:
                escalated.append(prop["proposal_id"])
            else:
                try:
                    result = await _execute_proposal(prop, actor)
                    await db.db.planning_proposals.update_one(
                        {"proposal_id": prop["proposal_id"]},
                        {"$set": {"status": "AUTO_EXECUTED",
                                  "accepted_at": now_iso(), "result": result}})
                    prop["status"] = "AUTO_EXECUTED"
                    auto_executed.append({"proposal_id": prop["proposal_id"],
                                          **result})
                except Exception as e:
                    await db.db.planning_proposals.update_one(
                        {"proposal_id": prop["proposal_id"]},
                        {"$set": {"status": "EXECUTION_FAILED",
                                  "result": {"error": str(e)[:200]}}})
    await audit("MRP_RUN", run_id, "COMPLETED", actor,
                details={"proposals": len(proposals),
                         "auto_executed": len(auto_executed),
                         "escalated": len(escalated)})
    return {"run_id": run_id, "proposals": proposals,
            "auto_executed": auto_executed, "escalated": escalated}


async def _is_strategic_product(sku: str) -> bool:
    p = await db.db.products.find_one({"sku": sku})
    return bool(p and p.get("strategic"))


async def _execute_proposal(prop: dict, actor: Optional[dict]) -> dict:
    act = actor or {"type": "AGENT", "id": "supply-chain-agent"}
    result: Dict[str, Any] = {"proposal_id": prop["proposal_id"]}
    if prop["action"] == "TRANSFER" and prop.get("options"):
        from app.domains.warehouse.service import create_transfer_order

        src = prop["options"][0]
        trf = await create_transfer_order({
            "from_warehouse": src["warehouse_id"],
            "to_warehouse": prop.get("to_warehouse", "WH-MAIN"),
            "lines": [{"sku": prop["product_id"], "quantity": prop["quantity"]}],
        }, act)
        result["transfer_order"] = trf["transfer_id"]
    elif prop["action"] == "PRODUCE":
        from app.domains.production.service import create_production_order

        mpo = await create_production_order({
            "product_id": prop["product_id"],
            "batch_size": prop["quantity"],
        }, act)
        result["production_order"] = mpo["order_id"]
    elif prop["action"] == "BUY":
        from app.domains.procurement.service import create_pr

        pr = await create_pr({
            "title": f"Replenishment {prop['product_id']}",
            "lines": [{"sku": prop["product_id"], "quantity": prop["quantity"]}],
            "department": "SUPPLY_CHAIN",
        }, act)
        result["pr_id"] = pr["pr_id"]
    return result


async def accept_proposal(proposal_id: str, actor: dict) -> dict:
    """Accepting a proposal executes it: transfer order / production order / PR."""
    prop = await db.db.planning_proposals.find_one({"proposal_id": proposal_id})
    if not prop:
        raise NotFound(f"Proposal {proposal_id} not found")
    if prop["status"] != "PROPOSED":
        raise ValidationFailed(f"Proposal already processed: {prop['status']}")
    result = await _execute_proposal(prop, actor)
    await db.db.planning_proposals.update_one(
        {"proposal_id": proposal_id},
        {"$set": {"status": "ACCEPTED", "accepted_at": now_iso(),
                  "result": result}})
    await audit("PLANNING_PROPOSAL", proposal_id, "ACCEPTED", actor,
                details=result)
    return result


async def decide_proposal(proposal_id: str, decision: str, actor: dict,
                          reason: Optional[str] = None) -> dict:
    """Decide on a planning proposal: ACCEPTED/APPROVED executes it, REJECTED closes it."""
    dec = (decision or "APPROVED").upper()
    if dec in ("ACCEPTED", "APPROVED"):
        return await accept_proposal(proposal_id, actor)
    elif dec in ("REJECTED", "DECLINED"):
        prop = await db.db.planning_proposals.find_one({"proposal_id": proposal_id})
        if not prop:
            raise NotFound(f"Proposal {proposal_id} not found")
        if prop["status"] != "PROPOSED":
            raise ValidationFailed(f"Proposal already processed: {prop['status']}")
        await db.db.planning_proposals.update_one(
            {"proposal_id": proposal_id},
            {"$set": {"status": "REJECTED", "rejected_at": now_iso(),
                      "reject_reason": reason or "User rejected"}})
        await audit("PLANNING_PROPOSAL", proposal_id, "REJECTED", actor,
                    details={"reason": reason})
        return {"proposal_id": proposal_id, "status": "REJECTED"}
    else:
        raise ValidationFailed(f"Invalid proposal decision: {decision}")


async def list_proposals(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.planning_proposals.find(q).sort("created_at", -1).limit(200)]


# ------------------------------------------------------------- stock plans
STOCK_PLAN_STATUSES = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "RUNNING",
                       "COMPLETED", "CANCELLED"]


async def create_stock_plan(payload: dict, actor: dict) -> dict:
    """Persistent demand/supply plan for a horizon (reference: stock plans)."""
    if not payload.get("name"):
        raise ValidationFailed("Stock plan needs name")
    if not payload.get("lines"):
        raise ValidationFailed("Stock plan needs lines [{sku, planned_qty}]")
    plan_id = await _next_id("stock_plan", "SPL")
    lines = []
    for l in payload["lines"]:
        lines.append({"sku": l["sku"],
                      "planned_qty": float(l.get("planned_qty") or 0),
                      "netted": False, "result": None})
    doc = {
        "plan_id": plan_id,
        "name": payload["name"],
        "horizon_days": int(payload.get("horizon_days", 30)),
        "lines": lines,
        "status": "DRAFT",
        "run": None,
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
    }
    await db.db.stock_plans.insert_one(doc)
    await audit("STOCK_PLAN", plan_id, "CREATED", actor)
    return _clean(doc)


async def submit_stock_plan(plan_id: str, actor: dict) -> dict:
    p = await _get_plan(plan_id)
    if p["status"] != "DRAFT":
        raise ConflictError(f"Plan not DRAFT: {p['status']}")
    await db.db.stock_plans.update_one(
        {"plan_id": plan_id},
        {"$set": {"status": "PENDING_APPROVAL", "updated_at": now_iso()}})
    return await _get_plan(plan_id)


async def approve_stock_plan(plan_id: str, actor: dict) -> dict:
    """Management authority approves the plan before it can drive proposals."""
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & MANAGEMENT_AUTHORITY_ROLES):
        from app.core.errors import PermissionDenied

        raise PermissionDenied("Stock plan approval requires management")
    p = await _get_plan(plan_id)
    if p["status"] != "PENDING_APPROVAL":
        raise ConflictError(f"Plan not PENDING_APPROVAL: {p['status']}")
    await db.db.stock_plans.update_one(
        {"plan_id": plan_id},
        {"$set": {"status": "APPROVED", "approved_by": actor,
                  "approved_at": now_iso(), "updated_at": now_iso()}})
    await audit("STOCK_PLAN", plan_id, "APPROVED", actor)
    return await _get_plan(plan_id)


async def run_stock_plan(plan_id: str, actor: dict) -> dict:
    """Execute an approved plan: net planned qty against live supply per line,
    create proposals/PRs for the residual net requirements."""
    p = await _get_plan(plan_id)
    if p["status"] != "APPROVED":
        raise ConflictError(f"Plan not APPROVED: {p['status']}")
    if p.get("run"):
        raise ConflictError("Plan already executed")
    results = []
    for line in p["lines"]:
        try:
            live = await compute_supply_plan(line["sku"], p["horizon_days"], actor)
            residual = max(float(line["planned_qty"]) - live["net_requirement"], 0)
            if live.get("action") not in ("NONE",) and residual <= 0:
                action = live["action"]
            else:
                action = live.get("action", "BUY") if live.get("net_requirement", 0) > 0 else "NONE"
            outcome = {"sku": line["sku"], "planned_qty": line["planned_qty"],
                       "live_net_requirement": live["net_requirement"],
                       "action": action}
            if live["net_requirement"] > 0:
                from app.domains.procurement.service import create_pr

                pr = await create_pr({
                    "title": f"Stock plan {p['plan_id']} {line['sku']}",
                    "lines": [{"sku": line["sku"],
                               "quantity": live["net_requirement"]}],
                    "department": "SUPPLY_CHAIN",
                }, actor or {"type": "AGENT", "id": "supply-chain-agent"})
                outcome["pr_id"] = pr["pr_id"]
            line["netted"] = True
            line["result"] = outcome
            results.append(outcome)
        except NotFound:
            results.append({"sku": line["sku"], "error": "product not found"})
    run = {"at": now_iso(), "results": results,
           "by": actor or {"type": "AGENT", "id": "supply-chain-agent"}}
    await db.db.stock_plans.update_one(
        {"plan_id": plan_id},
        {"$set": {"lines": p["lines"], "run": run, "status": "COMPLETED",
                  "updated_at": now_iso()}})
    await audit("STOCK_PLAN", plan_id, "RUN_COMPLETED", actor,
                details={"lines": len(results)})
    return await _get_plan(plan_id)


async def list_stock_plans(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.stock_plans.find(q).sort("created_at", -1).limit(200)]


async def _get_plan(plan_id: str) -> dict:
    doc = await db.db.stock_plans.find_one({"plan_id": plan_id})
    if not doc:
        raise NotFound(f"Stock plan {plan_id} not found")
    return doc
