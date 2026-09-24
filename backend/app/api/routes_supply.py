"""Warehouse, Inventory, Planning, Logistics routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request

from app.core.rbac import require_command, require_permission, require_read
from app.core.security import get_current_principal
from app.domains import inventory as inv_svc
from app.domains import logistics as log_svc
from app.domains import planning as plan_svc
from app.domains import warehouse as wh_svc

warehouse_router = APIRouter(prefix="/api/warehouse", tags=["warehouse"])
inventory_router = APIRouter(prefix="/api/inventory", tags=["inventory"])
planning_router = APIRouter(prefix="/api/planning", tags=["planning"])
logistics_router = APIRouter(prefix="/api/logistics", tags=["logistics"])


# -------------------------------------------------------------------- warehouse
@warehouse_router.get("/grns")
async def list_grns(status: Optional[str] = Query(None),
                    principal: dict = Depends(get_current_principal)):
    return await wh_svc.list_grns(status)


@warehouse_router.get("/grns/{grn_id}")
async def get_grn(grn_id: str, principal: dict = Depends(get_current_principal)):
    return await wh_svc.get_grn(grn_id)


@warehouse_router.post("/grn")
async def create_grn(request: Request,
                     payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None) or request.headers.get("idempotency-key")
    return await wh_svc.create_grn(payload, principal, idem)


@warehouse_router.post("/grn/{grn_id}/putaway")
async def putaway(grn_id: str, payload: dict = Body(default={}),
                  principal: dict = Depends(get_current_principal)):
    return await wh_svc.putaway(grn_id, principal, payload.get("locations"))


@warehouse_router.get("/health")
async def wh_health(principal: dict = Depends(get_current_principal)):
    return await wh_svc.warehouse_health()


@warehouse_router.get("/picks")
async def list_picks(status: Optional[str] = Query(None),
                     principal: dict = Depends(get_current_principal)):
    return await wh_svc.list_pick_tasks(status)


@warehouse_router.post("/pick")
async def pick(payload: dict = Body(...),
               principal: dict = Depends(get_current_principal)):
    return await wh_svc.pick(payload, principal)


@warehouse_router.post("/pick/{task_id}/confirm")
async def confirm_pick(task_id: str, payload: dict = Body(default={}),
                       principal: dict = Depends(get_current_principal)):
    return await wh_svc.confirm_pick(task_id, principal,
                                     payload.get("scanned_batch_id"))


@warehouse_router.post("/pack")
async def pack(payload: dict = Body(...),
               principal: dict = Depends(get_current_principal)):
    return await wh_svc.pack(payload, principal)


@warehouse_router.post("/transfers")
async def create_transfer(payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    return await wh_svc.create_transfer_order(payload, principal)


@warehouse_router.get("/transfers")
async def list_transfers(status: Optional[str] = Query(None),
                         from_warehouse: Optional[str] = Query(None),
                         to_warehouse: Optional[str] = Query(None),
                         principal: dict = Depends(get_current_principal)):
    return await wh_svc.list_transfers(status, from_warehouse, to_warehouse)


@warehouse_router.post("/transfers/{transfer_id}/execute")
async def execute_transfer(transfer_id: str,
                           principal: dict = Depends(get_current_principal)):
    return await wh_svc.execute_transfer(transfer_id, principal)


@warehouse_router.post("/cycle-count")
async def cycle_count(payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await inv_svc.cycle_count(payload["warehouse_id"], payload["product_id"],
                                     payload.get("batch_id"),
                                     float(payload["counted_qty"]), principal)


@warehouse_router.post("/cycle-count/{count_id}/reverse")
async def reverse_cycle_count(count_id: str,
                              principal: dict = Depends(get_current_principal)):
    return await inv_svc.reverse_cycle_count(count_id, principal)


# -------------------------------------------------------------------- inventory
@inventory_router.get("/balances")
async def balances(product_id: Optional[str] = Query(None),
                   warehouse_id: Optional[str] = Query(None),
                   stock_status: Optional[str] = Query(None),
                   batch_id: Optional[str] = Query(None),
                   principal: dict = Depends(require_read("inventory"))):
    # site-scoped read (GAP-2): non-super users only see warehouses in scope
    from app.core.rbac import site_scope

    scope = site_scope(principal)
    if scope is not None:
        from app.core.database import db

        allowed = {r["code"] async for r in db.db.warehouses.find(
            {"site_id": {"$in": list(scope)}})}
        if warehouse_id and warehouse_id not in allowed:
            from app.core.errors import PermissionDenied

            raise PermissionDenied(f"Warehouse {warehouse_id} outside site scope")
        if not warehouse_id:
            return [b for b in await inv_svc.balances(
                product_id, None, stock_status=stock_status, batch_id=batch_id)
                if b["warehouse_id"] in allowed]
    return await inv_svc.balances(product_id, warehouse_id,
                                  stock_status=stock_status, batch_id=batch_id)


@inventory_router.get("/status-summary/{product_id}")
async def status_summary(product_id: str,
                         warehouse_id: Optional[str] = Query(None),
                         principal: dict = Depends(get_current_principal)):
    return await inv_svc.status_summary(product_id, warehouse_id)


@inventory_router.get("/availability/{product_id}")
async def availability(product_id: str,
                       principal: dict = Depends(get_current_principal)):
    return await inv_svc.availability(product_id)


@inventory_router.get("/batches")
async def batches(product_id: Optional[str] = Query(None),
                  principal: dict = Depends(get_current_principal)):
    rows = []
    from app.core.database import db

    q = {"product_id": product_id} if product_id else {}
    async for b in db.db.batches.find(q):
        b.pop("_id", None)
        rows.append(b)
    return rows


@inventory_router.get("/batches/near-expiry")
async def near_expiry(days: int = Query(90),
                      principal: dict = Depends(get_current_principal)):
    return await inv_svc.batches_near_expiry(days)


@inventory_router.get("/movements")
async def movements(product_id: Optional[str] = Query(None),
                    batch_id: Optional[str] = Query(None),
                    warehouse_id: Optional[str] = Query(None),
                    principal: dict = Depends(get_current_principal)):
    return await inv_svc.movements(product_id, batch_id, warehouse_id)


@inventory_router.get("/traceability/{batch_id}")
async def traceability(batch_id: str,
                       principal: dict = Depends(get_current_principal)):
    return await inv_svc.traceability(batch_id)


@inventory_router.post("/reservations")
async def reserve(payload: dict = Body(...),
                  principal: dict = Depends(get_current_principal)):
    return await inv_svc.reserve(payload["product_id"], float(payload["quantity"]),
                                 payload.get("reference_type", "MANUAL"),
                                 payload["reference_id"],
                                 payload.get("warehouse_id"), principal)


@inventory_router.get("/reservations")
async def list_reservations(reference_type: Optional[str] = Query(None),
                            reference_id: Optional[str] = Query(None),
                            product_id: Optional[str] = Query(None),
                            status: Optional[str] = Query(None),
                            principal: dict = Depends(require_read("inventory"))):
    return await inv_svc.list_reservations(reference_type, reference_id, product_id, status)


@inventory_router.post("/batches/{batch_id}/block")
async def block_batch(batch_id: str, payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await inv_svc.block_batch(batch_id, payload.get("reason", "MANUAL"),
                                     principal)


@inventory_router.post("/batches/{batch_id}/unblock")
async def unblock_batch(batch_id: str, payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await inv_svc.unblock_batch(batch_id, principal,
                                       payload.get("reason", ""))


# --------------------------------------------------------------------- planning
@planning_router.get("/supply/{product_id}")
async def supply_plan(product_id: str, horizon_days: int = Query(30),
                      principal: dict = Depends(get_current_principal)):
    return await plan_svc.compute_supply_plan(product_id, horizon_days, principal)


@planning_router.post("/demand-history")
async def record_demand_history(payload: dict = Body(...),
                                principal: dict = Depends(require_permission("plan:write"))):
    """Record monthly demand history for the forecast (Part 2)."""
    return await plan_svc.record_demand_history(
        payload["product_id"], float(payload["quantity"]),
        payload["period"], payload.get("source", "SALES"))


@planning_router.get("/forecast/{product_id}")
async def forecast(product_id: str,
                   principal: dict = Depends(get_current_principal)):
    return await plan_svc.forecast_for(product_id)


@planning_router.post("/demand-history/import")
async def import_demand_history(payload: dict = Body(...),
                                principal: dict = Depends(require_permission("plan:write"))):
    """Bulk demand history: [{product_id, period, quantity}]."""
    out = []
    for row in payload.get("rows", []):
        out.append(await plan_svc.record_demand_history(
            row["product_id"], float(row["quantity"]), row["period"],
            row.get("source", "IMPORT")))
    return {"imported": len(out), "rows": out}


@planning_router.post("/mrp/run")
async def run_mrp(payload: dict = Body(default={}),
                  principal: dict = Depends(get_current_principal)):
    return await plan_svc.run_mrp(payload, principal)


@planning_router.get("/proposals")
async def list_proposals(status: Optional[str] = Query(None),
                         principal: dict = Depends(get_current_principal)):
    return await plan_svc.list_proposals(status)


@planning_router.post("/proposals/{proposal_id}/accept")
async def accept_proposal(proposal_id: str,
                          principal: dict = Depends(get_current_principal)):
    return await plan_svc.accept_proposal(proposal_id, principal)


# ------------------------------------------------------------------ stock plans
@planning_router.post("/stock-plans")
async def create_stock_plan(payload: dict = Body(...),
                            principal: dict = Depends(get_current_principal)):
    return await plan_svc.create_stock_plan(payload, principal)


@planning_router.get("/stock-plans")
async def list_stock_plans(status: Optional[str] = Query(None),
                           principal: dict = Depends(get_current_principal)):
    return await plan_svc.list_stock_plans(status)


@planning_router.post("/stock-plans/{plan_id}/submit")
async def submit_stock_plan(plan_id: str,
                            principal: dict = Depends(get_current_principal)):
    return await plan_svc.submit_stock_plan(plan_id, principal)


@planning_router.post("/stock-plans/{plan_id}/approve")
async def approve_stock_plan(plan_id: str,
                             principal: dict = Depends(get_current_principal)):
    return await plan_svc.approve_stock_plan(plan_id, principal)


@planning_router.post("/stock-plans/{plan_id}/run")
async def run_stock_plan(plan_id: str,
                         principal: dict = Depends(get_current_principal)):
    return await plan_svc.run_stock_plan(plan_id, principal)


# -------------------------------------------------------------------- logistics
@logistics_router.get("/inbound/asns")
async def list_asns(vendor_id: Optional[str] = Query(None),
                    principal: dict = Depends(get_current_principal)):
    return await log_svc.list_inbound(vendor_id)


@logistics_router.post("/inbound/asns")
async def create_asn(payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    return await log_svc.create_asn(payload, principal)


@logistics_router.post("/inbound/asns/{asn_id}/arrive")
async def arrive_asn(asn_id: str, payload: dict = Body(default={}),
                     principal: dict = Depends(get_current_principal)):
    return await log_svc.arrive_asn(asn_id, payload, principal)


@logistics_router.get("/shipments")
async def list_shipments(status: Optional[str] = Query(None),
                         principal: dict = Depends(get_current_principal)):
    return await log_svc.list_shipments(status)


@logistics_router.get("/shipments/{shipment_id}")
async def get_shipment(shipment_id: str,
                       principal: dict = Depends(get_current_principal)):
    return await log_svc.get_shipment(shipment_id)


@logistics_router.post("/shipments")
async def plan_shipment(payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await log_svc.plan_shipment(payload, principal)


@logistics_router.post("/shipments/{shipment_id}/split")
async def split_shipment(shipment_id: str, payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await log_svc.split_shipment(shipment_id, payload, principal)


@logistics_router.post("/shipments/{shipment_id}/dispatch")
async def dispatch_shipment(shipment_id: str, payload: dict = Body(default={}),
                            principal: dict = Depends(get_current_principal)):
    return await log_svc.dispatch_shipment(shipment_id, payload, principal)


@logistics_router.post("/shipments/{shipment_id}/track")
async def track_shipment(shipment_id: str, payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await log_svc.track(shipment_id, payload.get("event", "NOTE"),
                               payload, principal)


@logistics_router.post("/shipments/{shipment_id}/pod")
async def capture_pod(shipment_id: str, payload: dict = Body(default={}),
                      principal: dict = Depends(get_current_principal)):
    return await log_svc.capture_pod(shipment_id, payload, principal)


@logistics_router.post("/carriers")
async def create_carrier(payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await log_svc.create_carrier(payload, principal)


@logistics_router.get("/carriers")
async def list_carriers(status: Optional[str] = Query(None),
                        principal: dict = Depends(get_current_principal)):
    return await log_svc.list_carriers(status)


# --------------------------------------------- warehouse structure & inbound flow
@warehouse_router.post("/locations")
async def create_location(payload: dict = Body(...),
                          principal: dict = Depends(require_permission("putaway:write"))):
    return await wh_svc.create_location(payload, principal)


@warehouse_router.get("/locations")
async def list_locations(warehouse_id: str,
                         zone: Optional[str] = Query(None),
                         principal: dict = Depends(require_read("grn"))):
    return await wh_svc.list_locations(warehouse_id, zone)


@warehouse_router.post("/gate-entry")
async def gate_entry(payload: dict = Body(...),
                     principal: dict = Depends(require_permission("gate:write"))):
    return await wh_svc.gate_entry(payload, principal)


@warehouse_router.post("/gate-entry/{gate_entry_id}/unload")
async def confirm_unload(gate_entry_id: str, payload: dict = Body(default={}),
                         principal: dict = Depends(require_permission("gate:write"))):
    return await wh_svc.confirm_unload(gate_entry_id, payload, principal)


# ------------------------------------------------------------------ replenishment
@warehouse_router.post("/replenishment")
async def create_replenishment(payload: dict = Body(...),
                               principal: dict = Depends(require_permission("putaway:write"))):
    return await wh_svc.create_replenishment_task(payload, principal)


@warehouse_router.post("/replenishment/{task_id}/confirm")
async def confirm_replenishment(task_id: str, payload: dict = Body(default={}),
                                principal: dict = Depends(require_permission("putaway:write"))):
    return await wh_svc.confirm_replenishment(task_id, principal,
                                              payload.get("scanned_location"))


# --------------------------------------------------------------- outbound picking
@warehouse_router.post("/pick/{task_id}/short")
async def short_pick(task_id: str, payload: dict = Body(...),
                     principal: dict = Depends(require_permission("pick:write"))):
    return await wh_svc.short_pick(task_id, principal,
                                   payload.get("reason", "SHORT_PICK"))


@warehouse_router.post("/stage")
async def stage(payload: dict = Body(...),
                principal: dict = Depends(require_permission("pack:write"))):
    return await wh_svc.stage_order(payload, principal)


# ----------------------------------------------------------- transfer state steps
@warehouse_router.post("/transfers/{transfer_id}/approve")
async def approve_transfer(transfer_id: str,
                           principal: dict = Depends(require_permission("transfer:write"))):
    return await wh_svc.approve_transfer(transfer_id, principal)


@warehouse_router.post("/transfers/{transfer_id}/pick")
async def pick_transfer(transfer_id: str,
                        principal: dict = Depends(require_permission("transfer:write"))):
    return await wh_svc.pick_transfer(transfer_id, principal)


@warehouse_router.post("/transfers/{transfer_id}/dispatch")
async def dispatch_transfer(transfer_id: str,
                            principal: dict = Depends(require_permission("dispatch:write"))):
    return await wh_svc.dispatch_transfer(transfer_id, principal)


@warehouse_router.post("/transfers/{transfer_id}/receive")
async def receive_transfer(transfer_id: str,
                           principal: dict = Depends(require_permission("grn:write"))):
    return await wh_svc.receive_transfer(transfer_id, principal)


# ------------------------------------------------------------- counts & adjustment
@warehouse_router.get("/cycle-counts")
async def list_cycle_counts(status: Optional[str] = Query(None),
                            principal: dict = Depends(require_read("grn"))):
    q = {"status": status} if status else {}
    from app.core.database import db as _db

    out = []
    async for c in _db.db.cycle_counts.find(q).sort("created_at", -1).limit(200):
        c.pop("_id", None)
        out.append(c)
    return out


@warehouse_router.post("/cycle-count/{count_id}/adjust")
async def post_adjustment(count_id: str, payload: dict = Body(...),
                          principal: dict = Depends(require_command("inventory:adjust"))):
    approved_by = payload.get("approved_by") or (
        principal if payload.get("self_approved") else None)
    return await inv_svc.post_stock_adjustment(count_id, principal,
                                               payload.get("reason", ""),
                                               approved_by)


# ------------------------------------------------------- inventory: bins & status
@inventory_router.post("/move-location")
async def move_location(payload: dict = Body(...),
                        principal: dict = Depends(require_permission("transfer:write"))):
    return await inv_svc.move_location(
        payload["product_id"], payload["warehouse_id"], payload.get("batch_id"),
        payload.get("from_location"), payload["to_location"],
        float(payload["quantity"]), principal,
        stock_status=payload.get("stock_status", "AVAILABLE"),
        uom=payload.get("uom", "BOX"))


@inventory_router.post("/status-change")
async def change_stock_status(payload: dict = Body(...),
                              principal: dict = Depends(require_command("qa:hold"))):
    return await inv_svc.change_stock_status(
        payload["product_id"], payload["warehouse_id"], payload.get("batch_id"),
        payload["from_status"], payload["to_status"], float(payload["quantity"]),
        principal, payload.get("reference_type", "MANUAL"),
        payload.get("reference_id", ""), payload.get("uom", "BOX"))


@inventory_router.post("/expire-sweep")
async def expire_sweep(principal: dict = Depends(require_command("qa:hold"))):
    return {"expired": await inv_svc.expire_due_batches(principal)}


# ------------------------------------------------- logistics: fleet & exceptions
@logistics_router.post("/vehicles")
async def create_vehicle(payload: dict = Body(...),
                         principal: dict = Depends(require_permission("carrier:write"))):
    return await log_svc.create_vehicle(payload, principal)


@logistics_router.post("/drivers")
async def create_driver(payload: dict = Body(...),
                        principal: dict = Depends(require_permission("carrier:write"))):
    return await log_svc.create_driver(payload, principal)


@logistics_router.post("/routes")
async def create_route(payload: dict = Body(...),
                       principal: dict = Depends(require_permission("carrier:write"))):
    return await log_svc.create_route(payload, principal)


@logistics_router.post("/appointments")
async def schedule_appointment(payload: dict = Body(...),
                               principal: dict = Depends(require_permission("inbound:write"))):
    return await log_svc.schedule_appointment(payload, principal)


@logistics_router.post("/shipments/optimize")
async def optimize_route(payload: dict = Body(...),
                         principal: dict = Depends(require_permission("dispatch:write"))):
    return await log_svc.optimize_route_plan(payload.get("shipment_ids", []),
                                             principal)


@logistics_router.post("/shipments/{shipment_id}/delivery-failed")
async def delivery_failed(shipment_id: str, payload: dict = Body(default={}),
                          principal: dict = Depends(require_permission("dispatch:write"))):
    return await log_svc.mark_delivery_failed(shipment_id, payload, principal)


@logistics_router.post("/shipments/{shipment_id}/reattempt")
async def reattempt_delivery(shipment_id: str, payload: dict = Body(default={}),
                             principal: dict = Depends(require_permission("dispatch:write"))):
    return await log_svc.reattempt_delivery(shipment_id, payload, principal)


@logistics_router.post("/shipments/{shipment_id}/temperature")
async def capture_temperature(shipment_id: str, payload: dict = Body(...),
                              principal: dict = Depends(require_permission("dispatch:write"))):
    return await log_svc.capture_transport_temperature(shipment_id, payload,
                                                       principal)


@logistics_router.get("/vehicles")
async def list_vehicles(principal: dict = Depends(get_current_principal)):
    from app.core.database import db as _db

    out = []
    async for v in _db.db.vehicles.find({}).limit(300):
        v.pop("_id", None)
        out.append(v)
    return out


@logistics_router.get("/drivers")
async def list_drivers(principal: dict = Depends(get_current_principal)):
    from app.core.database import db as _db

    out = []
    async for d in _db.db.drivers.find({}).limit(300):
        d.pop("_id", None)
        out.append(d)
    return out
