"""CRM + Sales routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile

from app.core.security import get_current_principal
from app.domains import crm as crm_svc
from app.domains import sales as sales_svc

crm_router = APIRouter(prefix="/api/crm", tags=["crm"])
sales_router = APIRouter(prefix="/api/sales", tags=["sales"])


# -------------------------------------------------------------------------- CRM
@crm_router.get("/leads")
async def list_leads(status: Optional[str] = Query(None),
                     principal: dict = Depends(get_current_principal)):
    return await crm_svc.list_leads(status)


@crm_router.post("/leads")
async def capture_lead(payload: dict = Body(...),
                       principal: dict = Depends(get_current_principal)):
    return await crm_svc.capture_lead(payload, principal)


@crm_router.post("/leads/{lead_id}/score")
async def score_lead(lead_id: str,
                     principal: dict = Depends(get_current_principal)):
    return await crm_svc.score_lead(lead_id, principal)


@crm_router.post("/leads/import")
async def import_leads_csv(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    """Bulk lead import: JSON {csv: "...", campaign_id?} (≤500 rows, deduped)."""
    return await crm_svc.import_leads_csv(payload.get("csv", ""), principal,
                                          payload.get("campaign_id"))


@crm_router.post("/campaigns")
async def create_campaign(payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    return await crm_svc.create_campaign(payload, principal)


@crm_router.get("/campaigns")
async def list_campaigns(status: Optional[str] = Query(None),
                         principal: dict = Depends(get_current_principal)):
    return await crm_svc.list_campaigns(status)


@crm_router.post("/campaigns/{campaign_id}/submit")
async def submit_campaign(campaign_id: str,
                          principal: dict = Depends(get_current_principal)):
    return await crm_svc.submit_campaign(campaign_id, principal)


@crm_router.post("/campaigns/{campaign_id}/decide")
async def decide_campaign(campaign_id: str, payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    return await crm_svc.decide_campaign(campaign_id, payload["decision"],
                                         principal, payload.get("reason", ""))


@crm_router.post("/campaigns/{campaign_id}/activate")
async def activate_campaign(campaign_id: str,
                            principal: dict = Depends(get_current_principal)):
    return await crm_svc.activate_campaign(campaign_id, principal)


@crm_router.post("/leads/{lead_id}/convert")
async def convert_lead(lead_id: str,
                       principal: dict = Depends(get_current_principal)):
    return await crm_svc.convert_lead(lead_id, principal)


@crm_router.get("/opportunities")
async def list_opps(principal: dict = Depends(get_current_principal)):
    return await crm_svc.list_opportunities()


@crm_router.post("/opportunities/{opp_id}/customer")
async def opp_to_customer(opp_id: str, payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    return await crm_svc.create_customer_from_opportunity(opp_id, payload, principal)


@crm_router.post("/inquiries")
async def create_inquiry(payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await crm_svc.create_inquiry(payload, principal)


@crm_router.get("/inquiries")
async def list_inquiries(principal: dict = Depends(get_current_principal)):
    return await crm_svc.list_inquiries()


@crm_router.post("/inquiries/{inq_id}/understand")
async def understand_inquiry(inq_id: str,
                             principal: dict = Depends(get_current_principal)):
    return await crm_svc.understand_inquiry(inq_id, principal)


# ------------------------------------------------------------------------ SALES
@sales_router.post("/quotations")
async def create_quotation(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await sales_svc.create_quotation(payload, principal)


@sales_router.post("/quotations/{quote_id}/send")
async def send_quotation(quote_id: str,
                         principal: dict = Depends(get_current_principal)):
    return await sales_svc.send_quotation(quote_id, principal)


@sales_router.post("/customer-pos")
async def intake_customer_po(payload: dict = Body(...),
                             principal: dict = Depends(get_current_principal)):
    return await sales_svc.intake_customer_po(payload, principal)


@sales_router.post("/customer-po-upload")
async def upload_customer_po(customer_id: str = Form(...),
                             file: UploadFile = File(...),
                             principal: dict = Depends(get_current_principal)):
    """Upload a customer PO PDF → document registered, classified, extracted."""
    from app.core.docai import register_document

    content = await file.read()
    doc = await register_document(file.filename, content,
                                  file.content_type or "application/pdf",
                                  entity_type="CUSTOMER_PO",
                                  uploaded_by=principal,
                                  doc_type_hint="customer_po")
    result = await sales_svc.intake_customer_po(
        {"customer_id": customer_id, "document_id": doc["document_id"]},
        principal)
    return {"document": {k: doc[k] for k in ("document_id", "filename",
                                             "processing_status")},
            "customer_po": result}


@sales_router.get("/orders")
async def list_orders(status: Optional[str] = Query(None),
                      customer_id: Optional[str] = Query(None),
                      principal: dict = Depends(get_current_principal)):
    return await sales_svc.list_orders(status, customer_id)


@sales_router.get("/orders/{order_id}")
async def get_order(order_id: str,
                    principal: dict = Depends(get_current_principal)):
    return await sales_svc.get_order(order_id)


@sales_router.post("/orders")
async def create_order(payload: dict = Body(...),
                       principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await sales_svc.create_sales_order(payload, principal, idem)


@sales_router.post("/orders/{order_id}/confirm")
async def confirm_order(order_id: str,
                        principal: dict = Depends(get_current_principal)):
    return await sales_svc.confirm_order(order_id, principal)


@sales_router.post("/orders/{order_id}/allocate")
async def allocate_order(order_id: str,
                         principal: dict = Depends(get_current_principal)):
    return await sales_svc.allocate_order(order_id, principal)


@sales_router.post("/orders/{order_id}/invoice")
async def invoice_order(order_id: str,
                        principal: dict = Depends(get_current_principal)):
    return await sales_svc.invoice_order(order_id, principal)


@sales_router.post("/orders/{order_id}/prescription")
async def attach_rx(order_id: str, payload: dict = Body(...),
                    principal: dict = Depends(get_current_principal)):
    return await sales_svc.attach_prescription(order_id,
                                               payload["prescription_id"],
                                               principal)


@sales_router.post("/pos/sale")
@sales_router.post("/pos")
async def pos_sale(payload: dict = Body(...),
                   principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await sales_svc.pos_sale(payload, principal, idem)


# ==================================================================
# Phase: OMS + CRM completion routes.
# ==================================================================
@sales_router.post("/orders/{order_id}/allocate-partial")
async def allocate_partial(order_id: str,
                           principal: dict = Depends(get_current_principal)):
    return await sales_svc.allocate_order_partial(order_id, principal)


@sales_router.post("/orders/{order_id}/fulfill")
async def record_fulfillment(order_id: str, payload: dict = Body(...),
                             principal: dict = Depends(get_current_principal)):
    return await sales_svc.record_fulfillment(order_id, payload, principal)


@sales_router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, payload: dict = Body(...),
                       principal: dict = Depends(get_current_principal)):
    return await sales_svc.cancel_order(order_id,
                                        payload.get("reason", "Customer request"),
                                        principal)


@sales_router.post("/inquiries/{inq_id}/quote")
async def quote_from_rfq(inq_id: str, payload: dict = Body(default={}),
                         principal: dict = Depends(get_current_principal)):
    return await sales_svc.quotation_from_rfq(
        inq_id, principal, float(payload.get("discount_pct", 0)))


@crm_router.post("/opportunities/{opp_id}/rfq")
async def opp_rfq(opp_id: str, payload: dict = Body(...),
                  principal: dict = Depends(get_current_principal)):
    return await crm_svc.create_opportunity_rfq(opp_id, payload, principal)


@crm_router.post("/opportunities/{opp_id}/close")
async def opp_close(opp_id: str, payload: dict = Body(...),
                    principal: dict = Depends(get_current_principal)):
    return await crm_svc.convert_opportunity(opp_id, payload, principal)


@crm_router.get("/customers/{customer_id}/360")
async def customer_360(customer_id: str,
                       principal: dict = Depends(get_current_principal)):
    return await crm_svc.customer_360(customer_id)


@crm_router.get("/tickets")
async def list_tickets(status: Optional[str] = Query(None),
                       principal: dict = Depends(get_current_principal)):
    from app.core.database import db
    q = {"status": status} if status else {}
    rows = []
    async for t in db.db.customer_tickets.find(q).sort("created_at", -1).limit(100):
        t.pop("_id", None)
        rows.append(t)
    return rows


@crm_router.post("/tickets")
async def create_ticket(payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    from app.core.database import db, now_iso, utcnow
    from app.core.audit import audit
    doc = {
        "ticket_id": f"TCK-{int(utcnow().timestamp())}",
        "customer_id": payload.get("customer_id"),
        "customer_name": payload.get("customer_name", "Customer"),
        "channel": payload.get("channel", "EMAIL"),
        "subject": payload.get("subject", "Inquiry"),
        "priority": payload.get("priority", "MEDIUM"),
        "category": payload.get("category", "ORDER_STATUS"),
        "status": "OPEN",
        "description": payload.get("description", ""),
        "ai_suggested_reply": payload.get("ai_suggested_reply") or "Thank you for reaching out. We have received your inquiry and our agent is tracking your order in real time.",
        "created_at": now_iso(),
    }
    await db.db.customer_tickets.insert_one(doc)
    await audit("CRM", doc["ticket_id"], "TICKET_CREATED", principal)
    doc.pop("_id", None)
    return doc


@crm_router.post("/tickets/{ticket_id}/resolve")
async def resolve_ticket(ticket_id: str, payload: dict = Body(default={}),
                         principal: dict = Depends(get_current_principal)):
    from app.core.database import db, now_iso
    from app.core.audit import audit
    await db.db.customer_tickets.update_one(
        {"ticket_id": ticket_id},
        {"$set": {"status": "RESOLVED", "resolution_notes": payload.get("notes", "Resolved by agent"), "resolved_at": now_iso()}}
    )
    await audit("CRM", ticket_id, "TICKET_RESOLVED", principal)
    return {"ticket_id": ticket_id, "status": "RESOLVED"}

