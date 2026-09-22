"""Rich demo dataset: executes REAL workflows through the service layer so the
Command Center, queues, ledgers and timelines are populated with meaningful,
internally-consistent data."""
import asyncio
from datetime import timedelta

from app.core.database import db, now_iso, utcnow


async def seed_demo():
    from app.domains import compliance as compliance_svc, crm as crm_svc, finance as finance_svc, logistics as logistics_svc
    from app.domains import pharmacy as pharmacy_svc, production as production_svc, qa as qa_svc, qc as qc_svc
    from app.domains import reverse as reverse_svc
    from app.domains import sales as sales_svc, sourcing as sourcing_svc, vendors as vendors_svc, warehouse as warehouse_svc
    finance = finance_svc
    production = production_svc
    reverse = reverse_svc
    compliance = compliance_svc
    pharmacy = pharmacy_svc
    from app.domains.inventory import service as inv

    sys_actor = {"type": "SYSTEM", "id": "seed"}
    agent = {"type": "AGENT", "id": "seed-agent"}
    buyer = {"type": "USER", "id": "buyer@pharmaos.local", "roles": ["BUYER"]}
    qa_actor = {"type": "USER", "id": "qa@pharmaos.local", "roles": ["QA"]}
    qc_actor = {"type": "USER", "id": "qc@pharmaos.local", "roles": ["QC"]}
    pharma_actor = {"type": "USER", "id": "pharmacist@pharmaos.local",
                    "roles": ["PHARMACIST"]}
    finance_actor = {"type": "USER", "id": "finance@pharmaos.local",
                     "roles": ["FINANCE"]}
    wh_actor = {"type": "USER", "id": "warehouse@pharmaos.local",
                "roles": ["WAREHOUSE"]}
    sales_actor = {"type": "USER", "id": "sales@pharmaos.local", "roles": ["SALES"]}

    if await db.db.demo_marker.find_one({"name": "demo_v1"}):
        return {"skipped": True}
    await db.db.demo_marker.insert_one({"name": "demo_v1", "at": now_iso()})

    fg_para = await db.db.products.find_one({"name": "Paracetamol 500mg Tablets"})
    fg_azi = await db.db.products.find_one({"name": "Azithromycin 500mg Tablets"})
    api_para = await db.db.products.find_one({"name": "Paracetamol API"})
    acme = await db.db.vendors.find_one({"code": "ACME"})
    globallabs = await db.db.vendors.find_one({"code": "GLOBALLABS"})
    citycare = await db.db.customers.find_one({"name": "CityCare Hospital"})
    medplus = await db.db.customers.find_one({"name": "MedPlus Distributors"})
    wellness = await db.db.customers.find_one({"name": "Wellness Pharmacy Chain"})

    # ---------------------------------------------------------- 1. Vendor lifecycle
    v1 = await vendors_svc.create_vendor({
        "name": "HealWell APIs Pvt Ltd", "vendor_type": "MANUFACTURER",
        "contact": {"email": "sales@healwell.example.com"},
        "strategic": False}, buyer)
    await vendors_svc.add_document(v1["vendor_id"], {
        "doc_type": "DRUG_LICENCE", "doc_number": "DL-20B-88291",
        "expiry_date": (utcnow() + timedelta(days=400)).strftime("%Y-%m-%d"),
    }, buyer)
    await vendors_svc.qualify(v1["vendor_id"], "commercial",
                              {"result": "PASS", "score": 85}, buyer)
    await vendors_svc.qualify(v1["vendor_id"], "qa",
                              {"result": "PASS", "score": 90}, buyer)
    await vendors_svc.approve_vendor(v1["vendor_id"], buyer, "Qualified supplier")

    # approve the seeded vendors fully so sourcing can use them
    for v in (acme, globallabs):
        await vendors_svc.add_document(v["vendor_id"], {
            "doc_type": "DRUG_LICENCE", "doc_number": f"DL-{v['code']}",
            "expiry_date": (utcnow() + timedelta(days=700)).strftime("%Y-%m-%d"),
        }, buyer)
        await vendors_svc.qualify(v["vendor_id"], "commercial",
                                  {"result": "PASS", "score": 80}, buyer)
        await vendors_svc.qualify(v["vendor_id"], "qa",
                                  {"result": "PASS", "score": 88}, buyer)
        await vendors_svc.approve_vendor(v["vendor_id"], buyer, "Seeded approval")

    # ---------------------------------------------------------- 2. CRM pipeline
    lead = await crm_svc.capture_lead({
        "company_name": "Sunrise Hospital Group", "source": "EXPO",
        "contact": {"email": "purchasing@sunrise.example.com"},
        "notes": "Urgent requirement for antibiotic range"}, sales_actor)
    await crm_svc.score_lead(lead["lead_id"], sales_actor)
    opp = await crm_svc.convert_lead(lead["lead_id"], sales_actor)
    cust_new = await crm_svc.create_customer_from_opportunity(opp["opp_id"], {
        "type": "HOSPITAL", "credit_limit": 400000,
        "contact": {"email": "purchasing@sunrise.example.com"}}, sales_actor)
    inq = await crm_svc.create_inquiry({
        "customer_id": cust_new["code"],
        "raw_text": "We need Paracetamol 500mg Tablets x 500 and "
                    "ORS Sachet x 300. Urgent.", "channel": "EMAIL"}, sales_actor)
    await crm_svc.understand_inquiry(inq["inq_id"], sales_actor)

    # ---------------------------------------------------------- 3. Quotation → PO → SO
    quote = await sales_svc.create_quotation({
        "customer_id": cust_new["code"],
        "lines": [{"sku": fg_para["sku"], "quantity": 500},
                  {"sku": (await db.db.products.find_one(
                      {"name": "ORS Sachet"}))["sku"], "quantity": 300}],
    }, sales_actor)
    await sales_svc.send_quotation(quote["quote_id"], sales_actor)
    cpo = await sales_svc.intake_customer_po({
        "customer_id": cust_new["code"],
        "raw_text": "Purchase Order PO-77341: Paracetamol 500mg Tablets x 500, "
                    "ORS Sachet x 300. Deliver to Sunrise Hospital."},
        sales_actor)
    so1 = await sales_svc.create_sales_order({
        "customer_id": cust_new["code"], "cpo_id": cpo["cpo_id"],
        "lines": [{"sku": fg_para["sku"], "quantity": 500},
                  {"sku": (await db.db.products.find_one(
                      {"name": "ORS Sachet"}))["sku"], "quantity": 300}],
    }, sales_actor, idempotency_key=f"demo-so1")
    await sales_svc.confirm_order(so1["order_id"], sales_actor)

    # ---------------------------------------------------------- 4. Sourcing → contract
    rfq = await sourcing_svc.create_event({
        "event_type": "RFQ", "title": "Paracetamol API 2000 KG annual",
        "lines": [{"sku": api_para["sku"], "quantity": 2000, "uom": "KG"}],
        "invited_vendors": [acme["vendor_id"], globallabs["vendor_id"],
                            v1["vendor_id"]]}, buyer)
    await sourcing_svc.publish_event(rfq["event_id"], buyer)
    await sourcing_svc.submit_bid(rfq["event_id"], {
        "vendor_id": acme["vendor_id"],
        "lines": [{"sku": api_para["sku"], "quantity": 2000,
                   "unit_price": 880, "amount": 1760000}],
        "lead_time_days": 21, "payment_terms": "NET30"}, buyer)
    await sourcing_svc.submit_bid(rfq["event_id"], {
        "vendor_id": globallabs["vendor_id"],
        "lines": [{"sku": api_para["sku"], "quantity": 2000,
                   "unit_price": 905, "amount": 1810000}],
        "lead_time_days": 14, "payment_terms": "NET45"}, buyer)
    bra = await sourcing_svc.recommend_award(rfq["event_id"], {
        "bid_id": (await sourcing_svc.list_bids(rfq["event_id"]))[0]["bid_id"]},
        buyer)
    await sourcing_svc.approve_award(bra["rec_id"], buyer, "Best combined score")
    contract = await sourcing_svc.create_contract(rfq["event_id"], {
        "vendor_id": acme["vendor_id"], "type": "BPA",
        "title": "Paracetamol API BPA",
        "valid_to": (utcnow() + timedelta(days=365)).strftime("%Y-%m-%d"),
        "price_items": [{"product_id": api_para["sku"], "price": 880}],
    }, buyer)

    # ---------------------------------------------------------- 5. P2P: PR → PO → ASN → GRN
    pr = await procurement_svc.create_pr({
        "title": "Paracetamol API for Q4 production",
        "lines": [{"sku": api_para["sku"], "quantity": 500, "unit_price": 880}],
        "department": "PLANT"}, buyer)
    await procurement_svc.submit_pr(pr["pr_id"], buyer)
    po1 = await procurement_svc.convert_pr(pr["pr_id"], {}, buyer)
    await procurement_svc.send_po(po1["po_id"], buyer)
    await procurement_svc.acknowledge_po(po1["po_id"], {"accepted": True},
                                         {"type": "VENDOR", "id": acme["vendor_id"]})
    asn = await logistics_svc.create_asn({
        "po_id": po1["po_id"], "carrier": "Bluedex Express",
        "vehicle_no": "KA01AB1234",
        "lines": [{"line_no": 1, "quantity": 500, "batch_id": "B-ACME-0001",
                   "expiry_date": (utcnow() + timedelta(days=1700)).strftime("%Y-%m-%d"),
                   "mfg_date": (utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")}]},
        {"type": "VENDOR", "id": acme["vendor_id"]})
    await logistics_svc.arrive_asn(asn["asn_id"], {"dock": "D1"}, wh_actor)
    grn1 = await warehouse_svc.create_grn({
        "asn_id": asn["asn_id"], "warehouse_id": "WH-PLANT",
        "lines": [{"line_no": 1, "quantity": 500,
                   "batch_id": "B-ACME-0001",
                   "expiry_date": (utcnow() + timedelta(days=1700)).strftime("%Y-%m-%d"),
                   "mfg_date": (utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")}]},
        wh_actor, idempotency_key="demo-grn1")

    # QC flow on GRN line: test & pass
    sample = await qc_svc.get_sample(grn1["lines"][0]["qc_sample_id"])
    await qc_svc.start_testing(sample["sample_id"], qc_actor)
    await qc_svc.enter_results(sample["sample_id"], [
        {"name": "description", "result": "White crystalline powder"},
        {"name": "identification", "result": "Conforms"},
        {"name": "assay", "result": 99.6},
    ], qc_actor)
    await qc_svc.complete_review(sample["sample_id"], qc_actor)
    await qc_svc.disposition_grn_line(grn1["grn_id"], 1, 500, 0,
                                      "QC passed", qa_actor)
    await warehouse_svc.putaway(grn1["grn_id"], wh_actor)

    # 4-way match: invoice + payment
    inv1 = await finance.receive_supplier_invoice({
        "po_id": po1["po_id"], "supplier_invoice_number": "ACME-INV-9911",
        "total_amount": 440000.0,
        "lines": [{"line_no": 1, "quantity": 500, "unit_price": 880,
                   "amount": 440000}]}, finance_actor,
        idempotency_key="demo-inv1")
    await finance.match_invoice(inv1["invoice_id"], finance_actor)
    await finance.approve_matched_invoice(inv1["invoice_id"], finance_actor)
    pay1 = await finance.create_payment_proposal(
        {"invoice_ids": [inv1["invoice_id"]]}, finance_actor)
    await finance.authorize_payment(pay1["payment_id"], finance_actor)
    await finance.execute_payment(pay1["payment_id"], {}, finance_actor,
                                  idempotency_key="demo-pay1")

    # second PO with partial QA rejection → 4-way mismatch + credit note demo
    po2 = await procurement_svc.create_po({
        "vendor_id": globallabs["vendor_id"],
        "lines": [{"sku": fg_azi["sku"], "quantity": 1000}]}, buyer)
    await procurement_svc.submit_po_for_approval(po2["po_id"], buyer)
    await procurement_svc.send_po(po2["po_id"], buyer)
    await procurement_svc.acknowledge_po(po2["po_id"], {"accepted": True},
                                         {"type": "VENDOR",
                                          "id": globallabs["vendor_id"]})
    asn2 = await logistics_svc.create_asn({
        "po_id": po2["po_id"],
        "lines": [{"line_no": 1, "quantity": 1000, "batch_id": "B-GL-0201",
                   "expiry_date": (utcnow() + timedelta(days=600)).strftime("%Y-%m-%d")}]},
        {"type": "VENDOR", "id": globallabs["vendor_id"]})
    await logistics_svc.arrive_asn(asn2["asn_id"], {}, wh_actor)
    grn2 = await warehouse_svc.create_grn({
        "asn_id": asn2["asn_id"], "warehouse_id": "WH-MAIN",
        "lines": [{"line_no": 1, "quantity": 1000, "batch_id": "B-GL-0201",
                   "expiry_date": (utcnow() + timedelta(days=600)).strftime("%Y-%m-%d")}]},
        wh_actor, idempotency_key="demo-grn2")
    s2 = await qc_svc.get_sample(grn2["lines"][0]["qc_sample_id"])
    await qc_svc.start_testing(s2["sample_id"], qc_actor)
    await qc_svc.enter_results(s2["sample_id"], [
        {"name": "description", "result": "Film coated"},
        {"name": "assay", "result": 97.1}], qc_actor)
    await qc_svc.complete_review(s2["sample_id"], qc_actor)
    # QA accepts only 970 → 30 rejected
    await qc_svc.disposition_grn_line(grn2["grn_id"], 1, 970, 30,
                                      "30 units failed packaging defect",
                                      qa_actor)
    await warehouse_svc.putaway(grn2["grn_id"], wh_actor)
    inv2 = await finance.receive_supplier_invoice({
        "po_id": po2["po_id"], "supplier_invoice_number": "GL-INV-4471",
        "total_amount": 145000.0,
        "lines": [{"line_no": 1, "quantity": 1000, "unit_price": 145,
                   "amount": 145000}]}, finance_actor)
    match2 = await finance.match_invoice(inv2["invoice_id"], finance_actor)
    # Finance agent requested credit note; supplier sends it:
    await finance.apply_credit_note(inv2["invoice_id"], {
        "amount": 4350.0, "reason": "QA rejected 30 units"}, finance_actor)

    # ---------------------------------------------------------- 6. Production batch
    mpo = await production.create_production_order({
        "product_id": fg_para["sku"], "batch_size": 1000,
        "equipment_codes": ["EQ-RMG-01", "EQ-COMP-01"]}, sys_actor,
        idempotency_key="demo-mpo1")
    await production.release_order(mpo["order_id"], sys_actor)
    await production.reserve_materials(mpo["order_id"], sys_actor)
    await production.line_clearance(mpo["order_id"], {
        "checks": {"area_clean": True, "previous_materials_removed": True,
                   "labels_ready": True, "documented": True}}, sys_actor)
    await production.issue_materials(mpo["order_id"], sys_actor)
    await production.start_batch(mpo["order_id"], sys_actor)
    await production.complete_step(mpo["order_id"], "GRANULATION",
                                   {"binder": "PVP", "temp_c": 60}, sys_actor)
    await production.complete_step(mpo["order_id"], "COMPRESSION",
                                   {"hardness_kp": 9.2, "weight_mg": 605},
                                   sys_actor)
    await production.in_process_control(mpo["order_id"], {
        "name": "weight_variation", "result": 2.1, "verdict": "PASS"}, sys_actor)
    await production.complete_production(mpo["order_id"], 992, sys_actor)
    await production.submit_for_qc(mpo["order_id"], sys_actor)
    order = await production.get_order(mpo["order_id"])
    fg_sample = None
    async for s in db.db.qc_samples.find({"ref_type": "PRODUCTION_ORDER",
                                          "ref_id": mpo["order_id"]}):
        fg_sample = s
        await qc_svc.start_testing(s["sample_id"], qc_actor)
        await qc_svc.enter_results(s["sample_id"], [
            {"name": "description", "result": "White round tablets"},
            {"name": "identification", "result": "Conforms"},
            {"name": "assay", "result": 98.4},
            {"name": "uniformity", "result": 3.2},
            {"name": "dissolution", "result": 86.0}], qc_actor)
        await qc_svc.complete_review(s["sample_id"], qc_actor)
        await qc_svc.generate_coa(s["sample_id"], qc_actor)
    await production.submit_for_qa_review(mpo["order_id"], qa_actor)
    await qa_svc.batch_release(mpo["order_id"], qa_actor, "RELEASE",
                               "All QC passed; dossier complete")

    # ---------------------------------------------------------- 7. O2C fulfilment
    await sales_svc.allocate_order(so1["order_id"], wh_actor)
    await warehouse_svc.pick({"sales_order_id": so1["order_id"]}, wh_actor)
    async for t in db.db.pick_tasks.find({"sales_order_id": so1["order_id"],
                                          "status": "PENDING"}):
        await warehouse_svc.confirm_pick(t["task_id"], wh_actor,
                                         t["batch_id"] if t["batch_id"] != "UNBATCHED"
                                         else None)
    await warehouse_svc.pack({"sales_order_id": so1["order_id"],
                              "packages": 12}, wh_actor)
    shp = await logistics_svc.plan_shipment({
        "sales_order_id": so1["order_id"], "warehouse_id": "WH-MAIN",
        "carrier_id": "CAR-00001",
        "ship_to": "Sunrise Hospital Group"}, wh_actor)
    await logistics_svc.dispatch_shipment(shp["shipment_id"], {}, wh_actor)
    await logistics_svc.track(shp["shipment_id"], "DELIVERED",
                              {"location": "Sunrise Hospital"}, wh_actor)
    await logistics_svc.capture_pod(shp["shipment_id"],
                                    {"received_by": "Store Manager"}, wh_actor)
    await sales_svc.mark_delivered(so1["order_id"], wh_actor)
    await sales_svc.invoice_order(so1["order_id"], sales_actor)
    cinv = await db.db.customer_invoices.find_one(
        {"sales_order_id": so1["order_id"]})
    await finance.apply_cash({"invoice_id": cinv["invoice_id"],
                              "amount": cinv["total_amount"],
                              "reference": "BANK-NEFT-7712"}, finance_actor)
    await finance.run_reconciliation({
        "statement_lines": [{"reference": pay1["payment_id"],
                             "amount": -pay1["amount"]},
                            {"reference": cinv["invoice_id"],
                             "amount": cinv["total_amount"]}]}, finance_actor)

    # ---------------------------------------------------------- 8. Pharmacy Rx flow
    rx = await pharmacy.upload_prescription({
        "raw_text": "Dr. R. Menon\nPatient: Anita R, age 34\n"
                    "Tab Azithromycin 500mg 1-0-0 x 3 days\n"
                    "Tab Paracetamol 500mg 1-1-1 x 5 days",
        "sales_order_id": None}, pharma_actor)
    await pharmacy.pharmacist_review(rx["rx_id"], "APPROVE",
                                     "Doses appropriate", pharma_actor)
    await pharmacy.dispense({"rx_id": rx["rx_id"], "warehouse_id": "WH-MAIN"},
                            pharma_actor)

    # POS retail sale
    await sales_svc.pos_sale({
        "customer_id": wellness["code"],
        "lines": [{"sku": fg_para["sku"], "quantity": 10}],
        "payment_method": "UPI"}, pharma_actor, idempotency_key="demo-pos1")

    # ---------------------------------------------------------- 9. Returns + recall
    ret = await reverse.create_return({
        "sales_order_id": so1["order_id"], "customer_id": cust_new["code"],
        "lines": [{"sku": fg_para["sku"], "quantity": 5}],
        "reason": "DAMAGED"}, wh_actor)
    await reverse.schedule_pickup(ret["return_id"], {"slot": "next-day"}, wh_actor)
    await db.db.return_requests.update_one(
        {"return_id": ret["return_id"]}, {"$set": {"status": "PICKED"}})
    await reverse.receive_return(ret["return_id"], wh_actor)
    await reverse.inspect_return(ret["return_id"],
                                 {"findings": "Outer carton crushed; blisters intact"},
                                 qa_actor)
    await reverse.dispose_return(ret["return_id"], "RESTOCK", qa_actor)

    # recall drill on one batch (kept open for demo)
    recall = await reverse.create_recall({
        "product_id": fg_azi["sku"], "batch_ids": ["B-GL-0201"],
        "reason": "Market complaint: discoloration", "class": "CLASS_III"},
        qa_actor, idempotency_key="demo-recall")

    # ---------------------------------------------------------- 10. Safety + compliance
    await safety_svc.report_adverse_event({
        "reporter": {"name": "City Chemist", "type": "PHARMACIST"},
        "patient": {"age": 58},
        "suspect_medicine": fg_azi["sku"], "batch_id": "B-GL-0201",
        "reaction": "Rash and itching", "outcome": "RECOVERED",
        "seriousness_criteria": []}, sales_actor)
    case = (await safety_svc.list_cases())[0]
    await safety_svc.triage_case(case["case_id"], qa_actor)
    await safety_svc.duplicate_check(case["case_id"], qa_actor)
    await safety_svc.medical_review(case["case_id"],
                                    {"expectedness": "EXPECTED",
                                     "needs_follow_up": False}, qa_actor)

    await compliance.register_licence({
        "licence_type": "DRUG_LICENCE_20B", "licence_number": "DL-20B-44101",
        "expiry_date": (utcnow() + timedelta(days=30)).strftime("%Y-%m-%d"),
    }, sales_actor)
    await compliance.register_licence({
        "licence_type": "GST_REGISTRATION", "licence_number": "29ABCDE1234F1Z5",
        "expiry_date": None}, sales_actor)

    # MRP run → planning proposals
    from app.domains.planning.service import run_mrp

    await run_mrp({}, agent)

    return {"ok": True}
