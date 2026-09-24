"""QA / QMS: deviations, CAPA, change control, quality holds, batch release,
controlled documents, complaints. QA reviews and releases; QC tests."""
from datetime import datetime
from typing import List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.workflow import transition


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


# ------------------------------------------------------------------ deviations
async def create_deviation(payload: dict, actor: dict) -> dict:
    if not payload.get("title"):
        raise ValidationFailed("Deviation needs title")
    dev_id = await _next_id("deviation", "DEV")
    doc = {
        "deviation_id": dev_id,
        "title": payload["title"],
        "description": payload.get("description"),
        "entity_type": payload.get("entity_type"),
        "entity_id": payload.get("entity_id"),
        "batch_id": payload.get("batch_id"),
        "severity": payload.get("severity", "MINOR"),
        "source": payload.get("source", "MANUAL"),
        "status": "OPEN",
        "capa_id": None,
        "raised_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "OPEN", "actor": actor, "at": now_iso()}],
    }
    await db.db.deviations.insert_one(doc)
    await bus.publish("deviation.created", {"deviation_id": dev_id,
                                            "severity": doc["severity"]}, actor)
    await audit("DEVIATION", dev_id, "RAISED", actor, new_state="OPEN")
    return _clean(doc)


async def investigate_deviation(dev_id: str, payload: dict, actor: dict) -> dict:
    await transition("deviation", dev_id, "deviations", "deviation_id",
                     "INVESTIGATION", actor)
    await db.db.deviations.update_one(
        {"deviation_id": dev_id},
        {"$set": {"investigation": payload.get("investigation"),
                  "root_cause": payload.get("root_cause"),
                  "impact_assessment": payload.get("impact_assessment"),
                  "updated_at": now_iso()}})
    return await _get_dev(dev_id)


async def close_deviation(dev_id: str, payload: dict, actor: dict) -> dict:
    dev = await _get_dev(dev_id)
    if dev["status"] not in ("INVESTIGATION", "OPEN", "APPROVED"):
        raise ConflictError(f"Deviation not closable: {dev['status']}")
    await transition("deviation", dev_id, "deviations", "deviation_id",
                     "CLOSED", actor, reason=payload.get("conclusion"))
    return await _get_dev(dev_id)


# ------------------------------------------------------------------------- CAPA
async def create_capa(payload: dict, actor: dict) -> dict:
    capa_id = await _next_id("capa", "CAPA")
    doc = {
        "capa_id": capa_id,
        "title": payload.get("title", capa_id),
        "deviation_id": payload.get("deviation_id"),
        "entity_type": payload.get("entity_type"),
        "entity_id": payload.get("entity_id"),
        "actions": payload.get("actions", []),
        "owner": payload.get("owner", actor),
        "due_date": payload.get("due_date"),
        "status": "IDENTIFIED",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "IDENTIFIED", "actor": actor, "at": now_iso()}],
    }
    await db.db.capas.insert_one(doc)
    if payload.get("deviation_id"):
        await db.db.deviations.update_one(
            {"deviation_id": payload["deviation_id"]},
            {"$set": {"capa_id": capa_id}})
    await bus.publish("capa.created", {"capa_id": capa_id}, actor)
    await audit("CAPA", capa_id, "RAISED", actor, new_state="IDENTIFIED")
    return _clean(doc)


async def advance_capa(capa_id: str, payload: dict, actor: dict) -> dict:
    capa = await db.db.capas.find_one({"capa_id": capa_id})
    if not capa:
        raise NotFound(f"CAPA {capa_id} not found")
    flow = {"IDENTIFIED": "PLANNED", "PLANNED": "IMPLEMENTATION",
            "IMPLEMENTATION": "VERIFICATION", "VERIFICATION": "CLOSED"}
    target = flow.get(capa["status"])
    if not target:
        raise ConflictError(f"CAPA terminal at {capa['status']}")
    if target == "CLOSED":
        # QA authority required to close
        from app.core.rbac import QA_AUTHORITY_ROLES

        if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
                and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
            raise ValidationFailed("CAPA closure requires QA authority")
    await transition("capa", capa_id, "capas", "capa_id", target, actor,
                     reason=payload.get("note"))
    return await db.db.capas.find_one({"capa_id": capa_id})


# ---------------------------------------------------------------- quality hold
async def apply_quality_hold(entity_type: str, entity_id: str, reason: str,
                             actor: dict) -> dict:
    hold_id = await _next_id("hold", "HLD")
    doc = {
        "hold_id": hold_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "reason": reason,
        "status": "ACTIVE",
        "applied_by": actor,
        "applied_at": now_iso(),
        "released_at": None,
    }
    await db.db.quality_holds.insert_one(doc)
    if entity_type == "BATCH":
        from app.domains.inventory.service import block_batch

        await block_batch(entity_id, f"QUALITY_HOLD {hold_id}: {reason}", actor,
                          source="QA")
    if entity_type == "PRODUCTION_ORDER":
        await db.db.production_orders.update_one(
            {"order_id": entity_id}, {"$set": {"status": "ON_HOLD"}})
    await bus.publish("qa.hold_applied",
                      {"hold_id": hold_id, "entity": f"{entity_type}:{entity_id}",
                       "reason": reason}, actor)
    await bus.publish("qa.hold",
                      {"hold_id": hold_id, "entity": f"{entity_type}:{entity_id}",
                       "reason": reason}, actor)
    await audit("QUALITY_HOLD", hold_id, "APPLIED", actor,
                details={"entity": f"{entity_type}:{entity_id}", "reason": reason})
    from app.core.notifications import notify

    await notify("QUALITY_HOLD_NOTICE_CREATED",
                 f"Quality hold applied: {entity_id}",
                 f"Hold {hold_id}: {reason}",
                 ["qa@pharmaos.local"], entity_type, entity_id)
    return doc


async def release_quality_hold(hold_id: str, actor: dict, reason: str) -> dict:
    hold = await db.db.quality_holds.find_one({"hold_id": hold_id})
    if not hold:
        raise NotFound(f"Hold {hold_id} not found")
    if hold["status"] != "ACTIVE":
        raise ConflictError("Hold not active")
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        raise ValidationFailed("Hold release requires QA authority")
    await db.db.quality_holds.update_one(
        {"hold_id": hold_id},
        {"$set": {"status": "RELEASED", "released_at": now_iso(),
                  "release_reason": reason}})
    if hold["entity_type"] == "BATCH":
        from app.domains.inventory.service import unblock_batch

        await unblock_batch(hold["entity_id"], actor, reason)
    await audit("QUALITY_HOLD", hold_id, "RELEASED", actor, reason=reason)
    return await db.db.quality_holds.find_one({"hold_id": hold_id})


# --------------------------------------------------------------- batch release
async def batch_release_review(production_order_id: str, actor: dict) -> dict:
    """Assemble the release dossier (QA agent prepares evidence)."""
    po_doc = await db.db.production_orders.find_one({"order_id": production_order_id})
    if not po_doc:
        raise NotFound(f"Production order {production_order_id} not found")
    samples = [_clean(dict(s)) async for s in db.db.qc_samples.find(
        {"ref_type": "PRODUCTION_ORDER", "ref_id": production_order_id})]
    deviations = [_clean(dict(d)) async for d in db.db.deviations.find(
        {"entity_type": "PRODUCTION_ORDER", "entity_id": production_order_id})]
    dossier = {
        "production_order": _clean(po_doc),
        "qc_samples": samples,
        "deviations": deviations,
        "batch_record_complete": bool(po_doc.get("batch_record_completed")),
        "yield_ok": (po_doc.get("yield_pct") or 0) >= float(
            po_doc.get("min_yield_pct", 95)),
        "all_qc_passed": bool(samples) and all(
            s["status"] == "COMPLETED" and
            all(t.get("verdict") == "PASS" for t in s["tests"]) for s in samples),
        "no_open_deviations": all(d["status"] == "CLOSED" for d in deviations),
    }
    await db.db.batch_releases.update_one(
        {"production_order_id": production_order_id},
        {"$set": {"dossier": dossier, "status": "REVIEW",
                  "reviewed_by": actor, "updated_at": now_iso()}},
        upsert=True)
    return dossier


async def batch_release(production_order_id: str, actor: dict, decision: str,
                        reason: str) -> dict:
    """QA authority decision: RELEASE / REJECT / HOLD."""
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        raise ValidationFailed("Batch release requires QA authority")

    po_doc = await db.db.production_orders.find_one({"order_id": production_order_id})
    if not po_doc:
        raise NotFound(f"Production order {production_order_id} not found")
    dossier = await batch_release_review(production_order_id, actor)

    release_id = await _next_id("release", "REL")
    doc = {
        "release_id": release_id,
        "production_order_id": production_order_id,
        "batch_id": po_doc.get("batch_id"),
        "decision": decision,
        "reason": reason,
        "dossier": dossier,
        "released_by": actor,
        "released_at": now_iso(),
    }
    await db.db.batch_releases.insert_one(doc)

    if decision == "RELEASE":
        await transition("production_order", production_order_id,
                         "production_orders", "order_id", "BATCH_RELEASED", actor,
                         reason=reason)
        # FG batch becomes available
        from app.domains.inventory.service import change_stock_status

        await db.db.batches.update_one(
            {"batch_id": po_doc["batch_id"]},
            {"$set": {"qa_status": "RELEASED", "blocked": False,
                      "block_reason": None, "updated_at": now_iso()}})
        # quantity-level status move: the FG stock row must leave QUARANTINE
        # and enter AVAILABLE, otherwise the released batch can never be
        # allocated/picked (parity + P0 correctness)
        rows = [_clean(dict(r)) async for r in db.db.inventory_balances.find(
            {"product_id": po_doc["product_id"],
             "batch_id": po_doc["batch_id"],
             "stock_status": "QUARANTINE", "quantity": {"$gt": 0}})]
        for row in rows:
            await change_stock_status(
                product_id=row["product_id"],
                warehouse_id=row["warehouse_id"],
                batch_id=row["batch_id"],
                from_status="QUARANTINE", to_status="AVAILABLE",
                quantity=float(row["quantity"]), actor=actor,
                reference_type="BATCH_RELEASE", reference_id=release_id,
                uom=row.get("uom", "BOX"),
                location_id=row.get("location_id"))
        await bus.publish("batch.released",
                          {"production_order_id": production_order_id,
                           "batch_id": po_doc["batch_id"]}, actor)
        await audit("BATCH_RELEASE", release_id, "RELEASED", actor, reason=reason)
        from app.core.notifications import notify

        await notify("QUALITY_HOLD_NOTICE_CREATED",
                     f"Batch released: {po_doc['batch_id']}",
                     f"Batch {po_doc['batch_id']} released by QA. Reason: {reason}",
                     ["warehouse@pharmaos.local", "sales@pharmaos.local"],
                     "BATCH", po_doc["batch_id"])
    elif decision == "REJECT":
        await transition("production_order", production_order_id,
                         "production_orders", "order_id", "REJECTED", actor,
                         reason=reason)
        await bus.publish("batch.blocked",
                          {"production_order_id": production_order_id}, actor)
        await audit("BATCH_RELEASE", release_id, "REJECTED", actor, reason=reason)
    else:  # HOLD
        await apply_quality_hold("PRODUCTION_ORDER", production_order_id,
                                 reason, actor)
        await audit("BATCH_RELEASE", release_id, "HELD", actor, reason=reason)
    return doc


# --------------------------------------------------------- controlled documents
async def create_controlled_document(payload: dict, actor: dict) -> dict:
    doc_id = await _next_id("cdoc", "SOP")
    doc = {
        "doc_id": doc_id,
        "title": payload["title"],
        "doc_type": payload.get("doc_type", "SOP"),
        "version": payload.get("version", 1),
        "file_ref": payload.get("file_ref"),
        "owner": actor,
        "effective_from": payload.get("effective_from", now_iso()[:10]),
        "status": "EFFECTIVE",
        "superseded_by": None,
        "training_required": payload.get("training_required", True),
        "created_at": now_iso(),
    }
    await db.db.controlled_documents.insert_one(doc)
    await audit("CONTROLLED_DOCUMENT", doc_id, "EFFECTIVE", actor,
                details={"version": doc["version"]})
    return doc


async def supersede_document(doc_id: str, payload: dict, actor: dict) -> dict:
    old = await db.db.controlled_documents.find_one({"doc_id": doc_id})
    if not old:
        raise NotFound(f"Document {doc_id} not found")
    new_version = float(old["version"]) + 1
    await db.db.controlled_documents.update_one(
        {"doc_id": doc_id},
        {"$set": {"status": "SUPERSEDED", "superseded_at": now_iso()}})
    new_doc = {**old, "_id": None, "version": new_version,
               "file_ref": payload.get("file_ref"),
               "status": "EFFECTIVE",
               "effective_from": payload.get("effective_from", now_iso()[:10]),
               "created_at": now_iso()}
    new_doc.pop("_id", None)
    await db.db.controlled_documents.insert_one(new_doc)
    await audit("CONTROLLED_DOCUMENT", doc_id, "SUPERSEDED", actor,
                details={"new_version": new_version})
    return new_doc


# ------------------------------------------------------------------ complaints
async def create_complaint(payload: dict, actor: dict) -> dict:
    comp_id = await _next_id("complaint", "CMP")
    doc = {
        "complaint_id": comp_id,
        "product_id": payload.get("product_id"),
        "batch_id": payload.get("batch_id"),
        "reporter": payload.get("reporter"),
        "description": payload.get("description"),
        "category": payload.get("category", "PRODUCT_QUALITY"),
        "status": "OPEN",
        "deviation_id": None,
        "safety_case_id": payload.get("safety_case_id"),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.complaints.insert_one(doc)
    # A complaint alleging possible harm (or an actual adverse event) is a
    # quality incident: it must trigger BOTH a QA deviation (investigation)
    # and a linked PV safety case (separate but connected workflows).
    if payload.get("adverse_event") or payload.get("suspected_adverse_event"):
        dev = await create_deviation({
            "title": f"Complaint {comp_id}",
            "entity_type": "COMPLAINT", "entity_id": comp_id,
            "batch_id": payload.get("batch_id"),
            "source": "COMPLAINT",
        }, actor)
        await db.db.complaints.update_one({"complaint_id": comp_id},
                                          {"$set": {"deviation_id": dev["deviation_id"]}})
        doc["deviation_id"] = dev["deviation_id"]
    # Complaint↔PV linkage: any suspected quality defect that may have caused
    # harm becomes a linked safety case (separate but connected workflows).
    if payload.get("suspected_adverse_event"):
        from app.domains.safety import service as safety

        try:
            ae = await safety.report_adverse_event({
                "reporter": payload.get("reporter", {}),
                "suspect_medicine": payload.get("product_id"),
                "batch_id": payload.get("batch_id"),
                "reaction": payload.get("suspected_reaction", "Unknown"),
                "complaint_id": comp_id,
            }, actor)
            await db.db.complaints.update_one(
                {"complaint_id": comp_id},
                {"$set": {"safety_case_id": ae["case_id"]}})
            doc["safety_case_id"] = ae["case_id"]
        except Exception:
            pass  # linkage failure never blocks complaint intake
    await bus.publish("complaint.opened",
                      {"complaint_id": comp_id,
                       "category": doc["category"],
                       "batch_id": doc["batch_id"],
                       "safety_case_id": doc.get("safety_case_id")}, actor)
    await audit("COMPLAINT", comp_id, "OPENED", actor,
                details={"category": doc["category"],
                         "safety_case_id": doc.get("safety_case_id")})
    return doc


async def investigate_complaint(complaint_id: str, payload: dict, actor: dict) -> dict:
    """Product/batch check → investigation record (→ CAPA if required)."""
    comp = await db.db.complaints.find_one({"complaint_id": complaint_id})
    if not comp:
        raise NotFound(f"Complaint {complaint_id} not found")
    if comp["status"] != "OPEN":
        raise ConflictError(f"Complaint not OPEN: {comp['status']}")
    # deterministic batch check: was this batch recalled / held / OOS?
    batch_flags = []
    if comp.get("batch_id"):
        batch = await db.db.batches.find_one({"batch_id": comp["batch_id"]})
        if batch and batch.get("blocked"):
            batch_flags.append("BLOCKED")
        if await db.db.recalls.find_one({"batch_ids": comp["batch_id"],
                                         "status": {"$nin": ["CLOSED", "CANCELLED"]}}):
            batch_flags.append("UNDER_RECALL")
        if await db.db.quality_holds.find_one({"batch_id": comp["batch_id"],
                                               "status": "ACTIVE"}):
            batch_flags.append("QUALITY_HOLD")
    investigation = {
        "investigator": actor,
        "findings": payload.get("findings", ""),
        "root_cause": payload.get("root_cause", ""),
        "batch_flags": batch_flags,
        "at": now_iso(),
    }
    await transition("complaint", complaint_id, "complaints", "complaint_id",
                     "INVESTIGATION", actor, reason=payload.get("findings", ""))
    await db.db.complaints.update_one(
        {"complaint_id": complaint_id},
        {"$set": {"investigation": investigation, "updated_at": now_iso()}})
    await audit("COMPLAINT", complaint_id, "INVESTIGATION", actor,
                details={"batch_flags": batch_flags})
    return await db.db.complaints.find_one({"complaint_id": complaint_id})


async def resolve_complaint(complaint_id: str, payload: dict, actor: dict) -> dict:
    """Resolution; optional CAPA linkage; complaint→QA dev path preserved."""
    comp = await db.db.complaints.find_one({"complaint_id": complaint_id})
    if not comp:
        raise NotFound(f"Complaint {complaint_id} not found")
    if comp["status"] not in ("OPEN", "INVESTIGATION"):
        raise ConflictError(f"Complaint not resolvable: {comp['status']}")
    capa_id = None
    if payload.get("capa_required") and not comp.get("capa_id"):
        capa = await create_capa({
            "title": f"CAPA for complaint {complaint_id}",
            "entity_type": "COMPLAINT", "entity_id": complaint_id,
            "actions": payload.get("actions", []),
        }, actor)
        capa_id = capa.get("capa_id") if isinstance(capa, dict) else capa
    await transition("complaint", complaint_id, "complaints", "complaint_id",
                     "RESOLVED", actor, reason=payload.get("resolution"))
    await db.db.complaints.update_one(
        {"complaint_id": complaint_id},
        {"$set": {"resolution": payload.get("resolution", ""),
                  "capa_id": capa_id or comp.get("capa_id"),
                  "resolved_by": actor, "updated_at": now_iso()}})
    await bus.publish("complaint.resolved", {"complaint_id": complaint_id}, actor)
    await audit("COMPLAINT", complaint_id, "RESOLVED", actor,
                details={"capa_id": capa_id})
    return await db.db.complaints.find_one({"complaint_id": complaint_id})


async def close_complaint(complaint_id: str, actor: dict) -> dict:
    comp = await db.db.complaints.find_one({"complaint_id": complaint_id})
    if not comp:
        raise NotFound(f"Complaint {complaint_id} not found")
    if comp["status"] != "RESOLVED":
        raise ConflictError("Complaint must be RESOLVED before closure")
    await transition("complaint", complaint_id, "complaints", "complaint_id",
                     "CLOSED", actor, reason=comp.get("resolution"))
    await audit("COMPLAINT", complaint_id, "CLOSED", actor)
    return await db.db.complaints.find_one({"complaint_id": complaint_id})


async def list_complaints(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.complaints.find(q).sort("created_at", -1).limit(200)]


async def _get_dev(dev_id: str) -> dict:
    doc = await db.db.deviations.find_one({"deviation_id": dev_id})
    if not doc:
        raise NotFound(f"Deviation {dev_id} not found")
    return doc


async def list_deviations(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.deviations.find(q).sort("created_at", -1).limit(200)]


async def list_capas(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.capas.find(q).sort("created_at", -1).limit(200)]


async def list_holds(active_only: bool = True) -> List[dict]:
    q = {"status": "ACTIVE"} if active_only else {}
    return [_clean(dict(r)) async for r in db.db.quality_holds.find(q).limit(200)]


# ---------------------------------------------------------------- change control
CHANGE_CATEGORIES = ("SPECIFICATION", "TEST_METHOD", "BOM_FORMULA", "EQUIPMENT",
                     "PROCESS", "SUPPLIER", "SOFTWARE", "SOP")


async def create_change_request(payload: dict, actor: dict) -> dict:
    """Change request with impact assessment; never overwrites approved masters
    directly — the change must go through implementation + verification (Part 15)."""
    cat = payload.get("category")
    if cat not in CHANGE_CATEGORIES:
        raise ValidationFailed(f"category must be one of {CHANGE_CATEGORIES}")
    for k in ("title", "target_id", "justification"):
        if not payload.get(k):
            raise ValidationFailed(f"Change request needs {k}")
    change_id = await _next_id("change_request", "CHG")
    doc = {"change_id": change_id,
           "category": cat,
           "title": payload["title"],
           "target_id": payload["target_id"],
           "justification": payload["justification"],
           "impact_assessment": payload.get("impact_assessment", ""),
           "current_version": payload.get("current_version"),
           "proposed_version": payload.get("proposed_version"),
           "proposed_payload": payload.get("proposed_payload", {}),
           "status": "SUBMITTED",
           "workflow": "CHANGE_CONTROL",
           "created_by": actor,
           "created_at": now_iso(),
           "history": [{"state": "SUBMITTED", "at": now_iso(), "by": actor}]}
    await db.db.change_requests.insert_one(doc)
    await bus.publish("change.created", {"change_id": change_id,
                                         "category": cat,
                                         "target_id": payload["target_id"]})
    await audit("CHANGE_REQUEST", change_id, "CREATED", actor,
                details={"category": cat, "target": payload["target_id"]})
    return _clean(doc)


async def assess_change(change_id: str, payload: dict, actor: dict) -> dict:
    """Impact assessment + QA review routing."""
    c = await db.db.change_requests.find_one({"change_id": change_id})
    if not c:
        raise NotFound(f"Change {change_id} not found")
    if c["status"] != "SUBMITTED":
        raise ConflictError(f"Change in state {c['status']}, SUBMITTED expected")
    doc = await transition("change_request", change_id, "change_requests",
                           "change_id", "IN_REVIEW", actor,
                           reason="impact assessment")
    assessment = {"assessment": payload.get("assessment", ""),
                  "risk_level": payload.get("risk_level", "LOW"),
                  "requires_training": bool(payload.get("requires_training")),
                  "requires_validation": bool(payload.get("requires_validation")),
                  "affected_documents": payload.get("affected_documents", []),
                  "assessed_by": actor, "assessed_at": now_iso()}
    await db.db.change_requests.update_one(
        {"change_id": change_id}, {"$set": {"impact": assessment}})
    await audit("CHANGE_REQUEST", change_id, "ASSESSMENT_RECORDED", actor,
                details={"risk": assessment["risk_level"]})
    return _clean(doc)


async def approve_change(change_id: str, payload: dict, actor: dict) -> dict:
    """Approver decision. SoD: an assessor cannot be the sole approver; a QA
    role must sign off before implementation is allowed."""
    c = await db.db.change_requests.find_one({"change_id": change_id})
    if not c:
        raise NotFound(f"Change {change_id} not found")
    if c["status"] not in ("IN_REVIEW", "PENDING_APPROVAL"):
        raise ConflictError(f"Change in state {c['status']}")
    roles = set(actor.get("roles") or [])
    if "QA" not in roles and "MANAGEMENT" not in roles:
        raise ValidationFailed("QA or Management must approve change control")
    if (c.get("impact") or {}).get("assessed_by", {}).get("user_id") == \
            actor.get("user_id") and len(roles) == 1:
        raise ConflictError("Change assessor cannot solely approve their own "
                            "assessment (segregation of duties)")
    decision = payload.get("decision", "APPROVED")
    if decision == "REJECTED":
        doc = await transition("change_request", change_id, "change_requests",
                               "change_id", "REJECTED", actor,
                               reason=payload.get("reason", ""))
        return _clean(doc)
    doc = await transition("change_request", change_id, "change_requests",
                           "change_id", "APPROVED", actor,
                           reason=payload.get("reason", ""))
    await db.db.change_requests.update_one(
        {"change_id": change_id},
        {"$set": {"approval": {"approved_by": actor, "at": now_iso(),
                               "reason": payload.get("reason", "")}}})
    await bus.publish("change.approved", {"change_id": change_id})
    return _clean(doc)


async def implement_change(change_id: str, payload: dict, actor: dict) -> dict:
    """Apply the proposed payload to the target master with full version
    history; the previous approved version is archived, never overwritten."""
    c = await db.db.change_requests.find_one({"change_id": change_id})
    if not c:
        raise NotFound(f"Change {change_id} not found")
    if c["status"] != "APPROVED":
        raise ConflictError("Change must be APPROVED before implementation")
    await transition("change_request", change_id, "change_requests",
                     "change_id", "IMPLEMENTED", actor,
                     reason=payload.get("implementation_notes", ""))
    proposed = c.get("proposed_payload") or {}
    update = {"$set": {f"change_applied.{k}": v for k, v in proposed.items()},
              "$push": {"change_history": {
                  "change_id": change_id,
                  "applied_at": now_iso(),
                  "applied_by": actor,
                  "previous_version": c.get("current_version"),
                  "new_version": c.get("proposed_version"),
                  "snapshot_before": {"version": c.get("current_version")}}}}
    res = await db.db.specifications.update_one(
        {"spec_id": c["target_id"]}, update)
    if res.matched_count == 0:
        # specifications may be keyed by product_id (latest approved version)
        res = await db.db.specifications.update_one(
            {"product_id": c["target_id"]}, update)
    if res.matched_count == 0:
        await db.db.products.update_one({"product_id": c["target_id"]}, update)
    await audit("CHANGE_REQUEST", change_id, "IMPLEMENTED", actor,
                details={"target": c["target_id"],
                         "new_version": c.get("proposed_version")})
    await bus.publish("change.implemented", {"change_id": change_id})
    return {"change_id": change_id, "status": "IMPLEMENTED",
            "target_id": c["target_id"]}


async def verify_change(change_id: str, payload: dict, actor: dict) -> dict:
    """Post-implementation verification → CLOSED. Human/authorized QA only."""
    c = await db.db.change_requests.find_one({"change_id": change_id})
    if not c:
        raise NotFound(f"Change {change_id} not found")
    if c["status"] != "IMPLEMENTED":
        raise ConflictError("Change must be IMPLEMENTED before verification")
    doc = await transition("change_request", change_id, "change_requests",
                           "change_id", "CLOSED", actor,
                           reason=payload.get("verification", ""))
    await db.db.change_requests.update_one(
        {"change_id": change_id},
        {"$set": {"verification": {"verified_by": actor, "at": now_iso(),
                                   "notes": payload.get("verification", "")}}})
    await audit("CHANGE_REQUEST", change_id, "CLOSED", actor,
                details={"verification": payload.get("verification", "")})
    return _clean(doc)


# --------------------------------------------------------- risk assessments
async def create_risk_assessment(payload: dict, actor: dict) -> dict:
    """Quality risk assessment (FMEA-style scoring) linked to any entity."""
    for k in ("subject_type", "subject_id"):
        if not payload.get(k):
            raise ValidationFailed(f"Risk assessment needs {k}")
    sev = float(payload.get("severity") or 0)
    occ = float(payload.get("occurrence") or 0)
    det = float(payload.get("detectability") or 0)
    if not (1 <= sev <= 10 and 1 <= occ <= 10 and 1 <= det <= 10):
        raise ValidationFailed("severity/occurrence/detectability must be 1-10")
    ra_id = await _next_id("risk_assessment", "RA")
    doc = {"risk_id": ra_id,
           "subject_type": payload["subject_type"],
           "subject_id": payload["subject_id"],
           "severity": sev, "occurrence": occ, "detectability": det,
           "rpn": sev * occ * det,
           "risk_level": "HIGH" if sev * occ * det >= 100 else
                         "MEDIUM" if sev * occ * det >= 40 else "LOW",
           "mitigation": payload.get("mitigation", ""),
           "status": "OPEN",
           "created_by": actor, "created_at": now_iso()}
    await db.db.risk_assessments.insert_one(doc)
    await audit("RISK_ASSESSMENT", ra_id, "CREATED", actor,
                details={"subject": f"{payload['subject_type']}/{payload['subject_id']}",
                         "rpn": doc["rpn"]})
    return _clean(doc)


async def close_risk_assessment(ra_id: str, payload: dict, actor: dict) -> dict:
    r = await db.db.risk_assessments.find_one({"risk_id": ra_id})
    if not r:
        raise NotFound(f"Risk assessment {ra_id} not found")
    if r["status"] == "CLOSED":
        raise ConflictError("Already closed")
    doc = await transition("risk_assessment", ra_id, "risk_assessments",
                           "risk_id", "CLOSED", actor,
                           reason=payload.get("mitigation_done", ""))
    await db.db.risk_assessments.update_one(
        {"risk_id": ra_id},
        {"$set": {"residual_rpn": float(payload.get("residual_rpn") or 0),
                  "closed_by": actor, "closed_at": now_iso()}})
    await audit("RISK_ASSESSMENT", ra_id, "CLOSED", actor,
                details={"residual": payload.get("residual_rpn")})
    return _clean(doc)


async def list_risk_assessments(status: Optional[str] = None) -> List[dict]:
    q = {"status": status} if status else {}
    return [_clean(dict(r)) async for r in
            db.db.risk_assessments.find(q).sort("_id", -1).limit(200)]


# ------------------------------------------------------ CAPA effectiveness + overdue
async def close_capa(capa_id: str, payload: dict, actor: dict) -> dict:
    """CAPA closure requires an EFFECTIVE effectiveness check (Part 14)."""
    c = await db.db.capas.find_one({"capa_id": capa_id})
    if not c:
        raise NotFound(f"CAPA {capa_id} not found")
    if c["status"] == "CLOSED":
        raise ConflictError("CAPA already closed")
    if c["status"] != "VERIFICATION":
        raise ConflictError(f"CAPA must be in VERIFICATION before closure "
                            f"(now {c['status']})")
    eff = payload.get("effectiveness") or {}
    if eff.get("outcome") != "EFFECTIVE":
        raise ValidationFailed("CAPA can only close with effectiveness=EFFECTIVE; "
                               "re-plan corrective actions otherwise")
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        raise ValidationFailed("CAPA closure requires QA authority")
    doc = await transition("capa", capa_id, "capas", "capa_id", "CLOSED", actor,
                           reason=eff.get("notes", ""))
    await db.db.capas.update_one(
        {"capa_id": capa_id},
        {"$set": {"effectiveness": {**eff, "checked_by": actor,
                                    "checked_at": now_iso()},
                  "closed_at": now_iso()}})
    await audit("CAPA", capa_id, "CLOSED", actor,
                details={"outcome": eff.get("outcome")})
    await bus.publish("capa.closed", {"capa_id": capa_id})
    return _clean(doc)


async def capa_overdue_report() -> dict:
    """Overdue CAPA monitor for the QA dashboard + agent monitoring."""
    today = now_iso()[:10]
    overdue = [_clean(dict(r)) async for r in db.db.capas.find({
        "status": {"$nin": ["CLOSED", "CANCELLED"]},
        "due_date": {"$ne": None, "$lt": today}}).limit(200)]
    for c in overdue:
        days = (datetime.fromisoformat(today) -
                datetime.fromisoformat(str(c["due_date"])[:10])).days
        c["days_overdue"] = days
    return {"overdue_count": len(overdue), "overdue": overdue}
