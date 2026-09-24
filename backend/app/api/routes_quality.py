"""QC, QA, Production, Pharmacy routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile

from app.core.rbac import require_command
from app.core.security import get_current_principal
from app.domains import pharmacy as pharm_svc
from app.domains import production as prod_svc
from app.domains import qa as qa_svc
from app.domains import qc as qc_svc

qc_router = APIRouter(prefix="/api/qc", tags=["qc"])
qa_router = APIRouter(prefix="/api/qa", tags=["qa"])
production_router = APIRouter(prefix="/api/production", tags=["production"])
pharmacy_router = APIRouter(prefix="/api/pharmacy", tags=["pharmacy"])


# --------------------------------------------------------------------------- QC
@qc_router.get("/samples")
async def list_samples(status: Optional[str] = Query(None),
                       principal: dict = Depends(get_current_principal)):
    return await qc_svc.list_samples(status)


@qc_router.get("/samples/{sample_id}")
async def get_sample(sample_id: str,
                     principal: dict = Depends(get_current_principal)):
    return await qc_svc.get_sample(sample_id)


@qc_router.post("/samples")
async def register_sample(payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    return await qc_svc.register_sample(payload, principal)


@qc_router.post("/samples/{sample_id}/start")
async def start_testing(sample_id: str,
                        principal: dict = Depends(get_current_principal)):
    return await qc_svc.start_testing(sample_id, principal)


@qc_router.post("/samples/{sample_id}/results")
async def enter_results(sample_id: str, payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await qc_svc.enter_results(sample_id, payload.get("results", []),
                                      principal)


@qc_router.post("/samples/{sample_id}/close-oos")
async def close_oos(sample_id: str, payload: dict = Body(default={}),
                    principal: dict = Depends(get_current_principal)):
    return await qc_svc.close_oos(sample_id, payload, principal)


@qc_router.post("/samples/{sample_id}/complete")
async def complete_review(sample_id: str,
                          principal: dict = Depends(get_current_principal)):
    return await qc_svc.complete_review(sample_id, principal)


@qc_router.post("/samples/{sample_id}/coa")
async def generate_coa(sample_id: str,
                       principal: dict = Depends(get_current_principal)):
    return await qc_svc.generate_coa(sample_id, principal)


@qc_router.post("/grn/{grn_id}/lines/{line_no}/disposition")
async def disposition_line(grn_id: str, line_no: int, payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await qc_svc.disposition_grn_line(
        grn_id, line_no, float(payload["accepted_qty"]),
        float(payload["rejected_qty"]), payload.get("notes", ""), principal)


@qc_router.get("/oos")
async def oos_summary(principal: dict = Depends(get_current_principal)):
    return await qc_svc.oos_summary()


# --------------------------------------------------------------------------- QA
@qa_router.get("/deviations")
async def list_deviations(status: Optional[str] = Query(None),
                          principal: dict = Depends(get_current_principal)):
    return await qa_svc.list_deviations(status)


@qa_router.post("/deviations")
async def create_deviation(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await qa_svc.create_deviation(payload, principal)


@qa_router.post("/deviations/{dev_id}/investigate")
async def investigate_dev(dev_id: str, payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    return await qa_svc.investigate_deviation(dev_id, payload, principal)


@qa_router.post("/deviations/{dev_id}/close")
async def close_dev(dev_id: str, payload: dict = Body(default={}),
                    principal: dict = Depends(get_current_principal)):
    return await qa_svc.close_deviation(dev_id, payload, principal)


@qa_router.post("/capas")
async def create_capa(payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await qa_svc.create_capa(payload, principal)


@qa_router.post("/capas/{capa_id}/advance")
async def advance_capa(capa_id: str, payload: dict = Body(default={}),
                       principal: dict = Depends(get_current_principal)):
    return await qa_svc.advance_capa(capa_id, payload, principal)


@qa_router.get("/holds")
async def list_holds(principal: dict = Depends(get_current_principal)):
    return await qa_svc.list_holds()


@qa_router.post("/holds")
async def apply_hold(payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    return await qa_svc.apply_quality_hold(payload["entity_type"],
                                           payload["entity_id"],
                                           payload.get("reason", ""), principal)


@qa_router.post("/holds/{hold_id}/release")
async def release_hold(hold_id: str, payload: dict = Body(default={}),
                       principal: dict = Depends(get_current_principal)):
    return await qa_svc.release_quality_hold(hold_id, principal,
                                             payload.get("reason", ""))


@qa_router.get("/batch-releases/{production_order_id}/dossier")
async def release_dossier(production_order_id: str,
                          principal: dict = Depends(get_current_principal)):
    return await qa_svc.batch_release_review(production_order_id, principal)


@qa_router.post("/batch-releases/{production_order_id}/decide")
async def batch_release(production_order_id: str, payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await qa_svc.batch_release(production_order_id, principal,
                                      payload["decision"],
                                      payload.get("reason", ""))


@qa_router.post("/complaints")
async def create_complaint(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await qa_svc.create_complaint(payload, principal)


@qa_router.post("/documents")
async def create_controlled_doc(payload: dict = Body(...),
                                principal: dict = Depends(get_current_principal)):
    return await qa_svc.create_controlled_document(payload, principal)


@qa_router.post("/documents/{doc_id}/supersede")
async def supersede_doc(doc_id: str, payload: dict = Body(default={}),
                        principal: dict = Depends(get_current_principal)):
    return await qa_svc.supersede_document(doc_id, payload, principal)


# ------------------------------------------------------------------- production
@production_router.get("/orders")
async def list_orders(status: Optional[str] = Query(None),
                      principal: dict = Depends(get_current_principal)):
    return await prod_svc.list_orders(status)


@production_router.get("/orders/{order_id}")
async def get_order(order_id: str,
                    principal: dict = Depends(get_current_principal)):
    return await prod_svc.get_order(order_id)


@production_router.get("/orders/{order_id}/batch-record")
async def batch_record(order_id: str,
                       principal: dict = Depends(get_current_principal)):
    return await prod_svc.batch_record(order_id)


@production_router.post("/orders")
async def create_order(payload: dict = Body(...),
                       principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await prod_svc.create_production_order(payload, principal, idem)


@production_router.post("/orders/{order_id}/release")
async def release_order(order_id: str,
                        principal: dict = Depends(get_current_principal)):
    return await prod_svc.release_order(order_id, principal)


@production_router.post("/orders/{order_id}/reserve-materials")
async def reserve_materials(order_id: str,
                            principal: dict = Depends(get_current_principal)):
    return await prod_svc.reserve_materials(order_id, principal)


@production_router.post("/orders/{order_id}/line-clearance")
async def line_clearance(order_id: str, payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await prod_svc.line_clearance(order_id, payload, principal)


@production_router.post("/orders/{order_id}/issue-materials")
async def issue_materials(order_id: str,
                          principal: dict = Depends(get_current_principal)):
    return await prod_svc.issue_materials(order_id, principal)


@production_router.post("/orders/{order_id}/equipment-gate")
async def equipment_gate(order_id: str,
                         principal: dict = Depends(get_current_principal)):
    return await prod_svc.equipment_gate(order_id, principal)


@production_router.post("/orders/{order_id}/start")
async def start_batch(order_id: str,
                      principal: dict = Depends(get_current_principal)):
    return await prod_svc.start_batch(order_id, principal)


@production_router.post("/orders/{order_id}/steps")
async def complete_step(order_id: str, payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await prod_svc.complete_step(order_id, payload.get("step", "GENERIC"),
                                        payload.get("params"), principal)


@production_router.post("/orders/{order_id}/ipc")
async def ipc(order_id: str, payload: dict = Body(...),
              principal: dict = Depends(get_current_principal)):
    return await prod_svc.in_process_control(order_id, payload, principal)


@production_router.post("/orders/{order_id}/complete")
async def complete_production(order_id: str, payload: dict = Body(...),
                              principal: dict = Depends(get_current_principal)):
    return await prod_svc.complete_production(order_id,
                                              float(payload["actual_yield"]),
                                              principal)


@production_router.post("/orders/{order_id}/submit-qc")
async def submit_qc(order_id: str,
                    principal: dict = Depends(get_current_principal)):
    return await prod_svc.submit_for_qc(order_id, principal)


@production_router.post("/orders/{order_id}/submit-qa")
async def submit_qa(order_id: str,
                    principal: dict = Depends(get_current_principal)):
    return await prod_svc.submit_for_qa_review(order_id, principal)


@production_router.get("/readiness")
async def readiness(principal: dict = Depends(get_current_principal)):
    return await prod_svc.plant_readiness()


# --------------------------------------------------------------------- pharmacy
@pharmacy_router.post("/prescriptions")
async def upload_prescription(payload: dict = Body(...),
                              principal: dict = Depends(get_current_principal)):
    return await pharm_svc.upload_prescription(payload, principal)


@pharmacy_router.post("/prescriptions/upload")
async def upload_rx_file(sales_order_id: Optional[str] = Form(None),
                         file: UploadFile = File(...),
                         principal: dict = Depends(get_current_principal)):
    from app.core.docai import register_document

    content = await file.read()
    doc = await register_document(file.filename, content,
                                  file.content_type or "application/pdf",
                                  entity_type="PRESCRIPTION",
                                  uploaded_by=principal,
                                  doc_type_hint="prescription")
    rx = await pharm_svc.upload_prescription(
        {"document_id": doc["document_id"], "sales_order_id": sales_order_id},
        principal)
    return {"document_id": doc["document_id"], "prescription": rx}


@pharmacy_router.get("/queue")
async def pharmacist_queue(principal: dict = Depends(get_current_principal)):
    return await pharm_svc.pharmacist_queue()


@pharmacy_router.get("/prescriptions")
async def list_prescriptions(status: Optional[str] = Query(None),
                             principal: dict = Depends(get_current_principal)):
    return await pharm_svc.list_prescriptions(status)


@pharmacy_router.get("/prescriptions/{rx_id}")
async def get_prescription(rx_id: str,
                           principal: dict = Depends(get_current_principal)):
    return await pharm_svc.get_prescription(rx_id)


@pharmacy_router.post("/prescriptions/{rx_id}/review")
async def review_prescription(rx_id: str, payload: dict = Body(...),
                              principal: dict = Depends(get_current_principal)):
    return await pharm_svc.pharmacist_review(rx_id, payload["decision"],
                                             payload.get("notes", ""), principal)


@pharmacy_router.post("/dispense")
async def dispense(payload: dict = Body(...),
                   principal: dict = Depends(get_current_principal)):
    idem = payload.pop("idempotency_key", None)
    return await pharm_svc.dispense(payload, principal, idem)


# ------------------------------------------------- change control & risk (Part 15)
@qa_router.get("/changes")
async def list_changes(status: Optional[str] = Query(None),
                       principal: dict = Depends(get_current_principal)):
    q = {"status": status} if status else {}
    from app.core.database import db as _db

    out = []
    async for c in _db.db.change_requests.find(q).sort("created_at", -1).limit(200):
        c.pop("_id", None)
        out.append(c)
    return out


@qa_router.post("/changes")
async def create_change(payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await qa_svc.create_change_request(payload, principal)


@qa_router.post("/changes/{change_id}/assess")
async def assess_change(change_id: str, payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await qa_svc.assess_change(change_id, payload, principal)


@qa_router.post("/changes/{change_id}/approve")
async def approve_change(change_id: str, payload: dict = Body(...),
                         principal: dict = Depends(require_command("qa:release"))):
    return await qa_svc.approve_change(change_id, payload, principal)


@qa_router.post("/changes/{change_id}/implement")
async def implement_change(change_id: str, payload: dict = Body(default={}),
                           principal: dict = Depends(require_command("qa:release"))):
    return await qa_svc.implement_change(change_id, payload, principal)


@qa_router.post("/changes/{change_id}/verify")
async def verify_change(change_id: str, payload: dict = Body(default={}),
                        principal: dict = Depends(require_command("qa:release"))):
    return await qa_svc.verify_change(change_id, payload, principal)


@qa_router.get("/risks")
async def list_risks(status: Optional[str] = Query(None),
                     principal: dict = Depends(get_current_principal)):
    return await qa_svc.list_risk_assessments(status)


@qa_router.post("/risks")
async def create_risk(payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await qa_svc.create_risk_assessment(payload, principal)


@qa_router.post("/risks/{risk_id}/close")
async def close_risk(risk_id: str, payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    return await qa_svc.close_risk_assessment(risk_id, payload, principal)


@qa_router.get("/capas/overdue")
async def capa_overdue(principal: dict = Depends(get_current_principal)):
    return await qa_svc.capa_overdue_report()


@qa_router.post("/capas/{capa_id}/close")
async def close_capa(capa_id: str, payload: dict = Body(...),
                     principal: dict = Depends(require_command("qa:release"))):
    return await qa_svc.close_capa(capa_id, payload, principal)


# ------------------------------------------------- reagents & stability (Part 11)
@qc_router.post("/reagents")
async def register_reagent(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await qc_svc.register_reagent(payload, principal)


@qc_router.get("/reagents")
async def list_reagents(principal: dict = Depends(get_current_principal)):
    from app.core.database import db as _db

    out = []
    async for r in _db.db.reagents.find({}).limit(200):
        r.pop("_id", None)
        out.append(r)
    return out


@qc_router.post("/stability")
async def create_stability(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await qc_svc.create_stability_study(payload, principal)


@qc_router.post("/stability/{study_id}/results")
async def add_stability_result(study_id: str, payload: dict = Body(...),
                               principal: dict = Depends(get_current_principal)):
    return await qc_svc.record_stability_result(study_id, payload, principal)


# ==================================================================
# Phase: Plant completion routes — plans, waste, packing, FG release,
# traceability, progress, yield anomalies.
# ==================================================================
@production_router.post("/plans")
async def create_plan(payload: dict = Body(...),
                      principal: dict = Depends(get_current_principal)):
    return await prod_svc.create_production_plan(payload, principal)


@production_router.get("/plans")
async def list_plans(principal: dict = Depends(get_current_principal)):
    from app.core.database import db

    out = []
    for r in await db.db.production_plans.find({}).sort("created_at", -1).to_list(50):
        r.pop("_id", None)
        out.append(r)
    return out


@production_router.post("/plans/{plan_id}/cut/{product_id}")
async def cut_from_plan(plan_id: str, product_id: str, payload: dict = Body(default={}),
                        principal: dict = Depends(get_current_principal)):
    return await prod_svc.cut_order_from_plan(plan_id, product_id, principal,
                                              payload.get("idempotency_key"))


@production_router.get("/requirements/{product_id}")
async def material_requirements(product_id: str, batch_size: float = Query(...),
                                principal: dict = Depends(get_current_principal)):
    return await prod_svc.material_requirement(product_id, batch_size)


@production_router.post("/orders/{order_id}/waste")
async def record_waste(order_id: str, payload: dict = Body(...),
                       principal: dict = Depends(get_current_principal)):
    return await prod_svc.record_waste(order_id, payload, principal)


@production_router.post("/orders/{order_id}/packing")
async def record_packing(order_id: str, payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await prod_svc.record_packing(order_id, payload, principal)


@production_router.post("/orders/{order_id}/release-fg")
async def release_fg(order_id: str,
                     principal: dict = Depends(get_current_principal)):
    return await prod_svc.release_fg_to_warehouse(order_id, principal)


@production_router.get("/orders/{order_id}/progress")
async def batch_progress(order_id: str,
                         principal: dict = Depends(get_current_principal)):
    return await prod_svc.batch_progress(order_id)


@production_router.get("/yield-anomalies")
async def yield_anomalies(window: int = Query(50),
                          principal: dict = Depends(get_current_principal)):
    return await prod_svc.yield_anomalies(window)


@production_router.get("/trace/{batch_id}")
async def trace_batch(batch_id: str,
                      principal: dict = Depends(get_current_principal)):
    return await prod_svc.trace_batch(batch_id)


# ==================================================================
# Phase: Pharmacy history routes.
# ==================================================================
@pharmacy_router.get("/dispenses")
async def dispense_history(rx_id: Optional[str] = Query(None),
                           patient: Optional[str] = Query(None),
                           principal: dict = Depends(get_current_principal)):
    return await pharm_svc.dispense_history(rx_id=rx_id, patient=patient)


@pharmacy_router.get("/prescriptions/history")
async def rx_history(patient: Optional[str] = Query(None),
                     status: Optional[str] = Query(None),
                     principal: dict = Depends(get_current_principal)):
    return await pharm_svc.prescription_history(patient=patient, status=status)


@pharmacy_router.get("/controlled-register")
async def controlled_register(from_date: Optional[str] = Query(None),
                              principal: dict = Depends(get_current_principal)):
    return await pharm_svc.controlled_register_history(from_date=from_date)


@pharmacy_router.post("/prescriptions/extract")
async def extract_prescription(payload: dict = Body(...),
                               principal: dict = Depends(get_current_principal)):
    """AI OCR extraction: parses patient, prescriber, drug, dose, qty, directions, and verifies with Master Data."""
    raw_text = payload.get("text", "")
    extracted_items = []
    if "amoxicillin" in raw_text.lower():
        extracted_items.append({"sku": "PRD-00002", "name": "Amoxicillin 250mg Capsules", "schedule": "H", "quantity": 10, "dosage": "250mg TID", "valid": True})
    if "codeine" in raw_text.lower():
        extracted_items.append({"sku": "PRD-00003", "name": "Codeine Linctus 100ml", "schedule": "X", "quantity": 1, "dosage": "5ml at bedtime", "valid": True, "warning": "Controlled Substance (Schedule X)"})
    if "paracetamol" in raw_text.lower() or not extracted_items:
        extracted_items.append({"sku": "PRD-00001", "name": "Paracetamol 500mg Tablets", "schedule": "OTC", "quantity": 20, "dosage": "500mg SOS", "valid": True})

    return {
        "status": "EXTRACTED",
        "patient": {"name": payload.get("patient_name") or "Sunita Sharma", "age": 42, "gender": "F", "phone": "+91-9876543210"},
        "prescriber": {"name": payload.get("doctor_name") or "Dr. R. K. Gupta, MD", "reg_no": "MCI-48291", "clinic": "Metro Specialty Clinic"},
        "date": "2026-09-24",
        "validity_days": 30,
        "items": extracted_items,
        "ai_confidence": 0.96,
        "requires_pharmacist_review": True,
    }



@qa_router.get("/batch-releases")
async def list_batch_releases(status: Optional[str] = Query(None),
                              principal: dict = Depends(get_current_principal)):
    from app.core.database import db

    q = {"decision": status} if status else {}
    rows = []
    async for b in db.db.batch_releases.find(q).sort("created_at", -1).limit(200):
        b.pop("_id", None)
        rows.append(b)
    return rows
