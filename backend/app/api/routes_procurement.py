"""Vendors, Vendor Portal, Sourcing, Procurement routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from app.core.rbac import require_permission
from app.core.security import get_current_principal
from app.domains import procurement as procurement_svc
from app.domains import sourcing as sourcing_svc
from app.domains import vendors as vendors_svc

vendors_router = APIRouter(prefix="/api/vendors", tags=["vendors"])
portal_router = APIRouter(prefix="/api/portal/vendor", tags=["vendor-portal"])
sourcing_router = APIRouter(prefix="/api/sourcing", tags=["sourcing"])
procurement_router = APIRouter(prefix="/api/procurement", tags=["procurement"])


# --------------------------------------------------------------------- vendors
@vendors_router.get("")
async def list_vendors(status: Optional[str] = Query(None),
                       principal: dict = Depends(get_current_principal)):
    return await vendors_svc.list_vendors(status)


@vendors_router.post("")
async def create_vendor(payload: dict = Body(...),
                        principal: dict = Depends(require_permission("vendor:write"))):
    return await vendors_svc.create_vendor(payload, principal)


@vendors_router.get("/{vendor_id}")
async def get_vendor(vendor_id: str,
                     principal: dict = Depends(get_current_principal)):
    return await vendors_svc.get_vendor(vendor_id)


@vendors_router.post("/{vendor_id}/documents")
async def add_vendor_document(vendor_id: str, payload: dict = Body(...),
                              principal: dict = Depends(require_permission("vendor:document:write"))):
    return await vendors_svc.add_document(vendor_id, payload, principal)


@vendors_router.post("/{vendor_id}/qualify")
async def qualify_vendor(vendor_id: str, payload: dict = Body(...),
                         principal: dict = Depends(require_permission("vendor:write"))):
    return await vendors_svc.qualify(vendor_id, payload.get("kind", "commercial"),
                                     payload, principal)


@vendors_router.post("/{vendor_id}/approve")
async def approve_vendor(vendor_id: str, payload: dict = Body(default={}),
                         principal: dict = Depends(require_permission("vendor:approve"))):
    return await vendors_svc.approve_vendor(
        vendor_id, principal, payload.get("reason", ""))


@vendors_router.post("/{vendor_id}/suspend")
async def suspend_vendor(vendor_id: str, payload: dict = Body(...),
                         principal: dict = Depends(require_permission("vendor:approve"))):
    return await vendors_svc.suspend_vendor(vendor_id, principal,
                                            payload.get("reason", ""))


@vendors_router.post("/{vendor_id}/bank-details")
async def change_bank(vendor_id: str, payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await vendors_svc.update_bank_details(vendor_id, payload, principal)


@vendors_router.get("/{vendor_id}/performance")
async def vendor_performance(vendor_id: str,
                             principal: dict = Depends(get_current_principal)):
    return await vendors_svc.performance(vendor_id)


@vendors_router.get("/{vendor_id}/360")
async def vendor_360(vendor_id: str,
                     principal: dict = Depends(get_current_principal)):
    return await vendors_svc.vendor_360(vendor_id)


# --------------------------------------------------------------- vendor portal
@portal_router.get("/context")
async def portal_context(principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_context(principal)


@portal_router.get("/rfqs")
async def portal_rfqs(principal: dict = Depends(get_current_principal)):
    vendor = await vendors_svc.portal_context(principal)
    events = await sourcing_svc.list_events()
    return [e for e in events
            if vendor["vendor_id"] in (e.get("published_to") or [])
            or vendor["vendor_id"] in (e.get("invited_vendors") or [])]


@portal_router.post("/rfqs/{event_id}/bids")
async def portal_bid(event_id: str, payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_submit_bid(principal["vendor_id"], event_id,
                                               payload, principal)


@portal_router.get("/pos")
async def portal_pos(principal: dict = Depends(get_current_principal)):
    vendor = await vendors_svc.portal_context(principal)
    return await procurement_svc.list_pos(vendor_id=vendor["vendor_id"])


@portal_router.post("/pos/{po_id}/ack")
async def portal_ack(po_id: str, payload: dict = Body(default={}),
                     principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_ack_po(principal["vendor_id"], po_id,
                                           payload, principal)


@portal_router.post("/asns")
async def portal_asn(payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_create_asn(principal["vendor_id"], payload,
                                               principal)


@portal_router.post("/invoices")
async def portal_invoice(payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_submit_invoice(principal["vendor_id"],
                                                   payload, principal)


# -------------------------------------------------------------------- sourcing
@sourcing_router.get("/events")
async def list_events(status: Optional[str] = Query(None),
                      principal: dict = Depends(get_current_principal)):
    return await sourcing_svc.list_events(status)


@sourcing_router.post("/events")
async def create_event(payload: dict = Body(...),
                       principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.create_event(payload, principal)


@sourcing_router.get("/events/{event_id}")
async def get_event(event_id: str,
                    principal: dict = Depends(get_current_principal)):
    return await sourcing_svc.get_event(event_id)


@sourcing_router.post("/events/{event_id}/publish")
async def publish_event(event_id: str,
                        principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.publish_event(event_id, principal)


@sourcing_router.post("/events/{event_id}/bids")
async def submit_bid(event_id: str, payload: dict = Body(...),
                     principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.submit_bid(event_id, payload, principal)


@sourcing_router.get("/events/{event_id}/bids")
async def list_bids(event_id: str,
                    principal: dict = Depends(get_current_principal)):
    return await sourcing_svc.list_bids(event_id)


@sourcing_router.post("/events/{event_id}/auction")
async def create_auction(event_id: str, payload: dict = Body(...),
                         principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.create_auction(event_id, payload, principal)


@sourcing_router.post("/auctions/{auction_id}/start")
async def start_auction(auction_id: str,
                        principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.start_auction(auction_id, principal)


@sourcing_router.post("/auctions/{auction_id}/bid")
async def auction_bid(auction_id: str, payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await sourcing_svc.auction_bid(auction_id, payload["vendor_id"],
                                          float(payload["price"]), principal)


@sourcing_router.post("/auctions/{auction_id}/close")
async def close_auction(auction_id: str,
                        principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.close_auction(auction_id, principal)


@sourcing_router.post("/awards")
async def recommend_award(payload: dict = Body(...),
                          principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.recommend_award(payload["event_id"], payload,
                                              principal)


@sourcing_router.post("/awards/{rec_id}/approve")
async def approve_award(rec_id: str, payload: dict = Body(default={}),
                        principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.approve_award(rec_id, principal,
                                            payload.get("reason", ""))


@sourcing_router.get("/contracts")
async def list_contracts(vendor_id: Optional[str] = Query(None),
                         principal: dict = Depends(get_current_principal)):
    return await sourcing_svc.list_contracts(vendor_id)


@sourcing_router.post("/contracts")
async def create_contract(payload: dict = Body(...),
                          principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.create_contract(payload.get("event_id"), payload,
                                              principal)


@sourcing_router.post("/contracts/{contract_id}/release-po")
async def release_contract_po(contract_id: str, payload: dict = Body(...),
                              principal: dict = Depends(require_permission("po:write"))):
    return await sourcing_svc.contract_release(contract_id, payload.get("lines", []),
                                               payload, principal)


# ----------------------------------------------------------------- procurement
@procurement_router.get("/prs")
async def list_prs(status: Optional[str] = Query(None),
                   principal: dict = Depends(get_current_principal)):
    return await procurement_svc.list_prs(status)


@procurement_router.post("/prs")
async def create_pr(payload: dict = Body(...),
                    principal: dict = Depends(require_permission("pr:write"))):
    idem = payload.pop("idempotency_key", None)
    return await procurement_svc.create_pr(payload, principal, idem)


@procurement_router.post("/prs/{pr_id}/submit")
async def submit_pr(pr_id: str,
                    principal: dict = Depends(require_permission("pr:write"))):
    return await procurement_svc.submit_pr(pr_id, principal)


@procurement_router.post("/prs/{pr_id}/convert")
async def convert_pr(pr_id: str, payload: dict = Body(default={}),
                     principal: dict = Depends(require_permission("po:write"))):
    return await procurement_svc.convert_pr(pr_id, payload, principal)


@procurement_router.get("/pos")
async def list_pos(status: Optional[str] = Query(None),
                   vendor_id: Optional[str] = Query(None),
                   principal: dict = Depends(get_current_principal)):
    return await procurement_svc.list_pos(status, vendor_id)


@procurement_router.get("/pos/{po_id}")
async def get_po(po_id: str, principal: dict = Depends(get_current_principal)):
    return await procurement_svc.get_po(po_id)


@procurement_router.get("/pos/{po_id}/workflow")
async def po_workflow(po_id: str,
                      principal: dict = Depends(get_current_principal)):
    return await procurement_svc.workflow_view(po_id)


@procurement_router.post("/pos")
async def create_po(payload: dict = Body(...),
                    principal: dict = Depends(require_permission("po:write"))):
    idem = payload.pop("idempotency_key", None)
    return await procurement_svc.create_po(payload, principal, idem)


@procurement_router.post("/pos/{po_id}/submit")
async def submit_po(po_id: str,
                    principal: dict = Depends(require_permission("po:write"))):
    return await procurement_svc.submit_po_for_approval(po_id, principal)


@procurement_router.post("/pos/{po_id}/send")
async def send_po(po_id: str,
                  principal: dict = Depends(require_permission("po:send"))):
    return await procurement_svc.send_po(po_id, principal)


@procurement_router.post("/pos/{po_id}/ack")
async def ack_po(po_id: str, payload: dict = Body(default={}),
                 principal: dict = Depends(require_permission("po:ack"))):
    return await procurement_svc.acknowledge_po(po_id, payload, principal)


@procurement_router.post("/pos/{po_id}/close")
async def close_po(po_id: str, payload: dict = Body(default={}),
                   principal: dict = Depends(require_permission("po:write"))):
    return await procurement_svc.close_po(po_id, principal,
                                          payload.get("reason", "Completed"))
