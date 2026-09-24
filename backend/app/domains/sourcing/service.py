"""Strategic Sourcing: RFI/RFP/RFQ events, bids, reverse auctions, BRA
(sourcing_award_recommendation), contracts & BPAs."""
import json
from typing import Any, Dict, List, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import ConflictError, NotFound, ValidationFailed
from app.core.events import bus
from app.core.workflow import transition, record_node

EVENT_TYPES = ["RFI", "RFP", "RFQ"]


def _clean(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _next_id(name: str, prefix: str, width: int = 5) -> str:
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )
    return f"{prefix}-{int(doc['seq']):05d}"


async def create_event(payload: dict, actor: dict) -> dict:
    if payload.get("event_type") not in EVENT_TYPES:
        raise ValidationFailed(f"event_type must be in {EVENT_TYPES}")
    if not payload.get("lines"):
        raise ValidationFailed("Sourcing event needs lines")
    event_id = await _next_id("sourcing_event", payload["event_type"])
    doc = {
        "event_id": event_id,
        "event_type": payload["event_type"],
        "title": payload.get("title", event_id),
        "description": payload.get("description"),
        "lines": payload["lines"],
        "pr_id": payload.get("pr_id"),
        "invited_vendors": payload.get("invited_vendors", []),
        "bids": [],
        "auction_id": None,
        "award_recommendation": None,
        "contract_id": None,
        "evaluation": {"technical": {}, "commercial": {}},
        "status": "DRAFT",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "version": 1,
        "timeline": [{"state": "DRAFT", "actor": actor, "at": now_iso()}],
    }
    await db.db.sourcing_events.insert_one(doc)
    await bus.publish("rfq.created",
                      {"event_id": event_id, "event_type": payload["event_type"],
                       "lines": len(payload["lines"])}, actor)
    await audit("SOURCING_EVENT", event_id, "CREATED", actor, new_state="DRAFT")
    return _clean(doc)


async def publish_event(event_id: str, actor: dict) -> dict:
    evt = await _get(event_id)
    # only APPROVED vendors receive the RFQ
    approved = [_clean(dict(v)) async for v in db.db.vendors.find(
        {"vendor_id": {"$in": evt["invited_vendors"]}, "status": "APPROVED"})]
    approved_ids = [v["vendor_id"] for v in approved]
    await transition("sourcing_event", event_id, "sourcing_events", "event_id",
                     "PUBLISHED", actor,
                     extra_update={"$set": {"published_to": approved_ids}})
    for v in approved:
        from app.core.notifications import notify

        contact = v.get("contact", {}).get("email", "vendor@example.com")
        await notify("VENDOR_REMINDER_CREATED",
                     f"New {evt['event_type']}: {evt['title']}",
                     f"You are invited to bid on {event_id}. Submit bids in the vendor portal.",
                     [contact], "SOURCING_EVENT", event_id)
    await bus.publish("rfq.published",
                      {"event_id": event_id, "vendors": approved_ids}, actor)
    await record_node("sourcing", event_id, "rfq", f"{evt['event_type']} Published",
                      "DONE", actor)
    return await _get(event_id)


async def submit_bid(event_id: str, payload: dict, actor: dict) -> dict:
    evt = await _get(event_id)
    if evt["status"] not in ("PUBLISHED", "BIDDING"):
        raise ConflictError(f"Bids not accepted in status {evt['status']}")
    vendor_id = payload.get("vendor_id")
    if not vendor_id:
        raise ValidationFailed("bid needs vendor_id")
    vendor = await db.db.vendors.find_one({"vendor_id": vendor_id})
    if not vendor or vendor["status"] != "APPROVED":
        raise ValidationFailed("Only approved vendors can bid")
    if not payload.get("lines"):
        raise ValidationFailed("Bid needs lines")
    bid_id = await _next_id("bid", "BID")
    doc = {
        "bid_id": bid_id,
        "event_id": event_id,
        "vendor_id": vendor_id,
        "currency": payload.get("currency", "INR"),
        "lines": payload["lines"],
        "lead_time_days": payload.get("lead_time_days"),
        "payment_terms": payload.get("payment_terms"),
        "technical_score": None,
        "commercial_score": None,
        "total_amount": sum(float(l.get("amount") or
                                  (float(l.get("quantity") or 0) * float(l.get("unit_price") or 0)))
                            for l in payload["lines"]),
        "status": "RECEIVED",
        "received_at": now_iso(),
        "channel": actor.get("type", "USER"),
    }
    await db.db.bids.insert_one(doc)
    await db.db.sourcing_events.update_one(
        {"event_id": event_id},
        {"$push": {"bids": bid_id}, "$set": {"status": "BIDDING",
                                             "updated_at": now_iso()}},
    )
    await bus.publish("bid.received", {"event_id": event_id, "bid_id": bid_id,
                                       "vendor_id": vendor_id}, actor)
    await audit("BID", bid_id, "RECEIVED", actor, details={"event": event_id})
    return _clean(doc)


# ------------------------------------------------- evaluation (technical/commercial)
async def evaluate_bid(event_id: str, bid_id: str, kind: str, score: float,
                       notes: str, actor: dict) -> dict:
    """Technical or commercial evaluation of a bid (buyer/QA input to BRA)."""
    if kind not in ("technical", "commercial"):
        raise ValidationFailed("kind must be technical|commercial")
    if not (0 <= score <= 100):
        raise ValidationFailed("score must be 0-100")
    bid = await db.db.bids.find_one({"bid_id": bid_id, "event_id": event_id})
    if not bid:
        raise NotFound(f"Bid {bid_id} not found for event")
    field = "technical_score" if kind == "technical" else "commercial_score"
    await db.db.bids.update_one(
        {"bid_id": bid_id},
        {"$set": {field: float(score), f"{kind}_notes": notes,
                  f"{kind}_by": actor, f"{kind}_at": now_iso()}})
    await db.db.sourcing_events.update_one(
        {"event_id": event_id},
        {"$set": {f"evaluation.{kind}.{bid_id}": {"score": float(score),
                                                  "notes": notes}}})
    await audit("BID", bid_id, f"{kind.upper()}_EVALUATED", actor,
                details={"score": score})
    return _clean(await db.db.bids.find_one({"bid_id": bid_id}))


# ------------------------------------------------------------------- auctions
async def create_auction(event_id: str, payload: dict, actor: dict) -> dict:
    evt = await _get(event_id)
    if evt["status"] not in ("PUBLISHED", "BIDDING"):
        raise ConflictError("Auction requires a published event")
    auction_id = await _next_id("auction", "AUC")
    doc = {
        "auction_id": auction_id,
        "event_id": event_id,
        "start_price": float(payload.get("start_price") or 0),
        "decrement_step": float(payload.get("decrement_step") or 0),
        "reserve_price": float(payload.get("reserve_price") or 0),
        "duration_minutes": int(payload.get("duration_minutes", 30)),
        "participants": evt.get("bids", []),
        "bid_log": [],
        "status": "SCHEDULED",
        "created_at": now_iso(),
    }
    await db.db.auctions.insert_one(doc)
    await db.db.sourcing_events.update_one(
        {"event_id": event_id}, {"$set": {"auction_id": auction_id}}
    )
    return _clean(doc)


async def start_auction(auction_id: str, actor: dict) -> dict:
    await _get_auction(auction_id)
    await transition("auction", auction_id, "auctions", "auction_id", "LIVE", actor)
    return await _get_auction(auction_id)


async def auction_bid(auction_id: str, vendor_id: str, price: float, actor: dict) -> dict:
    """Deterministic reverse auction: single-writer decrement ladder.

    Rules are pure functions of the stored state (start price, reserve,
    decrement step) so replays are reproducible. Concurrency-safe: the new low
    price is applied with a conditional update on current_lowest."""
    auc = await _get_auction(auction_id)
    if auc["status"] != "LIVE":
        raise ConflictError("Auction not live")
    if price >= auc["start_price"]:
        raise ValidationFailed("Bid must be below start price")
    if auc.get("reserve_price") and price < auc["reserve_price"]:
        raise ValidationFailed("Below reserve price")
    step = auc.get("decrement_step") or 0
    last = auc["bid_log"][-1]["price"] if auc["bid_log"] else auc["start_price"]
    if step and (last - price) < step:
        raise ValidationFailed(f"Decrement must be at least {step}")
    if auc.get("current_lowest") is not None and price >= auc["current_lowest"]:
        raise ValidationFailed(
            f"Bid must beat current lowest {auc['current_lowest']}")
    res = await db.db.auctions.update_one(
        {"auction_id": auction_id,
         "current_lowest": {"$in": [None, auc.get("current_lowest")]}},
        {"$push": {"bid_log": {"vendor_id": vendor_id, "price": float(price),
                               "actor": actor, "at": now_iso()}},
         "$set": {"current_lowest": float(price)}})
    if res.modified_count == 0:
        raise ConflictError("Auction moved; re-bid against the new lowest price")
    return await _get_auction(auction_id)


async def close_auction(auction_id: str, actor: dict) -> dict:
    auc = await _get_auction(auction_id)
    await transition("auction", auction_id, "auctions", "auction_id", "COMPLETED", actor)
    winner = auc["bid_log"][-1] if auc["bid_log"] else None
    await db.db.auctions.update_one(
        {"auction_id": auction_id},
        {"$set": {"winner_vendor_id": winner["vendor_id"] if winner else None,
                  "winning_price": winner["price"] if winner else None}},
    )
    await bus.publish("auction.completed",
                      {"auction_id": auction_id,
                       "winner": winner["vendor_id"] if winner else None}, actor)
    return await _get_auction(auction_id)


async def recommend_award(event_id: str, payload: dict, actor: dict) -> dict:
    """BRA — stored generically as sourcing_award_recommendation.

    Ranking is deterministic (weighted multi-factor). AI *analyzes* price,
    freight, discount, credit terms, lead time, shelf life, quality,
    rejection, reliability and risk — it never decides the award."""
    evt = await _get(event_id)
    if not payload.get("bid_id"):
        raise ValidationFailed("Award recommendation needs winning bid_id")
    bid = await db.db.bids.find_one({"bid_id": payload["bid_id"]})
    if not bid or bid["event_id"] != event_id:
        raise NotFound("Bid not found for this event")

    all_bids = [_clean(dict(b)) async for b in db.db.bids.find({"event_id": event_id})]
    ranked = await _rank_bids_weighted(all_bids, evt,
                                       payload.get("weights"))
    rec_id = await _next_id("award_rec", "BRA")
    winner = next((r for r in ranked if r["bid_id"] == bid["bid_id"]), None)
    doc = {
        "rec_id": rec_id,
        "event_id": event_id,
        "bid_id": bid["bid_id"],
        "vendor_id": bid["vendor_id"],
        "total_amount": bid["total_amount"],
        "total_landed_cost": (winner or {}).get("total_landed_cost",
                                               bid["total_amount"]),
        "ranking": ranked,
        "weights": {**WEIGHTS_DEFAULT, **(payload.get("weights") or {})},
        "justification": payload.get("justification",
                                     f"Best weighted score among {len(all_bids)} bids "
                                     "(price, freight, discount, credit terms, lead "
                                     "time, shelf life, quality, rejection, "
                                     "reliability, risk)"),
        "status": "PROPOSED",
        "created_by": actor,
        "created_at": now_iso(),
    }
    doc["ai_analysis"] = await _ai_analyze_award(doc, all_bids)
    await db.db.sourcing_award_recommendation.insert_one(doc)
    await db.db.sourcing_events.update_one(
        {"event_id": event_id},
        {"$set": {"award_recommendation": rec_id, "updated_at": now_iso()}},
    )
    if evt["status"] in ("PUBLISHED", "BIDDING"):
        await transition("sourcing_event", event_id, "sourcing_events", "event_id",
                         "EVALUATION", actor)
    await transition("sourcing_event", event_id, "sourcing_events", "event_id",
                     "RECOMMENDATION", actor, reason=f"BRA {rec_id}")
    await bus.publish("award.recommended",
                      {"event_id": event_id, "rec_id": rec_id,
                       "vendor_id": bid["vendor_id"],
                       "bid_id": bid["bid_id"]}, actor)
    await audit("AWARD_RECOMMENDATION", rec_id, "PROPOSED", actor,
                details={"event": event_id, "bid": bid["bid_id"]})
    return doc


async def _ai_analyze_award(rec: dict, bids: List[dict]) -> dict:
    """AI consultation on the BRA (draft/advisory only; offline fallback)."""
    from app.core.ai_gateway import ai

    summary = {
        "winner": {"vendor": rec["vendor_id"], "amount": rec["total_amount"],
                   "landed": rec.get("total_landed_cost")},
        "ranking_top3": rec["ranking"][:3],
        "bid_count": len(bids),
    }
    res = await ai.extract_json(
        "You are a pharmaceutical sourcing analyst. Given an award recommendation "
        "summary with weighted factors (price, freight, discount, credit terms, "
        "lead time, shelf life, quality, rejection, reliability, risk), produce "
        'JSON {"risk_notes": str, "recommendation": str, "confidence": 0-100}. '
        "Do NOT re-decide the award; analyze only.",
        json.dumps(summary, default=str))
    if not res.get("ok"):
        lowest = min(bids, key=lambda b: float(b.get("total_amount") or 0)) if bids else {}
        price_gap = (round(100.0 * (float(rec["total_amount"])
                                    - float(lowest.get("total_amount") or 0))
                           / max(float(lowest.get("total_amount") or 1), 1), 2)
                     if bids else 0.0)
        return {"fallback": True,
                "risk_notes": ("Award is not the lowest price; gap vs lowest bid: "
                               f"{price_gap}%" if price_gap > 0
                               else "Award is the lowest-priced qualifying bid"),
                "recommendation": "Weighted score supports the recommended vendor",
                "confidence": 60}
    try:
        out = dict(res["data"])
        out["fallback"] = False
        return out
    except Exception:
        return {"fallback": True, "risk_notes": "AI parse failed",
                "recommendation": "See deterministic ranking", "confidence": 50}


def _rank_bids(bids: List[dict], evt: dict) -> List[dict]:
    """Deterministic 70/30 price/lead-time score; AI never decides awards."""
    if not bids:
        return []
    prices = [float(b["total_amount"]) for b in bids]
    leads = [float(b.get("lead_time_days") or 30) for b in bids]
    pmin, pmax = min(prices), max(prices)
    lmin, lmax = min(leads), max(leads)
    ranked = []
    for b in bids:
        p = float(b["total_amount"])
        l = float(b.get("lead_time_days") or 30)
        pscore = 100 if pmax == pmin else 100 * (pmax - p) / (pmax - pmin)
        lscore = 100 if lmax == lmin else 100 * (lmax - l) / (lmax - lmin)
        score = round(0.7 * pscore + 0.3 * lscore, 1)
        ranked.append({"bid_id": b["bid_id"], "vendor_id": b["vendor_id"],
                       "amount": p, "lead_time_days": l, "score": score})
    ranked.sort(key=lambda r: -r["score"])
    return ranked


# ------------------------------------------------------------------ BRA / award
WEIGHTS_DEFAULT = {
    "price": 0.35, "freight": 0.05, "discount": 0.05, "credit_terms": 0.05,
    "lead_time": 0.10, "shelf_life": 0.05, "quality": 0.15, "rejection": 0.05,
    "reliability": 0.10, "risk": 0.05,
}


async def _vendor_factors(vendor_id: str) -> dict:
    """Quality/reliability/risk factors from vendor master + performance."""
    v = await db.db.vendors.find_one({"vendor_id": vendor_id}) or {}
    perf = v.get("performance") or {}
    quality = perf.get("score") or 60.0
    rejection = perf.get("rejection_pct") or 0.0
    reliability = perf.get("otd_pct") or 70.0
    risk_map = {"LOW": 100.0, "MEDIUM": 60.0, "HIGH": 20.0}
    risk = risk_map.get(v.get("risk_level"), 50.0)
    return {"quality": quality, "rejection": max(0.0, 100.0 - rejection * 5),
            "reliability": reliability, "risk": risk}


def _norm(values: List[float], value: float, lower_is_better: bool) -> float:
    """Ratio normalization: best value = 100, others proportional (never 0
    just for being worse). Equal values all score 100. Prevents min-max
    normalization from amplifying small gaps into extreme poles."""
    if not values or max(values) == min(values):
        return 100.0
    v = float(value) if value else 0.0
    if lower_is_better:
        best = min(x for x in values if x > 0) or min(values)
        score = 100.0 * best / v if v > 0 else 0.0
    else:
        best = max(values)
        score = 100.0 * v / best if best > 0 else 100.0
    return round(max(0.0, min(100.0, score)), 2)


async def _rank_bids_weighted(bids: List[dict], evt: dict,
                              weights: Optional[dict] = None) -> List[dict]:
    """Deterministic weighted multi-factor ranking — never lowest price alone.

    Factors: price, freight, discount, credit terms, lead time, shelf life
    (expiry), quality score, rejection rate, reliability (OTD), risk.
    """
    if not bids:
        return []
    w = {**WEIGHTS_DEFAULT, **(weights or {})}
    prices = [float(b.get("total_amount") or 0) for b in bids]
    freights = [float(b.get("freight") or 0) for b in bids]
    discounts = [float(b.get("discount_pct") or 0) for b in bids]
    credits = [float(b.get("credit_terms_days") or 0) for b in bids]
    leads = [float(b.get("lead_time_days") or 30) for b in bids]
    shelfs = [float(b.get("shelf_life_months") or 0) for b in bids]
    vendor_factors = {b["vendor_id"]: await _vendor_factors(b["vendor_id"])
                      for b in bids}
    ranked = []
    for b in bids:
        f = vendor_factors[b["vendor_id"]]
        parts = {
            "price": _norm(prices, float(b.get("total_amount") or 0), True),
            "freight": _norm(freights, float(b.get("freight") or 0), True),
            "discount": _norm(discounts, float(b.get("discount_pct") or 0), False),
            "credit_terms": _norm(credits, float(b.get("credit_terms_days") or 0), False),
            "lead_time": _norm(leads, float(b.get("lead_time_days") or 30), True),
            "shelf_life": _norm(shelfs, float(b.get("shelf_life_months") or 0), False),
            "quality": f["quality"],
            "rejection": f["rejection"],
            "reliability": f["reliability"],
            "risk": f["risk"],
        }
        technical = b.get("technical_score")
        commercial = b.get("commercial_score")
        score = sum(w[k] * v for k, v in parts.items())
        if technical is not None:
            score = 0.7 * score + 0.3 * float(technical)  # technical gate blend
        if commercial is not None:
            score = 0.8 * score + 0.2 * float(commercial)
        amount = float(b.get("total_amount") or 0)
        landed = round(amount + float(b.get("freight") or 0)
                       - amount * float(b.get("discount_pct") or 0) / 100.0, 2)
        ranked.append({"bid_id": b["bid_id"], "vendor_id": b["vendor_id"],
                       "amount": amount,
                       "total_landed_cost": landed,
                       "lead_time_days": b.get("lead_time_days"),
                       "technical_score": technical,
                       "commercial_score": commercial,
                       "factors": parts, "score": round(score, 2),
                       "status": b.get("status", "RECEIVED")})
    ranked.sort(key=lambda r: -r["score"])
    return ranked


async def approve_award(rec_id: str, actor: dict, reason: str = "") -> dict:
    rec = await db.db.sourcing_award_recommendation.find_one({"rec_id": rec_id})
    if not rec:
        raise NotFound(f"Award recommendation {rec_id} not found")
    # Strategic award → human decision queue
    amount = float(rec.get("total_amount") or 0)
    from app.core.policies import get_rule

    strategic_limit = float(await get_rule("strategic_award_limit", 500000))
    if amount > strategic_limit:
        from app.core.approvals import create_approval
        from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

        approval_id = await create_approval(
            category="STRATEGIC",
            title=f"Sourcing award approval: {rec_id} (amount {amount})",
            entity_type="AWARD_RECOMMENDATION", entity_id=rec_id,
            requested_by=actor,
            evidence={"ranking": rec.get("ranking"),
                      "justification": rec.get("justification"),
                      "amount": amount},
            options=["APPROVE", "REJECT"],
            authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
            on_approve="sourcing_award_approve",
            payload={"rec_id": rec_id},
        )
        return {"approval_id": approval_id, "status": "PENDING_APPROVAL"}
    return await _do_award({"rec_id": rec_id}, actor)


async def _do_award(payload: dict, actor: dict) -> dict:
    rec = await db.db.sourcing_award_recommendation.find_one({"rec_id": payload["rec_id"]})
    await _get(rec["event_id"])
    await transition("sourcing_event", rec["event_id"], "sourcing_events", "event_id",
                     "AWARDED", actor, reason=f"Award to {rec['vendor_id']}")
    await db.db.sourcing_award_recommendation.update_one(
        {"rec_id": rec["rec_id"]}, {"$set": {"status": "APPROVED",
                                             "approved_by": actor,
                                             "approved_at": now_iso()}}
    )
    await bus.publish("award.approved",
                      {"event_id": rec["event_id"], "vendor_id": rec["vendor_id"],
                       "rec_id": rec["rec_id"]}, actor)
    await record_node("sourcing", rec["event_id"], "award", "Award Approved",
                      "DONE", actor)
    rec = _clean(rec)
    rec["status"] = "APPROVED"
    return rec


async def create_contract(event_id: Optional[str], payload: dict, actor: dict) -> dict:
    contract_id = await _next_id("contract", "CTR")
    doc = {
        "contract_id": contract_id,
        "type": payload.get("type", "CONTRACT"),  # CONTRACT | BPA
        "vendor_id": payload["vendor_id"],
        "event_id": event_id,
        "title": payload.get("title", contract_id),
        "valid_from": payload.get("valid_from", now_iso()[:10]),
        "valid_to": payload.get("valid_to"),
        "currency": payload.get("currency", "INR"),
        "price_items": payload.get("price_items", []),
        "payment_terms": payload.get("payment_terms"),
        "min_commitment": payload.get("min_commitment"),
        "auto_release": payload.get("auto_release", True),
        "status": "ACTIVE",
        "created_by": actor,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.db.contracts.insert_one(doc)
    if event_id:
        evt = await _get(event_id)
        if evt["status"] == "AWARDED":
            await transition("sourcing_event", event_id, "sourcing_events",
                             "event_id", "CONTRACTED", actor,
                             reason=f"Contract {contract_id}")
    await bus.publish("contract.created",
                      {"contract_id": contract_id, "vendor_id": doc["vendor_id"]}, actor)
    await audit("CONTRACT", contract_id, "CREATED", actor, new_state="ACTIVE")
    return doc


async def list_contracts(vendor_id: Optional[str] = None) -> List[dict]:
    q = {} if not vendor_id else {"vendor_id": vendor_id}
    return [_clean(dict(r)) async for r in db.db.contracts.find(q)]


async def active_contract_for(product_id: str, vendor_id: Optional[str] = None) -> Optional[dict]:
    q: Dict[str, Any] = {"status": "ACTIVE",
                         "price_items.product_id": product_id}
    if vendor_id:
        q["vendor_id"] = vendor_id
    doc = await db.db.contracts.find_one(q, sort=[("created_at", -1)])
    return _clean(doc) if doc else None


async def contract_release(contract_id: str, lines: List[dict], payload: dict,
                           actor: dict) -> dict:
    """Release a PO directly against a contract/BPA."""
    from app.domains.procurement.service import create_po

    contract = await db.db.contracts.find_one({"contract_id": contract_id})
    if not contract or contract["status"] != "ACTIVE":
        raise NotFound(f"Active contract {contract_id} not found")
    for l in lines:
        item = next((p for p in contract.get("price_items", [])
                     if p.get("product_id") == l.get("sku")), None)
        if item:
            l.setdefault("unit_price", item["price"])
    payload = {**payload, "vendor_id": contract["vendor_id"],
               "contract_id": contract_id, "lines": lines}
    return await create_po(payload, actor)


async def _get(event_id: str) -> dict:
    doc = await db.db.sourcing_events.find_one({"event_id": event_id})
    if not doc:
        raise NotFound(f"Sourcing event {event_id} not found")
    return _clean(doc)


async def _get_auction(auction_id: str) -> dict:
    doc = await db.db.auctions.find_one({"auction_id": auction_id})
    if not doc:
        raise NotFound(f"Auction {auction_id} not found")
    return _clean(doc)


async def get_event(event_id: str) -> dict:
    return await _get(event_id)


async def list_events(status: Optional[str] = None) -> List[dict]:
    q = {} if not status else {"status": status}
    return [_clean(dict(r)) async for r in db.db.sourcing_events.find(q).limit(200)]


async def list_bids(event_id: str) -> List[dict]:
    return [_clean(dict(r)) async for r in db.db.bids.find({"event_id": event_id})]


async def rank_bids(event_id: str, weights: Optional[dict] = None) -> dict:
    """Public deterministic bid comparison (agent tool + UI BRA preview)."""
    evt = await _get(event_id)
    bids = [_clean(dict(b)) async for b in db.db.bids.find({"event_id": event_id})]
    ranked = await _rank_bids_weighted(bids, evt, weights)
    return {"event_id": event_id, "weights": {**WEIGHTS_DEFAULT, **(weights or {})},
            "ranking": ranked}
