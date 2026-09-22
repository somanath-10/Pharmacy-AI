"""Supply Planning: demand aggregation, MRP run, transfer-vs-produce-vs-buy,
safety stock & expiry optimization."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import NotFound, ValidationFailed
from app.core.events import bus
from app.domains.inventory import service as inventory


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


async def compute_supply_plan(product_id: str, horizon_days: int = 30,
                              actor: Optional[dict] = None) -> dict:
    """Demand vs supply for one product across the horizon."""
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
    forecast = float(product.get("forecast_qty") or 0)
    safety_stock = float(product.get("safety_stock") or 0)

    # ---- supply: on-hand (unblocked) + in-transit + open production
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

    net_requirement = max(demand + safety_stock - on_hand - in_transit -
                          open_production, 0)

    decision = {
        "product_id": product_id,
        "horizon_days": horizon_days,
        "demand": {"confirmed": demand, "forecast": forecast,
                   "safety_stock": safety_stock, "sources": demand_sources[:20]},
        "supply": {"on_hand": on_hand, "reserved": avail["reserved"],
                   "in_transit": in_transit, "open_production": open_production},
        "net_requirement": net_requirement,
    }

    if net_requirement <= 0:
        decision["action"] = "NONE"
        decision["reason"] = "Stock + inbound covers demand & safety stock"
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
    """MRP across active products → planning proposals."""
    skus = payload.get("skus")
    if not skus:
        skus = [p["sku"] async for p in db.db.products.find(
            {"status": "ACTIVE", "type": {"$in": ["FINISHED_GOOD", "TRADE_ITEM"]}},
            {"sku": 1}).limit(200)]
    proposals = []
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
    await audit("MRP_RUN", run_id, "COMPLETED", actor,
                details={"proposals": len(proposals)})
    return {"run_id": run_id, "proposals": proposals}


async def accept_proposal(proposal_id: str, actor: dict) -> dict:
    """Accepting a proposal executes it: transfer order / production order / PR."""
    prop = await db.db.planning_proposals.find_one({"proposal_id": proposal_id})
    if not prop:
        raise NotFound(f"Proposal {proposal_id} not found")
    if prop["status"] != "PROPOSED":
        raise ValidationFailed("Proposal already processed")
    result: Dict[str, Any] = {"proposal_id": proposal_id}
    if prop["action"] == "TRANSFER" and prop.get("options"):
        from app.domains.warehouse.service import create_transfer_order

        src = prop["options"][0]
        trf = await create_transfer_order({
            "from_warehouse": src["warehouse_id"],
            "to_warehouse": prop.get("to_warehouse", "WH-MAIN"),
            "lines": [{"sku": prop["product_id"], "quantity": prop["quantity"]}],
        }, actor)
        result["transfer_order"] = trf["transfer_id"]
    elif prop["action"] == "PRODUCE":
        from app.domains.production.service import create_production_order

        mpo = await create_production_order({
            "product_id": prop["product_id"],
            "batch_size": prop["quantity"],
        }, actor)
        result["production_order"] = mpo["order_id"]
    elif prop["action"] == "BUY":
        from app.domains.procurement.service import create_pr

        pr = await create_pr({
            "title": f"Replenishment {prop['product_id']}",
            "lines": [{"sku": prop["product_id"], "quantity": prop["quantity"]}],
            "department": "SUPPLY_CHAIN",
        }, actor or {"type": "AGENT", "id": "supply-chain-agent"})
        result["pr_id"] = pr["pr_id"]
    await db.db.planning_proposals.update_one(
        {"proposal_id": proposal_id},
        {"$set": {"status": "ACCEPTED", "accepted_at": now_iso(),
                  "result": result}})
    await audit("PLANNING_PROPOSAL", proposal_id, "ACCEPTED", actor,
                details=result)
    return result


async def list_proposals(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.planning_proposals.find(q).sort("created_at", -1).limit(200)]
