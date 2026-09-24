"""QC / LIMS: specifications-driven sampling, testing, OOS/OOT, CoA."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.workflow import transition, record_node


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


async def register_sample(payload: dict, actor: dict) -> dict:
    """Sample registration against an approved specification."""
    if not payload.get("product_id") or not payload.get("batch_id"):
        raise ValidationFailed("sample needs product_id + batch_id")
    spec = await db.db.specifications.find_one(
        {"product_id": payload["product_id"], "status": "APPROVED"},
        sort=[("version", -1)])
    if not spec:
        raise ValidationFailed(
            f"No approved specification for {payload['product_id']}")
    sample_id = await _next_id("sample", "SMP")
    await bus.publish("qc.sample.created",
                      {"sample_id": sample_id,
                       "product_id": payload["product_id"],
                       "batch_id": payload["batch_id"],
                       "stage": payload.get("stage")}, actor)
    doc = {
        "sample_id": sample_id,
        "product_id": payload["product_id"],
        "batch_id": payload["batch_id"],
        "stage": payload.get("stage", "RAW_MATERIAL"),
        "ref_type": payload.get("ref_type"),
        "ref_id": payload.get("ref_id"),
        "spec_id": str(spec["_id"]),
        "spec_version": spec["version"],
        "tests": [{"name": t["name"], "method": t["method"],
                   "acceptance": t["acceptance"],
                   "result": None, "verdict": None} for t in spec["tests"]],
        "sampling_plan": spec.get("sampling_plan", {"n": 3}),
        "analyst": None,
        "status": "REGISTERED",
        "oos": None,
        "coa_id": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "REGISTERED", "actor": actor, "at": now_iso()}],
    }
    await db.db.qc_samples.insert_one(doc)
    await bus.publish("qc.sampled",
                      {"sample_id": sample_id, "batch_id": payload["batch_id"],
                       "stage": doc["stage"]}, actor)
    await audit("QC_SAMPLE", sample_id, "REGISTERED", actor,
                details={"batch": payload["batch_id"]})
    if payload.get("ref_type") == "GRN_LINE":
        grn_id = payload["ref_id"].split(":")[0]
        await record_node("purchase_order", await _po_of_grn(grn_id), "qc",
                          "QC Sampling", "IN_PROGRESS", actor,
                          detail=sample_id)
    return _clean(doc)


async def start_testing(sample_id: str, actor: dict) -> dict:
    sample = await _get(sample_id)
    # walk the state chain: REGISTERED → SAMPLING → TESTING
    if sample["status"] == "REGISTERED":
        await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                         "SAMPLING", actor, reason="Sample drawn per plan")
    await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                     "TESTING", actor, reason="Analysis started")
    await db.db.qc_samples.update_one({"sample_id": sample_id},
                                      {"$set": {"analyst": actor}})
    return await _get(sample_id)


async def enter_results(sample_id: str, results: List[dict], actor: dict) -> dict:
    """Enter results per test; deterministic evaluation against acceptance
    criteria from the approved specification (AI never invents limits).
    Also runs the deterministic OOT trend check (Part 12) and validates the
    instrument used is calibration-valid (Part 16 linkage)."""
    sample = await _get(sample_id)
    if sample["status"] not in ("TESTING", "RESULTS_ENTERED", "OOS_INVESTIGATION"):
        raise ConflictError(f"Sample not in TESTING: {sample['status']}")
    tests = {t["name"]: t for t in sample["tests"]}
    for r in results:
        name = r.get("name")
        if name not in tests:
            raise ValidationFailed(f"Unknown test {name}")
        # deterministic equipment gate: results from invalid instruments are rejected
        if r.get("instrument_code"):
            from app.domains.masters.service import require_equipment_ready

            await require_equipment_ready(r["instrument_code"])
            tests[name]["instrument_code"] = r["instrument_code"]
        tests[name]["result"] = r.get("result")
        tests[name]["verdict"] = _evaluate(name, r.get("result"), tests[name]["acceptance"])
    oos = [t["name"] for t in tests.values() if t["verdict"] == "FAIL"]
    oot = await detect_oot(sample["product_id"], sample["batch_id"], tests)
    await db.db.qc_samples.update_one(
        {"sample_id": sample_id},
        {"$set": {"tests": list(tests.values()),
                  "updated_at": now_iso(),
                  "oos": oos if oos else None,
                  "oot": oot if oot else None}})
    if sample["status"] == "TESTING":
        await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                         "RESULTS_ENTERED", actor)
    if oos:
        await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                         "OOS_INVESTIGATION", actor,
                         reason=f"OOS: {', '.join(oos)}")
        await bus.publish("qc.failed",
                          {"sample_id": sample_id, "oos": oos,
                           "batch_id": sample["batch_id"]}, actor)
        await audit("QC_SAMPLE", sample_id, "OOS", actor, details={"oos": oos})
    elif oot:
        # OOT: within spec but abnormal trend → investigation, batch NOT blocked
        await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                         "OOS_INVESTIGATION", actor,
                         reason=f"OOT trend: {', '.join(t['test'] for t in oot)}")
        await bus.publish("qc.oot_detected",
                          {"sample_id": sample_id, "oot": oot,
                           "batch_id": sample["batch_id"]}, actor)
        await audit("QC_SAMPLE", sample_id, "OOT", actor, details={"oot": oot})
    else:
        await bus.publish("qc.completed",
                          {"sample_id": sample_id,
                           "batch_id": sample["batch_id"],
                           "verdict": "PASS"}, actor)
    return await _get(sample_id)


async def detect_oot(product_id: str, batch_id: str, tests: Dict[str, dict]) -> List[dict]:
    """Deterministic OOT (Part 12): numeric result deviates from the mean of
    the last N passing batches by more than 3 historical standard deviations
    (min 2 batches of history). No AI in the determination."""
    import statistics

    out = []
    for name, t in tests.items():
        if t.get("verdict") != "PASS" or not isinstance(t.get("result"),
                                                        (int, float)):
            continue
        value = float(t["result"])
        history = []
        async for s in db.db.qc_samples.find(
                {"product_id": product_id, "status": "COMPLETED",
                 "batch_id": {"$ne": batch_id}}).sort("created_at", -1).limit(10):
            for st in s.get("tests", []):
                if st.get("name") == name and st.get("verdict") == "PASS" \
                        and isinstance(st.get("result"), (int, float)):
                    history.append(float(st["result"]))
        if len(history) < 2:
            continue
        mean = statistics.fmean(history)
        stdev = statistics.pstdev(history) or (abs(mean) * 0.01) or 1e-9
        z = abs(value - mean) / stdev
        if z > 3.0:
            out.append({"test": name, "result": value, "historical_mean":
                        round(mean, 4), "z": round(z, 2)})
    return out


def _evaluate(name: str, result: Any, acceptance: Any) -> str:
    """Deterministic pass/fail vs acceptance criteria."""
    if result is None:
        return "PENDING"
    try:
        if isinstance(acceptance, dict):
            lo, hi = acceptance.get("min"), acceptance.get("max")
            val = float(result)
            if lo is not None and val < float(lo):
                return "FAIL"
            if hi is not None and val > float(hi):
                return "FAIL"
            return "PASS"
        if isinstance(acceptance, list):
            return "PASS" if str(result) in [str(a) for a in acceptance] else "FAIL"
        return "PASS" if str(result).strip().lower() == str(acceptance).strip().lower() else "FAIL"
    except (TypeError, ValueError):
        return "FAIL"


async def close_oos(sample_id: str, payload: dict, actor: dict) -> dict:
    """OOS closure is a QA-authority step (Part 12): QC analysts may
    investigate, only QA closes. Retest/resample needs QA authorization
    recorded in the payload (authorized_by).
    """
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        from app.core.errors import PermissionDenied

        raise PermissionDenied("OOS closure requires QA authority")
    sample = await _get(sample_id)
    if sample["status"] != "OOS_INVESTIGATION":
        raise ConflictError("No active OOS investigation")
    conclusion = payload.get("conclusion")
    if not conclusion:
        raise ValidationFailed("OOS closure requires a conclusion")
    await db.db.qc_samples.update_one(
        {"sample_id": sample_id},
        {"$set": {"oos_conclusion": {
            "conclusion": conclusion,
            "root_cause": payload.get("root_cause"),
            "authorized_retest": payload.get("authorized_retest", False),
            "authorized_by": payload.get("authorized_by"),
            "closed_by": actor, "closed_at": now_iso()}}})
    await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                     "REVIEW", actor, reason=conclusion[:200])
    # QA may raise deviation/CAPA from here (linked via ref)
    return await _get(sample_id)


async def request_retest(sample_id: str, payload: dict, actor: dict) -> dict:
    """Retest/resample after OOS (Part 12): only with QA authority AND a QA
    closure that authorized the retest. Creates a fresh linked sample."""
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        from app.core.errors import PermissionDenied

        raise PermissionDenied("Retest authorization requires QA authority")
    sample = await _get(sample_id)
    concl = sample.get("oos_conclusion") or {}
    if not concl.get("authorized_retest"):
        raise ConflictError(
            "Retest not authorized; QA must close OOS with authorized_retest")
    new_sample = await register_sample({
        "product_id": sample["product_id"],
        "batch_id": sample["batch_id"],
        "stage": sample["stage"],
        "ref_type": sample.get("ref_type"),
        "ref_id": sample.get("ref_id"),
        "retest_of": sample_id,
    }, actor)
    await db.db.qc_samples.update_one(
        {"sample_id": sample_id},
        {"$set": {"retest_sample_id": new_sample["sample_id"]}})
    await audit("QC_SAMPLE", sample_id, "RETEST_AUTHORIZED", actor,
                details={"new": new_sample["sample_id"]})
    return new_sample


# ---------------------------------------------------------- stability studies
async def create_stability_study(payload: dict, actor: dict) -> dict:
    """Stability testing program (Part 11): product/batch on conditions with
    scheduled pull points."""
    for k in ("product_id", "batch_id", "conditions"):
        if not payload.get(k):
            raise ValidationFailed(f"Stability study needs {k}")
    study_id = await _next_id("stab", "STB")
    doc = {"study_id": study_id,
           "product_id": payload["product_id"],
           "batch_id": payload["batch_id"],
           "conditions": payload["conditions"],  # e.g. [{name: 25C/60RH, min:0, max:70, temp:25, rh:60}]
           "pull_schedule": payload.get("pull_schedule",
                                        [0, 3, 6, 9, 12, 18, 24, 36]),
           "samples": [], "status": "ACTIVE",
           "created_by": actor, "created_at": now_iso()}
    await db.db.stability_studies.insert_one(doc)
    await audit("STABILITY_STUDY", study_id, "CREATED", actor)
    return _clean(doc)


async def record_stability_result(study_id: str, payload: dict,
                                  actor: dict) -> dict:
    study = await db.db.stability_studies.find_one({"study_id": study_id})
    if not study:
        raise NotFound(f"Stability study {study_id} not found")
    entry = {"month": payload.get("month"),
             "condition": payload.get("condition"),
             "tests": payload.get("tests", []),
             "recorded_by": actor, "at": now_iso()}
    await db.db.stability_studies.update_one(
        {"study_id": study_id}, {"$push": {"samples": entry}})
    await audit("STABILITY_STUDY", study_id, "RESULT_RECORDED", actor,
                details={"month": entry["month"]})
    return _clean(dict(await db.db.stability_studies.find_one(
        {"study_id": study_id})))


async def complete_review(sample_id: str, actor: dict) -> dict:
    sample = await _get(sample_id)
    if sample["status"] not in ("RESULTS_ENTERED", "REVIEW"):
        raise ConflictError(f"Sample not ready for completion: {sample['status']}")
    if sample["status"] == "RESULTS_ENTERED":
        await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                         "REVIEW", actor, reason="Reviewer assignment")
    await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                     "COMPLETED", actor)
    passed = all(t.get("verdict") == "PASS" for t in sample["tests"])
    await audit("QC_SAMPLE", sample_id, "PASSED" if passed else "FAILED_QC_REVIEW",
                actor)
    return await _get(sample_id)


async def generate_coa(sample_id: str, actor: dict) -> dict:
    sample = await _get(sample_id)
    if sample["status"] != "COMPLETED":
        raise ConflictError("CoA after QC completion")
    coa_id = await _next_id("coa", "COA")
    doc = {
        "coa_id": coa_id,
        "sample_id": sample_id,
        "product_id": sample["product_id"],
        "batch_id": sample["batch_id"],
        "tests": sample["tests"],
        "overall": "PASS" if all(t.get("verdict") == "PASS"
                                 for t in sample["tests"]) else "FAIL",
        "issued_by": actor,
        "issued_at": now_iso(),
    }
    await db.db.certificates_of_analysis.insert_one(doc)
    await db.db.qc_samples.update_one({"sample_id": sample_id},
                                      {"$set": {"coa_id": coa_id}})
    return doc


async def disposition_grn_line(grn_id: str, line_no: int, accepted: float,
                               rejected: float, notes: str, actor: dict) -> dict:
    """QA disposition of a received line; drives inventory + GRN state."""
    grn = await db.db.grns.find_one({"grn_id": grn_id})
    if not grn:
        raise NotFound(f"GRN {grn_id} not found")
    line = next((l for l in grn["lines"] if l["line_no"] == line_no), None)
    if not line:
        raise NotFound(f"GRN line {line_no} not found")
    if accepted + rejected != float(line["received_qty"]):
        raise ValidationFailed(
            f"accepted+rejected must equal received {line['received_qty']}")
    line["accepted_qty"] = float(accepted)
    line["rejected_qty"] = float(rejected)
    line["disposition_notes"] = notes

    from app.domains.inventory import service as inventory

    if rejected > 0:
        # rejected quantity: QUARANTINE → REJECTED stock status (ledger-first)
        await inventory.change_stock_status(
            product_id=line["sku"], warehouse_id=grn["warehouse_id"],
            batch_id=line["batch_id"], from_status="QUARANTINE",
            to_status="REJECTED", quantity=rejected, actor=actor,
            reference_type="QA_REJECTION",
            reference_id=f"{grn_id}:{line_no}",
            uom=line.get("uom", "BOX"))
        await db.db.batches.update_one(
            {"batch_id": line["batch_id"]},
            {"$set": {"qa_status": "REJECTED", "blocked": True,
                      "block_reason": f"QA_REJECTION: {notes}",
                      "updated_at": now_iso()}})
    if accepted > 0:
        # accepted quantity: QUARANTINE → AVAILABLE (ledger-first status move)
        await inventory.change_stock_status(
            product_id=line["sku"], warehouse_id=grn["warehouse_id"],
            batch_id=line["batch_id"], from_status="QUARANTINE",
            to_status="AVAILABLE", quantity=accepted, actor=actor,
            reference_type="QA_RELEASE",
            reference_id=f"{grn_id}:{line_no}",
            uom=line.get("uom", "BOX"))
        await db.db.batches.update_one(
            {"batch_id": line["batch_id"]},
            {"$set": {"qa_status": "RELEASED", "blocked": False,
                      "block_reason": None, "updated_at": now_iso()}})

    # GRN state roll-up
    all_lines = grn["lines"]
    idx = [i for i, l in enumerate(all_lines) if l["line_no"] == line_no][0]
    all_lines[idx] = line
    any_rejected = any(float(l.get("rejected_qty") or 0) > 0 for l in all_lines)
    all_dispositioned = all(
        (float(l.get("accepted_qty") or 0) + float(l.get("rejected_qty") or 0))
        == float(l["received_qty"]) for l in all_lines)
    if all_dispositioned:
        status = "QC_FAILED" if all(
            float(l.get("accepted_qty") or 0) == 0 for l in all_lines) else (
            "QC_PARTIAL" if any_rejected else "QC_PASSED")
        await db.db.grns.update_one(
            {"grn_id": grn_id},
            {"$set": {"lines": all_lines, "status": status,
                      "updated_at": now_iso()}})
        await bus.publish("qa.released" if status in ("QC_PASSED", "QC_PARTIAL")
                          else "qa.rejected",
                          {"grn_id": grn_id, "status": status}, actor)
    else:
        await db.db.grns.update_one(
            {"grn_id": grn_id},
            {"$set": {"lines": all_lines, "updated_at": now_iso()}})
    await audit("GRN_LINE", f"{grn_id}:{line_no}", "QA_DISPOSITIONED", actor,
                details={"accepted": accepted, "rejected": rejected, "notes": notes})
    return await db.db.grns.find_one({"grn_id": grn_id})


async def _po_of_grn(grn_id: str) -> Optional[str]:
    g = await db.db.grns.find_one({"grn_id": grn_id})
    return (g or {}).get("po_id")


async def _get(sample_id: str) -> dict:
    doc = await db.db.qc_samples.find_one({"sample_id": sample_id})
    if not doc:
        raise NotFound(f"Sample {sample_id} not found")
    return doc


async def get_sample(sample_id: str) -> dict:
    return await _get(sample_id)


async def list_samples(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.qc_samples.find(q).sort("created_at", -1).limit(200)]


async def oos_summary() -> dict:
    open_oos = await db.db.qc_samples.count_documents({"status": "OOS_INVESTIGATION"})
    total_failed = await db.db.qc_samples.count_documents({"oos": {"$ne": None}})
    return {"open_oos": open_oos, "total_oos": total_failed}


# ------------------------------------------------- reagents / reference standards
async def register_reagent(payload: dict, actor: dict) -> dict:
    """Reagent or reference-standard master with expiry + CoA (Part 11)."""
    for k in ("name", "kind"):
        if not payload.get(k):
            raise ValidationFailed(f"Reagent needs {k}")
    if payload["kind"] not in ("REAGENT", "REFERENCE_STANDARD"):
        raise ValidationFailed("kind must be REAGENT|REFERENCE_STANDARD")
    reagent_id = await _next_id("reagent", "RG")
    doc = {"reagent_id": reagent_id,
           "kind": payload["kind"],
           "name": payload["name"],
           "lot_no": payload.get("lot_no"),
           "purity": payload.get("purity"),
           "expiry_date": payload.get("expiry_date"),
           "coa_ref": payload.get("coa_ref"),
           "status": "ACTIVE",
           "created_at": now_iso()}
    await db.db.reagents.insert_one(doc)
    await audit("REAGENT", reagent_id, "REGISTERED", actor,
                details={"kind": payload["kind"]})
    return _clean(doc)


async def consume_reagent(reagent_id: str, sample_id: str, actor: dict) -> dict:
    """Log reagent/standard usage against a sample; expired reagents blocked."""
    r = await db.db.reagents.find_one({"reagent_id": reagent_id})
    if not r:
        raise NotFound(f"Reagent {reagent_id} not found")
    if r.get("status") != "ACTIVE":
        raise ConflictError(f"Reagent not ACTIVE: {r['status']}")
    if r.get("expiry_date") and str(r["expiry_date"])[:10] <= now_iso()[:10]:
        raise ConflictError(f"Reagent {reagent_id} expired {r['expiry_date']}")
    await db.db.reagents.update_one(
        {"reagent_id": reagent_id},
        {"$push": {"usage": {"sample_id": sample_id, "at": now_iso(),
                             "by": actor}}})
    await audit("REAGENT", reagent_id, "CONSUMED", actor,
                details={"sample": sample_id})
    return {"reagent_id": reagent_id, "sample_id": sample_id, "logged": True}
