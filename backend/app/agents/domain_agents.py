"""Domain agents: every tool is a governed function behind the gateway.

Agents return data or drafts; persistence always flows through domain APIs
(state machines + events + audit apply).
"""
from typing import Any, Dict, Optional

from app.agents.gateway import agent_tool, register_agent
from app.core.database import db, now_iso


# ------------------------------------------------------------ Procurement Agent
register_agent(
    "procurement-agent",
    "Automates PR/PO creation, RFQ publication and contract releases",
    ["get_inventory", "get_open_purchase_orders", "get_active_contracts",
     "create_draft_pr", "create_rfq", "create_draft_po",
     "submit_po_for_approval", "release_contract_po"],
    ["procurement", "sourcing", "inventory"],
)


@agent_tool("inventory", "get_inventory",
            description="Read product availability snapshot")
async def t_get_inventory(payload: dict, actor: dict):
    from app.domains.inventory.service import availability

    return await availability(payload["product_id"])


@agent_tool("procurement", "get_open_purchase_orders",
            description="Open POs for a product")
async def t_get_open_pos(payload: dict, actor: dict):
    from app.domains.procurement.service import open_pos_for_product

    return await open_pos_for_product(payload["product_id"])


@agent_tool("sourcing", "get_active_contracts",
            description="Active contracts covering a product")
async def t_get_contracts(payload: dict, actor: dict):
    from app.domains.sourcing.service import active_contract_for

    c = await active_contract_for(payload["product_id"])
    return c or {"contract": None}


@agent_tool("procurement", "create_draft_pr",
            description="Create a draft purchase requisition")
async def t_create_pr(payload: dict, actor: dict):
    from app.domains.procurement.service import create_pr

    return await create_pr(payload, actor)


@agent_tool("sourcing", "create_rfq",
            description="Create and publish an RFQ to qualified vendors")
async def t_create_rfq(payload: dict, actor: dict):
    from app.domains.sourcing.service import create_event, publish_event

    evt = await create_event({**payload, "event_type": "RFQ"}, actor)
    await publish_event(evt["event_id"], actor)
    return await evt and evt


@agent_tool("procurement", "create_draft_po",
            description="Draft a purchase order (policy check on submit)")
async def t_create_po(payload: dict, actor: dict):
    from app.domains.procurement.service import create_po

    return await create_po(payload, actor)


@agent_tool("procurement", "submit_po_for_approval",
            description="Submit PO → auto-approve or route to human queue")
async def t_submit_po(payload: dict, actor: dict):
    from app.domains.procurement.service import submit_po_for_approval

    return await submit_po_for_approval(payload["po_id"], actor)


@agent_tool("procurement", "release_contract_po",
            description="Release PO against an active contract/BPA")
async def t_release_contract(payload: dict, actor: dict):
    from app.domains.sourcing.service import contract_release

    return await contract_release(payload["contract_id"], payload["lines"],
                                  payload.get("payload", {}), actor)


# ------------------------------------------------------------- SupplyChain Agent
register_agent(
    "supply-chain-agent",
    "Runs supply plans and proposes transfer/produce/buy actions",
    ["get_inventory_snapshot", "compute_supply_plan", "run_mrp",
     "accept_proposal"],
    ["planning", "inventory"],
)


@agent_tool("planning", "get_inventory_snapshot",
            description="Balances + batches snapshot")
async def t_snapshot(payload: dict, actor: dict):
    from app.domains.inventory.service import balances

    return await balances(product_id=payload.get("product_id"))


@agent_tool("planning", "compute_supply_plan",
            description="Demand vs supply with recommended action")
async def t_supply_plan(payload: dict, actor: dict):
    from app.domains.planning.service import compute_supply_plan

    return await compute_supply_plan(payload["product_id"],
                                     int(payload.get("horizon_days", 30)), actor)


@agent_tool("planning", "run_mrp",
            description="MRP run across products → proposals")
async def t_run_mrp(payload: dict, actor: dict):
    from app.domains.planning.service import run_mrp

    return await run_mrp(payload, actor)


@agent_tool("planning", "accept_proposal",
            description="Execute an accepted planning proposal")
async def t_accept_proposal(payload: dict, actor: dict):
    from app.domains.planning.service import accept_proposal

    return await accept_proposal(payload["proposal_id"], actor)


# --------------------------------------------------------------- Finance Agent
register_agent(
    "finance-agent",
    "Invoice matching investigation, credit notes, payment proposals",
    ["get_invoice_match_detail", "investigate_mismatch", "apply_credit_note",
     "create_payment_proposal"],
    ["finance"],
)


@agent_tool("finance", "get_invoice_match_detail",
            description="Read the stored match result of an invoice")
async def t_match_detail(payload: dict, actor: dict):
    inv = await db.db.supplier_invoices.find_one({"invoice_id":
                                                  payload["invoice_id"]})
    return {"match": (inv or {}).get("match"), "status": (inv or {}).get("status")}


@agent_tool("finance", "investigate_mismatch",
            description="Re-run 4-way match; self-resolution loop engages")
async def t_investigate(payload: dict, actor: dict):
    from app.domains.finance.service import match_invoice

    return await match_invoice(payload["invoice_id"], actor)


@agent_tool("finance", "apply_credit_note",
            description="Apply supplier credit note and re-match",
            requires_approval_over=1_000_000)
async def t_credit_note(payload: dict, actor: dict):
    from app.domains.finance.service import apply_credit_note

    return await apply_credit_note(payload["invoice_id"], payload, actor)


@agent_tool("finance", "create_payment_proposal",
            description="Propose AP payment run")
async def t_payment_proposal(payload: dict, actor: dict):
    from app.domains.finance.service import create_payment_proposal

    return await create_payment_proposal(payload, actor)


# ------------------------------------------------------------- Warehouse Agent
register_agent(
    "warehouse-agent",
    "Putaway proposals, pick waves, warehouse health",
    ["propose_putaway", "get_warehouse_health", "get_fefo_plan"],
    ["warehouse", "inventory"],
)


@agent_tool("warehouse", "propose_putaway",
            description="Suggest putaway bins for a GRN")
async def t_propose_putaway(payload: dict, actor: dict):
    grn = await db.db.grns.find_one({"grn_id": payload["grn_id"]})
    if not grn:
        return {"error": "GRN not found"}
    suggestions = []
    for line in grn.get("lines", []):
        suggestions.append({"line_no": line["line_no"],
                            "sku": line["sku"],
                            "suggested_location":
                                f"{grn['warehouse_id']}-GOODS-001"})
    return {"grn_id": payload["grn_id"], "suggestions": suggestions}


@agent_tool("warehouse", "get_warehouse_health",
            description="Warehouse KPIs")
async def t_wh_health(payload: dict, actor: dict):
    from app.domains.warehouse.service import warehouse_health

    return await warehouse_health()


@agent_tool("inventory", "get_fefo_plan",
            description="FEFO allocation plan for a quantity")
async def t_fefo(payload: dict, actor: dict):
    from app.domains.inventory.service import fefo_batches

    return await fefo_batches(payload["product_id"],
                              payload.get("warehouse_id"),
                              payload.get("quantity"))


# ----------------------------------------------------------------- QA/QC Agent
register_agent(
    "qa-agent",
    "Assembles release dossiers, drafts deviations, OOS summaries",
    ["assemble_batch_release_dossier", "draft_deviation", "get_oos_summary"],
    ["qa", "qc"],
)


@agent_tool("qa", "assemble_batch_release_dossier",
            description="Evidence bundle for QA batch release decision")
async def t_dossier(payload: dict, actor: dict):
    from app.domains.qa.service import batch_release_review as _d  # noqa
    from app.domains.qa import service as qa_svc

    d = await qa_svc.batch_release_review(payload["production_order_id"], actor)
    return d


@agent_tool("qa", "draft_deviation",
            description="Draft a deviation record for QA review")
async def t_draft_dev(payload: dict, actor: dict):
    from app.domains.qa.service import create_deviation

    return await create_deviation({**payload, "source": "AGENT_DRAFT"}, actor)


@agent_tool("qc", "get_oos_summary",
            description="OOS queue summary")
async def t_oos(payload: dict, actor: dict):
    from app.domains.qc.service import oos_summary

    return await oos_summary()


# -------------------------------------------------------------- Pharmacy Agent
register_agent(
    "pharmacy-agent",
    "Prepares prescription reviews (pharmacist decides)",
    ["match_prescription_to_master", "get_pharmacist_queue"],
    ["pharmacy"],
)


@agent_tool("pharmacy", "match_prescription_to_master",
            description="Re-run matching + compliance checks on a prescription")
async def t_match_rx(payload: dict, actor: dict):
    from app.domains.pharmacy.service import match_and_validate

    return await match_and_validate(payload["rx_id"], actor)


@agent_tool("pharmacy", "get_pharmacist_queue",
            description="Prescriptions awaiting pharmacist review")
async def t_rx_queue(payload: dict, actor: dict):
    from app.domains.pharmacy.service import pharmacist_queue

    return await pharmacist_queue()


# ------------------------------------------------------------- Logistics Agent
register_agent(
    "logistics-agent",
    "Carrier selection, ETA estimation, shipment tracking",
    ["select_carrier", "estimate_eta", "get_shipment_status"],
    ["logistics"],
)


@agent_tool("logistics", "select_carrier",
            description="Choose best carrier for a shipment")
async def t_carrier(payload: dict, actor: dict):
    from app.domains.logistics.service import select_carrier

    return await select_carrier(payload, actor)


@agent_tool("logistics", "estimate_eta",
            description="ETA estimate")
async def t_eta(payload: dict, actor: dict):
    from app.domains.logistics.service import estimate_eta

    return await estimate_eta(payload, actor)


@agent_tool("logistics", "get_shipment_status",
            description="Shipment status + tracking events")
async def t_ship_status(payload: dict, actor: dict):
    from app.domains.logistics.service import get_shipment

    return await get_shipment(payload["shipment_id"])


# ----------------------------------------------------------------- Sales Agent
register_agent(
    "sales-agent",
    "Lead scoring, quotation drafts, order document intake",
    ["score_lead", "intake_customer_po", "understand_inquiry"],
    ["crm", "sales"],
)


@agent_tool("crm", "score_lead",
            description="Enrich + score a lead")
async def t_score_lead(payload: dict, actor: dict):
    from app.domains.crm.service import score_lead

    return await score_lead(payload["lead_id"], actor)


@agent_tool("sales", "intake_customer_po",
            description="Customer PO document intake (Document AI)")
async def t_intake_po(payload: dict, actor: dict):
    from app.domains.sales.service import intake_customer_po

    return await intake_customer_po(payload, actor)


@agent_tool("crm", "understand_inquiry",
            description="Extract requirements from an inquiry")
async def t_understand(payload: dict, actor: dict):
    from app.domains.crm.service import understand_inquiry

    return await understand_inquiry(payload["inq_id"], actor)


# ------------------------------------------------------- Customer Service Agent
register_agent(
    "customer-service-agent",
    "Order status answers and proactive customer notifications",
    ["get_order_status", "notify_customer"],
    ["sales"],
)


@agent_tool("sales", "get_order_status",
            description="Order status for customer service")
async def t_order_status(payload: dict, actor: dict):
    from app.domains.sales.service import get_order

    so = await get_order(payload["order_id"])
    return {"order_id": so["order_id"], "status": so["status"],
            "total": so.get("total_amount"),
            "timeline": so.get("timeline", [])}


@agent_tool("sales", "notify_customer",
            description="Send simulated customer notification")
async def t_notify(payload: dict, actor: dict):
    from app.core.notifications import notify

    return {"notification_id": await notify(
        "ORDER_CONFIRMATION_CREATED", payload.get("subject", "Update"),
        payload.get("body", ""), payload.get("recipients", []),
        payload.get("entity_type"), payload.get("entity_id"))}


# ------------------------------------------------------------ Compliance Agent
register_agent(
    "compliance-agent",
    "Licence expiry sweeps and SoD checks",
    ["check_licence_expiries", "sod_report", "initiate_recall"],
    ["compliance", "reverse"],
)


@agent_tool("compliance", "check_licence_expiries",
            description="Sweep licences for expiry warnings")
async def t_licences(payload: dict, actor: dict):
    from app.domains.compliance.service import licence_status_sweep

    return await licence_status_sweep(actor)


@agent_tool("compliance", "sod_report",
            description="Segregation-of-duties conflict report")
async def t_sod(payload: dict, actor: dict):
    from app.domains.compliance.service import sod_report

    return await sod_report()


@agent_tool("reverse", "initiate_recall",
            description="Initiate recall with global batch block")
async def t_recall(payload: dict, actor: dict):
    from app.domains.reverse.service import create_recall

    return await create_recall(payload, actor)


# -------------------------------------------------------------- Document AI Agent
register_agent(
    "docai-agent",
    "Classify, extract and validate business documents",
    ["process_document", "get_document"],
    ["documents"],
)


@agent_tool("documents", "process_document",
            description="Run the Document AI pipeline on a document")
async def t_process_doc(payload: dict, actor: dict):
    from app.core.docai import process

    return await process(payload["document_id"])


@agent_tool("documents", "get_document",
            description="Fetch document processing state")
async def t_get_doc(payload: dict, actor: dict):
    doc = await db.db.documents.find_one({"document_id":
                                          payload["document_id"]})
    if not doc:
        return {"error": "not found"}
    doc.pop("_id", None)
    return doc


# -------------------------------------------------------------- Analytics Agent
register_agent(
    "analytics-agent",
    "Command-center KPI computation",
    ["get_command_center_kpis", "get_department_health"],
    ["analytics"],
)


@agent_tool("analytics", "get_command_center_kpis",
            description="Enterprise KPIs for the Command Center")
async def t_kpis(payload: dict, actor: dict):
    from app.api.routes_analytics import command_center_payload

    return await command_center_payload()


@agent_tool("analytics", "get_department_health",
            description="Per-department health snapshot")
async def t_dept_health(payload: dict, actor: dict):
    from app.api.routes_analytics import department_health_payload

    return await department_health_payload()


AGENT_TOOLS_MAP = {
    "procurement-agent": [t_get_inventory, t_get_open_pos, t_get_contracts,
                          t_create_pr, t_create_rfq, t_create_po, t_submit_po,
                          t_release_contract],
    "supply-chain-agent": [t_snapshot, t_supply_plan, t_run_mrp,
                           t_accept_proposal],
    "finance-agent": [t_match_detail, t_investigate, t_credit_note,
                      t_payment_proposal],
    "warehouse-agent": [t_propose_putaway, t_wh_health, t_fefo],
    "qa-agent": [t_dossier, t_draft_dev, t_oos],
    "pharmacy-agent": [t_match_rx, t_rx_queue],
    "logistics-agent": [t_carrier, t_eta, t_ship_status],
    "sales-agent": [t_score_lead, t_intake_po, t_understand],
    "customer-service-agent": [t_order_status, t_notify],
    "compliance-agent": [t_licences, t_sod, t_recall],
    "docai-agent": [t_process_doc, t_get_doc],
    "analytics-agent": [t_kpis, t_dept_health],
}
