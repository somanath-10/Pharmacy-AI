"""Warehouse, Inventory, Planning, Logistics routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request

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


# -------------------------------------------------------------------- inventory
@inventory_router.get("/balances")
async def balances(product_id: Optional[str] = Query(None),
                   warehouse_id: Optional[str] = Query(None),
                   principal: dict = Depends(get_current_principal)):
    return await inv_svc.balances(product_id, warehouse_id)


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
