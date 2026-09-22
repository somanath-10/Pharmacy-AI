"""Logistics: ASN (inbound), shipments (outbound), carriers, vehicles, POD."""
from datetime import datetime, timedelta
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
    eta = payload.get("eta") or (datetime.utcnow() + timedelta(days=3)).isoformat()
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
    return doc


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
    return await _get_shipment(shipment_id)


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
    eta = datetime.utcnow() + timedelta(hours=hours)
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
