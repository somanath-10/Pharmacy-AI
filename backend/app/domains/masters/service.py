"""Masters domain: warehouses, products, customers, equipment, specifications, BOMs."""
from typing import List, Optional, Tuple

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import NotFound, ValidationFailed


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def next_code(coll: str, prefix: str) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": f"code_{coll}"}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


# ----------------------------------------------------------------- warehouses
WAREHOUSE_TYPES = ["MAIN", "COLD_STORAGE", "NARCOTICS", "QUARANTINE", "RETURNS", "TRANSIT", "PLANT"]

async def create_warehouse(payload: dict) -> dict:
    required = ["name", "type", "site_id"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValidationFailed(f"Missing fields: {missing}")
    if payload["type"] not in WAREHOUSE_TYPES:
        raise ValidationFailed(f"type must be one of {WAREHOUSE_TYPES}")
    code = payload.get("code") or await next_code("warehouse", "WH")
    doc = {
        "code": code,
        "name": payload["name"],
        "type": payload["type"],
        "site_id": payload["site_id"],
        "address": payload.get("address", ""),
        "storage_conditions": payload.get("storage_conditions", "AMBIENT"),
        "temperature_range": payload.get("temperature_range"),
        "zones": payload.get("zones", []),
        "status": "ACTIVE",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    if payload.get("zones"):
        doc["locations"] = [
            {"location_id": f"{code}-{z['name']}-{i:03d}", "zone": z["name"],
             "rack": loc.get("rack"), "bin": loc.get("bin"),
             "temp_control": z.get("temp_control", False)}
            for z in payload["zones"]
            for i, loc in enumerate((z.get("locations") or [{}]), 1)
        ]
    else:
        doc["locations"] = []
    await db.db.warehouses.insert_one(doc)
    return _clean(doc)


async def list_warehouses() -> List[dict]:
    return [_clean(dict(r)) async for r in db.db.warehouses.find({"status": "ACTIVE"})]


# ------------------------------------------------------------------- products
PRODUCT_TYPES = ["FINISHED_GOOD", "RAW_MATERIAL", "PACKAGING_MATERIAL", "TRADE_ITEM"]
DRUG_SCHEDULES = [None, "H", "H1", "X", "NARCOTIC", "OTC"]

async def create_product(payload: dict) -> dict:
    required = ["name", "type", "uom"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValidationFailed(f"Missing fields: {missing}")
    if payload["type"] not in PRODUCT_TYPES:
        raise ValidationFailed(f"type must be one of {PRODUCT_TYPES}")
    if payload.get("schedule") not in DRUG_SCHEDULES:
        raise ValidationFailed(f"schedule must be one of {DRUG_SCHEDULES}")
    sku = payload.get("sku") or await next_code("product", "PRD")
    doc = {
        "sku": sku,
        "name": payload["name"],
        "type": payload["type"],
        "uom": payload["uom"],
        "gtin": payload.get("gtin"),
        "barcode": payload.get("barcode"),
        "category": payload.get("category"),
        "manufacturer": payload.get("manufacturer"),
        "schedule": payload.get("schedule"),
        "is_prescription": payload.get("is_prescription", payload.get("schedule") in ("H", "H1", "X", "NARCOTIC")),
        "is_controlled": payload.get("schedule") in ("X", "NARCOTIC"),
        "storage_conditions": payload.get("storage_conditions", "AMBIENT"),
        "shelf_life_days": payload.get("shelf_life_days"),
        "mrp": payload.get("mrp"),
        "std_cost": payload.get("std_cost"),
        "min_stock": payload.get("min_stock", 0),
        "safety_stock": payload.get("safety_stock", 0),
        "reorder_point": payload.get("reorder_point", 0),
        "status": "ACTIVE",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.products.insert_one(doc)
    return _clean(doc)


async def list_products(product_type: Optional[str] = None) -> List[dict]:
    q = {"status": "ACTIVE"}
    if product_type:
        q["type"] = product_type
    return [_clean(dict(r)) async for r in db.db.products.find(q).limit(500)]


async def get_product(sku: str) -> dict:
    doc = await db.db.products.find_one({"sku": sku})
    if not doc:
        raise NotFound(f"Product {sku} not found")
    return _clean(doc)


# ------------------------------------------------------------------ customers
async def create_customer(payload: dict) -> dict:
    required = ["name"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValidationFailed(f"Missing fields: {missing}")
    code = payload.get("code") or await next_code("customer", "CUS")
    doc = {
        "code": code,
        "name": payload["name"],
        "type": payload.get("type", "RETAIL"),  # RETAIL | HOSPITAL | DISTRIBUTOR | MANUFACTURER
        "contact": payload.get("contact", {}),
        "gstn": payload.get("gstn"),
        "drug_licence": payload.get("drug_licence"),
        "credit_limit": payload.get("credit_limit", 0),
        "payment_terms_days": payload.get("payment_terms_days", 30),
        "pricing_tier": payload.get("pricing_tier", "STANDARD"),
        "status": "ACTIVE",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.customers.insert_one(doc)
    return _clean(doc)


async def list_customers() -> List[dict]:
    return [_clean(dict(r)) async for r in db.db.customers.find({"status": "ACTIVE"})]


# ------------------------------------------------------------------ equipment
EQUIPMENT_STATUSES = ["RELEASED", "UNDER_MAINTENANCE", "QUARANTINE", "DECOMMISSIONED"]

async def create_equipment(payload: dict) -> dict:
    required = ["name", "site_id"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValidationFailed(f"Missing fields: {missing}")
    code = payload.get("code") or await next_code("equipment", "EQ")
    doc = {
        "code": code,
        "name": payload["name"],
        "site_id": payload["site_id"],
        "equipment_type": payload.get("equipment_type", "GENERAL"),
        "qualification_status": payload.get("qualification_status", "IQ_OQ_PQ_DONE"),
        "calibration_status": payload.get("calibration_status", "VALID"),
        "calibration_due": payload.get("calibration_due"),
        "maintenance_status": payload.get("maintenance_status", "OK"),
        "maintenance_due": payload.get("maintenance_due"),
        "cleaning_status": payload.get("cleaning_status", "VALID"),
        "status": payload.get("status", "RELEASED"),
        "logs": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.equipment.insert_one(doc)
    return _clean(doc)


def equipment_ready(equipment: dict) -> Tuple[bool, List[str]]:
    """GMP gate used before batch start, QC testing and equipment use (Part 16).

    Deterministic: date-based checks win over stale label flags — a record
    marked VALID but past its due date is still blocked."""
    today = now_iso()[:10]
    blockers = []
    if equipment.get("calibration_status") != "VALID":
        blockers.append(f"calibration_status={equipment.get('calibration_status')}")
    cal_due = equipment.get("calibration_due")
    if cal_due and str(cal_due)[:10] <= today:
        blockers.append(f"calibration_due={str(cal_due)[:10]} (expired)")
    if equipment.get("maintenance_status") == "OVERDUE":
        blockers.append("maintenance_status=OVERDUE")
    maint_due = equipment.get("maintenance_due")
    if maint_due and str(maint_due)[:10] <= today:
        blockers.append(f"maintenance_due={str(maint_due)[:10]} (overdue)")
    if equipment.get("cleaning_status") != "VALID":
        blockers.append(f"cleaning_status={equipment.get('cleaning_status')}")
    qual = equipment.get("qualification_status")
    if qual in ("PENDING", "FAILED", "EXPIRED"):
        blockers.append(f"qualification_status={qual}")
    elif qual is None:
        # legacy equipment without qualification_status field: treat as qualified
        pass
    if equipment.get("on_hold"):
        blockers.append("equipment on HOLD")
    if equipment.get("status") != "RELEASED":
        blockers.append(f"status={equipment.get('status')}")
    return (len(blockers) == 0, blockers)


async def get_equipment(code: str) -> Optional[dict]:
    """Equipment lookup by code; None when unknown."""
    return await db.db.equipment.find_one({"code": code})


async def require_equipment_ready(code: str) -> dict:
    """Gate for QC result entry / production / any equipment use: raises when
    the instrument is not fit for use. Deterministic and centralized."""
    eq = await db.db.equipment.find_one({"code": code})
    if not eq:
        raise NotFound(f"Equipment {code} not found")
    ok, blockers = equipment_ready(eq)
    if not ok:
        raise ValidationFailed(
            f"Equipment {code} not usable: " + "; ".join(blockers))
    return eq


async def calibrate_equipment(code: str, payload: dict, actor: dict) -> dict:
    """Record calibration; extends the due date deterministically and clears
    the calibration blocker."""
    eq = await db.db.equipment.find_one({"code": code})
    if not eq:
        raise NotFound(f"Equipment {code} not found")
    due = payload.get("next_due")
    if not due:
        raise ValidationFailed("calibration needs next_due date")
    if str(due)[:10] <= now_iso()[:10]:
        raise ValidationFailed("next_due must be in the future")
    await db.db.equipment.update_one(
        {"code": code},
        {"$set": {"calibration_status": "VALID", "calibration_due": str(due)[:10],
                  "last_calibrated_at": now_iso(), "updated_at": now_iso()},
         "$push": {"logs": {"event": "CALIBRATION", "result": payload.get("result", "PASS"),
                            "certificate": payload.get("certificate_ref"),
                            "actor": actor, "at": now_iso()}}})
    await audit("EQUIPMENT", code, "CALIBRATED", actor,
                details={"next_due": str(due)[:10]})
    return await db.db.equipment.find_one({"code": code})


async def maintenance_equipment(code: str, payload: dict, actor: dict) -> dict:
    """Preventive/breakdown maintenance completion: clears OVERDUE + optional
    hold, sets the next maintenance due date."""
    eq = await db.db.equipment.find_one({"code": code})
    if not eq:
        raise NotFound(f"Equipment {code} not found")
    due = payload.get("next_due")
    if not due or str(due)[:10] <= now_iso()[:10]:
        raise ValidationFailed("maintenance needs a future next_due date")
    update = {"maintenance_status": "OK", "maintenance_due": str(due)[:10],
              "last_maintenance_at": now_iso(), "updated_at": now_iso()}
    if payload.get("back_to_service", True):
        update["status"] = "RELEASED"
        update["on_hold"] = False
    await db.db.equipment.update_one(
        {"code": code},
        {"$set": update,
         "$push": {"logs": {"event": payload.get("kind", "PREVENTIVE_MAINTENANCE"),
                            "notes": payload.get("notes", ""),
                            "actor": actor, "at": now_iso()}}})
    await audit("EQUIPMENT", code, "MAINTENANCE_DONE", actor,
                details={"next_due": str(due)[:10]})
    return await db.db.equipment.find_one({"code": code})


async def hold_equipment(code: str, reason: str, actor: dict) -> dict:
    """Place equipment on hold (breakdown, suspicion of fault): immediately
    unusable by the deterministic gate."""
    res = await db.db.equipment.update_one(
        {"code": code},
        {"$set": {"on_hold": True, "status": "UNDER_MAINTENANCE",
                  "updated_at": now_iso()},
         "$push": {"logs": {"event": "HOLD", "reason": reason,
                            "actor": actor, "at": now_iso()}}})
    if res.matched_count == 0:
        raise NotFound(f"Equipment {code} not found")
    await audit("EQUIPMENT", code, "HOLD", actor, reason=reason)
    return await db.db.equipment.find_one({"code": code})


async def use_equipment(code: str, purpose: str, actor: dict) -> dict:
    """Usage log with the deterministic gate — no use without passing it."""
    eq = await require_equipment_ready(code)
    await db.db.equipment.update_one(
        {"code": code},
        {"$push": {"logs": {"event": "USAGE", "purpose": purpose,
                            "actor": actor, "at": now_iso()}}})
    return {"equipment": eq["code"], "usage_logged": True, "purpose": purpose}


async def log_equipment_event(code: str, event: str, payload: dict, actor: dict):
    res = await db.db.equipment.update_one(
        {"code": code},
        {"$push": {"logs": {"event": event, "payload": payload,
                            "actor": actor, "at": now_iso()}},
         "$set": {"updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise NotFound(f"Equipment {code} not found")


async def equipment_due_report() -> dict:
    """Calibration/maintenance due report for dashboards + agent monitoring."""
    today = now_iso()[:10]
    horizon = await _cal_horizon()
    soon_cut = _shift_date(today, horizon)
    out = {"calibration_due": [], "maintenance_due": [], "expired": []}
    async for e in db.db.equipment.find({"status": {"$ne": "DECOMMISSIONED"}}):
        cal = str(e.get("calibration_due") or "")[:10]
        mnt = str(e.get("maintenance_due") or "")[:10]
        if cal and today < cal <= soon_cut:
            out["calibration_due"].append({"code": e["code"], "due": cal})
        if mnt and today < mnt <= soon_cut:
            out["maintenance_due"].append({"code": e["code"], "due": mnt})
        blocked = equipment_ready(e)[1]
        if blocked:
            out["expired"].append({"code": e["code"], "blockers": blocked})
    return out


def _shift_date(day: str, days: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(day) + timedelta(days=days)).isoformat()[:10]


async def _cal_horizon() -> int:
    from app.core.config import settings
    return int(getattr(settings, "equipment_due_warn_days", 30))


# ------------------------------------------------------------- specifications
TEST_NAMES = ["description", "identification", "assay", "impurities", "dissolution",
              "uniformity", "microbiology", "ph", "moisture"]

async def create_specification(payload: dict) -> dict:
    """Create a specification. Quality-critical materials enter DRAFT and need
    QA approval before they are usable (GAP-11); callers may pass
    status=APPROVED only for non-controlled items via approve_specification."""
    required = ["product_id", "version", "tests"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValidationFailed(f"Missing fields: {missing}")
    for t in payload["tests"]:
        if t.get("name") not in TEST_NAMES:
            raise ValidationFailed(f"Unknown test {t.get('name')} in {TEST_NAMES}")
        if "method" not in t or "acceptance" not in t:
            raise ValidationFailed(f"Test {t['name']} needs method + acceptance criteria")
    existing = await db.db.specifications.find_one(
        {"product_id": payload["product_id"], "version": payload["version"]}
    )
    if existing:
        raise ValidationFailed(f"Specification version {payload['version']} exists")
    # quality-critical (controlled / prescription) materials: draft → QA approval
    prod = await db.db.products.find_one({"sku": payload["product_id"]})
    quality_critical = bool(prod and (prod.get("is_prescription")
                                      or prod.get("is_controlled")))
    initial = "DRAFT" if quality_critical else payload.get("status", "APPROVED")
    doc = {
        "product_id": payload["product_id"],
        "version": payload["version"],
        "tests": payload["tests"],
        "sampling_plan": payload.get("sampling_plan", {"n": 3, "method": "SQRT_N_1"}),
        "effective_from": payload.get("effective_from", now_iso()),
        "status": initial,
        "approved_by": None,
        "created_at": now_iso(),
    }
    await db.db.specifications.insert_one(doc)
    return _clean(doc)


async def approve_specification(product_id: str, version: int, actor: dict,
                                notes: str = "") -> dict:
    """QA authority approves a DRAFT specification (GAP-11)."""
    from app.core.rbac import QA_AUTHORITY_ROLES

    if actor.get("type") == "USER" and "SUPER_ADMIN" not in actor.get("roles", []) \
            and not (set(actor.get("roles", [])) & QA_AUTHORITY_ROLES):
        from app.core.errors import PermissionDenied

        raise PermissionDenied("Specification approval requires QA authority")
    doc = await db.db.specifications.find_one(
        {"product_id": product_id, "version": version})
    if not doc:
        raise NotFound(f"Specification {product_id} v{version} not found")
    if doc["status"] == "APPROVED":
        return _clean(doc)
    if doc["status"] != "DRAFT":
        raise ValidationFailed(f"Specification not DRAFT: {doc['status']}")
    await db.db.specifications.update_one(
        {"product_id": product_id, "version": version},
        {"$set": {"status": "APPROVED", "approved_by": actor,
                  "approval_notes": notes, "approved_at": now_iso()}})
    return _clean(await db.db.specifications.find_one(
        {"product_id": product_id, "version": version}))


async def get_active_specification(product_id: str) -> dict:
    doc = await db.db.specifications.find_one(
        {"product_id": product_id, "status": "APPROVED"},
        sort=[("version", -1)],
    )
    if not doc:
        raise NotFound(f"No approved specification for {product_id}")
    return _clean(doc)


# ------------------------------------------------------------------------- BOM
async def create_bom(payload: dict) -> dict:
    required = ["product_id", "version", "batch_size", "components"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValidationFailed(f"Missing fields: {missing}")
    if not payload["components"]:
        raise ValidationFailed("BOM needs components")
    for c in payload["components"]:
        if not c.get("sku") or not c.get("qty_per_batch"):
            raise ValidationFailed("Component needs sku + qty_per_batch")
    doc = {
        "product_id": payload["product_id"],
        "version": payload["version"],
        "batch_size": float(payload["batch_size"]),
        "uom": payload.get("uom", "BOX"),
        "components": payload["components"],
        "process_steps": payload.get("process_steps", [
            {"step": "DISPENSING", "label": "Dispensing & Weighing"},
            {"step": "GRANULATION", "label": "Granulation"},
            {"step": "COMPRESSION", "label": "Compression"},
            {"step": "COATING", "label": "Coating"},
            {"step": "PACKAGING", "label": "Blister & Carton Packaging"},
        ]),
        "master_formula_ref": payload.get("master_formula_ref"),
        "status": payload.get("status", "APPROVED"),
        "approved_by": payload.get("approved_by"),
        "created_at": now_iso(),
    }
    await db.db.boms.insert_one(doc)
    return _clean(doc)


async def get_active_bom(product_id: str) -> dict:
    doc = await db.db.boms.find_one(
        {"product_id": product_id, "status": "APPROVED"}, sort=[("version", -1)]
    )
    if not doc:
        raise NotFound(f"No approved BOM for {product_id}")
    return _clean(doc)


# ------------------------------------------------------------------ price list
async def upsert_price(payload: dict) -> dict:
    required = ["product_id", "tier", "price"]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValidationFailed(f"Missing fields: {missing}")
    doc = {
        "product_id": payload["product_id"],
        "tier": payload["tier"],
        "currency": payload.get("currency", "INR"),
        "price": float(payload["price"]),
        "min_qty": payload.get("min_qty", 0),
        "valid_from": payload.get("valid_from", now_iso()),
        "valid_to": payload.get("valid_to"),
        "updated_at": now_iso(),
    }
    await db.db.price_lists.update_one(
        {"product_id": payload["product_id"], "tier": payload["tier"]},
        {"$set": doc}, upsert=True,
    )
    return doc


async def get_price(product_id: str, tier: str = "STANDARD") -> Optional[float]:
    doc = await db.db.price_lists.find_one(
        {"product_id": product_id, "tier": tier},
        sort=[("valid_from", -1)],
    )
    return float(doc["price"]) if doc else None
