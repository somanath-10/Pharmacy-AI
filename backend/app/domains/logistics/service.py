"""Logistics: ASN (inbound), shipments (outbound), carriers, vehicles, POD."""
from datetime import datetime, timedelta, timezone
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


# ------------------------------------------------------------------ ASN inbound
async def create_asn(payload: dict, actor: dict) -> dict:
    if not payload.get("po_id"):
        raise ValidationFailed("ASN needs po_id")
    if not payload.get("lines"):
        raise ValidationFailed("ASN needs lines")
    po = await db.db.purchase_orders.find_one({"po_id": payload["po_id"]})
    if not po:
        raise NotFound(f"PO {payload['po_id']} not found")
    if po["status"] not in ("SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"):
        raise ConflictError(f"ASN not allowed for PO status {po['status']}")
    asn_id = payload.get("asn_id") or await _next_id("asn", "ASN")
    eta = payload.get("eta") or (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    doc = {
        "asn_id": asn_id,
        "po_id": po["po_id"],
        "vendor_id": po["vendor_id"],
        "carrier": payload.get("carrier"),
        "vehicle_no": payload.get("vehicle_no"),
        "driver": payload.get("driver"),
        "eta": eta,
        "lines": [{"line_no": l["line_no"], "quantity": float(l["quantity"]),
                   "batch_id": l.get("batch_id"),
                   "expiry_date": l.get("expiry_date"),
                   "mfg_date": l.get("mfg_date")} for l in payload["lines"]],
        "docs": payload.get("docs", []),
        "status": "IN_TRANSIT",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.asns.insert_one(doc)
    await bus.publish("asn.created", {"asn_id": asn_id, "po_id": po["po_id"],
                                      "eta": eta}, actor)
    await record_node("purchase_order", po["po_id"], "asn", "ASN Created",
                      "DONE", actor, detail=f"ETA {eta[:10]}")
    await audit("ASN", asn_id, "CREATED", actor, details={"po_id": po["po_id"]})
    return _clean(doc)


async def arrive_asn(asn_id: str, payload: dict, actor: dict) -> dict:
    asn = await _get_asn(asn_id)
    gate_entry_id = await _next_id("gate", "GATE")
    doc = {
        "gate_entry_id": gate_entry_id,
        "asn_id": asn_id,
        "vehicle_no": payload.get("vehicle_no") or asn.get("vehicle_no"),
        "driver": payload.get("driver") or asn.get("driver"),
        "arrived_at": now_iso(),
        "dock": payload.get("dock"),
        "transport_condition": payload.get("transport_condition"),
        "sensor_readings": payload.get("sensor_readings", []),
        "status": "CHECKED_IN",
        "created_by": actor,
    }
    await db.db.gate_entries.insert_one(doc)
    await db.db.asns.update_one({"asn_id": asn_id},
                                {"$set": {"status": "ARRIVED",
                                          "gate_entry_id": gate_entry_id,
                                          "updated_at": now_iso()}})
    await bus.publish("shipment.arrived", {"asn_id": asn_id}, actor)
    await record_node("purchase_order", asn["po_id"], "inbound",
                      "Inbound Arrived", "DONE", actor)
    # Cold chain (Part 10): arrival readings are checked against the lane's
    # required range; an excursion auto-blocks the PO's batches via QA hold.
    if payload.get("sensor_readings"):
        await check_temperature_excursion(
            entity_type="ASN", entity_id=asn_id,
            readings=payload["sensor_readings"],
            required_range=payload.get("required_temperature"), actor=actor)
    return doc


# ------------------------------------------------------------- cold chain
async def check_temperature_excursion(entity_type: str, entity_id: str,
                                      readings: List[dict],
                                      required_range: Optional[dict],
                                      actor: Optional[dict] = None) -> dict:
    """Deterministic cold-chain gate (Part 10).

    readings: [{sensor_id, temp_c, at}]; required_range: {min, max}.
    Excursion → temperature.excursion event + batches auto-blocked
    (QUALITY_HOLD) so nothing stays AVAILABLE during QA review.
    """
    if not required_range:
        return {"checked": 0, "excursion": False}
    lo = required_range.get("min")
    hi = required_range.get("max")
    if lo is None or hi is None:
        return {"checked": 0, "excursion": False}
    # normalize: readings may carry temp_c or a plain temp value
    norm = [{**r, "temp_c": r.get("temp_c", r.get("temp"))} for r in readings]
    bad = [r for r in norm
           if r.get("temp_c") is not None
           and (float(r["temp_c"]) < float(lo) or float(r["temp_c"]) > float(hi))]
    result = {"checked": len(readings), "excursions": len(bad),
              "excursion": bool(bad), "min": lo, "max": hi}
    if not bad:
        await audit("COLD_CHAIN", f"{entity_type}:{entity_id}", "IN_RANGE",
                    actor, details={"readings": len(readings)})
        return result
    result["affected_batches"] = []

    async def _block(bid: str):
        await db.db.batches.update_one(
            {"batch_id": bid},
            {"$set": {"blocked": True,
                      "block_reason": "TEMPERATURE_EXCURSION",
                      "updated_at": now_iso()}})
        result["affected_batches"].append(bid)

    if entity_type == "BATCH":
        await _block(entity_id)
    elif entity_type == "ASN":
        asn = await db.db.asns.find_one({"asn_id": entity_id}) or {}
        po = await db.db.purchase_orders.find_one({"po_id": asn.get("po_id")}) or {}
        for line in po.get("lines", []):
            batch_ids = [b["batch_id"] async for b in db.db.batches.find(
                {"product_id": line.get("sku"),
                 "supplier_id": po.get("vendor_id")}).limit(5)]
            for bid in batch_ids:
                await _block(bid)
    elif entity_type == "SHIPMENT":
        shp = await db.db.shipments.find_one({"shipment_id": entity_id}) or {}
        skus = [l.get("sku") for l in shp.get("lines", []) if l.get("sku")]
        async for b in db.db.batches.find({"product_id": {"$in": skus},
                                           "blocked": {"$ne": True}}).limit(20):
            await _block(b["batch_id"])
    await bus.publish("temperature.excursion",
                      {"entity_type": entity_type, "entity_id": entity_id,
                       "excursions": bad[:20],
                       "affected_batches": result["affected_batches"]},
                      actor or {"type": "AGENT", "id": "logistics-agent"})
    await audit("COLD_CHAIN", f"{entity_type}:{entity_id}",
                "TEMPERATURE_EXCURSION", actor,
                details={"excursions": len(bad),
                         "blocked": result["affected_batches"]})
    return result


# ------------------------------------------------- outbound shipments / dispatch
async def plan_shipment(payload: dict, actor: dict) -> dict:
    if not payload.get("sales_order_id"):
        raise ValidationFailed("shipment needs sales_order_id")
    shipment_id = await _next_id("shipment", "SHP")
    doc = {
        "shipment_id": shipment_id,
        "sales_order_id": payload["sales_order_id"],
        "warehouse_id": payload.get("warehouse_id"),
        "ship_to": payload.get("ship_to"),
        "carrier_id": payload.get("carrier_id"),
        "vehicle_id": payload.get("vehicle_id"),
        "route": payload.get("route"),
        "required_range": payload.get("required_range"),
        "planned_pickup": payload.get("planned_pickup"),
        "eta": payload.get("eta"),
        "packages": payload.get("packages", []),
        "status": "PLANNED",
        "tracking_events": [],
        "pod": None,
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "PLANNED", "actor": actor, "at": now_iso()}],
    }
    await db.db.shipments.insert_one(doc)
    await audit("SHIPMENT", shipment_id, "PLANNED", actor)
    return _clean(doc)


async def dispatch_shipment(shipment_id: str, payload: dict, actor: dict) -> dict:
    shp = await _get_shipment(shipment_id)
    if shp["status"] not in ("PLANNED", "LOADING"):
        raise ConflictError(f"Shipment {shipment_id} status {shp['status']}")
    # walk PLANNED → LOADING → DISPATCHED (load-check is implicit at dispatch)
    if shp["status"] == "PLANNED":
        await transition("shipment", shipment_id, "shipments", "shipment_id",
                         "LOADING", actor, reason="Loading at dock")
    await transition("shipment", shipment_id, "shipments", "shipment_id",
                     "DISPATCHED", actor, reason="Dispatched from warehouse")
    await transition("shipment", shipment_id, "shipments", "shipment_id",
                     "IN_TRANSIT", actor, reason="On the road")
    await db.db.shipments.update_one(
        {"shipment_id": shipment_id},
        {"$push": {"tracking_events": {"event": "DISPATCHED", "at": now_iso(),
                                       "location": payload.get("location"),
                                       "by": actor}}},
    )
    await bus.publish("shipment.dispatched",
                      {"shipment_id": shipment_id,
                       "sales_order_id": shp["sales_order_id"]}, actor)
    from app.domains.sales.service import mark_shipped

    await mark_shipped(shp["sales_order_id"], shipment_id, actor)
    return await _get_shipment(shipment_id)


async def split_shipment(shipment_id: str, payload: dict, actor: dict) -> dict:
    """Split a planned shipment: move line quantities to a new shipment.

    Line quantities across (parent + splits) can never exceed the planned
    original quantities. SO lines carry shipped_qty to keep balance at the
    order level.
    """
    shp = await _get_shipment(shipment_id)
    if shp["status"] not in ("PLANNED", "LOADING"):
        raise ConflictError(f"Shipment {shipment_id} not splittable in {shp['status']}")
    take = payload.get("lines") or []
    if not take:
        raise ValidationFailed("split needs lines [{line_no, quantity}]")

    # normalize current per-line quantity map (parent may already be split)
    cur: Dict[int, float] = {}
    for l in shp.get("lines", []):
        cur[int(l["line_no"])] = cur.get(int(l["line_no"]), 0.0) + float(l["quantity"])

    # planned original quantities: sum of parent + prior splits
    planned: Dict[int, float] = dict(cur)
    async for s in db.db.shipments.find({"split_parent": shipment_id}):
        for l in s.get("lines", []):
            k = int(l["line_no"])
            planned[k] = planned.get(k, 0.0) + float(l["quantity"])
    if not planned:
        raise ValidationFailed("Shipment has no lines to split")

    new_lines = []
    for l in take:
        line_no = int(l["line_no"])
        qty = float(l["quantity"])
        if qty <= 0:
            raise ValidationFailed("split line quantity must be positive")
        if qty > cur.get(line_no, 0.0) + 1e-9:
            raise ValidationFailed(
                f"Split quantity {qty} for line {line_no} exceeds remaining "
                f"{cur.get(line_no, 0.0)} (planned {planned.get(line_no, 0.0)})")
        cur[line_no] = cur.get(line_no, 0.0) - qty
        new_lines.append({"line_no": line_no, "quantity": qty})

    child_id = await _next_id("shipment", "SHP")
    child = {
        "shipment_id": child_id,
        "sales_order_id": shp.get("sales_order_id"),
        "warehouse_id": shp.get("warehouse_id"),
        "ship_to": payload.get("ship_to") or shp.get("ship_to"),
        "carrier_id": payload.get("carrier_id") or shp.get("carrier_id"),
        "vehicle_id": payload.get("vehicle_id"),
        "route": payload.get("route"),
        "required_range": payload.get("required_range"),
        "planned_pickup": payload.get("planned_pickup"),
        "eta": payload.get("eta"),
        "packages": payload.get("packages", []),
        "lines": new_lines,
        "split_parent": shipment_id,
        "status": "PLANNED",
        "tracking_events": [],
        "pod": None,
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "PLANNED", "actor": actor, "at": now_iso()}],
    }
    await db.db.shipments.insert_one(child)
    await db.db.shipments.update_one(
        {"shipment_id": shipment_id},
        {"$set": {"lines": [{"line_no": k, "quantity": v}
                            for k, v in sorted(cur.items()) if v > 0],
                  "updated_at": now_iso()}})
    await audit("SHIPMENT", shipment_id, "SPLIT", actor,
                details={"child": child_id, "lines": new_lines})
    return _clean(child)


async def track(shipment_id: str, event: str, payload: dict, actor: dict) -> dict:
    shp = await _get_shipment(shipment_id)
    await db.db.shipments.update_one(
        {"shipment_id": shipment_id},
        {"$push": {"tracking_events": {"event": event,
                                       "location": payload.get("location"),
                                       "note": payload.get("note"),
                                       "at": now_iso(), "by": actor}}},
    )
    if event == "DELIVERED":
        await transition("shipment", shipment_id, "shipments", "shipment_id",
                         "DELIVERED", actor, reason="Delivered at customer site")
        await bus.publish("delivery.completed",
                          {"shipment_id": shipment_id,
                           "sales_order_id": shp["sales_order_id"]}, actor)
        # interlink: shipment delivery → sales order DELIVERED
        if shp.get("sales_order_id"):
            from app.domains.sales.service import mark_delivered

            await mark_delivered(shp["sales_order_id"], actor)
    elif event == "FAILED_DELIVERY":
        # Delivery exception (Part 9): stays IN_TRANSIT, records exception,
        # schedules reattempt; humans decide address/return escalation.
        if shp["status"] not in ("IN_TRANSIT", "DISPATCHED"):
            raise ConflictError(
                f"FAILED_DELIVERY only in transit, not {shp['status']}")
        await db.db.shipments.update_one(
            {"shipment_id": shipment_id},
            {"$set": {"delivery_exception": {
                "reason": payload.get("reason", "CUSTOMER_UNAVAILABLE"),
                "reattempt_at": payload.get("reattempt_at"),
                "at": now_iso(), "by": actor}}})
        await bus.publish("delivery.failed",
                          {"shipment_id": shipment_id,
                           "reason": payload.get("reason")}, actor)
    return await _get_shipment(shipment_id)


async def reattempt_delivery(shipment_id: str, payload: dict, actor: dict) -> dict:
    """Schedule/execute a reattempt after a failed delivery (Part 9): clears the
    exception, bumps the attempt counter, keeps the shipment IN_TRANSIT."""
    shp = await _get_shipment(shipment_id)
    if not shp.get("delivery_exception"):
        raise ConflictError("No delivery exception to reattempt")
    if shp["status"] not in ("IN_TRANSIT", "DISPATCHED"):
        raise ConflictError(f"Reattempt only in transit, not {shp['status']}")
    attempts = int(shp.get("delivery_attempts", 0)) + 1
    await db.db.shipments.update_one(
        {"shipment_id": shipment_id},
        {"$set": {"delivery_exception": None,
                  "delivery_attempts": attempts,
                  "last_reattempt_at": now_iso(),
                  "next_reattempt_at": payload.get("next_reattempt_at")},
         "$push": {"tracking_events": {
             "event": "REATTEMPT_SCHEDULED",
             "note": payload.get("note", f"reattempt #{attempts}"),
             "at": now_iso(), "by": actor}}})
    await audit("SHIPMENT", shipment_id, "DELIVERY_REATTEMPT", actor,
                details={"attempt": attempts})
    return await _get_shipment(shipment_id)


async def capture_transport_temperature(shipment_id: str, payload: dict,
                                        actor: dict) -> dict:
    """Sensor/logger reading for an outbound shipment (Part 10): any excursion
    runs the deterministic cold-chain gate (auto quality hold)."""
    shp = await _get_shipment(shipment_id)
    readings = payload.get("readings") or (
        [{"temp": payload.get("temp"), "at": payload.get("at") or now_iso()}]
        if payload.get("temp") is not None else [])
    if not readings:
        raise ValidationFailed("temperature capture needs readings or temp")
    required = payload.get("required_range") or shp.get("required_range")
    return await check_temperature_excursion("SHIPMENT", shipment_id,
                                             readings, required, actor)


# ------------------------------------------------- fleet: vehicles/drivers/routes
VEHICLE_TYPES = ["TRUCK", "VAN", "REFRIGERATED_TRUCK", "REFRIGERATED_VAN",
                 "BIKE", "CONTAINER"]


async def create_vehicle(payload: dict, actor: dict) -> dict:
    if payload.get("vehicle_type") not in VEHICLE_TYPES:
        raise ValidationFailed(f"vehicle_type in {VEHICLE_TYPES}")
    vehicle_id = payload.get("vehicle_id") or await _next_id("veh", "VEH")
    doc = {"vehicle_id": vehicle_id,
           "vehicle_type": payload["vehicle_type"],
           "registration_no": payload.get("registration_no"),
           "capacity": payload.get("capacity"),
           "temperature_controlled": bool(
               payload.get("temperature_controlled")
               or "REFRIGERATED" in payload["vehicle_type"]),
           "temperature_min": payload.get("temperature_min"),
           "temperature_max": payload.get("temperature_max"),
           "site_id": payload.get("site_id"),
           "status": "ACTIVE",
           "created_at": now_iso()}
    await db.db.vehicles.insert_one(doc)
    await audit("VEHICLE", vehicle_id, "CREATED", actor)
    return _clean(doc)


async def create_driver(payload: dict, actor: dict) -> dict:
    if not payload.get("name"):
        raise ValidationFailed("Driver needs name")
    driver_id = payload.get("driver_id") or await _next_id("drv", "DRV")
    doc = {"driver_id": driver_id, "name": payload["name"],
           "license_no": payload.get("license_no"),
           "license_expiry": payload.get("license_expiry"),
           "phone": payload.get("phone"), "status": "ACTIVE",
           "created_at": now_iso()}
    await db.db.drivers.insert_one(doc)
    await audit("DRIVER", driver_id, "CREATED", actor)
    return _clean(doc)


async def create_route(payload: dict, actor: dict) -> dict:
    if not payload.get("name"):
        raise ValidationFailed("Route needs name")
    route_id = payload.get("route_id") or await _next_id("route", "RTE")
    doc = {"route_id": route_id, "name": payload["name"],
           "stops": payload.get("stops", []),
           "distance_km": payload.get("distance_km"),
           "estimated_hours": payload.get("estimated_hours"),
           "status": "ACTIVE", "created_at": now_iso()}
    await db.db.routes.insert_one(doc)
    await audit("ROUTE", route_id, "CREATED", actor)
    return _clean(doc)


async def schedule_appointment(payload: dict, actor: dict) -> dict:
    """Inbound dock appointment (Part 9): gate/dock slot against an ASN."""
    if not payload.get("asn_id"):
        raise ValidationFailed("Appointment needs asn_id")
    appt_id = await _next_id("appt", "APT")
    doc = {"appointment_id": appt_id, "asn_id": payload["asn_id"],
           "warehouse_id": payload.get("warehouse_id"),
           "dock": payload.get("dock"),
           "slot_from": payload.get("slot_from"),
           "slot_to": payload.get("slot_to"),
           "status": "BOOKED", "created_by": actor,
           "created_at": now_iso()}
    await db.db.dock_appointments.insert_one(doc)
    await audit("DOCK_APPOINTMENT", appt_id, "BOOKED", actor,
                details={"asn": payload["asn_id"], "dock": doc["dock"]})
    return _clean(doc)


async def optimize_route_plan(shipment_ids: List[str],
                              actor: Optional[dict] = None) -> dict:
    """Routine consolidation + carrier/route assignment (Part 9/18).

    Deterministic: group by destination, pick carriers by type+capacity,
    prefer refrigerated vehicles for cold chain. Humans execute the physical
    transport; the system decides the plan."""
    groups: Dict[str, dict] = {}
    for sid in shipment_ids:
        shp = await _get_shipment(sid)
        if shp["status"] not in ("PLANNED", "LOADING"):
            continue
        dest = str(shp.get("ship_to") or "UNKNOWN")
        groups.setdefault(dest, {"shipments": [], "weight": 0.0,
                                 "cold": False})
        groups[dest]["shipments"].append(sid)
        groups[dest]["weight"] += float(shp.get("weight") or 0)
        if shp.get("cold_chain"):
            groups[dest]["cold"] = True
    plan = []
    for dest, g in groups.items():
        carrier = None
        async for c in db.db.carriers.find({"status": "ACTIVE"}):
            carrier = c
            break
        vehicle = None
        vq: Dict[str, Any] = {"status": "ACTIVE"}
        if g["cold"]:
            vq["temperature_controlled"] = True
        vehicle = await db.db.vehicles.find_one(vq) or \
            await db.db.vehicles.find_one({"status": "ACTIVE"})
        for sid in g["shipments"]:
            upd = {"carrier_id": (carrier or {}).get("carrier_id"),
                   "vehicle_id": (vehicle or {}).get("vehicle_id"),
                   "route": f"CONSOL-{dest}"}
            await db.db.shipments.update_one({"shipment_id": sid},
                                             {"$set": upd})
        plan.append({"destination": dest, "shipments": g["shipments"],
                     "carrier": (carrier or {}).get("carrier_id"),
                     "vehicle": (vehicle or {}).get("vehicle_id"),
                     "cold_chain": g["cold"]})
    await audit("SHIPMENT_PLANNING", ",".join(shipment_ids)[:100],
                "ROUTE_OPTIMIZED", actor or {"type": "AGENT",
                                             "id": "logistics-agent"},
                details={"groups": len(plan)})
    return {"groups": plan}


async def capture_pod(shipment_id: str, payload: dict, actor: dict) -> dict:
    shp = await _get_shipment(shipment_id)
    if shp["status"] != "DELIVERED":
        raise ConflictError("POD only after delivery")
    pod = {"received_by": payload.get("received_by"),
           "document_id": payload.get("document_id"),
           "photo_ref": payload.get("photo_ref"),
           "notes": payload.get("notes"),
           "at": now_iso(), "by": actor}
    await transition("shipment", shipment_id, "shipments", "shipment_id",
                     "CLOSED", actor, reason="POD captured")
    await db.db.shipments.update_one({"shipment_id": shipment_id},
                                     {"$set": {"pod": pod}})
    await audit("SHIPMENT", shipment_id, "POD_CAPTURED", actor)
    return await _get_shipment(shipment_id)


# ------------------------------------------------------------------- carriers
CARRIER_TYPES = ["ROAD", "AIR", "SEA", "COURIER", "RIDER"]

async def create_carrier(payload: dict, actor: dict) -> dict:
    if payload.get("carrier_type") not in CARRIER_TYPES:
        raise ValidationFailed(f"carrier_type in {CARRIER_TYPES}")
    carrier_id = await _next_id("carrier", "CAR")
    doc = {
        "carrier_id": carrier_id,
        "name": payload["name"],
        "carrier_type": payload["carrier_type"],
        "contact": payload.get("contact", {}),
        "serviceable_zones": payload.get("serviceable_zones", []),
        "temperature_controlled": payload.get("temperature_controlled", False),
        "rating": payload.get("rating"),
        "on_time_pct": None,
        "status": "ACTIVE",
        "created_at": now_iso(),
    }
    await db.db.carriers.insert_one(doc)
    return _clean(doc)


async def list_carriers(status: Optional[str] = None) -> List[dict]:
    """List carriers with optional status filter."""
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    rows = []
    async for r in db.db.carriers.find(q).sort("created_at", -1).limit(500):
        rows.append(_clean(dict(r)))
    return rows


async def select_carrier(payload: dict, actor: dict) -> dict:
    """Logistics agent tool: pick best carrier (temperature + rating)."""
    q: Dict[str, Any] = {"status": "ACTIVE"}
    if payload.get("temperature_controlled"):
        q["temperature_controlled"] = True
    carriers = [_clean(dict(c)) async for c in db.db.carriers.find(q)]
    if not carriers:
        raise NotFound("No active carriers available")
    carriers.sort(key=lambda c: (-(c.get("rating") or 0)))
    return carriers[0]


async def estimate_eta(payload: dict, actor: dict) -> dict:
    """Deterministic ETA heuristic; AI may refine when available."""
    distance_km = float(payload.get("distance_km") or 100)
    avg_speed = float(payload.get("avg_speed_kmph") or 40)
    hours = distance_km / max(avg_speed, 1)
    eta = datetime.now(timezone.utc) + timedelta(hours=hours)
    return {"hours": round(hours, 1),
            "eta": eta.isoformat(),
            "method": "DETERMINISTIC_HEURISTIC"}


async def list_shipments(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in
            db.db.shipments.find(q).sort("created_at", -1).limit(200)]


async def get_shipment(shipment_id: str) -> dict:
    return await _get_shipment(shipment_id)


async def list_inbound(vendor_id: Optional[str] = None) -> List[dict]:
    q = {} if not vendor_id else {"vendor_id": vendor_id}
    return [_clean(dict(r)) async for r in db.db.asns.find(q).sort("created_at", -1).limit(200)]


async def _get_asn(asn_id: str) -> dict:
    doc = await db.db.asns.find_one({"asn_id": asn_id})
    if not doc:
        raise NotFound(f"ASN {asn_id} not found")
    return doc


async def _get_shipment(shipment_id: str) -> dict:
    doc = await db.db.shipments.find_one({"shipment_id": shipment_id})
    if not doc:
        raise NotFound(f"Shipment {shipment_id} not found")
    return doc
