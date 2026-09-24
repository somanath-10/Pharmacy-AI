"""Domain agents: every tool is a governed function behind the gateway.

Agents return data or drafts; persistence always flows through domain APIs
(state machines + events + audit apply).
"""
from typing import Any, Dict

from app.agents.gateway import agent_tool, register_agent
from app.core.config import settings
from app.core.database import db, now_iso


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


# ------------------------------------------------------------ Procurement Agent
register_agent(
    "procurement-agent",
    "Automates PR/PO creation, RFQ publication and contract releases",
    ["get_inventory", "get_open_purchase_orders", "get_active_contracts",
     "create_draft_pr", "create_rfq", "create_draft_po",
     "submit_po_for_approval", "release_contract_po",
     "track_po_acknowledgement", "chase_pending_asn", "monitor_po_delays"],
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
    return await publish_event(evt["event_id"], actor)


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


@agent_tool("procurement", "track_po_acknowledgement",
            description="Sent POs awaiting supplier acknowledgement")
async def t_track_ack(payload: dict, actor: dict):
    stale = []
    cutoff_days = int(payload.get("older_than_days", 3))
    cutoff = _days_ago_iso(cutoff_days)
    async for po in db.db.purchase_orders.find(
            {"status": "SENT", "updated_at": {"$lt": cutoff}}).limit(100):
        stale.append({"po_id": po["po_id"], "vendor_id": po["vendor_id"],
                      "sent_at": po.get("updated_at"),
                      "total": po.get("total_amount")})
    return {"awaiting_ack": stale, "count": len(stale)}


@agent_tool("procurement", "chase_pending_asn",
            description="Acknowledged POs with no ASN yet (vendor follow-up list)")
async def t_chase_asn(payload: dict, actor: dict):
    out = []
    async for po in db.db.purchase_orders.find(
            {"status": {"$in": ["ACKNOWLEDGED", "PARTIALLY_RECEIVED"]}}).limit(100):
        asn = await db.db.asns.find_one({"po_id": po["po_id"]})
        if not asn:
            out.append({"po_id": po["po_id"], "vendor_id": po["vendor_id"],
                        "needed_by": po.get("needed_by")})
    return {"pending_asn": out, "count": len(out)}


@agent_tool("procurement", "monitor_po_delays",
            description="Open POs past their needed_by date")
async def t_po_delays(payload: dict, actor: dict):
    today = now_iso()[:10]
    delayed = []
    async for po in db.db.purchase_orders.find({
            "status": {"$in": ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"]},
            "needed_by": {"$lt": today}}).limit(100):
        delayed.append({"po_id": po["po_id"], "vendor_id": po["vendor_id"],
                        "needed_by": po.get("needed_by"),
                        "status": po["status"]})
    return {"delayed": delayed, "count": len(delayed)}


def _days_ago_iso(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ------------------------------------------------------------- SupplyChain Agent
register_agent(
    "supply-chain-agent",
    "Runs supply plans and proposes transfer/produce/buy actions",
    ["get_inventory_snapshot", "compute_supply_plan", "run_mrp",
     "accept_proposal", "forecast_demand", "detect_expiry_and_excess",
     "recommend_transfer_vs_buy"],
    ["planning", "inventory"],
)


@agent_tool("planning", "forecast_demand",
            description="Deterministic demand forecast (3-month moving average)")
async def t_forecast(payload: dict, actor: dict):
    from app.domains.planning.service import forecast_for

    return await forecast_for(payload["product_id"])


@agent_tool("planning", "detect_expiry_and_excess",
            description="Batches near expiry + warehouses holding excess stock")
async def t_expiry_excess(payload: dict, actor: dict):
    from app.domains.inventory.service import batches_near_expiry, balances

    days = int(payload.get("days", 90))
    near = await batches_near_expiry(days)
    out = {"near_expiry": near[:50], "excess": []}
    if payload.get("product_id"):
        rows = await balances(product_id=payload["product_id"])
        product = await db.db.products.find_one({"sku": payload["product_id"]}) or {}
        safety = float(product.get("safety_stock") or 0)
        by_wh: Dict[str, float] = {}
        for r in rows:
            if r.get("stock_status") == "AVAILABLE" and r.get("batch_id") is None:
                by_wh[r["warehouse_id"]] = by_wh.get(r["warehouse_id"], 0) \
                    + float(r.get("quantity") or 0)
        out["excess"] = [{"warehouse_id": w, "excess_qty": round(q - safety, 2)}
                         for w, q in by_wh.items() if q > safety]
    return out


@agent_tool("planning", "recommend_transfer_vs_buy",
            description="Decision-ladder recommendation with rationale")
async def t_transfer_vs_buy(payload: dict, actor: dict):
    from app.domains.planning.service import compute_supply_plan

    plan = await compute_supply_plan(payload["product_id"], actor=actor)
    return {"action": plan["action"], "reason": plan["reason"],
            "net_requirement": plan["net_requirement"],
            "options": plan.get("options", []), "anomaly": plan.get("anomaly")}


# ------------------------------------------------------- Vendor / Sourcing Agent
register_agent(
    "vendor-sourcing-agent",
    "Vendor document checks, licence expiry monitoring, qualified-vendor "
    "discovery, RFQ creation, bid comparison, auction analysis, BRA prep, "
    "vendor scoring",
    ["check_vendor_documents", "monitor_licence_expiry", "find_qualified_vendors",
     "create_vendor_rfq", "compare_bids", "analyze_auction",
     "prepare_award_recommendation", "vendor_score", "auto_block_expired_licences"],
    ["vendors", "sourcing", "compliance"],
)


@agent_tool("vendors", "check_vendor_documents",
            description="Missing/expired vendor documents for onboarding")
async def t_vendor_docs(payload: dict, actor: dict):
    from app.domains.vendors.service import get_vendor

    vendor = await get_vendor(payload["vendor_id"])
    docs = [_d async for _d in
            db.db.vendor_documents.find({"vendor_id": payload["vendor_id"]})]
    have = {d["doc_type"] for d in docs}
    required = ["COMPANY_REGISTRATION", "TAX_CERTIFICATE", "DRUG_LICENCE",
                "BANK_DETAILS"]
    missing = [d for d in required if d not in have]
    expired = [d["doc_type"] for d in docs
               if d.get("expiry_date") and str(d["expiry_date"])[:10]
               < now_iso()[:10]]
    return {"vendor_id": payload["vendor_id"], "missing": missing,
            "expired": expired,
            "complete": not missing, "lifecycle": vendor["status"]}


@agent_tool("vendors", "monitor_licence_expiry",
            description="Drug licences expiring within N days")
async def t_licence_expiry(payload: dict, actor: dict):
    from app.domains.vendors.service import licence_expiry_sweep

    return {"warnings": await licence_expiry_sweep(
        int(payload.get("days", settings.LICENCE_EXPIRY_WARN_DAYS)))}


@agent_tool("vendors", "auto_block_expired_licences",
            description="Auto-block vendors whose drug licence lapsed (policy)")
async def t_auto_block(payload: dict, actor: dict):
    from app.domains.vendors.service import auto_block_on_licence_expiry

    return {"blocked": await auto_block_on_licence_expiry(actor)}


@agent_tool("vendors", "find_qualified_vendors",
            description="QA-approved vendors for a product at a site")
async def t_qualified_vendors(payload: dict, actor: dict):
    q: Dict[str, Any] = {"status": "QA_APPROVED",
                         "valid_to": {"$gte": now_iso()}}
    if payload.get("product_id"):
        q["product_id"] = payload["product_id"]
    if payload.get("site_id"):
        q["site_id"] = payload["site_id"]
    rows = [_clean(dict(r)) async for r in
            db.db.vendor_qualifications.find(q)]
    return {"qualifications": rows,
            "vendor_ids": sorted({r["vendor_id"] for r in rows})}


@agent_tool("sourcing", "create_vendor_rfq",
            description="Create + publish an RFQ to qualified vendors")
async def t_vendor_rfq(payload: dict, actor: dict):
    from app.domains.sourcing.service import create_event, publish_event

    evt = await create_event({**payload, "event_type": "RFQ"}, actor)
    published = await publish_event(evt["event_id"], actor)
    return published


@agent_tool("sourcing", "compare_bids",
            description="Weighted multi-factor deterministic bid ranking")
async def t_compare_bids(payload: dict, actor: dict):
    from app.domains.sourcing.service import rank_bids

    return await rank_bids(payload["event_id"], payload.get("weights"))


@agent_tool("sourcing", "analyze_auction",
            description="Deterministic auction outcome analysis")
async def t_auction_analysis(payload: dict, actor: dict):
    auc = await db.db.auctions.find_one({"auction_id": payload["auction_id"]})
    if not auc:
        return {"error": "auction not found"}
    log_ = auc.get("bid_log") or []
    if not log_:
        return {"auction_id": payload["auction_id"], "bids": 0,
                "winner": None, "analysis": "no bids"}
    winner = log_[-1]
    start = float(auc.get("start_price") or 0)
    low = float(winner["price"])
    return {"auction_id": payload["auction_id"], "bids": len(log_),
            "winner_vendor_id": winner["vendor_id"],
            "winning_price": low,
            "savings_pct": round(100.0 * (start - low) / start, 2) if start else None,
            "analysis": "deterministic lowest-decrement winner"}


@agent_tool("sourcing", "prepare_award_recommendation",
            description="Draft BRA from weighted ranking (human approves)")
async def t_prepare_bra(payload: dict, actor: dict):
    from app.domains.sourcing.service import rank_bids, recommend_award

    ranked = await rank_bids(payload["event_id"], payload.get("weights"))
    if not ranked["ranking"]:
        return {"error": "no bids to recommend"}
    best = ranked["ranking"][0]
    rec = await recommend_award(payload["event_id"],
                                {"bid_id": best["bid_id"],
                                 "weights": payload.get("weights")},
                                actor)
    return {"rec_id": rec["rec_id"], "status": rec["status"],
            "vendor_id": rec["vendor_id"],
            "ranking_top": ranked["ranking"][:3],
            "ai_analysis": rec.get("ai_analysis")}


@agent_tool("vendors", "vendor_score",
            description="Composite vendor performance score (OTD/quality/risk)")
async def t_vendor_score(payload: dict, actor: dict):
    from app.domains.vendors.service import performance

    return await performance(payload["vendor_id"])


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
    "Putaway proposals, pick waves, warehouse health, expiry/variance detection, "
    "replenishment and transfer proposals",
    ["propose_putaway", "get_warehouse_health", "get_fefo_plan",
     "detect_expiry_risk", "get_stock_variances", "create_replenishment_task",
     "propose_transfer"],
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

@agent_tool("inventory", "detect_expiry_risk",
            description="Batches expiring within the warning horizon")
async def t_expiry_risk(payload: dict, actor: dict):
    from app.core.config import get_settings
    from app.domains.inventory.service import batches_near_expiry

    days = int(payload.get("days")
               or get_settings().expiry_risk_warn_days)
    return {"horizon_days": days,
            "batches": await batches_near_expiry(days)}


@agent_tool("warehouse", "get_stock_variances",
            description="Recent cycle counts with unresolved variance")
async def t_variances(payload: dict, actor: dict):
    q = {"status": "VARIANCE_DETECTED", "adjustment_posted": {"$ne": True}}
    out = []
    async for c in db.db.cycle_counts.find(q).sort("created_at", -1).limit(50):
        c.pop("_id", None)
        out.append(c)
    return {"unresolved": len(out), "counts": out}


@agent_tool("warehouse", "create_replenishment_task",
            description="Create a replenishment task (system decides source "
                        "and destination; human confirms the physical move)")
async def t_replenish(payload: dict, actor: dict):
    from app.domains.warehouse.service import create_replenishment_task

    return await create_replenishment_task(payload, actor)


@agent_tool("warehouse", "propose_transfer",
            description="Propose a stock transfer between warehouses "
                        "(deterministic feasibility check; approval per policy)")
async def t_transfer_proposal(payload: dict, actor: dict):
    from app.domains.inventory.service import availability

    av = await availability(payload["product_id"])
    dest = av.get("by_warehouse", {}).get(payload["to_warehouse"], 0)
    need = float(payload["quantity"])
    if dest >= need:
        return {"transfer_required": False,
                "reason": "destination already holds sufficient stock",
                "destination_on_hand": dest}
    return {"transfer_required": True,
            "from_warehouse": payload.get("from_warehouse"),
            "to_warehouse": payload["to_warehouse"],
            "product_id": payload["product_id"],
            "shortfall": round(need - float(dest or 0), 3),
            "note": "execute via transfer order API with approval per policy"}


# ----------------------------------------------------------------- QA/QC Agent
register_agent(
    "qa-agent",
    "Assembles release dossiers, drafts deviations, OOS summaries, CAPA "
    "monitoring, recurring-issue detection. NEVER releases batches or closes "
    "OOS/cases autonomously",
    ["assemble_batch_release_dossier", "draft_deviation", "get_oos_summary",
     "get_capa_overdue", "detect_recurring_quality_issues", "equipment_due_report"],
    ["qa", "qc"],
)


@agent_tool("qa", "assemble_batch_release_dossier",
            description="Evidence bundle for QA batch release decision")
async def t_dossier(payload: dict, actor: dict):
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

@agent_tool("qa", "get_capa_overdue",
            description="Overdue CAPA monitoring report (read-only)")
async def t_capa_overdue(payload: dict, actor: dict):
    from app.domains.qa.service import capa_overdue_report

    return await capa_overdue_report()


@agent_tool("qa", "detect_recurring_quality_issues",
            description="Cluster OOS tests and deviations by product/test to "
                        "surface recurring quality issues")
async def t_recurring(payload: dict, actor: dict):
    from collections import Counter

    oos_counter: Counter = Counter()
    async for smp in db.db.qc_samples.find({"oos": {"$ne": None}}):
        for t in smp.get("tests", []):
            if t.get("verdict") == "FAIL":
                oos_counter[f"{smp.get('product_id')}:{t['name']}"] += 1
    dev_counter: Counter = Counter()
    async for d in db.db.deviations.find({}):
        dev_counter[d.get("category") or "UNCLASSIFIED"] += 1
    return {"recurring_oos": oos_counter.most_common(10),
            "deviations_by_category": dev_counter.most_common(10)}


@agent_tool("qa", "equipment_due_report",
            description="Equipment calibration/maintenance due + expired report")
async def t_equipment_due(payload: dict, actor: dict):
    from app.domains.masters.service import equipment_due_report

    return await equipment_due_report()


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
    "Carrier selection, ETA estimation, shipment tracking, delay detection",
    ["select_carrier", "estimate_eta", "get_shipment_status",
     "flag_delayed_shipments"],
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

@agent_tool("logistics", "flag_delayed_shipments",
            description="Outbound shipments past ETA or with delivery exceptions")
async def t_delayed(payload: dict, actor: dict):
    now = now_iso()
    out = []
    async for sh in db.db.shipments.find({
            "direction": {"$ne": "INBOUND"},
            "status": {"$nin": ["DELIVERED", "CLOSED", "CANCELLED"]}}):
        eta = str(sh.get("eta") or "")[:10]
        if eta and eta < now[:10] or sh.get("last_exception"):
            out.append({"shipment_id": sh["shipment_id"],
                        "status": sh["status"], "eta": eta,
                        "exception": sh.get("last_exception")})
    return {"delayed_count": len(out), "shipments": out}


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
                          t_release_contract, t_track_ack, t_chase_asn,
                          t_po_delays],
    "supply-chain-agent": [t_snapshot, t_supply_plan, t_run_mrp,
                          t_accept_proposal, t_forecast, t_expiry_excess,
                          t_transfer_vs_buy],
    "vendor-sourcing-agent": [t_vendor_docs, t_licence_expiry, t_auto_block,
                              t_qualified_vendors, t_vendor_rfq, t_compare_bids,
                              t_auction_analysis, t_prepare_bra, t_vendor_score],
    "finance-agent": [t_match_detail, t_investigate, t_credit_note,
                      t_payment_proposal],
    "warehouse-agent": [t_propose_putaway, t_wh_health, t_fefo,
                        t_expiry_risk, t_variances, t_replenish,
                        t_transfer_proposal],
    "qa-agent": [t_dossier, t_draft_dev, t_oos, t_capa_overdue, t_recurring,
                 t_equipment_due],
    "pharmacy-agent": [t_match_rx, t_rx_queue],
    "logistics-agent": [t_carrier, t_eta, t_ship_status, t_delayed],
    "sales-agent": [t_score_lead, t_intake_po, t_understand],
    "customer-service-agent": [t_order_status, t_notify],
    "compliance-agent": [t_licences, t_sod, t_recall],
    "docai-agent": [t_process_doc, t_get_doc],
    "analytics-agent": [t_kpis, t_dept_health],
}


# ==================================================================
# Phase: Plant Agent (new) + Sales/CS/Pharmacy additions for the
# Plant-CRM-Sales-Pharmacy-O2C chain. All tools are read/propose or
# flow through domain APIs — no direct Mongo writes outside gateways.
# ==================================================================
register_agent(
    "plant-agent",
    "Production planning, material requirements, shortage detection, batch "
    "progress, yield anomaly detection",
    ["material_requirements", "production_plan_draft", "batch_progress",
     "yield_anomalies", "trace_batch"],
    ["production", "inventory"],
)


@agent_tool("production", "material_requirements",
            description="Explode BOM into material requirements with shortages")
async def t_mat_req(payload: dict, actor: dict):
    from app.domains.production.service import material_requirement

    return await material_requirement(payload["product_id"],
                                      float(payload["batch_size"]))


@agent_tool("production", "production_plan_draft",
            description="Draft a production plan (feasibility evaluated, no stock moves)",
            requires_approval_over=1_000_000)
async def t_plan_draft(payload: dict, actor: dict):
    from app.domains.production.service import create_production_plan

    return await create_production_plan(payload, actor)


@agent_tool("production", "batch_progress",
            description="Progress %, eBMR steps, waste for a production order")
async def t_batch_progress(payload: dict, actor: dict):
    from app.domains.production.service import batch_progress

    return await batch_progress(payload["order_id"])


@agent_tool("production", "yield_anomalies",
            description="Recent batches with yield below threshold")
async def t_yield_anom(payload: dict, actor: dict):
    from app.domains.production.service import yield_anomalies

    return await yield_anomalies(int(payload.get("window", 50)))


@agent_tool("production", "trace_batch",
            description="Forward+backward batch traceability (recall-ready)")
async def t_trace(payload: dict, actor: dict):
    from app.domains.production.service import trace_batch

    return await trace_batch(payload["batch_id"])


# Sales Agent: quotation drafting + order proposals (agents propose, humans approve)
@agent_tool("sales", "draft_quotation_from_rfq",
            description="Prepare a quotation from an understood inquiry/RFQ")
async def t_quote_draft(payload: dict, actor: dict):
    from app.domains.sales.service import quotation_from_rfq

    return await quotation_from_rfq(payload["inq_id"], actor,
                                    float(payload.get("discount_pct", 0)))


@agent_tool("sales", "order_status_sweep",
            description="Orders stuck in a status (agent follow-up list)")
async def t_order_sweep(payload: dict, actor: dict):
    from app.core.database import db

    stuck = []
    async for so in db.db.sales_orders.find({
            "status": {"$in": list(payload.get("statuses",
                                               ["DRAFT", "CONFIRMED", "ALLOCATED"]))}}) \
            .limit(100):
        created = so.get("created_at", "")
        if created and created < payload.get("older_than", ""):
            stuck.append({"order_id": so["order_id"], "customer": so.get("customer_id"),
                          "status": so["status"], "age_since": created})
    return {"stuck_count": len(stuck), "orders": stuck[:25]}


# Customer Service Agent: full status answer in one call
@agent_tool("sales", "get_full_order_status",
            description="Order + shipment + invoice status for customer answers")
async def t_full_status(payload: dict, actor: dict):
    from app.domains.sales.service import get_order

    so = await get_order(payload["order_id"])
    shipments = []
    async for sh in db.db.shipments.find({"sales_order_id": so["order_id"]}):
        shipments.append({"shipment_id": sh.get("shipment_id"),
                          "status": sh.get("status"),
                          "carrier": sh.get("carrier_id"),
                          "eta": sh.get("eta"),
                          "exception": sh.get("delivery_exception") is not None})
    invoices = []
    async for inv in db.db.customer_invoices.find({"sales_order_id":
                                                   so["order_id"]}):
        invoices.append({"invoice_id": inv.get("invoice_id"),
                         "status": inv.get("status"),
                         "balance": inv.get("balance_amount")})
    return {"order_id": so["order_id"], "status": so["status"],
            "lines": [{"sku": l["sku"], "ordered": l["quantity"],
                       "shipped": l.get("shipped_qty", 0),
                       "backorder": l.get("backorder_qty", 0)} for l in so["lines"]],
            "shipments": shipments, "invoices": invoices}


# Pharmacy Agent: stock check + review prep for prescriptions
@agent_tool("pharmacy", "rx_stock_check",
            description="Availability of every matched medicine on a prescription")
async def t_rx_stock(payload: dict, actor: dict):
    from app.core.database import db

    from app.domains.inventory.service import availability

    rx = await db.db.prescriptions.find_one({"rx_id": payload["rx_id"]})
    if not rx:
        return {"error": "not found"}
    out = []
    for m in rx.get("matched", []):
        if m.get("matched") and m.get("product_id"):
            a = await availability(m["product_id"])
            out.append({"product_id": m["product_id"],
                        "atp": a.get("available_to_promise", 0),
                        "sufficient": a.get("available_to_promise", 0) >=
                        float((m.get("raw") or {}).get("dispense_qty") or 1)})
    return {"rx_id": payload["rx_id"], "stock": out}


@agent_tool("pharmacy", "rx_history",
            description="Prior prescriptions/dispenses for a patient (review prep)")
async def t_rx_history(payload: dict, actor: dict):
    from app.domains.pharmacy.service import prescription_history, dispense_history

    return {"prescriptions": await prescription_history(
                patient=payload.get("patient"), limit=10),
            "dispenses": await dispense_history(
                patient=payload.get("patient"), limit=10)}


AGENT_TOOLS_MAP.update({
    "plant-agent": [t_mat_req, t_plan_draft, t_batch_progress, t_yield_anom,
                    t_trace],
    "sales-agent": [t_score_lead, t_intake_po, t_understand, t_quote_draft,
                    t_order_sweep],
    "customer-service-agent": [t_order_status, t_notify, t_full_status],
    "pharmacy-agent": [t_match_rx, t_rx_queue, t_rx_stock, t_rx_history],
})


# ==================================================================
# Phase: Finance + Returns + Recall + PV + Agents.
# Finance agent completion, Compliance anomaly sweep, new PV agent.
# All tools flow through the governed gateway (permission + policy +
# domain API + event + audit). Agents never write Mongo directly.
# ==================================================================

# ---------------- Finance Agent: finance operations ----------------
@agent_tool("finance", "duplicate_scan",
            description="Scan supplier invoices for duplicate submissions")
async def t_dup_scan(payload: dict, actor: dict):
    seen, dups = set(), []
    async for inv in db.db.supplier_invoices.find({}).sort("created_at", -1):
        k = (str(inv.get("vendor_id")), str(inv.get("invoice_number", "")).strip().upper())
        if not k[1]:
            continue
        if k in seen:
            dups.append({"invoice_id": inv.get("invoice_id"),
                         "duplicate_of": inv.get("invoice_number"),
                         "vendor_id": inv.get("vendor_id")})
        seen.add(k)
    return {"duplicates": dups[:20], "scanned": True}


@agent_tool("finance", "ar_aging",
            description="Accounts-receivable aging buckets (0-30/31-60/61-90/90+)")
async def t_ar_aging(payload: dict, actor: dict):
    from app.domains.finance.service import ar_aging

    return await ar_aging()


@agent_tool("finance", "collection_reminders",
            description="Queue overdue-invoice collection reminders")
async def t_collections(payload: dict, actor: dict):
    from app.domains.finance.service import collection_reminders

    return await collection_reminders(actor)


@agent_tool("finance", "recon_status",
            description="Bank reconciliation run status and unreconciled items")
async def t_recon_status(payload: dict, actor: dict):
    rows = []
    async for r in db.db.reconciliations.find({}).sort("created_at", -1).limit(10):
        r.pop("_id", None)
        rows.append(r)
    open_inv = await db.db.supplier_invoices.count_documents(
        {"status": {"$in": ["PENDING_MATCH", "MISMATCH"]}})
    return {"recent_runs": rows, "unmatched_supplier_invoices": open_inv}


# ---------------- Compliance Agent: anomaly sweep ----------------
@agent_tool("compliance", "anomaly_sweep",
            description="Security/financial anomaly sweep (role changes, "
                        "after-hours payments, failed approvals, policy hits)")
async def t_anomaly_sweep(payload: dict, actor: dict):
    from app.domains.compliance.service import anomaly_sweep

    return await anomaly_sweep(actor)


# ---------------- PV Agent (new) ----------------
register_agent(
    "pv-agent",
    "Pharmacovigilance assistant: AE intake prep, duplicate detection, "
    "follow-up tracking, case summaries. Medical/regulatory decisions stay human.",
    ["ae_duplicate_precheck", "case_summary", "followup_queue"],
    ["safety"],
)


@agent_tool("safety", "ae_duplicate_precheck",
            description="Pre-intake duplicate check for an adverse event")
async def t_ae_dup(payload: dict, actor: dict):
    from app.domains.safety.service import duplicate_check

    return await duplicate_check(payload["case_id"], actor)


@agent_tool("safety", "case_summary",
            description="Structured case summary for medical review prep")
async def t_case_summary(payload: dict, actor: dict):
    from app.core.database import db as _db

    c = await _db.db.safety_cases.find_one({"case_id": payload["case_id"]})
    if not c:
        raise ValueError(f"Case {payload['case_id']} not found")
    c.pop("_id", None)
    ae = None
    if c.get("ae_id"):
        ae = await _db.db.adverse_events.find_one({"ae_id": c["ae_id"]})
        if ae:
            ae.pop("_id", None)
    return {"case": c, "adverse_event": ae,
            "serious": bool((ae or {}).get("seriousness") or c.get("serious"))}


@agent_tool("safety", "followup_queue",
            description="Safety cases with open follow-ups due")
async def t_followup_queue(payload: dict, actor: dict):
    rows = []
    async for c in db.db.safety_cases.find(
            {"status": {"$in": ["TRIAGED", "UNDER_REVIEW", "FOLLOW_UP",
                                "AWAITING_FOLLOWUP", "OPEN"]}}
    ).limit(50):
        c.pop("_id", None)
        rows.append({"case_id": c.get("case_id"), "status": c.get("status"),
                     "follow_up_due": c.get("follow_up_due"),
                     "serious": c.get("serious")})
    return {"cases": rows}


AGENT_TOOLS_MAP.update({
    "finance-agent": [t_match_detail, t_investigate, t_credit_note,
                      t_payment_proposal, t_dup_scan, t_ar_aging,
                      t_collections, t_recon_status],
    "compliance-agent": [t_licences, t_sod, t_recall, t_anomaly_sweep],
    "pv-agent": [t_ae_dup, t_case_summary, t_followup_queue],
})
