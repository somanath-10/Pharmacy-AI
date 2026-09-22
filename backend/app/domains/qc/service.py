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
    """Enter results per test; deterministic evaluation against acceptance criteria."""
    sample = await _get(sample_id)
    if sample["status"] not in ("TESTING", "RESULTS_ENTERED", "OOS_INVESTIGATION"):
        raise ConflictError(f"Sample not in TESTING: {sample['status']}")
    tests = {t["name"]: t for t in sample["tests"]}
    for r in results:
        name = r.get("name")
        if name not in tests:
            raise ValidationFailed(f"Unknown test {name}")
        tests[name]["result"] = r.get("result")
        tests[name]["verdict"] = _evaluate(name, r.get("result"), tests[name]["acceptance"])
    all_pass = all(t["verdict"] == "PASS" for t in tests.values())
    oos = [t["name"] for t in tests.values() if t["verdict"] == "FAIL"]
    await db.db.qc_samples.update_one(
        {"sample_id": sample_id},
        {"$set": {"tests": list(tests.values()),
                  "updated_at": now_iso(),
                  "oos": oos if oos else None}})
    if sample["status"] == "TESTING":
        target = "RESULTS_ENTERED"
        await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                         target, actor)
    if oos:
        await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                         "OOS_INVESTIGATION", actor,
                         reason=f"OOS: {', '.join(oos)}")
        await bus.publish("qc.failed",
                          {"sample_id": sample_id, "oos": oos,
                           "batch_id": sample["batch_id"]}, actor)
        await audit("QC_SAMPLE", sample_id, "OOS", actor, details={"oos": oos})
    else:
        await bus.publish("qc.passed",
                          {"sample_id": sample_id,
                           "batch_id": sample["batch_id"]}, actor)
    return await _get(sample_id)


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
    """OOS investigation closure → back to REVIEW with final verdicts."""
    sample = await _get(sample_id)
    if sample["status"] != "OOS_INVESTIGATION":
        raise ConflictError("No active OOS investigation")
    await transition("qc_sample", sample_id, "qc_samples", "sample_id",
                     "REVIEW", actor,
                     reason=payload.get("conclusion", "OOS investigated"))
    # QA may raise deviation/CAPA from here (linked via ref)
    return await _get(sample_id)


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
        # rejected quantity is written off from quarantine stock
        await inventory.record_movement(
            movement_type="NEGATIVE_ADJUSTMENT",
            product_id=line["sku"],
            warehouse_id=grn["warehouse_id"],
            quantity=rejected,
            batch_id=line["batch_id"],
            reference_type="QA_REJECTION",
            reference_id=f"{grn_id}:{line_no}",
            performed_by=actor,
            note=f"QA rejected: {notes}",
        )
        await db.db.batches.update_one(
            {"batch_id": line["batch_id"]},
            {"$set": {"qa_status": "REJECTED", "blocked": True,
                      "block_reason": f"QA_REJECTION: {notes}",
                      "updated_at": now_iso()}})
    if accepted > 0:
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
