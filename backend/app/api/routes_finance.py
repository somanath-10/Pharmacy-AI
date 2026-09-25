"""Finance, Reverse, Safety, Compliance, Platform routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile

from app.core.errors import PermissionDenied
from app.core.rbac import assert_party, require_permission
from app.core.security import get_current_principal
from app.domains import compliance as comp_svc
from app.domains import finance as fin_svc
from app.domains import reverse as rev_svc
from app.domains import safety as safety_svc

def _internal_principal(principal: dict = Depends(get_current_principal)) -> dict:
    """Supplier/customer traffic is handled by the party-scoped portal APIs."""
    roles = set(principal.get("roles", []))
    if "SUPER_ADMIN" not in roles and roles & {"SUPPLIER", "CUSTOMER"}:
        raise PermissionDenied("Use the scoped external portal API")
    return principal


finance_router = APIRouter(prefix="/api/finance", tags=["finance"],
                           dependencies=[Depends(_internal_principal)])
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
                           principal: dict = Depends(require_permission("payment:write"))):
    idem = payload.pop("idempotency_key", None)
    return await fin_svc.create_payment_proposal(payload, principal, idem)


@finance_router.post("/payments/{payment_id}/authorize")
async def authorize_payment(payment_id: str,
                            principal: dict = Depends(require_permission("payment:authorize"))):
    return await fin_svc.authorize_payment(payment_id, principal)


@finance_router.post("/payments/{payment_id}/pay")
async def execute_payment(payment_id: str, payload: dict = Body(default={}),
                          principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await fin_svc.execute_payment(payment_id, payload, principal, idem)


@finance_router.get("/customer-invoices")
async def customer_invoices(status: Optional[str] = Query(None),
                            principal: dict = Depends(get_current_principal)):
    customer_id = principal.get("customer_id")
    if "CUSTOMER" in principal.get("roles", []):
        if not customer_id:
            from app.core.errors import PermissionDenied

            raise PermissionDenied("Customer identity is missing customer_id")
        assert_party(principal, customer_id=customer_id)
    return await fin_svc.list_customer_invoices(status, customer_id)


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


@reverse_router.post("/returns/{return_id}/picked")
async def confirm_pickup(return_id: str,
                         principal: dict = Depends(get_current_principal)):
    return await rev_svc.confirm_pickup(return_id, principal)


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


# ------------------------------------------------------------ service requests
@compliance_router.get("/service-requests")
async def list_service_requests(status: Optional[str] = Query(None),
                                principal: dict = Depends(get_current_principal)):
    return await comp_svc.list_service_requests(status)


@compliance_router.post("/service-requests")
async def create_service_request(payload: dict = Body(...),
                                 principal: dict = Depends(get_current_principal)):
    return await comp_svc.create_service_request(payload, principal)


@compliance_router.post("/service-requests/{sr_id}/resolve")
async def resolve_service_request(sr_id: str, payload: dict = Body(...),
                                  principal: dict = Depends(get_current_principal)):
    return await comp_svc.resolve_service_request(sr_id, payload, principal)


# ------------------------------------------------------- documents (GAP-13)
@platform_router.get("/api/documents/search")
async def search_documents(q: Optional[str] = Query(None),
                           doc_type: Optional[str] = Query(None),
                           entity_type: Optional[str] = Query(None),
                           entity_id: Optional[str] = Query(None),
                           principal: dict = Depends(get_current_principal)):
    from app.core.docai import search_documents

    return await search_documents(q, doc_type, entity_type, entity_id)


@platform_router.get("/api/documents/{document_id}/export.pdf")
async def export_document_pdf(document_id: str,
                              principal: dict = Depends(get_current_principal)):
    from fastapi import Response
    from app.core.docai import export_document_pdf as export_pdf

    content = await export_pdf(document_id)
    return Response(content=content, media_type="application/pdf")


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
                        payload.get("reason", ""),
                        payload.get("modification"))


@platform_router.get("/api/workflows/{entity_type}/{entity_id}")
async def workflow_timeline(entity_type: str, entity_id: str,
                            principal: dict = Depends(get_current_principal)):
    from app.core.database import db
    from app.core.workflow import get_machine

    wf = await db.db.workflows.find_one({"entity_type": entity_type,
                                         "entity_id": entity_id})
    rows = []
    async for e in db.db.audit_events.find(
            {"entity_type": entity_type, "entity_id": entity_id}
    ).sort("timestamp", 1):
        e.pop("_id", None)
        rows.append(e)

    # Part 5: completed / active / pending steps even when no explicit
    # workflow nodes were recorded — derived from the state machine + audit.
    machine = None
    try:
        machine = get_machine(entity_type.lower())
    except Exception:
        machine = None
    current = rows[-1].get("new_state") if rows else None
    nodes = (wf or {}).get("nodes", [])
    if not nodes and machine is not None and current:
        # Find the longest simple path from the initial state that passes
        # through the current state (fallback: longest path).
        from collections import deque

        start = machine.initial
        q = deque([[start]])
        best, best_with = [start], None
        while q:
            p = q.popleft()
            if len(p) > len(best):
                best = p
            if current in p and (best_with is None or len(p) > len(best_with)):
                best_with = p
            if len(p) >= 15:
                continue
            for s in machine.transitions.get(p[-1], []):
                if s not in p:  # simple paths only
                    q.append(p + [s])
        path = best_with or best
        cur_idx = path.index(current) if current in path else None
        nodes = []
        for i, s in enumerate(path):
            if cur_idx is None or i > cur_idx:
                st = "PENDING"
            elif i == cur_idx:
                st = "ACTIVE"
            else:
                st = "DONE"
            detail = ""
            if st == "ACTIVE" and rows:
                detail = f"current (per audit: {rows[-1].get('action', '')})"
            nodes.append({"node": s, "label": s, "status": st,
                          "detail": detail})
    allowed_next = []
    if machine is not None and current:
        allowed_next = machine.next_states(current)
    return {"entity_type": entity_type, "entity_id": entity_id,
            "nodes": nodes, "audit": rows, "allowed_next": allowed_next}


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


# ==================================================================
# Phase: Finance + Returns + Recall + PV + Agents — route additions
# ==================================================================

# ------------------------- finance ops -------------------------
@finance_router.get("/ar-aging")
async def ar_aging_report(principal: dict = Depends(get_current_principal)):
    from app.domains.finance.service import ar_aging

    return await ar_aging()


@finance_router.post("/collections/reminders")
async def collection_reminders(principal: dict = Depends(get_current_principal)):
    from app.domains.finance.service import collection_reminders

    return await collection_reminders(principal)


@finance_router.get("/duplicates/scan")
async def duplicate_scan(principal: dict = Depends(get_current_principal)):
    from app.domains.finance.service import list_supplier_invoices

    seen, dups = set(), []
    for inv in await list_supplier_invoices():
        k = (str(inv.get("vendor_id")),
             str(inv.get("invoice_number", "")).strip().upper())
        if k[1] and k in seen:
            dups.append({"invoice_id": inv.get("invoice_id"),
                         "vendor_id": inv.get("vendor_id"),
                         "invoice_number": inv.get("invoice_number")})
        seen.add(k)
    return {"duplicates": dups}


@finance_router.get("/anomalies/compliance")
async def compliance_anomalies(principal: dict = Depends(get_current_principal)):
    from app.domains.compliance.service import anomaly_sweep

    return await anomaly_sweep(principal)


# ------------------------- returns -------------------------
@reverse_router.post("/returns/{return_id}/approve")
async def approve_return(return_id: str,
                         principal: dict = Depends(get_current_principal)):
    from app.domains.reverse.service import approve_return

    return await approve_return(return_id, principal)


# ------------------------- recall -------------------------
@reverse_router.get("/recalls/{recall_id}/reconciliation")
async def recall_reconciliation(recall_id: str,
                                principal: dict = Depends(get_current_principal)):
    from app.domains.reverse.service import recall_reconciliation

    return await recall_reconciliation(recall_id, principal)


# ------------------------- complaints -------------------------
@platform_router.get("/api/complaints")
async def list_complaints(status: Optional[str] = Query(None),
                          principal: dict = Depends(get_current_principal)):
    from app.domains.qa.service import list_complaints

    return await list_complaints(status)


@platform_router.post("/api/complaints/{complaint_id}/investigate")
async def investigate_complaint(complaint_id: str, payload: dict = Body(...),
                                principal: dict = Depends(get_current_principal)):
    from app.domains.qa.service import investigate_complaint

    return await investigate_complaint(complaint_id, payload, principal)


@platform_router.post("/api/complaints/{complaint_id}/resolve")
async def resolve_complaint(complaint_id: str, payload: dict = Body(...),
                            principal: dict = Depends(get_current_principal)):
    from app.domains.qa.service import resolve_complaint

    return await resolve_complaint(complaint_id, payload, principal)


@platform_router.post("/api/complaints/{complaint_id}/close")
async def close_complaint(complaint_id: str,
                          principal: dict = Depends(get_current_principal)):
    from app.domains.qa.service import close_complaint

    return await close_complaint(complaint_id, principal)


# ------------------------- agents -------------------------
# Monitoring tools per agent: read-only checks safe for supervised runs.
# Module-level constant so startup can assert names against the registry.
AGENT_MONITORING_TOOLS = {
    "procurement-agent": ["track_po_acknowledgement", "chase_pending_asn",
                          "monitor_po_delays"],
    "supply-chain-agent": ["detect_expiry_and_excess",
                           "get_inventory_snapshot"],
    "vendor-sourcing-agent": ["monitor_licence_expiry",
                              "check_vendor_documents"],
    "warehouse-agent": ["get_warehouse_health", "detect_expiry_risk",
                        "get_stock_variances"],
    "qa-agent": ["get_oos_summary", "get_capa_overdue",
                 "detect_recurring_quality_issues",
                 "equipment_due_report"],
    "plant-agent": ["yield_anomalies"],
    "sales-agent": ["order_status_sweep"],
    "logistics-agent": ["flag_delayed_shipments"],
    "finance-agent": ["ar_aging", "duplicate_scan", "recon_status"],
    "compliance-agent": ["anomaly_sweep"],
    "pv-agent": ["followup_queue"],
    "pharmacy-agent": ["get_pharmacist_queue"],
}


def assert_monitoring_tools_registered() -> None:
    """Fail fast when the monitoring map references unregistered tools.

    Called at startup: a typo'd tool name would otherwise silently no-op
    agent sweeps (HTTP 200 with an empty tools_executed list).
    """
    from app.agents.domain_agents import AGENT_TOOLS_MAP

    for agent_id, names in AGENT_MONITORING_TOOLS.items():
        registered = {getattr(t, "tool_name", None)
                      for t in AGENT_TOOLS_MAP.get(agent_id, [])}
        unknown = [n for n in names if n not in registered]
        if unknown:
            raise RuntimeError(
                f"agent monitoring-map drift: {agent_id} references "
                f"unregistered tools {unknown}")


@platform_router.post("/api/agents/run/{agent_id}")
async def run_agent(agent_id: str, payload: dict = Body(default={}),
                    principal: dict = Depends(get_current_principal)):
    """Run an agent's routine sweep (goal-driven).

    Executes the agent's read-only monitoring tools through the governed
    gateway and records an agent_runs document. Human-required actions still
    land in the decision queue — an agent run never bypasses approvals.
    """
    from app.agents.gateway import AGENT_REGISTRY
    from app.core.errors import PermissionDenied
    from app.core.database import db as _db
    from app.core.database import now_iso as _now

    reg = AGENT_REGISTRY.get(agent_id)
    if not reg:
        raise PermissionDenied(f"Unknown agent {agent_id}")
    if reg["status"] != "ACTIVE":
        raise PermissionDenied(f"Agent {agent_id} is {reg['status']}")

    goal = payload.get("goal", "routine_sweep")
    from app.agents.domain_agents import AGENT_TOOLS_MAP

    tools = AGENT_TOOLS_MAP.get(agent_id, [])
    by_name = {getattr(t, "tool_name", None): t for t in tools}
    results = {}
    actor = {"type": "AGENT", "id": agent_id}
    meta = {"reason": f"run request: {goal}", "model": "deterministic-tools",
            "triggered_by": (principal.get("email") or
                             principal.get("id") or "operator")}
    for name in AGENT_MONITORING_TOOLS.get(agent_id, []):
        tool = by_name.get(name)
        if tool is None:
            continue
        try:
            results[name] = await tool(agent_id, {}, actor, meta)
        except Exception as e:
            results[name] = {"error": str(e)[:200]}
    run_doc = {"agent": agent_id, "type": "REQUESTED_RUN", "goal": goal,
               "results": results,
               "tools_executed": list(results.keys()),
               "triggered_by": meta["triggered_by"],
               "created_at": _now()}
    await _db.db.agent_runs.insert_one(run_doc)
    return {"status": "ok", "agent": agent_id, "goal": goal,
            "tools_executed": list(results.keys())}


@platform_router.post("/api/agents/{agent_id}/tools/{tool_name}")
async def run_agent_tool(agent_id: str, tool_name: str, payload: dict = Body(default={}),
                         principal: dict = Depends(get_current_principal)):
    """Dispatch an agent tool through the governed gateway.

    Every call is permission-checked, policy-checked, persisted with full
    metadata and audited. The human always sees what the agent did and why.
    """
    from app.agents.gateway import AGENT_REGISTRY
    from app.core.errors import PermissionDenied

    reg = AGENT_REGISTRY.get(agent_id)
    if not reg:
        raise PermissionDenied(f"Unknown agent {agent_id}")
    if tool_name not in reg["allowed_tools"]:
        raise PermissionDenied(
            f"Tool {tool_name} not allowed for {agent_id}")
    # Human operators may run agent tools on the agent's behalf (supervised
    # execution); the gateway records both identities.
    actor = {"type": "AGENT", "id": agent_id}
    meta = {"reason": payload.pop("meta_reason", None),
            "confidence": payload.pop("meta_confidence", None),
            "model": payload.pop("meta_model", None),
            "escalation_reason": payload.pop("meta_escalation_reason", None),
            "triggered_by": principal.get("email") or principal.get("id")}
    from app.agents.domain_agents import AGENT_TOOLS_MAP

    fn = AGENT_TOOLS_MAP.get(agent_id, [])
    tool = next((t for t in fn
                 if getattr(t, "tool_name", None) == tool_name), None)
    if tool is None:
        raise PermissionDenied(f"Tool {tool_name} not registered")
    return await tool(agent_id, payload, actor, meta)


@safety_router.get("/signals")
async def list_signals(principal: dict = Depends(get_current_principal)):
    from app.domains.safety.service import list_signals

    return await list_signals()


@safety_router.get("/follow-ups")
async def safety_followups(principal: dict = Depends(get_current_principal)):
    from app.domains.safety.service import followup_queue

    return await followup_queue()


@platform_router.get("/api/workflows")
async def workflow_machines(principal: dict = Depends(get_current_principal)):
    """All standard workflow state machines (Part 6: controlled statuses)."""
    from app.core.workflow import machine_overview

    return await machine_overview()


# ==================================================================
# Production phase: agent governance, AI cost tracking, payment anomalies
# ==================================================================

@platform_router.post("/api/agents/{agent_id}/status")
async def change_agent_status(agent_id: str, payload: dict = Body(...),
                              principal: dict = Depends(get_current_principal)):
    """Kill-switch: disable/pause/re-enable an agent. Audited + role-gated."""
    roles = set(principal.get("roles", []))
    if "SUPER_ADMIN" not in roles and "COMPLIANCE" not in roles \
            and "MANAGEMENT" not in roles:
        from app.core.errors import PermissionDenied

        raise PermissionDenied("Agent administration requires elevated roles")
    from app.agents.gateway import set_agent_status

    return await set_agent_status(agent_id, payload["status"], principal,
                                  payload.get("reason", ""))


@platform_router.get("/api/ai/usage")
async def ai_usage(principal: dict = Depends(get_current_principal)):
    """Token/cost usage per model per day (real metered data, no estimates)."""
    from app.core.database import db

    rows = []
    async for r in db.db.ai_usage.find({}).sort("day", -1).limit(60):
        r.pop("_id", None)
        rows.append(r)
    total = sum(r.get("estimated_cost_usd", 0) for r in rows)
    return {"days": rows, "total_estimated_cost_usd": round(total, 4)}


@finance_router.get("/payments/anomalies")
async def payment_anomalies(principal: dict = Depends(get_current_principal)):
    """Deterministic payment-anomaly detection: duplicate payments, amount
    spikes, after-hours execution, repeated failures."""
    from app.core.database import db

    out = {"duplicates": [], "spikes": [], "after_hours": [], "repeated_failures": []}

    # duplicate payments: same invoice paid twice / same amount+method in a day
    pipeline = [
        {"$match": {"status": {"$in": ["PAID", "EXECUTED"]}}},
        {"$group": {"_id": {"invoice": "$invoice_id", "amount": "$amount"},
                    "n": {"$sum": 1},
                    "payment_ids": {"$addToSet": "$payment_id"}}},
        {"$match": {"n": {"$gt": 1}}},
    ]
    try:
        async for row in db.db.payments.aggregate(pipeline):
            out["duplicates"].append({"invoice_id": (row["_id"] or {}).get("invoice"),
                                      "amount": (row["_id"] or {}).get("amount"),
                                      "count": row["n"],
                                      "payments": row["payment_ids"][:4]})
    except Exception:
        pass

    # amount spike: paid amount > 3x median of recent paid payments
    try:
        paid = []
        async for p in db.db.payments.find(
                {"status": {"$in": ["PAID", "EXECUTED"]}}).sort("created_at", -1).limit(100):
            paid.append(float(p.get("amount") or 0))
        paid_sorted = sorted(paid)
        if len(paid_sorted) >= 10:
            median = paid_sorted[len(paid_sorted) // 2]
            if median > 0:
                high = [a for a in paid if a > 3 * median]
                out["spikes"] = [{"count": len(high), "threshold": 3 * median}]
    except Exception:
        pass

    # repeated failures per invoice
    pipeline2 = [
        {"$match": {"status": "FAILED"}},
        {"$group": {"_id": "$invoice_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": 2}}},
        {"$sort": {"n": -1}}, {"$limit": 10},
    ]
    try:
        async for row in db.db.payments.aggregate(pipeline2):
            out["repeated_failures"].append(
                {"invoice_id": row["_id"], "failures": row["n"]})
    except Exception:
        pass
    return out


@finance_router.get("/payments")
async def list_payments(status: Optional[str] = Query(None),
                        limit: int = Query(200),
                        principal: dict = Depends(get_current_principal)):
    from app.core.database import db

    q = {"status": status} if status else {}
    rows = []
    async for p in db.db.payments.find(q).sort("created_at", -1).limit(limit):
        p.pop("_id", None)
        rows.append(p)
    return rows
