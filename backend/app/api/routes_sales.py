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
async def pos_sale(payload: dict = Body(...),
                   principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await sales_svc.pos_sale(payload, principal, idem)
