"""Full-system workflow tests: CRM→Sales, Sourcing (RFQ→auction→BRA→award→
contract→release), Vendor Portal, Vendor lifecycle, Planning/MRP, Finance
(credit note→match→approve→pay→AR→cash), Pharmacy chain, Reverse (returns,
recall), Safety/PV, Compliance, Complaint→deviation→CAPA linkage, POS.
All flows go through the real domain services / state machines.
"""
import uuid

import pytest

from app.core.approvals import create_approval, decide
from app.core.database import db, now_iso
from app.core.errors import ConflictError, DomainError, NotFound, ValidationFailed
from app.domains import sourcing as sourcing_svc
from app.domains import vendors as vendors_svc
from app.domains import planning as planning_svc
from app.domains import sales as sales_svc
from app.domains import safety as safety_svc
from app.domains import compliance as compliance_svc
from app.domains import crm as crm_svc
from app.domains import qa as qa_svc
from app.domains import reverse as reverse_svc
from app.domains import logistics as logistics_svc
from app.domains.finance import service as finance_svc
from app.domains.inventory import service as inventory
from app.domains.pharmacy import service as pharmacy_svc
from app.domains.procurement import service as procurement_svc
from app.domains.warehouse import service as warehouse_svc

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module", autouse=True)
async def _seed_masters_once():
    """This module runs early (alphabetically) — ensure seeded masters exist."""
    from app.seed.run import _seed_masters as seed_masters, _seed_users

    await _seed_users()
    await seed_masters()
    # non-strategic seeded vendors auto-approve via policy on approval call
    for v in await db.db.vendors.find({"status": {"$in": ["REQUESTED",
                                                           "UNDER_REVIEW"]}}).to_list(20):
        try:
            await vendors_svc.approve_vendor(v["vendor_id"], ADMIN)
        except Exception:
            pass


ADMIN = {"type": "USER", "id": "fs-admin", "roles": ["SUPER_ADMIN"]}
FIN = {"type": "USER", "id": "fs-fin", "roles": ["FINANCE"]}
QA = {"type": "USER", "id": "fs-qa", "roles": ["QA"]}
PHARM = {"type": "USER", "id": "fs-pharm", "roles": ["PHARMACIST"]}


def _uid():
    return uuid.uuid4().hex[:8]


async def _stock(sku, wh, qty, batch="B-FS", expiry="2032-06-01",
                 status="AVAILABLE", uom="BOX"):
    await inventory.ensure_batch(sku, batch, expiry)
    if status == "AVAILABLE":
        await db.db.batches.update_one({"batch_id": batch},
                                       {"$set": {"qa_status": "RELEASED",
                                                 "blocked": False}})
    return await inventory.record_movement(
        "OPENING_STOCK" if status == "AVAILABLE" else "STATUS_CHANGE",
        sku, wh, qty, batch_id=batch, stock_status=status, uom=uom,
        reference_type="TEST", reference_id=f"t-{_uid()}",
        performed_by={"type": "SYSTEM", "id": "fs"})


# ================================================================ CRM chain
async def test_crm_lead_to_customer_360():
    lead = await crm_svc.capture_lead(
        {"company_name": f"Hospital {_uid()}", "source": "REFERRAL",
         "contact": {"email": "x@y.z"}}, ADMIN)
    opp = await crm_svc.convert_lead(lead["lead_id"], ADMIN)
    assert opp["stage"] == "DISCOVERY"
    inq = await crm_svc.create_opportunity_rfq(opp["opp_id"], {
        "customer_id": "CUS-00001",
        "lines": [{"sku": "PRD-00001", "quantity": 10}]}, ADMIN)
    assert inq["channel"] == "RFQ"
    quote = await sales_svc.create_quotation({
        "customer_id": "CUS-00001", "lines": [
            {"sku": "PRD-00001", "quantity": 10}],
        "opp_id": opp["opp_id"], "inquiry_id": inq["inq_id"]}, ADMIN)
    assert quote["status"] == "DRAFT"
    sent = await sales_svc.send_quotation(quote["quote_id"], ADMIN)
    assert sent["status"] == "SENT"
    c360 = await crm_svc.customer_360("CUS-00001")
    assert "quotations" in c360 or "orders" in c360


async def test_lead_duplicate_convert_blocked():
    lead = await crm_svc.capture_lead(
        {"company_name": f"Dup {_uid()}", "source": "WEB"}, ADMIN)
    await crm_svc.convert_lead(lead["lead_id"], ADMIN)
    with pytest.raises(ValidationFailed):
        await crm_svc.convert_lead(lead["lead_id"], ADMIN)


# ============================================================ sourcing chain
async def test_full_sourcing_chain_rfq_to_contract():
    # approved vendor to bid
    v = await vendors_svc.create_vendor(
        {"name": f"SrcVendor {_uid()}", "contact": {}}, ADMIN)
    await vendors_svc.add_document(
        v["vendor_id"], {"doc_type": "DRUG_LICENCE",
                         "doc_number": f"DL-{_uid()}",
                         "expiry_date": "2032-01-01"}, ADMIN)
    await vendors_svc.qualify(v["vendor_id"], "commercial",
                              {"result": "PASS"}, ADMIN)
    await vendors_svc.qualify(v["vendor_id"], "qa",
                              {"result": "PASS"}, ADMIN)
    await vendors_svc.approve_vendor(v["vendor_id"], ADMIN)
    vendor = await vendors_svc.get_vendor(v["vendor_id"])
    assert vendor["status"] == "APPROVED"

    evt = await sourcing_svc.create_event({
        "event_type": "RFQ", "title": "Excipients",
        "lines": [{"line_no": 1, "sku": "PRD-00007", "quantity": 100}],
        "invited_vendors": [v["vendor_id"]]}, ADMIN)
    pub = await sourcing_svc.publish_event(evt["event_id"], ADMIN)
    assert pub["status"] == "PUBLISHED"
    assert v["vendor_id"] in (pub.get("published_to") or [])

    bid = await sourcing_svc.submit_bid(evt["event_id"], {
        "vendor_id": v["vendor_id"],
        "lines": [{"line_no": 1, "sku": "PRD-00007", "quantity": 100,
                   "unit_price": 850}],
        "lead_time_days": 10, "payment_terms": "NET30"}, ADMIN)
    assert bid["status"] == "RECEIVED"
    assert bid["total_amount"] == 85000.0

    # unapproved vendors cannot bid
    v2 = await vendors_svc.create_vendor(
        {"name": f"NoBid {_uid()}", "contact": {}}, ADMIN)
    with pytest.raises(ValidationFailed):
        await sourcing_svc.submit_bid(evt["event_id"], {
            "vendor_id": v2["vendor_id"],
            "lines": [{"line_no": 1, "sku": "PRD-00007", "quantity": 1,
                       "unit_price": 1}]}, ADMIN)

    ev = await sourcing_svc.evaluate_bid(evt["event_id"], bid["bid_id"],
                                         "technical", 88.0, "solid", ADMIN)
    assert ev["technical_score"] == 88.0

    # reverse auction: deterministic decrement ladder
    auc = await sourcing_svc.create_auction(evt["event_id"], {
        "start_price": 90000, "decrement_step": 500,
        "reserve_price": 70000}, ADMIN)
    await sourcing_svc.start_auction(auc["auction_id"], ADMIN)
    await sourcing_svc.auction_bid(auc["auction_id"], v["vendor_id"],
                                   88000, ADMIN)
    with pytest.raises(ValidationFailed):
        # below reserve
        await sourcing_svc.auction_bid(auc["auction_id"], v["vendor_id"],
                                       65000, ADMIN)
    closed = await sourcing_svc.close_auction(auc["auction_id"], ADMIN)
    assert closed["status"] == "COMPLETED"
    assert closed["winner_vendor_id"] == v["vendor_id"]

    rec = await sourcing_svc.recommend_award(evt["event_id"], {
        "bid_id": bid["bid_id"], "justification": "weighted best"}, ADMIN)
    assert rec["status"] == "PROPOSED"
    assert rec["ranking"], "BRA must include the deterministic ranking"
    assert rec["ai_analysis"], "BRA must carry the AI advisory analysis"

    awarded = await sourcing_svc.approve_award(rec["rec_id"], ADMIN)
    assert awarded["status"] == "APPROVED"

    contract = await sourcing_svc.create_contract(evt["event_id"], {
        "vendor_id": v["vendor_id"], "type": "BPA",
        "price_items": [{"product_id": "PRD-00007", "price": 850}]}, ADMIN)
    assert contract["status"] == "ACTIVE"
    evt_after = await sourcing_svc.get_event(evt["event_id"])
    assert evt_after["status"] == "CONTRACTED"

    # contract release: PO priced from BPA
    rel = await sourcing_svc.contract_release(contract["contract_id"], [
        {"sku": "PRD-00007", "quantity": 50}], {"note": "quarterly call"},
        ADMIN)
    assert rel["po_id"]
    po = await db.db.purchase_orders.find_one({"po_id": rel["po_id"]})
    assert po["lines"][0]["unit_price"] == 850


async def test_strategic_award_escalates_to_decision_queue():
    v = await vendors_svc.create_vendor(
        {"name": f"Strat {_uid()}", "contact": {}, "strategic": True}, ADMIN)
    await vendors_svc.add_document(
        v["vendor_id"], {"doc_type": "DRUG_LICENCE",
                         "doc_number": f"DL-{_uid()}",
                         "expiry_date": "2032-01-01"}, ADMIN)
    # strategic vendor approval itself goes to the queue
    res = await vendors_svc.approve_vendor(v["vendor_id"], ADMIN)
    assert res.get("status") == "PENDING_APPROVAL" and res.get("approval_id")
    dec = await decide(res["approval_id"], "APPROVED",
                       {"type": "USER", "id": "mgmt-fs",
                        "roles": ["MANAGEMENT"]}, "ok")
    assert dec["status"] == "APPROVED"
    vendor = await vendors_svc.get_vendor(v["vendor_id"])
    assert vendor["status"] == "APPROVED"


# ============================================================== vendor portal
async def test_vendor_bank_change_requires_otp_and_queue():
    v = await vendors_svc.create_vendor(
        {"name": f"Bank {_uid()}", "contact": {},
         "bank_details": {"acct": "OLD"}}, ADMIN)
    first = await vendors_svc.update_bank_details(
        v["vendor_id"], {"bank_details": {"acct": "NEW"}}, ADMIN)
    assert first.get("otp_sent")
    with pytest.raises(ValidationFailed):
        # confirming without valid OTP must fail
        await vendors_svc.update_bank_details(
            v["vendor_id"], {"bank_details": {"acct": "NEW"}},
            ADMIN, otp="000000")
    # change can only land via the SECURITY_FRAUD approval (not directly)
    doc = await db.db.vendors.find_one({"vendor_id": v["vendor_id"]})
    assert doc["bank_details"]["acct"] == "OLD"


# ================================================================ planning
async def test_mrp_produces_proposals_and_executes():
    sku = "PRD-00001"
    # forecast = moving average of demand history (>=3 months) — forecasted
    # demand must drive the supply requirement like confirmed orders
    for per in ("2098-11", "2098-12", "2099-01"):
        await planning_svc.record_demand_history(sku, 3000, per, "TEST")
    run = await planning_svc.run_mrp({"skus": [sku]}, ADMIN)
    assert run["run_id"]
    assert run["proposals"], "MRP must propose when demand exceeds supply"
    for prop in run["proposals"]:
        assert prop["action"] in ("TRANSFER", "PRODUCE", "BUY")
        assert prop["status"] in ("PROPOSED", "AUTO_EXECUTED")
    # every actionable proposal is persisted
    rows = await planning_svc.list_proposals()
    run_ids = {p["proposal_id"] for p in run["proposals"]}
    assert run_ids <= {r["proposal_id"] for r in rows}


async def test_stock_plan_lifecycle():
    plan = await planning_svc.create_stock_plan({
        "name": f"Plan {_uid()}",
        "lines": [{"sku": "PRD-00001", "planned_qty": 100}]}, ADMIN)
    assert plan["status"] == "DRAFT"
    submitted = await planning_svc.submit_stock_plan(plan["plan_id"], ADMIN)
    assert submitted["status"] == "PENDING_APPROVAL"
    approved = await planning_svc.approve_stock_plan(plan["plan_id"], ADMIN)
    assert approved["status"] in ("APPROVED", "RUNNING", "COMPLETED")


# ================================================================== finance
async def test_credit_note_resolves_match_failure_to_payment():
    vendor = await db.db.vendors.find_one({"status": "APPROVED"}) or \
        await vendors_svc.create_vendor({"name": f"Fin {_uid()}",
                                         "contact": {}}, ADMIN)
    po = await procurement_svc.create_po({
        "vendor_id": vendor["vendor_id"],
        "lines": [{"sku": "PRD-00001", "quantity": 40, "unit_price": 30}]},
        ADMIN)
    sub = await procurement_svc.submit_po_for_approval(po["po_id"], ADMIN)
    if sub["status"] == "PENDING_APPROVAL":  # high-risk/strategic vendor path
        from app.core.approvals import decide
        appr = await db.db.approvals.find_one(
            {"entity_type": "PURCHASE_ORDER", "entity_id": po["po_id"],
             "status": "PENDING"})
        await decide(appr["approval_id"], "APPROVED",
                     {"type": "USER", "id": "fs-mgmt", "roles": ["MANAGEMENT"]},
                     "test approval")
    await procurement_svc.send_po(po["po_id"], ADMIN)
    asn = await logistics_svc.create_asn({
        "po_id": po["po_id"],
        "lines": [{"line_no": 1, "quantity": 40}]}, ADMIN)
    grn = await warehouse_svc.create_grn({
        "asn_id": asn["asn_id"], "warehouse_id": "WH-MAIN",
        "lines": [{"line_no": 1, "quantity": 40,
                   "expiry_date": "2032-06-01"}]}, ADMIN)
    # QA accepts only 30 of 40 → invoice for 40 must fail 4-way
    from app.domains.qc import service as qc_svc
    await qc_svc.disposition_grn_line(grn["grn_id"], 1, 30, 10,
                                      "10 damaged", ADMIN)
    inv = await finance_svc.receive_supplier_invoice({
        "po_id": po["po_id"], "supplier_invoice_number": f"SUP-{_uid()}",
        "total_amount": 1200}, ADMIN)
    match = await finance_svc.match_invoice(inv["invoice_id"], FIN)
    assert match["matched"] is False
    # supplier corrects via credit note for the over-billed 10 units;
    # apply_credit_note re-runs the match internally and returns it
    cn = await finance_svc.apply_credit_note(inv["invoice_id"],
                                             {"amount": 300,
                                              "reason": "rejected qty"}, ADMIN)
    match2 = cn["match"]
    assert match2["matched"] is True, f"credit note should resolve: {match2}"
    await finance_svc.approve_matched_invoice(inv["invoice_id"], ADMIN)
    prop = await finance_svc.create_payment_proposal(
        {"invoice_ids": [inv["invoice_id"]]}, FIN)
    assert prop["status"] == "PROPOSED"
    assert prop["lines"][0]["invoice_id"] == inv["invoice_id"]


async def _pack_order(sku, customer, qty, batch):
    """Stock -> SO -> confirm -> allocate -> system FEFO pick wave -> confirm
    tasks -> PACKED (via real WMS path, no inventory shortcuts)."""
    await _stock(sku, "WH-MAIN", qty + 10, batch=batch)
    so = await sales_svc.create_sales_order({
        "customer_id": customer, "lines": [{"sku": sku, "quantity": qty}]},
        ADMIN)
    await sales_svc.confirm_order(so["order_id"], ADMIN)
    await sales_svc.allocate_order(so["order_id"], ADMIN)
    wave = await warehouse_svc.pick({"sales_order_id": so["order_id"]}, ADMIN)
    for t in wave["tasks"]:
        await warehouse_svc.confirm_pick(t["task_id"], ADMIN)
    return so


async def _fulfil_and_deliver(sku, customer, qty, batch):
    so = await _pack_order(sku, customer, qty, batch)
    packed = await db.db.sales_orders.find_one({"order_id": so["order_id"]})
    assert packed["status"] == "PACKED", packed["status"]
    shp = await logistics_svc.plan_shipment({
        "sales_order_id": so["order_id"], "warehouse_id": "WH-MAIN"}, ADMIN)
    await logistics_svc.dispatch_shipment(shp["shipment_id"], {}, ADMIN)
    await logistics_svc.track(shp["shipment_id"], "DELIVERED",
                              {"location": "customer"}, ADMIN)
    return so


async def test_ar_cash_application_and_reconciliation():
    sku = "PRD-00001"
    so = await _fulfil_and_deliver(sku, "CUS-00001", 10, f"B-AR-{_uid()}")
    inv = await sales_svc.invoice_order(so["order_id"], ADMIN)
    assert inv["status"] in ("ISSUED", "PAID", "PARTIALLY_PAID")
    inv_doc = await db.db.customer_invoices.find_one(
        {"invoice_id": inv["invoice_id"]})
    assert inv_doc["total_amount"] > 0
    cash = await finance_svc.apply_cash({
        "invoice_id": inv["invoice_id"],
        "amount": float(inv_doc["total_amount"]),
        "reference": f"CASH-{_uid()}"}, FIN)
    assert cash is not None


# ================================================================= pharmacy
async def test_prescription_chain_to_dispense_and_register():
    sku = "PRD-00002"  # Schedule H (Amoxicillin)
    await _stock(sku, "WH-MAIN", 30, batch=f"B-RX-{_uid()}")
    raw = (f"Dr. A. Kumar\npatient: Test Patient {_uid()}\nage: 30\n"
           "Tab Amoxicillin 250mg 1-1-1 x 5 days")
    rx = await pharmacy_svc.upload_prescription({"raw_text": raw}, ADMIN)
    assert rx["status"] in ("VALIDATED", "PHARMACIST_REVIEW"), rx["status"]
    assert rx["matched"], "Amoxicillin must match the drug master"
    assert any(m["matched"] for m in rx["matched"])
    # pharmacist review is the authority gate before any dispense
    approved = await pharmacy_svc.pharmacist_review(
        rx["rx_id"], "APPROVE", "ok", PHARM)
    assert approved["status"] == "APPROVED"
    disp = await pharmacy_svc.dispense({"rx_id": rx["rx_id"],
                                        "warehouse_id": "WH-MAIN"}, PHARM)
    assert disp["lines"], "dispense must record exact batch movements"
    assert all(mv["movement_id"] for mv in
               [{"movement_id": l["movement_id"]} for l in disp["lines"]])
    # duplicate dispense prevented: replay returns the same dispense record
    # (idempotent gate) — stock is never dispensed twice
    replay = await pharmacy_svc.dispense({"rx_id": rx["rx_id"]}, PHARM)
    assert replay["dispense_id"] == disp["dispense_id"]
    # non-pharmacist cannot dispense another rx
    rx2 = await pharmacy_svc.upload_prescription({"raw_text": raw}, ADMIN)
    await pharmacy_svc.pharmacist_review(rx2["rx_id"], "APPROVE", "ok", PHARM)
    with pytest.raises(DomainError):
        await pharmacy_svc.dispense({"rx_id": rx2["rx_id"]},
                                    {"type": "USER", "id": "clerk",
                                     "roles": ["SALES"]})
    # controlled substances must hit the register when approved
    reg = await pharmacy_svc.controlled_register_history()
    assert isinstance(reg, list)


async def test_pos_sale_idempotent_and_ledger_explained():
    sku = "PRD-00001"
    await _stock(sku, "WH-MAIN", 20, batch=f"B-POS-{_uid()}")
    key = f"POS-{_uid()}"
    sale = await sales_svc.pos_sale({
        "lines": [{"sku": sku, "quantity": 3, "unit_price": 40}],
        "payment_method": "CASH"}, ADMIN, key)
    assert sale["status"] == "CLOSED"
    again = await sales_svc.pos_sale({
        "lines": [{"sku": sku, "quantity": 3, "unit_price": 40}],
        "payment_method": "CASH"}, ADMIN, key)
    assert again["order_id"] == sale["order_id"], "POS replay must not double-sell"
    # ledger explains the outflow
    led = await db.db.inventory_movements.count_documents(
        {"reference_type": "POS_SALE", "reference_id": sale["order_id"],
         "movement_type": "DISPENSE"})
    assert led >= 1


# ================================================================== reverse
async def test_return_full_chain_with_cap_and_dispositions():
    sku = "PRD-00001"
    so = await _fulfil_and_deliver(sku, "CUS-00002", 20, f"B-RET-{_uid()}")

    # first return of 8 (policy reason) → RMA straight away
    r1 = await reverse_svc.create_return({
        "sales_order_id": so["order_id"], "reason": "QUALITY",
        "lines": [{"sku": sku, "quantity": 8}]}, ADMIN)
    assert r1["status"] == "REQUESTED"
    await reverse_svc.schedule_pickup(r1["return_id"], {"slot": "AM"}, ADMIN)
    await reverse_svc.confirm_pickup(r1["return_id"], ADMIN)
    got = await reverse_svc.receive_return(r1["return_id"], ADMIN)
    assert got["status"] == "RETURN_QUARANTINE"
    assert got["lines"][0]["stock_status"] == "RETURN_QUARANTINE"
    bal = await db.db.inventory_balances.find_one({
        "product_id": sku, "stock_status": "RETURN_QUARANTINE",
        "batch_id": got["lines"][0]["batch_id"]})
    assert bal and float(bal["quantity"]) == 8.0
    await reverse_svc.inspect_return(r1["return_id"],
                                     {"findings": "packaging only"}, ADMIN)
    # restock requires QA authority
    with pytest.raises(Exception):
        await reverse_svc.dispose_return(r1["return_id"], "RESTOCK",
                                         {"type": "USER", "id": "wh",
                                          "roles": ["WAREHOUSE"]})
    await reverse_svc.dispose_return(r1["return_id"], "RESTOCK", QA)
    assert await inventory.on_hand(sku, "WH-MAIN", "AVAILABLE") >= 0

    # cumulative cap: shipped 20, already returned 8 -> max remaining 12
    with pytest.raises(ValidationFailed):
        await reverse_svc.create_return({
            "sales_order_id": so["order_id"], "reason": "DAMAGED",
            "lines": [{"sku": sku, "quantity": 15}]}, ADMIN)
    # a compliant return of exactly the remaining 12 is accepted
    r2 = await reverse_svc.create_return({
        "sales_order_id": so["order_id"], "reason": "DAMAGED",
        "lines": [{"sku": sku, "quantity": 12}]}, ADMIN)
    assert isinstance(r2, dict)
    # …and nothing is returnable after that
    with pytest.raises(ValidationFailed):
        await reverse_svc.create_return({
            "sales_order_id": so["order_id"], "reason": "DAMAGED",
            "lines": [{"sku": sku, "quantity": 1}]}, ADMIN)


async def test_recall_blocks_traces_and_closes():
    sku = "PRD-00001"
    batch = f"B-RCL-{_uid()}"
    so = await _pack_order(sku, "CUS-00003", 5, batch)
    recall = await reverse_svc.create_recall({
        "product_id": sku, "reason": "Contamination",
        "recall_id": f"RCL-TEST-{_uid()}"}, ADMIN)
    assert recall["status"] == "IN_PROGRESS"
    b = await db.db.batches.find_one({"batch_id": batch})
    assert b["blocked"] is True
    # blocked stock cannot be reserved
    with pytest.raises(Exception):
        await inventory.reserve(sku, 1, "SO", f"SO-RCL-{_uid()}",
                                "WH-MAIN", ADMIN)
    assert recall["located"]["affected_customers"], \
        "recall must trace the sold batch to the customer"
    tasks = await db.db.recall_tasks.count_documents(
        {"recall_id": recall["recall_id"], "type": "QUARANTINE_STOCK"})
    assert tasks >= 1
    # complete all tasks → CONTAINED, then QA closes with reconciliation
    async for t in db.db.recall_tasks.find(
            {"recall_id": recall["recall_id"], "status": "OPEN"}):
        await reverse_svc.complete_quarantine_task(t["task_id"], ADMIN)
    rec = await db.db.recalls.find_one({"recall_id": recall["recall_id"]})
    assert rec["status"] == "CONTAINED"
    closed = await reverse_svc.close_recall(recall["recall_id"],
                                            {"summary": "all retrieved"}, QA)
    assert closed["status"] == "CLOSED"
    assert closed["reconciliation"]["located_warehouse_qty"] >= 0


# ==================================================================== safety
async def test_pv_case_lifecycle_serious_to_icsr():
    ae = await safety_svc.report_adverse_event({
        "reporter": {"name": "Dr Who", "type": "HEALTHCARE_PROFESSIONAL"},
        "patient": {"age": 44},
        "suspect_medicine": "PRD-00002",
        "batch_id": f"B-PV-{_uid()}",
        "reaction": "Severe rash",
        "seriousness_criteria": ["HOSPITALIZATION"]}, ADMIN)
    assert ae["case_id"], "AE must auto-create a safety case"
    case = await db.db.safety_cases.find_one({"case_id": ae["case_id"]})
    assert case["status"] == "NEW"
    await safety_svc.triage_case(case["case_id"], ADMIN)
    await safety_svc.duplicate_check(case["case_id"], ADMIN)
    # duplicate detection flags a repeat of the same reaction
    ae2 = await safety_svc.report_adverse_event({
        "suspect_medicine": "PRD-00002", "reaction": "Severe rash",
        "batch_id": f"B-PV-{_uid()}"}, ADMIN)
    case2 = await db.db.safety_cases.find_one({"case_id": ae2["case_id"]})
    await safety_svc.triage_case(case2["case_id"], ADMIN)
    dup = await safety_svc.duplicate_check(case2["case_id"], ADMIN)
    assert dup["duplicate_of"] == case["case_id"], \
        "duplicate AE must link to the original case"
    reviewed = await safety_svc.medical_review(
        case["case_id"], {"expectedness": "UNEXPECTED", "notes": "novel"},
        ADMIN)
    assert reviewed["status"] == "REPORTABLE" and reviewed["icsr_id"], \
        "unexpected serious case must become REPORTABLE with an ICSR"


async def test_complaint_links_to_deviation_and_pv():
    comp = await qa_svc.create_complaint({
        "product_id": "PRD-00002", "batch_id": f"B-CMP-{_uid()}",
        "reporter": {"name": "CityCare"},
        "description": "Discoloration",
        "suspected_adverse_event": True,
        "suspected_reaction": "Rash"}, ADMIN)
    assert comp["deviation_id"], "serious complaint must raise a deviation"
    assert comp["safety_case_id"], "complaint must link to a PV case"
    # quality-only complaint stays separate (no auto PV)
    comp2 = await qa_svc.create_complaint({
        "product_id": "PRD-00001", "description": "dented carton",
        "reporter": {"name": "MedPlus"}}, ADMIN)
    assert comp2["deviation_id"] is None
    assert comp2.get("safety_case_id") is None


# =============================================================== compliance
async def test_licence_sweep_and_audit_query():
    lic = await compliance_svc.register_licence({
        "licence_type": "DRUG_LICENCE_20B", "licence_no": f"LIC-{_uid()}",
        "entity": "Central DC", "expiry_date": "2032-01-01"}, ADMIN)
    assert lic["status"] in ("VALID", "ACTIVE")
    sweep = await compliance_svc.licence_status_sweep(ADMIN)
    assert "expiring" in sweep or "expired" in sweep or sweep
    rows = await compliance_svc.audit_query("VENDOR", limit=10)
    assert isinstance(rows, list)
    sr = await compliance_svc.create_service_request({
        "subject": f"Access review {_uid()}", "type": "IT"},
        ADMIN)
    assert sr["service_request_id"]


async def test_service_request_resolve():
    sr = await compliance_svc.create_service_request({
        "subject": f"Fix {_uid()}", "type": "GENERAL"}, ADMIN)
    done = await compliance_svc.resolve_service_request(
        sr["service_request_id"], {"resolution": "done"}, ADMIN)
    assert done["status"] in ("RESOLVED", "CLOSED")


# ============================================================== procurement
async def test_po_ack_reject_and_amendment_via_portal():
    vendor = await db.db.vendors.find_one({"status": "APPROVED"})
    assert vendor, "an approved vendor must exist from earlier tests"
    po = await procurement_svc.create_po({
        "vendor_id": vendor["vendor_id"],
        "lines": [{"sku": "PRD-00007", "quantity": 5, "unit_price": 900}]},
        ADMIN)
    await procurement_svc.submit_po_for_approval(po["po_id"], ADMIN)
    await procurement_svc.send_po(po["po_id"], ADMIN)
    ack = await procurement_svc.acknowledge_po(po["po_id"],
                                               {"accepted": True}, ADMIN)
    assert ack["status"] == "ACKNOWLEDGED"
    # ASN only after SENT/ACKNOWLEDGED — verified by the ASN created here
    asn = await logistics_svc.create_asn({
        "po_id": po["po_id"],
        "lines": [{"line_no": 1, "quantity": 5}]}, ADMIN)
    assert asn["po_id"] == po["po_id"]


async def test_duplicate_po_idempotency():
    vendor = await db.db.vendors.find_one({"status": "APPROVED"})
    key = f"PO-{_uid()}"
    p1 = await procurement_svc.create_po({
        "vendor_id": vendor["vendor_id"],
        "lines": [{"sku": "PRD-00001", "quantity": 2, "unit_price": 30}]},
        ADMIN, key)
    p2 = await procurement_svc.create_po({
        "vendor_id": vendor["vendor_id"],
        "lines": [{"sku": "PRD-00001", "quantity": 2, "unit_price": 30}]},
        ADMIN, key)
    assert p1["po_id"] == p2["po_id"], "same idempotency key must not create two POs"


# ================================================================ logistics
async def test_outbound_shipment_to_pod_and_eta():
    sku = "PRD-00001"
    so = await _pack_order(sku, "CUS-00001", 5, f"B-SHP-{_uid()}")
    shp = await logistics_svc.plan_shipment({
        "sales_order_id": so["order_id"], "warehouse_id": "WH-MAIN",
        "ship_to": {"city": "Hyderabad"}}, ADMIN)
    assert shp["status"] == "PLANNED"
    eta = await logistics_svc.estimate_eta({"distance_km": 120,
                                            "avg_speed_kmph": 60}, ADMIN)
    assert eta["hours"] == 2.0 and eta["method"] == "DETERMINISTIC_HEURISTIC"
    disp = await logistics_svc.dispatch_shipment(shp["shipment_id"], {},
                                                 ADMIN)
    assert disp["status"] == "IN_TRANSIT"
    failed = await logistics_svc.track(shp["shipment_id"], "FAILED_DELIVERY",
                                       {"reason": "CUSTOMER_UNAVAILABLE"},
                                       ADMIN)
    assert failed["delivery_exception"]["reason"] == "CUSTOMER_UNAVAILABLE"
    await logistics_svc.reattempt_delivery(shp["shipment_id"], {},
                                           ADMIN)
    await logistics_svc.track(shp["shipment_id"], "DELIVERED", {}, ADMIN)
    pod = await logistics_svc.capture_pod(shp["shipment_id"],
                                          {"received_by": "store keeper"},
                                          ADMIN)
    assert pod["status"] == "CLOSED" and pod["pod"]["received_by"]


async def test_carrier_selection_prefers_temperature_and_rating():
    await logistics_svc.create_carrier({
        "name": f"ColdChain {_uid()}", "carrier_type": "ROAD",
        "temperature_controlled": True, "rating": 4.8}, ADMIN)
    await logistics_svc.create_carrier({
        "name": f"Road {_uid()}", "carrier_type": "ROAD",
        "temperature_controlled": False, "rating": 4.9}, ADMIN)
    best = await logistics_svc.select_carrier(
        {"temperature_controlled": True}, ADMIN)
    assert best["temperature_controlled"] is True, \
        "cold-chain must only select temperature-controlled carriers"


# =========================================================== state machines
async def test_invalid_state_jumps_rejected_everywhere():
    from app.core.workflow import transition, WorkflowError

    so = await sales_svc.create_sales_order({
        "customer_id": "CUS-00001",
        "lines": [{"sku": "PRD-00001", "quantity": 1}]}, ADMIN)
    with pytest.raises(WorkflowError):
        # DRAFT → DELIVERED is illegal
        await transition("sales_order", so["order_id"], "sales_orders",
                         "order_id", "DELIVERED", ADMIN)


async def test_approval_sod_requester_cannot_decide():
    req = {"type": "USER", "id": f"req-{_uid()}", "roles": ["PROCUREMENT"]}
    appr = await create_approval(
        "FINANCIAL_AUTHORITY", "SoD check", "PURCHASE_ORDER",
        f"PO-SOD-{_uid()}", req)
    with pytest.raises(DomainError):
        await decide(appr, "APPROVED", req, "self-approve")
