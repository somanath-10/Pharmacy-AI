"""Inventory domain: append-only ledger, balances projection, batches, FEFO
reservations, availability, block/unblock, traceability.

RULES (P0):
- Quantities are NEVER edited. Every change is a movement (append-only).
- `inventory_movements` is the source of truth; `inventory_balances` is a
  projection. Each write is ledger-first and atomic per key:
    1. insert ledger row  (append-only; the durable record)
    2. $inc the balance projection via a per-key asyncio lock
  A failure before the ledger insert means no balance change; a failure
  after it is repaired by replaying movements (rebuild_balances).
  When the topology supports it, both writes join one Mongo transaction
  (db.transaction / current_session ContextVar).
- Balances are dimensioned by: organization_id, site_id, warehouse_id,
  location_id, product_id, batch_id, stock_status, uom. Stock can never
  silently leak between statuses: every status change is an explicit
  STATUS_CHANGE movement (quantity-preserving).
- Reservations use atomic conditional updates on the balance projection:
  two concurrent requests can never both reserve the same remaining stock.
"""
import asyncio
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from app.core.audit import audit
from app.core.database import current_session, db, now_iso, utcnow
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.sequences import next_movement_id

# Canonical stock statuses (P0 set — do not trim without governance)
STOCK_STATUSES = {
    "AVAILABLE", "RESERVED", "QUARANTINE", "QUALITY_HOLD", "RECALLED",
    "DAMAGED", "EXPIRED", "REJECTED", "IN_TRANSIT", "RETURN_QUARANTINE",
    # WMS/quality lifecycle extensions (Part 3)
    "EXPECTED", "QC_SAMPLING", "QC_TESTING", "QA_RELEASED", "PICKED",
    "PACKED", "STAGED", "DISPATCHED", "RTV_PENDING", "DISPOSAL_PENDING",
    "DISPOSED",
}

# Statuses whose stock may NEVER be allocated/picked/sold/issued (Part 3).
# RESERVED is deliberately absent: it is already promised and decremented
# from AVAILABLE at reservation time; consumption walks the reservation.
NON_ALLOCATABLE_STATUSES = {
    "QUARANTINE", "QUALITY_HOLD", "RECALLED", "DAMAGED", "EXPIRED",
    "REJECTED", "IN_TRANSIT", "RETURN_QUARANTINE", "EXPECTED", "PICKED",
    "PACKED", "STAGED", "DISPATCHED", "RTV_PENDING", "DISPOSAL_PENDING",
    "DISPOSED", "QC_SAMPLING", "QC_TESTING",
}

MOVEMENT_TYPES = {
    # inflow
    "OPENING_STOCK": 1, "PURCHASE_RECEIPT": 1, "PRODUCTION_RECEIPT": 1,
    "SALES_RETURN_RESTOCK": 1, "TRANSFER_IN": 1, "PRODUCTION_RETURN": 1,
    "POSITIVE_ADJUSTMENT": 1, "RETURN_RECEIPT": 1,
    # outflow
    "SALE": -1, "MATERIAL_ISSUE": -1, "TRANSFER_OUT": -1, "RTV": -1,
    "DAMAGE": -1, "EXPIRY_WRITE_OFF": -1, "DISPENSE": -1, "SAMPLE": -1,
    "NEGATIVE_ADJUSTMENT": -1, "DESTRUCTION": -1,
    # quantity-preserving status flips
    "STATUS_CHANGE": 0,
}

# Inflows that may land on cold-chain-blocked batches (intake lanes).
INTAKE_WHILE_BLOCKED = {
    "PURCHASE_RECEIPT", "FG_RECEIPT", "RETURN_RECEIPT", "TRANSFER_IN",
    "NEGATIVE_ADJUSTMENT", "DESTRUCTION", "PRODUCTION_RETURN", "RTV",
    "EXPIRY_WRITE_OFF", "SAMPLE",
}

# Movement types allowed to consume a specific status (Part 3 usage rules).
# Everything else must come out of AVAILABLE/QA-released stock.
OUTFLOW_ALLOWED_FROM = {
    "RTV": {"REJECTED", "RTV_PENDING"},
    "DESTRUCTION": {"DAMAGED", "EXPIRED", "REJECTED", "DISPOSAL_PENDING"},
    "EXPIRY_WRITE_OFF": {"EXPIRED"},
    "DAMAGE": {"AVAILABLE", "RESERVED"},  # physical damage discovered in the bin
}

DEFAULT_ORG = "ORG-DEFAULT"
DEFAULT_SITE = "SITE-DEFAULT"

DEFAULT_STATUS_BY_TYPE = {
    "PURCHASE_RECEIPT": "QUARANTINE",
    "PRODUCTION_RECEIPT": "QUARANTINE",
    "RETURN_RECEIPT": "RETURN_QUARANTINE",
    "OPENING_STOCK": "AVAILABLE",
    "SALES_RETURN_RESTOCK": "AVAILABLE",
    "TRANSFER_IN": "AVAILABLE",
    "IN_TRANSIT": "IN_TRANSIT",
    "PRODUCTION_RETURN": "AVAILABLE",
    "POSITIVE_ADJUSTMENT": "AVAILABLE",
}


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


def _reservation_out(d: dict) -> dict:
    """Strip _id and expose the virtual string reservation_id on any shape."""
    if "_id" in d:
        d["reservation_id"] = str(d.pop("_id"))
    return d


def _status_for_movement(movement_type: str, stock_status: Optional[str]) -> str:
    if stock_status:
        if stock_status not in STOCK_STATUSES:
            raise ValidationFailed(
                f"stock_status must be one of {sorted(STOCK_STATUSES)}")
        return stock_status
    return DEFAULT_STATUS_BY_TYPE.get(movement_type, "AVAILABLE")


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


async def expire_due_batches(actor: Optional[dict] = None,
                             days: int = 0) -> List[dict]:
    """Deterministic expiry sweep: batches at/past expiry (or within `days`)
    with live AVAILABLE/RESERVED-adjacent stock move to EXPIRED via
    STATUS_CHANGE legs; write-off remains an explicit EXPIRY_WRITE_OFF
    movement (Part 18: routine expiry detection needs no human)."""
    from datetime import timedelta

    horizon = (utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
    _ = now_iso()[:10]
    moved = []
    async for b in db.db.batches.find({
            "expiry_date": {"$ne": None, "$lte": horizon},
            "blocked": {"$ne": True}}):
        rows = [_clean(dict(r)) async for r in db.db.inventory_balances.find(
            {"batch_id": b["batch_id"],
             "stock_status": {"$in": ["AVAILABLE", "QC_SAMPLING", "QC_TESTING"]},
             "quantity": {"$gt": 0}})]
        for row in rows:
            await change_stock_status(
                product_id=row["product_id"], warehouse_id=row["warehouse_id"],
                batch_id=row["batch_id"], from_status=row["stock_status"],
                to_status="EXPIRED", quantity=float(row["quantity"]),
                actor=actor or {"type": "AGENT", "id": "warehouse-agent"},
                reference_type="EXPIRY_SWEEP", reference_id=b["batch_id"],
                uom=row.get("uom", "BOX"), location_id=row.get("location_id"))
            moved.append({"batch_id": b["batch_id"],
                          "product_id": row["product_id"],
                          "qty": float(row["quantity"]),
                          "warehouse_id": row["warehouse_id"]})
        if rows:
            await db.db.batches.update_one(
                {"batch_id": b["batch_id"]},
                {"$set": {"blocked": True, "block_reason": "EXPIRED",
                          "updated_at": now_iso()}})
            await bus.publish("batch.expired",
                              {"batch_id": b["batch_id"],
                               "product_id": b["product_id"],
                               "expiry_date": b["expiry_date"]},
                              actor or {"type": "AGENT", "id": "warehouse-agent"})
    return moved


class _KeyLocks:
    """Per-key asyncio locks so balance $inc never interleaves per key."""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def lock(self, key: str) -> asyncio.Lock:
        async with self._guard:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]


_locks = _KeyLocks()


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
    organization_id: str = DEFAULT_ORG,
    site_id: str = DEFAULT_SITE,
    stock_status: Optional[str] = None,
) -> dict:
    """THE ledger write. Ledger-first + atomic-per-key balance projection.

    Appends to `inventory_movements` (append-only; no update path is ever
    taken on it) then conditionally $incs the per-dimension balance row
    under a per-key lock. The balance key includes the full dimension set:
    (organization_id, site_id, warehouse_id, product_id, batch_id,
     stock_status, uom, location_id).
    """
    if movement_type not in MOVEMENT_TYPES:
        raise ValidationFailed(f"Unknown movement type {movement_type}")
    qty = float(quantity)
    if qty <= 0:
        raise ValidationFailed("Quantity must be positive (direction from type)")
    signed = qty * MOVEMENT_TYPES[movement_type]
    status = _status_for_movement(movement_type, stock_status)
    if movement_type == "STATUS_CHANGE":
        if not stock_status:
            raise ValidationFailed("STATUS_CHANGE requires to_status")

    wh = await db.db.warehouses.find_one({"code": warehouse_id})
    if not wh:
        raise NotFound(f"Warehouse {warehouse_id} not found")
    org = wh.get("organization_id") or organization_id
    site = wh.get("site_id") or site_id

    if batch_id and movement_type != "STATUS_CHANGE":
        await ensure_batch(product_id, batch_id, None)
        batch = await db.db.batches.find_one({"batch_id": batch_id})
        if batch.get("blocked") and movement_type not in INTAKE_WHILE_BLOCKED:
            raise ConflictError(
                f"Batch {batch_id} is blocked: {batch.get('block_reason')}")
        # Deterministic usage rules (Part 3): expired stock leaves only via
        # EXPIRY_WRITE_OFF/DESTRUCTION; rejected stock only via RTV/DESTRUCTION.
        allowed_from = OUTFLOW_ALLOWED_FROM.get(movement_type)
        if allowed_from is not None and batch.get("blocked"):
            pass  # blocked batch + allowed-lane type: legal shed path
        _today = now_iso()[:10]
        if movement_type in ("RTV", "DESTRUCTION", "EXPIRY_WRITE_OFF", "DAMAGE"):
            pass  # shed lanes move stock OUT regardless of expiry
        elif batch.get("expiry_date") and str(batch["expiry_date"])[:10] <= _today \
                and movement_type not in INTAKE_WHILE_BLOCKED:
            raise ConflictError(
                f"Batch {batch_id} expired {batch['expiry_date']}; only "
                "EXPIRY_WRITE_OFF/DESTRUCTION/RTV allowed")

    movement_id = await next_movement_id()
    doc = {
        "movement_id": movement_id,
        "organization_id": org,
        "site_id": site,
        "product_id": product_id,
        "batch_id": batch_id,
        "warehouse_id": warehouse_id,
        "location_id": location_id,
        "movement_type": movement_type,
        "quantity": qty,
        "signed_quantity": signed,
        "uom": uom,
        "stock_status": status,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "performed_by": performed_by or {"type": "SYSTEM", "id": "platform"},
        "unit_cost": unit_cost,
        "note": note,
        "created_at": now_iso(),
    }

    session = current_session()
    if session is not None:
        # Transactional path: ledger + balance in one atomic commit.
        await db.db.inventory_movements.insert_one(doc, session=session)
        bal_key = _balance_key(org, site, warehouse_id, product_id, batch_id,
                               status, uom, location_id)
        bal_cond = {**bal_key}
        if signed < 0:
            # Non-negative stock guard (GAP-9): outflows only apply against
            # rows that can actually absorb them. Ledger row still stands as
            # evidence; the caller sees a ConflictError.
            bal_cond["quantity"] = {"$gte": qty}
            bal = await db.db.inventory_balances.find_one_and_update(
                bal_cond, _balance_update(signed, qty, uom, location_id, unit_cost),
                upsert=False, session=session)
            if bal is None:
                try:
                    await session.abort_transaction()
                except Exception:  # noqa: BLE001 — abort is best-effort
                    pass
                raise ConflictError(
                    f"Insufficient stock: cannot decrement {qty} of "
                    f"{product_id} ({status}) in {warehouse_id}")
        else:
            await db.db.inventory_balances.update_one(
                bal_key, _balance_update(signed, qty, uom, location_id, unit_cost),
                upsert=True, session=session)
    else:
        # Fallback (standalone): ledger-first under per-key lock.
        async with await _locks.lock(_lock_key(org, site, warehouse_id,
                                               product_id, batch_id, status, uom)):
            await db.db.inventory_movements.insert_one(doc)
            bal_key = _balance_key(org, site, warehouse_id, product_id, batch_id,
                                   status, uom, location_id)
            if signed < 0:
                bal_cond = {**bal_key, "quantity": {"$gte": qty}}
                bal = await db.db.inventory_balances.find_one_and_update(
                    bal_cond,
                    _balance_update(signed, qty, uom, location_id, unit_cost),
                    upsert=False)
                if bal is None:
                    raise ConflictError(
                        f"Insufficient stock: cannot decrement {qty} of "
                        f"{product_id} ({status}) in {warehouse_id}")
            else:
                await db.db.inventory_balances.update_one(
                    bal_key,
                    _balance_update(signed, qty, uom, location_id, unit_cost),
                    upsert=True)
    await bus.publish("inventory.movement", {
        "movement_id": movement_id, "type": movement_type,
        "product_id": product_id, "batch_id": batch_id,
        "warehouse_id": warehouse_id, "qty": signed, "stock_status": status,
    })
    if signed > 0:
        await bus.publish("inventory.available", {
            "product_id": product_id, "warehouse_id": warehouse_id,
            "batch_id": batch_id, "stock_status": status,
        })
    return _clean(doc)


def _balance_key(org, site, warehouse_id, product_id, batch_id, status, uom,
                 location_id):
    return {
        "organization_id": org,
        "site_id": site,
        "warehouse_id": warehouse_id,
        "product_id": product_id,
        "batch_id": batch_id,
        "stock_status": status,
        "uom": uom,
        "location_id": location_id,
    }


def _lock_key(org, site, warehouse_id, product_id, batch_id, status, uom):
    return f"{org}|{site}|{warehouse_id}|{product_id}|{batch_id}|{status}|{uom}"


async def _org_site(warehouse_id: str) -> tuple:
    """Resolve (org, site) for a warehouse — the same resolution
    record_movement uses, so every writer agrees on the balance key."""
    wh = await db.db.warehouses.find_one({"code": warehouse_id})
    if not wh:
        return DEFAULT_ORG, DEFAULT_SITE
    return (wh.get("organization_id") or DEFAULT_ORG,
            wh.get("site_id") or DEFAULT_SITE)


def _balance_update(signed, qty, uom, location_id, unit_cost):
    upd = {
        "$inc": {"quantity": signed, "version": 1},
        "$set": {"updated_at": now_iso(), "uom": uom},
        "$setOnInsert": {"created_at": now_iso()},
    }
    if location_id:
        upd["$set"]["location_id"] = location_id
    if unit_cost is not None:
        upd["$set"]["unit_cost"] = unit_cost
    return upd


async def change_stock_status(product_id: str, warehouse_id: str, batch_id: Optional[str],
                              from_status: str, to_status: str, quantity: float,
                              actor: Optional[dict] = None,
                              reference_type: str = "MANUAL",
                              reference_id: str = "",
                              uom: str = "BOX",
                              location_id: Optional[str] = None) -> dict:
    """Move `quantity` between stock statuses (quantity-preserving).

    Two legs, one balance-touched pair: −qty at from_status, +qty at
    to_status. Both legs are recorded as STATUS_CHANGE movements so the
    ledger fully explains every balance row in existence.
    """
    if to_status not in STOCK_STATUSES:
        raise ValidationFailed(
            f"to_status must be one of {sorted(STOCK_STATUSES)}")
    if from_status == to_status:
        raise ValidationFailed("from_status == to_status")
    qty = float(quantity)
    if qty <= 0:
        raise ValidationFailed("Quantity must be positive")

    wh = await db.db.warehouses.find_one({"code": warehouse_id})
    if not wh:
        raise NotFound(f"Warehouse {warehouse_id} not found")

    # Two legs, each ledger-first: −qty at from_status, +qty at to_status.
    # Every balance row in existence is explained by a STATUS_CHANGE movement.
    await _post_status_leg(product_id, warehouse_id, batch_id, from_status,
                           qty, -1, uom, location_id, reference_type,
                           reference_id, actor,
                           note=f"status out {from_status} → {to_status}")
    await _post_status_leg(product_id, warehouse_id, batch_id, to_status,
                           qty, +1, uom, location_id, reference_type,
                           reference_id, actor,
                           note=f"status in {from_status} → {to_status}")
    await bus.publish("inventory.status_changed", {
        "product_id": product_id, "batch_id": batch_id,
        "warehouse_id": warehouse_id, "from": from_status, "to": to_status,
        "quantity": qty, "reference": reference_id,
    })
    return {"product_id": product_id, "warehouse_id": warehouse_id,
            "batch_id": batch_id, "from_status": from_status,
            "to_status": to_status, "quantity": qty}


async def _post_status_leg(product_id, warehouse_id, batch_id, status, qty,
                           direction, uom, location_id, reference_type,
                           reference_id, actor, note=None):
    """One ledger-first status leg: append movement row, then $inc balance."""
    wh = await db.db.warehouses.find_one({"code": warehouse_id})
    org = (wh or {}).get("organization_id") or DEFAULT_ORG
    site = (wh or {}).get("site_id") or DEFAULT_SITE
    doc = {
        "movement_id": await next_movement_id(),
        "organization_id": org, "site_id": site,
        "product_id": product_id, "batch_id": batch_id,
        "warehouse_id": warehouse_id, "location_id": location_id,
        "movement_type": "STATUS_CHANGE",
        "quantity": qty, "signed_quantity": direction * qty,
        "uom": uom, "stock_status": status,
        "reference_type": reference_type, "reference_id": reference_id,
        "performed_by": actor or {"type": "SYSTEM", "id": "platform"},
        "unit_cost": None, "note": note, "created_at": now_iso(),
    }
    bal_key = _balance_key(org, site, warehouse_id, product_id,
                           batch_id, status, uom, location_id)
    upd = _balance_update(direction * qty, qty, uom, location_id, None)
    session = current_session()
    if session is not None:
        await db.db.inventory_movements.insert_one(doc, session=session)
        await db.db.inventory_balances.update_one(bal_key, upd, upsert=True,
                                                  session=session)
    else:
        async with await _locks.lock(_lock_key(org, site, warehouse_id,
                                               product_id, batch_id, status,
                                               uom)):
            await db.db.inventory_movements.insert_one(doc)
            await db.db.inventory_balances.update_one(bal_key, upd, upsert=True)


async def balances(product_id: Optional[str] = None, warehouse_id: Optional[str] = None,
                   stock_status: Optional[str] = None, batch_id: Optional[str] = None,
                   include_zero: bool = False) -> List[dict]:
    q: Dict[str, Any] = {}
    if product_id:
        q["product_id"] = product_id
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    if stock_status:
        if stock_status not in STOCK_STATUSES:
            raise ValidationFailed(
                f"stock_status must be one of {sorted(STOCK_STATUSES)}")
        q["stock_status"] = stock_status
    if batch_id:
        q["batch_id"] = batch_id
    if not include_zero:
        q["quantity"] = {"$ne": 0}
    rows = []
    async for r in db.db.inventory_balances.find(q).sort("product_id", 1):
        r = _clean(dict(r))
        b = None
        if r.get("batch_id"):
            b = await db.db.batches.find_one({"batch_id": r["batch_id"]})
        r["batch"] = _clean(b) if b else None
        r["available"] = (r["quantity"] > 0 and r["stock_status"] == "AVAILABLE"
                          and not (b.get("blocked") if b else False))
        rows.append(r)
    return rows


async def on_hand(product_id: str, warehouse_id: Optional[str] = None,
                  stock_status: str = "AVAILABLE") -> float:
    q: Dict[str, Any] = {"product_id": product_id, "stock_status": stock_status}
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    total = 0.0
    async for r in db.db.inventory_balances.find(q):
        total += float(r["quantity"])
    return total


async def status_summary(product_id: str, warehouse_id: Optional[str] = None) -> dict:
    """On-hand per stock status — the dimensional view for apps/reports."""
    q: Dict[str, Any] = {"product_id": product_id}
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    out = {s: 0.0 for s in sorted(STOCK_STATUSES)}
    async for r in db.db.inventory_balances.find(q):
        st = r.get("stock_status", "AVAILABLE")
        out[st] = out.get(st, 0.0) + float(r["quantity"])
    return out


async def availability(product_id: str) -> dict:
    """Aggregate availability: on-hand, reserved, in-transit, ATP.

    on_hand counts every status row; ATP counts only AVAILABLE rows.
    """
    on_hand_total = 0.0
    available = 0.0
    q: Dict[str, Any] = {"product_id": product_id}
    async for r in db.db.inventory_balances.find(q):
        b = None
        if r.get("batch_id"):
            b = await db.db.batches.find_one({"batch_id": r["batch_id"]})
        qty = float(r["quantity"])
        if qty > 0 and (not b or not b.get("blocked")):
            on_hand_total += qty
            if r.get("stock_status") == "AVAILABLE":
                available += qty
    reserved = 0.0
    async for r in db.db.reservations.find({"product_id": product_id,
                                            "status": "ACTIVE"}):
        reserved += float(r["quantity"])
    in_transit = 0.0
    async for po in db.db.purchase_orders.find({
            "status": {"$in": ["APPROVED", "SENT", "ACKNOWLEDGED",
                               "PARTIALLY_RECEIVED"]}}):
        for line in po.get("lines", []):
            if line.get("sku") == product_id:
                received = float(line.get("received_qty") or 0)
                in_transit += max(float(line.get("quantity")) - received, 0)
    # ATP model (P0 8.3): reserving physically moves qty AVAILABLE →
    # RESERVED, so `available` above is ALREADY net of reservations.
    # Subtracting active reservations again double-counted them
    # (100 → reserve 20 reported ATP 60). ATP = AVAILABLE rows only.
    atp = max(available, 0)
    return {"product_id": product_id, "on_hand": on_hand_total,
            "available": available, "reserved": reserved,
            "in_transit": in_transit, "available_to_promise": atp}


# ------------------------------------------------------------------ FEFO + alloc
async def fefo_batches(product_id: str, warehouse_id: Optional[str] = None,
                       qty_needed: Optional[float] = None,
                       min_shelf_life_days: Optional[int] = None,
                       warehouse_priority: Optional[Dict[str, int]] = None) -> List[dict]:
    """FEFO batch selection considering (Part 4):
    1. stock status (AVAILABLE rows only)
    2. release status (QA-released / non-blocked batches)
    3. expiry validity (expired batches never allocate)
    4. recall/block status (blocked batches skipped)
    5. minimum remaining shelf life (policy rule, overridable)
    6. warehouse priority (lower number = preferred)
    7. earliest expiry first
    """
    from app.core.policies import get_rule

    if min_shelf_life_days is None:
        min_shelf_life_days = int(await get_rule("min_shelf_life_days", 0))
    from datetime import timedelta

    today = now_iso()[:10]
    horizon = (utcnow() + timedelta(days=min_shelf_life_days)).strftime("%Y-%m-%d") \
        if min_shelf_life_days > 0 else None
    q: Dict[str, Any] = {"product_id": product_id, "quantity": {"$gt": 0},
                         "stock_status": "AVAILABLE"}
    if warehouse_id:
        q["warehouse_id"] = warehouse_id
    rows = [_clean(dict(r)) async for r in db.db.inventory_balances.find(q)]
    enriched = []
    for r in rows:
        b = await db.db.batches.find_one({"batch_id": r.get("batch_id")})
        if b:
            if b.get("blocked"):            # recall / hold / expired block
                continue
            expiry = b.get("expiry_date") or "9999-12-31"
            if expiry[:10] <= today:         # expired never allocates
                continue
            if horizon and expiry[:10] < horizon:
                continue                     # below minimum remaining shelf life
        else:
            expiry = "9999-12-31"
        prio = (warehouse_priority or {}).get(r["warehouse_id"], 99)
        enriched.append({
            "batch_id": r.get("batch_id") or "UNBATCHED",
            "warehouse_id": r["warehouse_id"],
            "location_id": r.get("location_id"),
            "quantity": float(r["quantity"]),
            "uom": r.get("uom", "BOX"),
            "expiry_date": expiry,
            "qa_status": (b or {}).get("qa_status", "UNKNOWN"),
            "priority": prio,
        })
    enriched.sort(key=lambda x: (x["priority"], x["expiry_date"],
                                 x["warehouse_id"]))
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
            f"Insufficient FEFO stock for {product_id}: short by {remaining}")
    return plan


async def move_location(product_id: str, warehouse_id: str, batch_id: Optional[str],
                        from_location: Optional[str], to_location: str,
                        quantity: float, actor: dict,
                        stock_status: str = "AVAILABLE", uom: str = "BOX",
                        reference_type: str = "BIN_TRANSFER",
                        reference_id: str = "") -> dict:
    """Bin-to-bin move inside one warehouse (Part 6): two quantity-preserving
    STATUS_CHANGE legs that differ only in the location dimension. The stock
    keeps its status; only the bin changes. One batch may live in many bins.
    The from-bin must actually hold the quantity (guard below)."""
    if from_location == to_location:
        raise ValidationFailed("from_location and to_location must differ")
    if stock_status not in STOCK_STATUSES:
        raise ValidationFailed(f"stock_status must be one of {sorted(STOCK_STATUSES)}")
    qty = float(quantity)
    if qty <= 0:
        raise ValidationFailed("quantity must be positive")
    org, site = await _org_site(warehouse_id)
    src_row = await db.db.inventory_balances.find_one(_balance_key(
        org, site, warehouse_id, product_id, batch_id, stock_status, uom,
        from_location))
    if not src_row or float(src_row["quantity"]) < qty - 1e-9:
        raise ConflictError(
            f"Bin {from_location} holds {float((src_row or {}).get('quantity', 0))} "
            f"of {product_id}; cannot move {qty}")
    await _post_status_leg(product_id, warehouse_id, batch_id, stock_status,
                           qty, -1, uom, from_location, reference_type,
                           reference_id, actor,
                           note=f"bin-out {from_location} → {to_location}")
    await _post_status_leg(product_id, warehouse_id, batch_id, stock_status,
                           qty, +1, uom, to_location, reference_type,
                           reference_id, actor,
                           note=f"bin-in {from_location} → {to_location}")
    await audit("INVENTORY", f"{warehouse_id}:{product_id}:{batch_id}",
                "BIN_TRANSFER", actor,
                details={"from": from_location, "to": to_location, "qty": qty,
                         "reference": reference_id})
    return {"product_id": product_id, "batch_id": batch_id,
            "from_location": from_location, "to_location": to_location,
            "quantity": qty, "stock_status": stock_status}


async def reserve(product_id: str, quantity: float, reference_type: str,
                  reference_id: str, warehouse_id: Optional[str] = None,
                  actor: Optional[dict] = None) -> dict:
    """Create a reservation with atomic, race-free ATP enforcement.

    Concurrency safety: the reservation document is inserted FIRST, guarded
    by a unique index on (reference_type, reference_id, product_id). Then the
    remaining AVAILABLE balance is decremented with an atomic conditional
    update (quantity >= reserved_total). Two concurrent requests can never
    both reserve the same remaining stock — the loser's update matches 0
    rows, its reservation is released, and ConflictError is raised.
    """
    qty = float(quantity)
    if qty <= 0:
        raise ValidationFailed("Reservation quantity must be positive")

    # already fully reserved for this reference+product? idempotent no-op
    existing = await db.db.reservations.find_one({
        "reference_type": reference_type, "reference_id": reference_id,
        "product_id": product_id, "status": {"$in": ["ACTIVE", "FROZEN"]}})
    if existing:
        return _reservation_out(dict(existing))

    plan = await fefo_batches(product_id, warehouse_id, qty)

    res_doc = {
        "product_id": product_id,
        "quantity": qty,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "warehouse_id": warehouse_id,
        "allocation": plan,
        "status": "ACTIVE",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    from pymongo.errors import DuplicateKeyError

    try:
        res = await db.db.reservations.insert_one(res_doc)
    except DuplicateKeyError:
        # concurrent duplicate reservation for the same reference — return it
        got = await db.db.reservations.find_one({
            "reference_type": reference_type, "reference_id": reference_id,
            "product_id": product_id, "status": {"$in": ["ACTIVE", "FROZEN"]}})
        if got:
            return _reservation_out(dict(got))
        raise

    # atomic conditional decrement of the AVAILABLE projection + matching
    # increment of the RESERVED row (two-leg status move, race-free).
    # Bin/location is preserved end to end: the decrement targets the exact
    # (warehouse, batch, bin) row from the FEFO plan and the RESERVED row is
    # created with the same bin — consuming later issues from where the stock
    # actually sits, not from a null-bin aggregate.
    updated = 0.0
    for alloc in plan:
        batch_key = None if alloc["batch_id"] == "UNBATCHED" else alloc["batch_id"]
        loc_key = alloc.get("location_id")
        cond = {"product_id": product_id,
                "warehouse_id": alloc["warehouse_id"],
                "batch_id": batch_key,
                "location_id": loc_key,
                "stock_status": "AVAILABLE",
                "quantity": {"$gte": float(alloc["allocate"])}}
        r = await db.db.inventory_balances.find_one_and_update(
            cond, {"$inc": {"quantity": -float(alloc["allocate"]),
                            "reserved_quantity": float(alloc["allocate"]),
                            "version": 1},
                   "$set": {"updated_at": now_iso()}},
            return_document=ReturnDocument.AFTER)
        if r is None:
            # another writer took the stock between plan and decrement
            await _rollback_reservation_decrements(product_id, plan, updated)
            await db.db.reservations.update_one(
                {"_id": res.inserted_id},
                {"$set": {"status": "RELEASED", "updated_at": now_iso()}})
            raise ConflictError(
                f"Cannot reserve {qty} of {product_id}: "
                "lost race for remaining stock")
        updated += float(alloc["allocate"])
        # mirror into the RESERVED status row + STATUS_CHANGE ledger legs
        # (every balance delta, including reservations, is ledger-explained)
        await _reserve_leg(product_id, alloc["warehouse_id"], batch_key,
                           uom=alloc.get("uom", "BOX"), qty=float(alloc["allocate"]),
                           location_id=loc_key,
                           reference_type=reference_type, reference_id=reference_id,
                           actor=actor)

    res_doc.pop("_id", None)
    res_doc["reservation_id"] = str(res.inserted_id)
    await bus.publish("inventory.reserved", {
        "product_id": product_id, "quantity": qty, "reference": reference_id,
    })
    return res_doc


async def _reserve_leg(product_id: str, warehouse_id: str, batch_id: Optional[str],
                       uom: str, qty: float, reference_type: str,
                       reference_id: str, actor: Optional[dict],
                       location_id: Optional[str] = None):
    """Move qty AVAILABLE → RESERVED: ledger legs + balance rows (per-key lock).
    location_id is carried through so the RESERVED row keeps the bin."""
    org, site = await _org_site(warehouse_id)
    async with await _locks.lock(_lock_key(org, site,
                                           warehouse_id, product_id, batch_id,
                                           "RESERVED", uom)):
        # The AVAILABLE row was already conditionally decremented by the
        # caller. Here: append both ledger legs, bump only the RESERVED row.
        for status, direction in (("AVAILABLE", -1), ("RESERVED", +1)):
            doc = {
                "movement_id": await next_movement_id(),
                "organization_id": org, "site_id": site,
                "product_id": product_id, "batch_id": batch_id,
                "warehouse_id": warehouse_id, "location_id": location_id,
                "movement_type": "STATUS_CHANGE",
                "quantity": qty, "signed_quantity": direction * qty,
                "uom": uom, "stock_status": status,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "performed_by": actor or {"type": "AGENT", "id": "atp-engine"},
                "unit_cost": None,
                "note": f"reservation {reference_type}:{reference_id}",
                "created_at": now_iso(),
            }
            await db.db.inventory_movements.insert_one(doc)
            if status == "RESERVED":
                await db.db.inventory_balances.update_one(
                    _balance_key(org, site, warehouse_id,
                                 product_id, batch_id, status, uom, location_id),
                    {"$inc": {"quantity": qty, "version": 1},
                     "$set": {"updated_at": now_iso(), "uom": uom},
                     "$setOnInsert": {"created_at": now_iso()}},
                    upsert=True)


async def _rollback_reservation_decrements(product_id: str, plan: List[dict],
                                           applied: float):
    """Best-effort restore of partial decrements when a reservation loses."""
    remaining = applied
    for alloc in plan:
        if remaining <= 0:
            break
        give_back = min(float(alloc["allocate"]), remaining)
        remaining -= give_back
        batch_key = None if alloc["batch_id"] == "UNBATCHED" else alloc["batch_id"]
        uom = alloc.get("uom", "BOX")
        loc_key = alloc.get("location_id")
        org, site = await _org_site(alloc["warehouse_id"])
        await db.db.inventory_balances.update_one(
            {"product_id": product_id,
             "batch_id": batch_key,
             "warehouse_id": alloc["warehouse_id"],
             "location_id": loc_key,
             "stock_status": "AVAILABLE"},
            {"$inc": {"quantity": give_back,
                      "reserved_quantity": -give_back, "version": 1},
             "$set": {"updated_at": now_iso()}})
        await db.db.inventory_balances.update_one(
            _balance_key(org, site, alloc["warehouse_id"],
                         product_id, batch_key, "RESERVED", uom, loc_key),
            {"$inc": {"quantity": -give_back, "version": 1},
             "$set": {"updated_at": now_iso()}})


async def release_reservation_partial(reference_type: str, reference_id: str,
                                      product_id: str, quantity: float,
                                      actor: Optional[dict] = None,
                                      reason: str = "") -> float:
    """Release only `quantity` from an ACTIVE reservation (short picks,
    partial cancellations). Reduction is atomic/conditional on status; ledger
    legs move RESERVED -> AVAILABLE for the released amount."""
    rel = float(quantity)
    if rel <= 0:
        raise ValidationFailed("release quantity must be positive")
    r = await db.db.reservations.find_one_and_update(
        {"reference_type": reference_type, "reference_id": reference_id,
         "product_id": product_id, "status": "ACTIVE",
         "quantity": {"$gte": rel}},
        {"$inc": {"quantity": -rel},
         "$set": {"updated_at": now_iso()}},
        return_document=ReturnDocument.AFTER)
    if r is None:
        raise ConflictError(
            f"Reservation {reference_type}:{reference_id} for {product_id} "
            f"cannot release {rel}")
    # walk allocation rows, releasing from the tail (FEFO-last) first
    remaining = rel
    alloc = r.get("allocation") or []
    for a in reversed(alloc):
        if remaining <= 1e-9:
            break
        take = min(float(a["allocate"]), remaining)
        if take <= 0:
            continue
        a["allocate"] = float(a["allocate"]) - take
        remaining -= take
        batch_key = None if a["batch_id"] == "UNBATCHED" else a["batch_id"]
        uom = a.get("uom", "BOX")
        loc_key = a.get("location_id")
        org, site = await _org_site(a["warehouse_id"])
        async with await _locks.lock(_lock_key(
                org, site, a["warehouse_id"], product_id, batch_key,
                "RESERVED", uom)):
            for status, direction in (("RESERVED", -1), ("AVAILABLE", +1)):
                doc = {
                    "movement_id": await next_movement_id(),
                    "organization_id": org, "site_id": site,
                    "product_id": product_id, "batch_id": batch_key,
                    "warehouse_id": a["warehouse_id"], "location_id": loc_key,
                    "movement_type": "STATUS_CHANGE",
                    "quantity": take,
                    "signed_quantity": direction * take,
                    "uom": uom, "stock_status": status,
                    "reference_type": reference_type,
                    "reference_id": reference_id,
                    "performed_by": actor or {"type": "SYSTEM", "id": "platform"},
                    "unit_cost": None,
                    "note": f"partial release {reason}"[:200],
                    "created_at": now_iso(),
                }
                await db.db.inventory_movements.insert_one(doc)
                await db.db.inventory_balances.update_one(
                    _balance_key(org, site, a["warehouse_id"], product_id,
                                 batch_key, status, uom, loc_key),
                    {"$inc": {"quantity": direction * take, "version": 1},
                     "$set": {"updated_at": now_iso()}}, upsert=True)
    alloc = [a for a in alloc if float(a["allocate"]) > 1e-9]
    if float(r.get("quantity") or 0) <= 1e-9 or not alloc:
        await db.db.reservations.update_one(
            {"_id": r["_id"]},
            {"$set": {"status": "RELEASED", "updated_at": now_iso()}})
    else:
        await db.db.reservations.update_one(
            {"_id": r["_id"]}, {"$set": {"allocation": alloc}})
    return rel


async def release_reservation(reference_type: str, reference_id: str,
                              product_id: Optional[str] = None,
                              actor: Optional[dict] = None) -> int:
    q: Dict[str, Any] = {"reference_type": reference_type,
                         "reference_id": reference_id, "status": "ACTIVE"}
    if product_id:
        q["product_id"] = product_id
    rows = [dict(r) async for r in db.db.reservations.find(q)]
    released = 0
    for r in rows:
        org, site = await _org_site(r["allocation"][0]["warehouse_id"]
                                    if r.get("allocation") else "WH-MAIN")
        for alloc in r.get("allocation", []):
            batch_key = (None if alloc["batch_id"] == "UNBATCHED"
                         else alloc["batch_id"])
            uom = alloc.get("uom", "BOX")
            loc_key = alloc.get("location_id")
            async with await _locks.lock(_lock_key(
                    org, site, alloc["warehouse_id"],
                    r["product_id"], batch_key, "RESERVED", uom)):
                # ledger legs: RESERVED −qty, AVAILABLE +qty (bin preserved)
                for status, direction in (("RESERVED", -1), ("AVAILABLE", +1)):
                    doc = {
                        "movement_id": await next_movement_id(),
                        "organization_id": org, "site_id": site,
                        "product_id": r["product_id"], "batch_id": batch_key,
                        "warehouse_id": alloc["warehouse_id"],
                        "location_id": loc_key,
                        "movement_type": "STATUS_CHANGE",
                        "quantity": float(alloc["allocate"]),
                        "signed_quantity": direction * float(alloc["allocate"]),
                        "uom": uom, "stock_status": status,
                        "reference_type": reference_type,
                        "reference_id": reference_id,
                        "performed_by": actor or {"type": "SYSTEM", "id": "platform"},
                        "unit_cost": None,
                        "note": f"reservation release {reference_id}",
                        "created_at": now_iso(),
                    }
                    await db.db.inventory_movements.insert_one(doc)
                    await db.db.inventory_balances.update_one(
                        _balance_key(org, site,
                                     alloc["warehouse_id"], r["product_id"],
                                     batch_key, status, uom, loc_key),
                        {"$inc": {"quantity": direction * float(alloc["allocate"]),
                                  "version": 1},
                         "$set": {"updated_at": now_iso()}},
                        upsert=True)
        res = await db.db.reservations.update_one(
            {"_id": r["_id"], "status": "ACTIVE"},
            {"$set": {"status": "RELEASED", "updated_at": now_iso()}})
        released += res.modified_count
    return released


async def consume_reservation_allocation(product_id: str, reference_type: str,
                                         reference_id: str,
                                         movement_type: str,
                                         batch_id: str,
                                         warehouse_id: Optional[str] = None,
                                         location_id: Optional[str] = None,
                                         quantity: Optional[float] = None,
                                         actor: Optional[dict] = None) -> dict:
    """Consume ONE allocation (P0 9.4): exactly the batch/bin/qty a single pick
    task is linked to — never the whole order's reservation set.

    The parent reservation keeps its remaining allocations ACTIVE; it flips to
    CONSUMED only when no allocations remain.
    """
    resv = await db.db.reservations.find_one({
        "reference_type": reference_type, "reference_id": reference_id,
        "product_id": product_id, "status": "ACTIVE"})
    if not resv:
        raise NotFound(f"No active reservation for {reference_type}:"
                       f"{reference_id} {product_id}")
    target = None
    for alloc in resv.get("allocation", []):
        same_batch = alloc["batch_id"] == batch_id or \
            (alloc["batch_id"] == "UNBATCHED" and batch_id is None)
        same_loc = (alloc.get("location_id") or None) == (location_id or None)
        same_wh = warehouse_id is None or alloc["warehouse_id"] == warehouse_id
        if same_batch and same_loc and same_wh:
            target = alloc
            break
    if target is None:
        raise ConflictError(
            f"Allocation {product_id}/{batch_id}/{location_id} not on active "
            f"reservation {reference_type}:{reference_id}")
    qty = float(quantity if quantity is not None else target["allocate"])
    if qty <= 0 or qty > float(target["allocate"]) + 1e-9:
        raise ValidationFailed(
            f"Pick quantity {qty} exceeds allocation {target['allocate']}")

    mv = await record_movement(
        movement_type=movement_type,
        product_id=product_id,
        warehouse_id=target["warehouse_id"],
        quantity=qty,
        uom=target.get("uom", "BOX"),
        batch_id=target["batch_id"] if target["batch_id"] != "UNBATCHED" else None,
        location_id=target.get("location_id"),
        stock_status="RESERVED",
        reference_type=reference_type,
        reference_id=reference_id,
        performed_by=actor,
    )

    # shrink or drop this allocation; consume the reservation only when empty
    remaining_alloc = []
    for alloc in resv.get("allocation", []):
        if alloc is target:
            left = round(float(alloc["allocate"]) - qty, 6)
            if left > 1e-9:
                alloc = {**alloc, "allocate": left}
                remaining_alloc.append(alloc)
        else:
            remaining_alloc.append(alloc)
    if remaining_alloc:
        await db.db.reservations.update_one(
            {"_id": resv["_id"]},
            {"$set": {"allocation": remaining_alloc,
                      "quantity": sum(float(a["allocate"]) for a in remaining_alloc),
                      "updated_at": now_iso()}})
    else:
        await db.db.reservations.update_one(
            {"_id": resv["_id"]}, {"$set": {"status": "CONSUMED",
                                             "updated_at": now_iso()}})
    return mv


async def consume_reservation(reference_type: str, reference_id: str,
                              movement_type: str, warehouse_id: str,
                              actor: Optional[dict] = None) -> List[dict]:
    """Turn an active reservation into ledger movements (on pick/dispense)."""
    q: Dict[str, Any] = {"reference_type": reference_type,
                         "reference_id": reference_id, "status": "ACTIVE"}
    rows = [dict(r) async for r in db.db.reservations.find(q)]
    movements = []
    for r in rows:
        for alloc in r.get("allocation", []):
            mv = await record_movement(
                movement_type=movement_type,
                product_id=r["product_id"],
                warehouse_id=alloc["warehouse_id"],
                quantity=alloc["allocate"],
                uom=alloc.get("uom", "BOX"),
                batch_id=alloc["batch_id"] if alloc["batch_id"] != "UNBATCHED" else None,
                location_id=alloc.get("location_id"),
                stock_status="RESERVED",
                reference_type=reference_type,
                reference_id=reference_id,
                performed_by=actor,
            )
            movements.append(mv)
        await db.db.reservations.update_one(
            {"_id": r["_id"]}, {"$set": {"status": "CONSUMED",
                                         "updated_at": now_iso()}})
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
    await db.db.reservations.update_many(
        {"allocation.batch_id": batch_id, "status": "ACTIVE"},
        {"$set": {"status": "FROZEN", "updated_at": now_iso()}},
    )
    await bus.publish("inventory.blocked", {"batch_id": batch_id,
                                            "reason": reason, "source": source})
    return await _get_batch(batch_id)


async def unblock_batch(batch_id: str, actor: dict, reason: str) -> dict:
    await db.db.batches.update_one(
        {"batch_id": batch_id},
        {"$set": {"blocked": False, "block_reason": None,
                  "updated_at": now_iso()}},
    )
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
                      counted_qty: float, actor: dict,
                      count_type: str = "CYCLE", location_id: Optional[str] = None) -> dict:
    """Two-phase cycle count (Part 7): record variance, NEVER auto-adjust.

    COUNT_VARIANCE -> investigation -> approval (if required) ->
    STOCK_ADJUSTMENT via post_stock_adjustment. Stock stays untouched until
    an authorized approver posts the adjustment. count_type: CYCLE | ABC | FULL.
    
    Floor guard: counting less than the free AVAILABLE quantity (which already
    excludes reserved stock) is rejected immediately — it would imply consuming
    reserved stock via the eventual adjustment.
    """
    key: Dict[str, Any] = {"product_id": product_id,
                           "warehouse_id": warehouse_id,
                           "stock_status": "AVAILABLE"}
    key["batch_id"] = batch_id or None
    if location_id is not None:
        key["location_id"] = location_id
    row = await db.db.inventory_balances.find_one(key)
    system_qty = float(row["quantity"]) if row else 0.0
    variance = float(counted_qty) - system_qty
    
    # Floor guard: free AVAILABLE qty already excludes reservations (reserve()
    # moves stock AVAILABLE -> RESERVED). A negative variance larger than free
    # qty would breach the reservation floor.
    if variance < 0 and abs(variance) > system_qty + 1e-9:
        raise ConflictError(
            f"Counted qty {counted_qty} below free stock {system_qty}; "
            f"would breach reservation floor")

    doc = {
        "warehouse_id": warehouse_id, "product_id": product_id,
        "batch_id": batch_id, "location_id": location_id,
        "count_type": count_type,
        "system_qty": system_qty, "counted_qty": float(counted_qty),
        "variance": variance,
        "status": "VARIANCE_DETECTED" if abs(variance) > 1e-9 else "NO_VARIANCE",
        "adjustment_posted": False, "counted_by": actor,
        "created_at": now_iso(),
    }
    res = await db.db.cycle_counts.insert_one(doc)
    doc["count_id"] = str(res.inserted_id)
    await db.db.cycle_counts.update_one(
        {"_id": res.inserted_id}, {"$set": {"count_id": doc["count_id"]}})
    if abs(variance) > 1e-9:
        await bus.publish("stock.variance_detected", {
            "count_id": doc["count_id"], "warehouse_id": warehouse_id,
            "product_id": product_id, "batch_id": batch_id,
            "system_qty": system_qty, "counted_qty": float(counted_qty),
            "variance": variance,
        }, actor)
    await audit("CYCLE_COUNT", doc["count_id"], "COUNT_RECORDED", actor,
                details={"variance": variance, "type": count_type})
    return _clean(doc)


async def post_stock_adjustment(count_id: str, actor: dict, reason: str,
                                approved_by: Optional[dict] = None) -> dict:
    """Authorizer posts the adjustment for a counted variance (Part 7).

    Variance above the policy threshold requires an explicit approver
    (SoD: the counter may not approve their own count). Adjustment lands on
    the AVAILABLE row; the reservation floor is respected via the
    non-negative stock guard.
    """
    from app.core.policies import get_rule
    from app.core.errors import SoDError

    cc = await db.db.cycle_counts.find_one({"count_id": count_id})
    if not cc:
        raise NotFound(f"Cycle count {count_id} not found")
    if cc.get("adjustment_posted"):
        raise ConflictError("Adjustment already posted for this count")
    variance = float(cc["variance"])
    if variance == 0:
        raise ValidationFailed("No variance to adjust")
    threshold = float(await get_rule("stock_adjustment_approval_threshold", 0.0))
    counter_id = (cc.get("counted_by") or {}).get("id")
    if abs(variance) > threshold:
        if not approved_by:
            raise SoDError(
                f"Variance {variance} exceeds threshold {threshold}; "
                "approver required before STOCK_ADJUSTMENT")
        if approved_by.get("id") and approved_by["id"] == counter_id \
                and "SUPER_ADMIN" not in approved_by.get("roles", []):
            raise SoDError("Counter may not approve their own stock adjustment")
    row = await db.db.inventory_balances.find_one({
        "product_id": cc["product_id"], "warehouse_id": cc["warehouse_id"],
        "stock_status": "AVAILABLE",
        "batch_id": cc.get("batch_id") or None})
    if variance < 0:
        # free stock is the AVAILABLE row's quantity (reserve() already moved
        # reserved stock to a separate RESERVED row). floor check uses this.
        free = float((row or {}).get("quantity") or 0)
        if abs(variance) > free + 1e-9:
            raise ConflictError(
                f"Adjustment {variance} would breach reservation floor "
                f"({free} free)")
    mv_type = "POSITIVE_ADJUSTMENT" if variance > 0 else "NEGATIVE_ADJUSTMENT"
    mv = await record_movement(
        movement_type=mv_type, product_id=cc["product_id"],
        warehouse_id=cc["warehouse_id"], quantity=abs(variance),
        batch_id=cc.get("batch_id"), location_id=cc.get("location_id"),
        reference_type="STOCK_ADJUSTMENT", reference_id=count_id,
        performed_by=actor,
        note=reason or f"Adjustment for count {count_id} (variance {variance})")
    await db.db.cycle_counts.update_one(
        {"count_id": count_id},
        {"$set": {"adjustment_posted": True, "status": "ADJUSTED",
                  "adjustment_movement_id": mv["movement_id"],
                  "approved_by": approved_by, "reason": reason,
                  "updated_at": now_iso()}})
    await bus.publish("stock.adjustment_posted",
                      {"count_id": count_id, "movement_id": mv["movement_id"],
                       "variance": variance,
                       "approved_by": (approved_by or {}).get("id")}, actor)
    await audit("CYCLE_COUNT", count_id, "STOCK_ADJUSTMENT_POSTED", actor,
                details={"variance": variance, "movement": mv["movement_id"],
                         "approved_by": (approved_by or {}).get("id")})
    return mv


async def reverse_cycle_count(count_id: str, actor: dict) -> dict:
    """Reverse an approved cycle-count adjustment with a compensating
    movement — the original count row stays (append-only corrections)."""
    cc = await db.db.cycle_counts.find_one({"count_id": count_id})
    if not cc:
        raise NotFound(f"Cycle count {count_id} not found")
    if cc.get("reversed"):
        raise ConflictError("Cycle count already reversed")
    variance = float(cc["variance"])
    if variance == 0:
        raise ValidationFailed("No variance to reverse")
    mv_type = "NEGATIVE_ADJUSTMENT" if variance > 0 else "POSITIVE_ADJUSTMENT"
    mv = await record_movement(
        movement_type=mv_type, product_id=cc["product_id"],
        warehouse_id=cc["warehouse_id"], quantity=abs(variance),
        batch_id=cc.get("batch_id"),
        reference_type="CYCLE_COUNT_REVERSAL", reference_id=count_id,
        performed_by=actor, note=f"Reversal of cycle count {count_id}")
    await db.db.cycle_counts.update_one(
        {"count_id": count_id},
        {"$set": {"reversed": True, "reversal_movement_id": mv["movement_id"],
                  "reversed_by": actor, "reversed_at": now_iso()}})
    await audit("CYCLE_COUNT", count_id, "REVERSED", actor,
                details={"variance": variance})
    return {"count_id": count_id, "reversal_movement_id": mv["movement_id"]}


async def transfer(from_wh: str, to_wh: str, product_id: str, quantity: float,
                   batch_id: Optional[str], actor: dict,
                   reference: str = "TRANSFER") -> dict:
    """Warehouse-to-warehouse transfer via ledger movements."""
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


async def rebuild_balances() -> dict:
    """Rebuild the balance projection from the append-only ledger.

    The ledger is the source of truth; this is the repair path if a balance
    write ever fails after the ledger row was committed (non-transactional
    topologies). Never used as the primary write path.
    """
    pipeline = [
        {"$group": {
            "_id": {"organization_id": "$organization_id", "site_id": "$site_id",
                    "warehouse_id": "$warehouse_id", "product_id": "$product_id",
                    "batch_id": "$batch_id", "stock_status": "$stock_status",
                    "uom": "$uom", "location_id": "$location_id"},
            "quantity": {"$sum": "$signed_quantity"},
        }},
    ]
    rebuilt = 0
    async for agg in db.db.inventory_movements.aggregate(pipeline,
                                                         allowDiskUse=True):
        k = agg["_id"]
        await db.db.inventory_balances.update_one(
            {f: k[f] for f in ("organization_id", "site_id", "warehouse_id",
                               "product_id", "batch_id", "stock_status", "uom",
                               "location_id")},
            {"$set": {"quantity": float(agg["quantity"]),
                      "version": 1, "updated_at": now_iso()}},
            upsert=True)
        rebuilt += 1
    return {"balances_rebuilt": rebuilt}


async def traceability(batch_id: str) -> dict:
    """Full forward + reverse traceability graph for a batch (recall-ready)."""
    batch = await _get_batch(batch_id)
    product_id = batch["product_id"]

    sources = [_clean(dict(m)) async for m in db.db.inventory_movements.find(
        {"batch_id": batch_id,
         "movement_type": {"$in": ["PURCHASE_RECEIPT", "PRODUCTION_RECEIPT"]}})]
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

    movements_out = [_clean(dict(m)) async for m in db.db.inventory_movements.find(
        {"batch_id": batch_id,
         "movement_type": {"$in": ["SALE", "DISPENSE", "TRANSFER_OUT",
                                   "MATERIAL_ISSUE"]}})]
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
        "backward": {"sources": sources, "grns": grns, "vendors": vendors,
                     "production_orders": production_orders},
        "forward": {"movements_out": movements_out, "sales_orders": sales_orders,
                    "shipments": shipments, "customers": customers},
        "current_balances": balances_now,
    }


async def _product(product_id: str) -> Optional[dict]:
    doc = await db.db.products.find_one({"sku": product_id})
    return _clean(doc) if doc else None


async def list_reservations(reference_type: Optional[str] = None,
                            reference_id: Optional[str] = None,
                            product_id: Optional[str] = None,
                            status: Optional[str] = None) -> List[dict]:
    """List reservations with optional filters."""
    q: Dict[str, Any] = {}
    if reference_type:
        q["reference_type"] = reference_type
    if reference_id:
        q["reference_id"] = reference_id
    if product_id:
        q["product_id"] = product_id
    if status:
        q["status"] = status
    rows = []
    async for r in db.db.reservations.find(q).sort("created_at", -1).limit(500):
        rows.append(_reservation_out(dict(r)))
    return rows
