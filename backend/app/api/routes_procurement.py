"""Vendors, Vendor Portal, Sourcing, Procurement routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.core.rbac import require_permission, require_read
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
                       principal: dict = Depends(require_read("vendor"))):
    return await vendors_svc.list_vendors(status)


@vendors_router.post("")
async def create_vendor(payload: dict = Body(...),
                        principal: dict = Depends(require_permission("vendor:write"))):
    return await vendors_svc.create_vendor(payload, principal)


@vendors_router.get("/{vendor_id}")
async def get_vendor(vendor_id: str,
                     principal: dict = Depends(get_current_principal)):
    from app.core.rbac import assert_party, can_read

    assert_party(principal, vendor_id=vendor_id)
    if not can_read(principal.get("roles", []), "vendor"):
        raise HTTPException(403, "No read grant for vendors")
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
    from app.core.rbac import authorize_command

    authorize_command(principal, "vendor:approve")
    return await vendors_svc.approve_vendor(
        vendor_id, principal, payload.get("reason", ""))


@vendors_router.post("/{vendor_id}/suspend")
async def suspend_vendor(vendor_id: str, payload: dict = Body(...),
                         principal: dict = Depends(require_permission("vendor:approve"))):
    return await vendors_svc.suspend_vendor(vendor_id, principal,
                                            payload.get("reason", ""))


@vendors_router.post("/{vendor_id}/qualifications")
async def propose_qualification(vendor_id: str, payload: dict = Body(...),
                                principal: dict = Depends(get_current_principal)):
    payload = {**payload, "vendor_id": vendor_id}
    return await vendors_svc.propose_qualification(payload, principal)


@vendors_router.get("/{vendor_id}/qualifications")
async def list_qualifications(vendor_id: str,
                              principal: dict = Depends(get_current_principal)):
    return await vendors_svc.list_qualifications(vendor_id)


@vendors_router.post("/qualifications/{qualification_id}/decide")
async def decide_qualification(qualification_id: str, payload: dict = Body(...),
                               principal: dict = Depends(get_current_principal)):
    return await vendors_svc.decide_qualification(
        qualification_id, payload["decision"], principal,
        payload.get("notes", ""))


@vendors_router.post("/{vendor_id}/bank-details")
async def change_bank(vendor_id: str, payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    """Two-step: no otp → sends OTP to vendor contact; with otp → files the
    change into the SECURITY_FRAUD approval queue."""
    return await vendors_svc.update_bank_details(
        vendor_id, payload, principal, otp=payload.get("otp"))


@vendors_router.post("/{vendor_id}/requalify")
async def requalify_vendor(vendor_id: str, payload: dict = Body(default={}),
                           principal: dict = Depends(require_permission("vendor:write"))):
    return await vendors_svc.create_requalification(vendor_id, payload, principal)


@vendors_router.post("/{vendor_id}/questionnaires")
async def send_questionnaire(vendor_id: str, payload: dict = Body(...),
                             principal: dict = Depends(require_permission("vendor:write"))):
    return await vendors_svc.send_questionnaire(
        vendor_id, payload.get("questions", []), principal)


@vendors_router.get("/disputes")
async def list_disputes(status: Optional[str] = Query(None),
                        principal: dict = Depends(get_current_principal)):
    from app.core.database import db

    q = {} if not status else {"status": status}
    from app.domains.vendors.service import _clean

    return [_clean(dict(d)) async for d in
            db.db.vendor_disputes.find(q).sort("created_at", -1).limit(100)]


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


# ---------------------------------------------- portal public + self-service
@portal_router.post("/register")
async def portal_register(payload: dict = Body(...)):
    """Public vendor self-registration (no auth). Provisioning of credentials
    happens after VENDOR_MANAGER document review."""
    return await vendors_svc.portal_register(payload)


@portal_router.post("/documents")
async def portal_upload_document(payload: dict = Body(...),
                                 principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_upload_document(
        principal["vendor_id"], payload, principal)


@portal_router.get("/questionnaires")
async def portal_questionnaires(principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_questionnaires(principal["vendor_id"])


@portal_router.post("/questionnaires/{questionnaire_id}/submit")
async def portal_submit_questionnaire(questionnaire_id: str,
                                      payload: dict = Body(...),
                                      principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_submit_questionnaire(
        principal["vendor_id"], questionnaire_id, payload.get("answers", {}),
        principal)


@portal_router.post("/pos/{po_id}/amendment-request")
async def portal_amendment_request(po_id: str, payload: dict = Body(...),
                                   principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_po_amendment_request(
        principal["vendor_id"], po_id, payload, principal)


@portal_router.post("/pos/{po_id}/reject")
async def portal_reject_po(po_id: str, payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    """Supplier rejects a PO (stays SENT with rejection recorded)."""
    return await vendors_svc.portal_ack_po(
        principal["vendor_id"], po_id,
        {**payload, "accepted": False}, principal)


@portal_router.post("/asns/{asn_id}/documents")
async def portal_shipment_docs(asn_id: str, payload: dict = Body(...),
                               principal: dict = Depends(get_current_principal)):
    """Upload shipment documents (invoice copy, packing list, CoA) to an ASN."""
    from app.core.database import db
    from app.core.errors import NotFound

    asn = await db.db.asns.find_one({"asn_id": asn_id,
                                     "vendor_id": principal["vendor_id"]})
    if not asn:
        raise NotFound(f"ASN {asn_id} not found for vendor")
    from app.core.database import now_iso

    docs = payload.get("docs", [])
    await db.db.asns.update_one(
        {"asn_id": asn_id},
        {"$push": {"docs": {"$each": docs}},
         "$set": {"updated_at": now_iso()}})
    return {"asn_id": asn_id, "docs_added": len(docs)}


@portal_router.get("/qa-issues")
async def portal_qa_issues(principal: dict = Depends(get_current_principal)):
    from app.core.database import db
    from app.domains.vendors.service import _clean

    return [_clean(dict(i)) async for i in db.db.qa_issues.find(
        {"vendor_id": principal["vendor_id"]})]


@portal_router.post("/qa-issues/{issue_id}/respond")
async def portal_respond_qa_issue(issue_id: str, payload: dict = Body(...),
                                  principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_respond_qa_issue(
        principal["vendor_id"], issue_id, payload, principal)


@portal_router.post("/disputes")
async def portal_create_dispute(payload: dict = Body(...),
                                principal: dict = Depends(get_current_principal)):
    return await vendors_svc.portal_create_dispute(
        principal["vendor_id"], payload, principal)


@portal_router.get("/payments")
async def portal_payment_status(principal: dict = Depends(get_current_principal)):
    """Payment status for the vendor's invoices."""
    from app.core.database import db
    from app.domains.vendors.service import _clean

    vendor_id = principal["vendor_id"]
    invoices = [i async for i in db.db.supplier_invoices.find(
        {"vendor_id": vendor_id}, {"invoice_id": 1, "_id": 0})]
    ids = [i["invoice_id"] for i in invoices]
    payments = [_clean(dict(p)) async for p in db.db.payments.find(
        {"lines.invoice_id": {"$in": ids}})]
    return [{"payment_id": p["payment_id"], "status": p["status"],
             "amount": p.get("amount"),
             "invoices": [l.get("invoice_id") for l in p.get("lines", [])]}
            for p in payments]


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


@sourcing_router.post("/events/{event_id}/complete-bidding")
async def complete_bidding(event_id: str,
                           principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.complete_bidding(event_id, principal)


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
    from app.core.rbac import assert_party, can_read

    if not can_read(principal.get("roles", []), "purchase_order"):
        raise HTTPException(403, "No read grant for purchase orders")
    po = await procurement_svc.get_po(po_id)
    assert_party(principal, vendor_id=po.get("vendor_id"))
    return po


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
    from app.core.rbac import authorize_command

    authorize_command(principal, "po:approve")
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


@procurement_router.post("/pos/{po_id}/amend")
async def amend_po(po_id: str, payload: dict = Body(...),
                   principal: dict = Depends(require_permission("po:write"))):
    """PO amendment: snapshot + new version; re-approval when already sent."""
    return await procurement_svc.amend_po(
        po_id, payload.get("changes", {}), principal,
        payload.get("reason", ""))


@procurement_router.get("/pos/{po_id}/versions")
async def po_versions(po_id: str,
                      principal: dict = Depends(get_current_principal)):
    return await procurement_svc.po_version_history(po_id)


@sourcing_router.post("/events/{event_id}/bids/{bid_id}/evaluate")
async def evaluate_bid(event_id: str, bid_id: str, payload: dict = Body(...),
                       principal: dict = Depends(require_permission("sourcing:write"))):
    return await sourcing_svc.evaluate_bid(
        event_id, bid_id, payload.get("kind", "technical"),
        float(payload["score"]), payload.get("notes", ""), principal)


@sourcing_router.get("/events/{event_id}/ranking")
async def bid_ranking(event_id: str,
                      principal: dict = Depends(get_current_principal)):
    """Deterministic weighted ranking (BRA preview)."""
    return await sourcing_svc.rank_bids(event_id)
