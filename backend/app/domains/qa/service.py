"""QA / QMS: deviations, CAPA, change control, quality holds, batch release,
controlled documents, complaints. QA reviews and releases; QC tests."""
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
        from app.domains.inventory.service import ensure_batch

        await db.db.batches.update_one(
            {"batch_id": po_doc["batch_id"]},
            {"$set": {"qa_status": "RELEASED", "blocked": False,
                      "block_reason": None, "updated_at": now_iso()}})
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
    if payload.get("adverse_event"):
        dev = await create_deviation({
            "title": f"Complaint {comp_id}",
            "entity_type": "COMPLAINT", "entity_id": comp_id,
            "batch_id": payload.get("batch_id"),
            "source": "COMPLAINT",
        }, actor)
        await db.db.complaints.update_one({"complaint_id": comp_id},
                                          {"$set": {"deviation_id": dev["deviation_id"]}})
    await audit("COMPLAINT", comp_id, "OPENED", actor)
    return doc


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
