"""Inventory domain: append-only ledger, balances projection, batches, FEFO
reservations, availability, block/unblock, traceability.

RULE: quantities are NEVER edited. Every change is a movement (append-only).
`inventory_balances` is a fast projection; the ledger is source of truth.
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.database import db, now_iso, utcnow
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.sequences import next_movement_id

MOVEMENT_TYPES = {
    # inflow
    "OPENING_STOCK": 1, "PURCHASE_RECEIPT": 1, "PRODUCTION_RECEIPT": 1,
    "SALES_RETURN_RESTOCK": 1, "TRANSFER_IN": 1, "PRODUCTION_RETURN": 1,
    "POSITIVE_ADJUSTMENT": 1,
    # outflow
    "SALE": -1, "MATERIAL_ISSUE": -1, "TRANSFER_OUT": -1, "RTV": -1,
    "DAMAGE": -1, "EXPIRY_WRITE_OFF": -1, "DISPENSE": -1, "SAMPLE": -1,
    "NEGATIVE_ADJUSTMENT": -1, "DESTRUCTION": -1,
}


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def ensure_batch(product_id: str, batch_id: str, expiry_date: str | None,
                       mfg_date: str | None = None, supplier_id: str | None = None,
                       storage_conditions: str = "AMBIENT") -> dict:
    """Get or create the batch/lot master record."""
    b = await db.db.batches.find_one({"batch_id": batch_id})
    if b:
        return _clean(b)
    doc = {
        "batch_id": batch_id,
        "product_id": product_id,
        "mfg_date": mfg_date,
        "expiry_date": expiry_date,
        "supplier_id": supplier_id,
        "manufacturer": None,
        "qa_status": "EXPECTED" if not expiry_date else "RECEIVED",
        "storage_conditions": storage_conditions,
        "blocked": False,
        "block_reason": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.batches.update_one({"batch_id": batch_id}, {"$set": doc}, upsert=True)
    return doc


async def record_movement(
    movement_type: str,
    product_id: str,
    warehouse_id: str,
    quantity: float,
    uom: str = "BOX",
    batch_id: Optional[str] = None,
    location_id: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    performed_by: Optional[Dict[str, Any]] = None,
    unit_cost: Optional[float] = None,
    note: Optional[str] = None,
) -> dict:
    """THE ledger write. Validated, atomic per (product, warehouse, batch)."""
    if movement_type not in MOVEMENT_TYPES:
        raise ValidationFailed(f"Unknown movement type {movement_type}")
    qty = float(quantity)
    if qty <= 0:
        raise ValidationFailed("Quantity must be positive (direction from type)")
    signed = qty * MOVEMENT_TYPES[movement_type]

    wh = await db.db.warehouses.find_one({"code": warehouse_id})
    if not wh:
        raise NotFound(f"Warehouse {warehouse_id} not found")

    if batch_id:
        await ensure_batch(product_id, batch_id, None)
        batch = await db.db.batches.find_one({"batch_id": batch_id})
        # Blocked batches may only RECEIVE stock (quarantine intake, returns intake,
        # production FG quarantine) or shed it via write-off/destruction/RTV flows.
        INTAKE_WHILE_BLOCKED = {
            "PURCHASE_RECEIPT", "FG_RECEIPT", "RETURN_RECEIPT", "TRANSFER_IN",
            "NEGATIVE_ADJUSTMENT", "DESTRUCTION", "PRODUCTION_RETURN", "RTV",
            "EXPIRY_WRITE_OFF", "SAMPLE",
        }
        if batch.get("blocked") and movement_type not in INTAKE_WHILE_BLOCKED:
            raise ConflictError(
                f"Batch {batch_id} is blocked: {batch.get('block_reason')}"
            )

    movement_id = await next_movement_id()
    doc = {
        "movement_id": movement_id,
        "product_id": product_id,
        "batch_id": batch_id,
        "warehouse_id": warehouse_id,
        "location_id": location_id,
        "movement_type": movement_type,
        "quantity": qty,
        "signed_quantity": signed,
        "uom": uom,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "performed_by": performed_by or {"type": "SYSTEM", "id": "platform"},
        "unit_cost": unit_cost,
        "note": note,
        "created_at": now_iso(),
    }
    # Balance upsert with per-key lock guard (balance projection)
    key = {"product_id": product_id, "warehouse_id": warehouse_id, "batch_id": batch_id}
    await db.db.inventory_balances.update_one(
        key,
        {
            "$inc": {"quantity": signed, "version": 1},
            "$setOnInsert": {"created_at": now_iso()},
            "$set": {"updated_at": now_iso(), "uom": uom},
        },
        upsert=True,
    )
    await db.db.inventory_movements.insert_one(doc)
    await bus.publish("inventory.movement", {
        "movement_id": movement_id, "type": movement_type, "product_id": product_id,
        "batch_id": batch_id, "warehouse_id": warehouse_id, "qty": signed,
    })
    if signed > 0:
        await bus.publish("inventory.available", {
            "product_id": product_id, "warehouse_id": warehouse_id, "batch_id": batch_id,
        })
    return _clean(doc)


async def balances(product_id: Optional[str] = None, warehouse_id: Optional[str] = None,
                   include_blocked: bool = True) -> List[dict]:
    q: Dict[str, Any] = {}
    if product_id:
        q["product_id"] = product_id
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    out = []
    async for r in db.db.inventory_balances.find(q):
        r = _clean(dict(r))
        if r.get("batch_id"):
            b = await db.db.batches.find_one({"batch_id": r["batch_id"]})
            r["batch"] = _clean(b) if b else None
            r["available"] = (r["quantity"] > 0) and not (
                b.get("blocked") if b else False
            )
        else:
            r["available"] = r["quantity"] > 0
        if not include_blocked and not r.get("available"):
            continue
        out.append(r)
    return out


async def on_hand(product_id: str, warehouse_id: Optional[str] = None) -> float:
    q: Dict[str, Any] = {"product_id": product_id}
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    else:
        q["batch_id"] = None
    row = await db.db.inventory_balances.find_one(q)
    return float(row["quantity"]) if row else 0.0


async def availability(product_id: str) -> dict:
    """Aggregate availability: on-hand, reserved, in-transit, available-to-promise."""
    on_hand_total = 0.0
    reserved = 0.0
    q: Dict[str, Any] = {"product_id": product_id}
    async for r in db.db.inventory_balances.find(q):
        b = None
        if r.get("batch_id"):
            b = await db.db.batches.find_one({"batch_id": r["batch_id"]})
        if not b or not b.get("blocked"):
            on_hand_total += float(r["quantity"])
    async for r in db.db.reservations.find({"product_id": product_id,
                                            "status": "ACTIVE"}):
        reserved += float(r["quantity"])
    in_transit = 0.0
    async for po in db.db.purchase_orders.find({
            "status": {"$in": ["APPROVED", "SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"]}}):
        for line in po.get("lines", []):
            if line.get("sku") == product_id:
                received = float(line.get("received_qty") or 0)
                in_transit += max(float(line.get("quantity")) - received, 0)
    atp = max(on_hand_total - reserved, 0)
    return {"product_id": product_id, "on_hand": on_hand_total, "reserved": reserved,
            "in_transit": in_transit, "available_to_promise": atp}


# ------------------------------------------------------------------ FEFO + alloc
async def fefo_batches(product_id: str, warehouse_id: Optional[str] = None,
                       qty_needed: Optional[float] = None) -> List[dict]:
    """First-Expiry-First-Out allocation proposal across batches."""
    q: Dict[str, Any] = {"product_id": product_id, "quantity": {"$gt": 0}}
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    rows = [_clean(dict(r)) async for r in db.db.inventory_balances.find(q)]
    enriched = []
    for r in rows:
        b = await db.db.batches.find_one({"batch_id": r.get("batch_id")})
        if b and b.get("blocked"):
            continue
        enriched.append({
            "batch_id": r.get("batch_id") or "UNBATCHED",
            "warehouse_id": r["warehouse_id"],
            "quantity": float(r["quantity"]),
            "expiry_date": (b or {}).get("expiry_date") or "9999-12-31",
            "qa_status": (b or {}).get("qa_status", "UNKNOWN"),
        })
    enriched.sort(key=lambda x: (x["expiry_date"], x["warehouse_id"]))
    if qty_needed is None:
        return enriched
    plan, remaining = [], qty_needed
    for row in enriched:
        if remaining <= 0:
            break
        take = min(row["quantity"], remaining)
        plan.append({**row, "allocate": take})
        remaining -= take
    if remaining > 0:
        raise ConflictError(
            f"Insufficient FEFO stock for {product_id}: short by {remaining}"
        )
    return plan


async def reserve(product_id: str, quantity: float, reference_type: str,
                  reference_id: str, warehouse_id: Optional[str] = None,
                  actor: Optional[dict] = None) -> dict:
    """Create reservation (no ledger movement until pick/ship)."""
    avail = await availability(product_id)
    if quantity > avail["available_to_promise"]:
        raise ConflictError(
            f"Cannot reserve {quantity} of {product_id}; ATP={avail['available_to_promise']}"
        )
    allocation_plan = await fefo_batches(product_id, warehouse_id, quantity)
    doc = {
        "product_id": product_id,
        "quantity": quantity,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "warehouse_id": warehouse_id,
        "allocation": allocation_plan,
        "status": "ACTIVE",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    res = await db.db.reservations.insert_one(doc)
    doc["reservation_id"] = str(res.inserted_id)
    doc.pop("_id", None)
    await bus.publish("inventory.reserved", {
        "product_id": product_id, "quantity": quantity, "reference": reference_id,
    })
    return doc


async def release_reservation(reference_type: str, reference_id: str,
                              product_id: Optional[str] = None) -> int:
    q: Dict[str, Any] = {"reference_type": reference_type,
                         "reference_id": reference_id, "status": "ACTIVE"}
    if product_id:
        q["product_id"] = product_id
    res = await db.db.reservations.update_many(
        q, {"$set": {"status": "RELEASED", "updated_at": now_iso()}}
    )
    return res.modified_count


async def consume_reservation(reference_type: str, reference_id: str,
                              movement_type: str, warehouse_id: str,
                              actor: Optional[dict] = None) -> List[dict]:
    """Turn an active reservation into ledger movements (on pick/dispense)."""
    q: Dict[str, Any] = {"reference_type": reference_type,
                         "reference_id": reference_id, "status": "ACTIVE"}
    rows = [_clean(dict(r)) async for r in db.db.reservations.find(q)]
    movements = []
    for r in rows:
        for alloc in r.get("allocation", []):
            mv = await record_movement(
                movement_type=movement_type,
                product_id=r["product_id"],
                warehouse_id=alloc["warehouse_id"],
                quantity=alloc["allocate"],
                batch_id=alloc["batch_id"] if alloc["batch_id"] != "UNBATCHED" else None,
                reference_type=reference_type,
                reference_id=reference_id,
                performed_by=actor,
            )
            movements.append(mv)
        await db.db.reservations.update_one(
            {"_id": r["_id"]}, {"$set": {"status": "CONSUMED", "updated_at": now_iso()}}
        )
    return movements


async def block_batch(batch_id: str, reason: str, actor: dict,
                      source: str = "MANUAL") -> dict:
    res = await db.db.batches.update_one(
        {"batch_id": batch_id},
        {"$set": {"blocked": True, "block_reason": reason, "blocked_by": actor,
                  "block_source": source, "updated_at": now_iso()}},
    )
    if res.matched_count == 0:
        raise NotFound(f"Batch {batch_id} not found")
    # freeze active reservations for this batch
    await db.db.reservations.update_many(
        {"allocation.batch_id": batch_id, "status": "ACTIVE"},
        {"$set": {"status": "FROZEN", "updated_at": now_iso()}},
    )
    await bus.publish("inventory.blocked", {"batch_id": batch_id, "reason": reason,
                                            "source": source})
    return await _get_batch(batch_id)


async def unblock_batch(batch_id: str, actor: dict, reason: str) -> dict:
    await db.db.batches.update_one(
        {"batch_id": batch_id},
        {"$set": {"blocked": False, "block_reason": None, "updated_at": now_iso()}},
    )
    from app.core.audit import audit

    await audit("BATCH", batch_id, "UNBLOCKED", actor, reason=reason)
    return await _get_batch(batch_id)


async def _get_batch(batch_id: str) -> dict:
    b = await db.db.batches.find_one({"batch_id": batch_id})
    if not b:
        raise NotFound(f"Batch {batch_id} not found")
    return _clean(b)


async def batches_near_expiry(days: int = 90) -> List[dict]:
    from datetime import timedelta

    cutoff = (utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
    q: Dict[str, Any] = {"expiry_date": {"$lte": cutoff, "$ne": None}}
    return [_clean(dict(r)) async for r in db.db.batches.find(q).sort("expiry_date", 1)]


async def cycle_count(warehouse_id: str, product_id: str, batch_id: Optional[str],
                      counted_qty: float, actor: dict) -> dict:
    """Cycle count: compute variance and post adjustment movements."""
    key: Dict[str, Any] = {"product_id": product_id, "warehouse_id": warehouse_id}
    if batch_id:
        key["batch_id"] = batch_id
    row = await db.db.inventory_balances.find_one(key)
    system_qty = float(row["quantity"]) if row else 0.0
    variance = float(counted_qty) - system_qty
    doc = {
        "warehouse_id": warehouse_id, "product_id": product_id, "batch_id": batch_id,
        "system_qty": system_qty, "counted_qty": float(counted_qty),
        "variance": variance, "status": "COMPLETED", "counted_by": actor,
        "created_at": now_iso(),
    }
    res = await db.db.cycle_counts.insert_one(doc)
    doc["count_id"] = str(res.inserted_id)
    if abs(variance) > 0:
        mv_type = "POSITIVE_ADJUSTMENT" if variance > 0 else "NEGATIVE_ADJUSTMENT"
        await record_movement(
            movement_type=mv_type, product_id=product_id, warehouse_id=warehouse_id,
            quantity=abs(variance), batch_id=batch_id,
            reference_type="CYCLE_COUNT", reference_id=doc["count_id"],
            performed_by=actor, note=f"Cycle count variance {variance}",
        )
    from app.core.audit import audit

    await audit("CYCLE_COUNT", doc["count_id"], "COMPLETED", actor,
                details={"variance": variance})
    return _clean(doc)


async def transfer(from_wh: str, to_wh: str, product_id: str, quantity: float,
                   batch_id: Optional[str], actor: dict, reference: str = "TRANSFER") -> dict:
    out = await record_movement("TRANSFER_OUT", product_id, from_wh, quantity,
                                batch_id=batch_id, reference_type=reference,
                                reference_id=from_wh, performed_by=actor)
    inn = await record_movement("TRANSFER_IN", product_id, to_wh, quantity,
                                batch_id=batch_id, reference_type=reference,
                                reference_id=to_wh, performed_by=actor)
    return {"out": out, "in": inn}


async def movements(product_id: Optional[str] = None, batch_id: Optional[str] = None,
                    warehouse_id: Optional[str] = None, limit: int = 200) -> List[dict]:
    q: Dict[str, Any] = {}
    if product_id:
        q["product_id"] = product_id
    if batch_id:
        q["batch_id"] = batch_id
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    cur = db.db.inventory_movements.find(q).sort("created_at", -1).limit(limit)
    return [_clean(dict(r)) async for r in cur]


async def traceability(batch_id: str) -> dict:
    """Full forward + reverse traceability graph for a batch (recall-ready)."""
    batch = await _get_batch(batch_id)
    product_id = batch["product_id"]

    # Reverse: where did it come from?
    sources = [_clean(dict(m)) async for m in db.db.inventory_movements.find(
        {"batch_id": batch_id, "movement_type": {"$in": ["PURCHASE_RECEIPT",
                                                         "PRODUCTION_RECEIPT"]}}
    )]
    grn_ids = [m["reference_id"] for m in sources if m.get("reference_type") == "GRN"]
    grns = [_clean(dict(g)) async for g in db.db.grns.find({"grn_id": {"$in": grn_ids}})]
    vendor_ids = {g.get("vendor_id") for g in grns if g.get("vendor_id")}
    vendors = [_clean(dict(v)) async for v in db.db.vendors.find(
        {"vendor_id": {"$in": list(vendor_ids)}})] if vendor_ids else []

    production_orders = []
    for m in sources:
        if m.get("reference_type") == "PRODUCTION_ORDER":
            po = await db.db.production_orders.find_one({"order_id": m["reference_id"]})
            if po:
                production_orders.append(_clean(po))

    # Forward: where did it go?
    movements_out = [_clean(dict(m)) async for m in db.db.inventory_movements.find(
        {"batch_id": batch_id, "movement_type": {"$in": ["SALE", "DISPENSE",
                                                         "TRANSFER_OUT", "MATERIAL_ISSUE"]}}
    )]
    so_ids = [m["reference_id"] for m in movements_out
              if m.get("reference_type") in ("SALES_ORDER", "POS_SALE")]
    sales_orders = [_clean(dict(s)) async for s in db.db.sales_orders.find(
        {"order_id": {"$in": so_ids}})]
    shipments = [_clean(dict(s)) async for s in db.db.shipments.find(
        {"sales_order_id": {"$in": so_ids}})]
    customers = []
    for so in sales_orders:
        if so.get("customer_id"):
            c = await db.db.customers.find_one({"customer_id": so["customer_id"]})
            if c:
                customers.append({"customer_id": c["customer_id"], "name": c["name"]})

    balances_now = await balances(product_id=product_id)
    return {
        "batch": batch,
        "product": await _product(product_id),
        "backward": {
            "sources": sources,
            "grns": grns,
            "vendors": vendors,
            "production_orders": production_orders,
        },
        "forward": {
            "movements_out": movements_out,
            "sales_orders": sales_orders,
            "shipments": shipments,
            "customers": customers,
        },
        "current_balances": balances_now,
    }


async def _product(product_id: str) -> Optional[dict]:
    doc = await db.db.products.find_one({"sku": product_id})
    return _clean(doc) if doc else None
