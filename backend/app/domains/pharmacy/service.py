"""Pharmacy: prescription intake (Document AI), compliance rules, pharmacist
review (authority), dispensing, controlled-substance registers."""
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, DomainError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.idempotency import idempotent
from app.core.workflow import transition
from app.domains.inventory import service as inventory


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


# ------------------------------------------------------------------ intake + AI
async def upload_prescription(payload: dict, actor: dict) -> dict:
    """Prescription arrives (document or text) → Document AI extraction."""
    document_id = payload.get("document_id")
    raw_text = payload.get("raw_text")
    if not document_id and not raw_text:
        raise ValidationFailed("Prescription needs document_id or raw_text")
    rx_id = await _next_id("rx", "RX")
    extraction: Dict[str, Any] = {}
    confidence = 0.0
    fallback = True
    if document_id:
        from app.core.docai import classify, extract

        doc_type = await classify(document_id)
        if doc_type != "prescription":
            raise ValidationFailed(f"Document classified as {doc_type}, not prescription")
        data = await extract(document_id)
        extraction = data or {}
        doc_row = await db.db.documents.find_one({"document_id": document_id})
        confidence = (doc_row or {}).get("confidence") or 0.0
        fallback = bool((doc_row or {}).get("extraction", {}).get("fallback"))
    else:
        from app.core.ai_gateway import offline_extract_prescription

        extraction = offline_extract_prescription(raw_text)
        confidence = 0.55 if extraction.get("medicines") else 0.2

    doc = {
        "rx_id": rx_id,
        "patient": extraction.get("patient"),
        "patient_age": extraction.get("patient_age"),
        "prescriber": extraction.get("prescriber"),
        "document_id": document_id,
        "extraction": extraction,
        "matched": [],
        "compliance": {},
        "confidence": confidence,
        "extraction_fallback": fallback,
        "status": "EXTRACTED",
        "sales_order_id": payload.get("sales_order_id"),
        "pharmacist": None,
        "review": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "EXTRACTED", "actor": actor, "at": now_iso()}],
    }
    await db.db.prescriptions.insert_one(doc)
    doc = await match_and_validate(rx_id, actor)
    await audit("PRESCRIPTION", rx_id, "INTAKE", actor,
                details={"confidence": confidence})
    return doc


async def match_and_validate(rx_id: str, actor: dict) -> dict:
    """Drug master matching + deterministic compliance rules."""
    rx = await _get(rx_id)
    if rx["status"] not in ("EXTRACTED", "PHARMACIST_REVIEW", "CLARIFICATION"):
        raise ConflictError(f"Rx not matchable: {rx['status']}")
    matched = []
    issues: List[str] = []
    controlled = False
    for med in rx.get("extraction", {}).get("medicines", []):
        name = med.get("name", "")
        prod = await db.db.products.find_one(
            {"name": {"$regex": name[:25], "$options": "i"},
             "type": "FINISHED_GOOD"})
        entry = {"raw": med, "product_id": prod["sku"] if prod else None,
                 "matched": bool(prod), "schedule": (prod or {}).get("schedule"),
                 "issues": []}
        if not prod:
            entry["issues"].append("No drug master match")
        else:
            if prod.get("is_controlled"):
                controlled = True
            if med.get("strength_mg"):
                # quantity limit check (deterministic rule)
                limit = 3000  # mg/day default cap for routine review
                freq = med.get("frequency") or "1-1-1"
                doses_per_day = freq.count("1") or 1
                daily = med["strength_mg"] * doses_per_day
                if daily > limit:
                    entry["issues"].append(
                        f"Daily dose {daily}mg exceeds review limit {limit}mg")
        matched.append(entry)
        issues.extend(entry["issues"])
    compliance = {
        "controlled_substance": controlled,
        "issues": issues,
        "needs_clarification": any(not m["matched"] for m in matched) or bool(issues),
        "checked_at": now_iso(),
    }
    status = "VALIDATED" if not compliance["needs_clarification"] else "PHARMACIST_REVIEW"
    await db.db.prescriptions.update_one(
        {"rx_id": rx_id},
        {"$set": {"matched": matched, "compliance": compliance,
                  "status": status, "updated_at": now_iso()}})
    await audit("PRESCRIPTION", rx_id, "VALIDATED" if status == "VALIDATED"
                else "COMPLIANCE_FLAGS", actor, details=compliance)
    return await _get(rx_id)


# ---------------------------------------------------------------- pharmacist
async def pharmacist_review(rx_id: str, decision: str, notes: str,
                            actor: dict) -> dict:
    """ONLY registered pharmacists decide (clinical authority)."""
    from app.core.rbac import PHARMACIST_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & PHARMACIST_AUTHORITY_ROLES):
        raise DomainError("Pharmacist authority required")
    rx = await _get(rx_id)
    if rx["status"] not in ("VALIDATED", "PHARMACIST_REVIEW", "CLARIFICATION"):
        raise ConflictError(f"Rx not reviewable: {rx['status']}")

    review = {"decision": decision, "notes": notes, "pharmacist": actor,
              "at": now_iso()}
    if decision == "APPROVE":
        # pharmacist review is the authority gate: VALIDATED/CLARIFICATION →
        # PHARMACIST_REVIEW → APPROVED
        if rx["status"] in ("VALIDATED", "CLARIFICATION"):
            await transition("prescription", rx_id, "prescriptions", "rx_id",
                             "PHARMACIST_REVIEW", actor,
                             reason="Pharmacist sign-off")
        await transition("prescription", rx_id, "prescriptions", "rx_id",
                         "APPROVED", actor, reason=notes)
        await db.db.prescriptions.update_one(
            {"rx_id": rx_id}, {"$set": {"review": review, "pharmacist": actor}})
        await bus.publish("prescription.approved", {"rx_id": rx_id}, actor)
        if rx.get("sales_order_id"):
            from app.domains.sales.service import rx_approved

            await rx_approved(rx["sales_order_id"], actor)
        if rx.get("compliance", {}).get("controlled_substance"):
            await db.db.controlled_registers.insert_one({
                "register": "SCHEDULE_H1_X",
                "rx_id": rx_id,
                "patient": rx.get("patient"),
                "prescriber": rx.get("prescriber"),
                "pharmacist": actor,
                "at": now_iso()})
    elif decision == "REJECT":
        await transition("prescription", rx_id, "prescriptions", "rx_id",
                         "REJECTED", actor, reason=notes)
        await db.db.prescriptions.update_one(
            {"rx_id": rx_id}, {"$set": {"review": review, "pharmacist": actor}})
        await bus.publish("prescription.rejected", {"rx_id": rx_id,
                                                    "reason": notes}, actor)
        if rx.get("sales_order_id"):
            await db.db.sales_orders.update_one(
                {"order_id": rx["sales_order_id"]},
                {"$set": {"status": "RX_REJECTED"}})
    else:  # CLARIFY
        await transition("prescription", rx_id, "prescriptions", "rx_id",
                         "CLARIFICATION", actor, reason=notes)
        await db.db.prescriptions.update_one(
            {"rx_id": rx_id}, {"$set": {"review": review, "pharmacist": actor}})
    await audit("PRESCRIPTION", rx_id, f"REVIEW_{decision}", actor,
                details={"notes": notes})
    return await _get(rx_id)


# -------------------------------------------------------------------- dispense
async def dispense(payload: dict, actor: dict,
                   idempotency_key: Optional[str] = None) -> dict:
    """Dispense against an approved prescription (physical, pharmacist present)."""
    rx_id = payload.get("rx_id")
    async with idempotent("DISPENSE", idempotency_key or rx_id) as gate:
        if not gate["first_time"]:
            return gate["result"]
        rx = await _get(rx_id)
        if rx["status"] != "APPROVED":
            raise ConflictError(f"Prescription not APPROVED: {rx['status']}")
        # duplicate-dispense prevention: a dispensed Rx can never dispense again
        # (the state machine already blocks it — belt-and-braces ledger check)
        prior = await db.db.dispenses.find_one({"rx_id": rx_id})
        if prior:
            raise ConflictError(
                f"Duplicate dispense blocked: Rx {rx_id} already dispensed "
                f"as {prior.get('dispense_id')}")
        from app.core.rbac import PHARMACIST_AUTHORITY_ROLES

        if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
                and not (set(actor.get("roles", [])) & PHARMACIST_AUTHORITY_ROLES):
            raise DomainError("Dispensing requires pharmacist")
        lines = []
        for m in rx["matched"]:
            if not m["matched"]:
                raise ValidationFailed(f"Unmatched medicine: {m['raw']}")
            qty = float((m.get("raw") or {}).get("dispense_qty") or 1)
            _ = await inventory.fefo_batches(m["product_id"],
                                             payload.get("warehouse_id"), qty)
            # atomic reservation first (race-free), then consume it into a
            # DISPENSE movement — never a direct AVAILABLE decrement (which
            # would double-decrement with the reservation legs)
            await inventory.reserve(m["product_id"], qty, "PRESCRIPTION",
                                    rx_id, payload.get("warehouse_id"), actor)
            mvs = await inventory.consume_reservation(
                "PRESCRIPTION", rx_id, "DISPENSE",
                payload.get("warehouse_id") or "WH-MAIN", actor=actor)
            for mv in mvs:
                lines.append({"sku": m["product_id"],
                              "batch_id": mv["batch_id"],
                              "quantity": mv["quantity"],
                              "movement_id": mv["movement_id"]})
        await transition("prescription", rx_id, "prescriptions", "rx_id",
                         "DISPENSED", actor, reason="Dispensed to patient")
        disp_id = await _next_id("dispense", "DSP")
        doc = {
            "dispense_id": disp_id,
            "rx_id": rx_id,
            "patient": rx.get("patient"),
            "lines": lines,
            "final_check_by": actor,
            "dispensed_at": now_iso(),
        }
        await db.db.dispenses.insert_one(doc)
        await bus.publish("dispense.completed",
                          {"dispense_id": disp_id, "rx_id": rx_id}, actor)
        await audit("DISPENSE", disp_id, "COMPLETED", actor,
                    details={"rx": rx_id})
        result = doc
        gate.store(result)
    return result


# ---------------------------------------------------------------------- queue
async def pharmacist_queue() -> List[dict]:
    """Queue ordered by SLA risk; AI evidence pre-assembled."""
    rows = [_clean(dict(r)) async for r in db.db.prescriptions.find(
        {"status": {"$in": ["EXTRACTED", "VALIDATED", "PHARMACIST_REVIEW",
                            "CLARIFICATION"]}})]
    rows.sort(key=lambda r: (r.get("compliance", {}).get("needs_clarification", False)
                             is not True, r.get("created_at") or ""), reverse=False)
    return rows


async def _get(rx_id: str) -> dict:
    doc = await db.db.prescriptions.find_one({"rx_id": rx_id})
    if not doc:
        raise NotFound(f"Prescription {rx_id} not found")
    return doc


async def get_prescription(rx_id: str) -> dict:
    return await _get(rx_id)


async def list_prescriptions(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.prescriptions.find(q).sort("created_at", -1).limit(200)]


# ==================================================================
# Phase: Pharmacy completion — duplicate-dispense prevention,
# prescription & dispense history.
# ==================================================================
async def dispense_history(rx_id: str = None, patient: str = None,
                           limit: int = 100) -> list:
    """Dispense history filterable by rx or patient (audited reads)."""
    q = {}
    if rx_id:
        q["rx_id"] = rx_id
    if patient:
        q["patient"] = {"$regex": patient, "$options": "i"}
    out = []
    async for d in db.db.dispenses.find(q).sort("dispensed_at", -1).limit(limit):
        d.pop("_id", None)
        out.append(d)
    return out


async def prescription_history(patient: str = None, status: str = None,
                               limit: int = 100) -> list:
    """Prescription history (per patient or status) for pharmacist review."""
    q = {}
    if patient:
        q["patient"] = {"$regex": patient, "$options": "i"}
    if status:
        q["status"] = status
    out = []
    async for r in db.db.prescriptions.find(q).sort("created_at", -1).limit(limit):
        r.pop("_id", None)
        out.append(r)
    return out


async def controlled_register_history(from_date: str = None,
                                      limit: int = 200) -> list:
    """Schedule X register entries (statutory reporting view)."""
    q = {}
    if from_date:
        q["created_at"] = {"$gte": from_date}
    out = []
    async for r in db.db.controlled_registers.find(q).sort(
            "created_at", -1).limit(limit):
        r.pop("_id", None)
        out.append(r)
    return out
