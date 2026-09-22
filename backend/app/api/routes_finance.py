"""Finance, Reverse, Safety, Compliance, Platform routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile

from app.core.security import get_current_principal
from app.domains import compliance as comp_svc
from app.domains import finance as fin_svc
from app.domains import reverse as rev_svc
from app.domains import safety as safety_svc

finance_router = APIRouter(prefix="/api/finance", tags=["finance"])
reverse_router = APIRouter(prefix="/api/reverse", tags=["reverse"])
safety_router = APIRouter(prefix="/api/safety", tags=["safety"])
compliance_router = APIRouter(prefix="/api/compliance", tags=["compliance"])
platform_router = APIRouter(tags=["platform"])


# ---------------------------------------------------------------------- finance
@finance_router.get("/supplier-invoices")
async def supplier_invoices(status: Optional[str] = Query(None),
                            principal: dict = Depends(get_current_principal)):
    return await fin_svc.list_supplier_invoices(status)


@finance_router.post("/supplier-invoices")
async def receive_supplier_invoice(payload: dict = Body(...),
                                   principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await fin_svc.receive_supplier_invoice(payload, principal, idem)


@finance_router.post("/supplier-invoice-upload")
async def upload_supplier_invoice(po_id: str = Form(...),
                                  file: UploadFile = File(...),
                                  principal: dict = Depends(get_current_principal)):
    from app.core.docai import process, register_document

    content = await file.read()
    doc = await register_document(file.filename, content,
                                  file.content_type or "application/pdf",
                                  entity_type="SUPPLIER_INVOICE",
                                  uploaded_by=principal)
    inv = await fin_svc.receive_supplier_invoice(
        {"po_id": po_id, "document_id": doc["document_id"]}, principal)
    processed = await process(doc["document_id"])
    return {"document": processed, "invoice": inv}


@finance_router.post("/invoices/{invoice_id}/match")
async def match_invoice(invoice_id: str,
                        principal: dict = Depends(get_current_principal)):
    return await fin_svc.match_invoice(invoice_id, principal)


@finance_router.post("/invoices/{invoice_id}/approve")
async def approve_invoice(invoice_id: str,
                          principal: dict = Depends(get_current_principal)):
    return await fin_svc.approve_matched_invoice(invoice_id, principal)


@finance_router.post("/invoices/{invoice_id}/credit-note")
async def credit_note(invoice_id: str, payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await fin_svc.apply_credit_note(invoice_id, payload, principal)


@finance_router.post("/payments/proposal")
async def payment_proposal(payload: dict = Body(default={}),
                           principal: dict = Depends(get_current_principal)):
    return await fin_svc.create_payment_proposal(payload, principal)


@finance_router.post("/payments/{payment_id}/authorize")
async def authorize_payment(payment_id: str,
                            principal: dict = Depends(get_current_principal)):
    return await fin_svc.authorize_payment(payment_id, principal)


@finance_router.post("/payments/{payment_id}/pay")
async def execute_payment(payment_id: str, payload: dict = Body(default={}),
                          principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await fin_svc.execute_payment(payment_id, payload, principal, idem)


@finance_router.get("/customer-invoices")
async def customer_invoices(status: Optional[str] = Query(None),
                            principal: dict = Depends(get_current_principal)):
    return await fin_svc.list_customer_invoices(status)


@finance_router.post("/cash/apply")
async def apply_cash(payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    return await fin_svc.apply_cash(payload, principal)


@finance_router.post("/reconciliation/run")
async def reconcile(payload: dict = Body(...),
                    principal: dict = Depends(get_current_principal)):
    return await fin_svc.run_reconciliation(payload, principal)


@finance_router.get("/valuation")
async def valuation(product_id: Optional[str] = Query(None),
                    principal: dict = Depends(get_current_principal)):
    return await fin_svc.inventory_valuation(product_id)


@finance_router.post("/write-off")
async def write_off(payload: dict = Body(...),
                    principal: dict = Depends(get_current_principal)):
    return await fin_svc.write_off(payload, principal)


@finance_router.get("/gl")
async def gl_export(account: Optional[str] = Query(None),
                    principal: dict = Depends(get_current_principal)):
    return await fin_svc.gl_export(account)


# ---------------------------------------------------------------------- reverse
@reverse_router.get("/returns")
async def list_returns(status: Optional[str] = Query(None),
                       principal: dict = Depends(get_current_principal)):
    return await rev_svc.list_returns(status)


@reverse_router.post("/returns")
async def create_return(payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await rev_svc.create_return(payload, principal)


@reverse_router.post("/returns/{return_id}/pickup")
async def schedule_pickup(return_id: str, payload: dict = Body(default={}),
                          principal: dict = Depends(get_current_principal)):
    return await rev_svc.schedule_pickup(return_id, payload, principal)


@reverse_router.post("/returns/{return_id}/receive")
async def receive_return(return_id: str,
                         principal: dict = Depends(get_current_principal)):
    return await rev_svc.receive_return(return_id, principal)


@reverse_router.post("/returns/{return_id}/inspect")
async def inspect_return(return_id: str, payload: dict = Body(default={}),
                         principal: dict = Depends(get_current_principal)):
    return await rev_svc.inspect_return(return_id, payload, principal)


@reverse_router.post("/returns/{return_id}/dispose")
async def dispose_return(return_id: str, payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await rev_svc.dispose_return(return_id, payload["disposition"],
                                        principal)


@reverse_router.get("/recalls")
async def list_recalls(status: Optional[str] = Query(None),
                       principal: dict = Depends(get_current_principal)):
    return await rev_svc.list_recalls(status)


@reverse_router.post("/recalls")
async def create_recall(payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await rev_svc.create_recall(payload, principal, idem)


@reverse_router.post("/recall-tasks/{task_id}/complete")
async def complete_recall_task(task_id: str,
                               principal: dict = Depends(get_current_principal)):
    return await rev_svc.complete_quarantine_task(task_id, principal)


@reverse_router.post("/recalls/{recall_id}/close")
async def close_recall(recall_id: str, payload: dict = Body(default={}),
                       principal: dict = Depends(get_current_principal)):
    return await rev_svc.close_recall(recall_id, payload, principal)


# ---------------------------------------------------------------------- safety
@safety_router.post("/adverse-events")
async def report_ae(payload: dict = Body(...),
                    principal: dict = Depends(get_current_principal)):
    return await safety_svc.report_adverse_event(payload, principal)


@safety_router.get("/cases")
async def list_cases(status: Optional[str] = Query(None),
                     principal: dict = Depends(get_current_principal)):
    return await safety_svc.list_cases(status)


@safety_router.post("/cases/{case_id}/triage")
async def triage(case_id: str,
                 principal: dict = Depends(get_current_principal)):
    return await safety_svc.triage_case(case_id, principal)


@safety_router.post("/cases/{case_id}/duplicate-check")
async def dup_check(case_id: str,
                    principal: dict = Depends(get_current_principal)):
    return await safety_svc.duplicate_check(case_id, principal)


@safety_router.post("/cases/{case_id}/medical-review")
async def medical_review(case_id: str, payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await safety_svc.medical_review(case_id, payload, principal)


@safety_router.post("/cases/{case_id}/follow-up")
async def follow_up(case_id: str, payload: dict = Body(default={}),
                    principal: dict = Depends(get_current_principal)):
    return await safety_svc.complete_follow_up(case_id, payload, principal)


@safety_router.post("/cases/{case_id}/close")
async def close_case(case_id: str, payload: dict = Body(default={}),
                     principal: dict = Depends(get_current_principal)):
    return await safety_svc.close_case(case_id, principal,
                                       payload.get("reason", ""))


@safety_router.post("/signals")
async def create_signal(payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await safety_svc.track_signal(payload, principal)


@safety_router.post("/psur")
async def generate_psur(payload: dict = Body(default={}),
                        principal: dict = Depends(get_current_principal)):
    return await safety_svc.generate_psur(payload, principal)


# ------------------------------------------------------------------ compliance
@compliance_router.get("/licences")
async def list_licences(status: Optional[str] = Query(None),
                        principal: dict = Depends(get_current_principal)):
    return await comp_svc.list_licences(status)


@compliance_router.post("/licences")
async def register_licence(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await comp_svc.register_licence(payload, principal)


@compliance_router.post("/licences/sweep")
async def licence_sweep(principal: dict = Depends(get_current_principal)):
    return await comp_svc.licence_status_sweep(principal)


@compliance_router.get("/sod")
async def sod_report(principal: dict = Depends(get_current_principal)):
    return await comp_svc.sod_report()


@compliance_router.post("/submissions")
async def reg_submission(payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await comp_svc.regulatory_submission(payload, principal)


# -------------------------------------------------------------------- platform
@platform_router.get("/api/documents")
async def list_documents(principal: dict = Depends(get_current_principal)):
    rows = []
    from app.core.database import db

    async for d in db.db.documents.find().sort("created_at", -1).limit(100):
        d.pop("_id", None)
        d.pop("text_preview", None)
        rows.append(d)
    return rows


@platform_router.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...),
                          entity_type: Optional[str] = Query(None),
                          entity_id: Optional[str] = Query(None),
                          principal: dict = Depends(get_current_principal)):
    from app.core.docai import register_document

    content = await file.read()
    doc = await register_document(file.filename, content,
                                  file.content_type or "application/octet-stream",
                                  entity_type=entity_type, entity_id=entity_id,
                                  uploaded_by=principal)
    doc.pop("_id", None)
    doc.pop("text_preview", None)
    return doc


@platform_router.post("/api/documents/{document_id}/process")
async def process_document(document_id: str,
                           principal: dict = Depends(get_current_principal)):
    from app.core.docai import process

    return await process(document_id)


@platform_router.get("/api/approvals")
async def list_approvals(status: Optional[str] = Query(None),
                         category: Optional[str] = Query(None),
                         principal: dict = Depends(get_current_principal)):
    from app.core.database import db

    q: dict = {}
    if status:
        q["status"] = status
    if category:
        q["category"] = category
    rows = []
    async for a in db.db.approvals.find(q).sort("created_at", -1).limit(200):
        a.pop("_id", None)
        rows.append(a)
    return rows


@platform_router.get("/api/approvals/stats")
async def approval_stats(principal: dict = Depends(get_current_principal)):
    from app.core.approvals import queue_stats

    return await queue_stats()


@platform_router.post("/api/approvals/{approval_id}/decide")
async def decide_approval(approval_id: str, payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    from app.core.approvals import decide

    return await decide(approval_id, payload["decision"], principal,
                        payload.get("reason", ""))


@platform_router.get("/api/workflows/{entity_type}/{entity_id}")
async def workflow_timeline(entity_type: str, entity_id: str,
                            principal: dict = Depends(get_current_principal)):
    from app.core.database import db

    wf = await db.db.workflows.find_one({"entity_type": entity_type,
                                         "entity_id": entity_id})
    rows = []
    async for e in db.db.audit_events.find(
            {"entity_type": entity_type, "entity_id": entity_id}
    ).sort("timestamp", 1):
        e.pop("_id", None)
        rows.append(e)
    return {"entity_type": entity_type, "entity_id": entity_id,
            "nodes": (wf or {}).get("nodes", []), "audit": rows}


@platform_router.get("/api/audit")
async def audit_query(entity_type: Optional[str] = Query(None),
                      entity_id: Optional[str] = Query(None),
                      limit: int = Query(200),
                      principal: dict = Depends(get_current_principal)):
    return await comp_svc.audit_query(entity_type, entity_id, limit=limit)


@platform_router.get("/api/notifications")
async def notifications(principal: dict = Depends(get_current_principal)):
    from app.core.notifications import list_notifications

    return await list_notifications()


@platform_router.get("/api/agents")
async def list_agents(principal: dict = Depends(get_current_principal)):
    from app.agents.gateway import list_agents

    return await list_agents()


@platform_router.get("/api/agents/tool-calls")
async def agent_tool_calls(limit: int = Query(100),
                           principal: dict = Depends(get_current_principal)):
    rows = []
    from app.core.database import db

    async for c in db.db.agent_tool_calls.find().sort(
            "created_at", -1).limit(limit):
        c.pop("_id", None)
        rows.append(c)
    return rows


@platform_router.post("/api/agents/supervisor/tick")
async def supervisor_tick(principal: dict = Depends(get_current_principal)):
    from app.agents.supervisor import tick

    return await tick()
