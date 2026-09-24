"""Event Engine: publish domain events → outbox collection → local handlers.

V1 uses a MongoDB transactional outbox (no external broker). The API process
runs a background pump; handlers are idempotent functions registered per event
name. Events are also mirrored into `events` for the activity stream.
"""
import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.core.database import db, now_iso, utcnow

log = logging.getLogger("pharmaos.events")

Handler = Callable[[Dict[str, Any]], Awaitable[None]]


class EventBus:
    def __init__(self):
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)
        self._pump_task: Optional[asyncio.Task] = None
        self._running = False

    def subscribe(self, event_name: str, handler: Handler):
        self._handlers[event_name].append(handler)

    def on(self, event_name: str):
        def decorator(fn: Handler):
            self.subscribe(event_name, fn)
            return fn

        return decorator

    async def publish(self, name: str, payload: Dict[str, Any], actor: dict | None = None):
        """Persist event in outbox (survives restart), then dispatch inline."""
        event = {
            "name": name,
            "payload": payload,
            "actor": actor or {"type": "SYSTEM", "id": "platform"},
            "created_at": now_iso(),
        }
        await db.db.outbox_events.insert_one(
            {
                "name": name,
                "payload": payload,
                "actor": event["actor"],
                "status": "PENDING",
                "attempts": 0,
                "created_at": now_iso(),
                "processed_at": None,
                "last_error": None,
            }
        )
        # Mirror into activity stream for UI timelines
        await db.db.events.insert_one(event)
        # Inline dispatch is best-effort: the outbox pump remains the durable
        # path. Claim the row FIRST so the pump can never double-dispatch the
        # same event after we've run handlers here (P0 7.1).
        claimed = await db.db.outbox_events.find_one_and_update(
            {"name": name, "payload": payload, "status": "PENDING"},
            {"$set": {"status": "PROCESSING", "claimed_at": now_iso()}},
        )
        if claimed is None:
            return  # pump already owns this event
        try:
            await self._dispatch(name, payload, event["actor"])
        except Exception:
            # release for pump retry with the error recorded — never DONE
            await db.db.outbox_events.update_one(
                {"_id": claimed["_id"], "status": "PROCESSING"},
                {"$set": {"status": "PENDING", "claimed_at": None,
                          "last_error": "inline dispatch failure"}})
            return
        await db.db.outbox_events.update_one(
            {"_id": claimed["_id"], "status": "PROCESSING"},
            {"$set": {"status": "DONE", "processed_at": now_iso()},
             "$inc": {"attempts": 1}})

    async def _dispatch(self, name: str, payload: dict, actor: dict):
        """Run handlers, propagating failures (P0 7.2).

        A handler exception must reach the caller so publish()/pump_once()
        can mark the outbox row retryable instead of DONE. Handlers that
        must never break the caller should catch their own errors.
        """
        for handler in self._handlers.get(name, []):
            await handler(payload)

    async def pump_once(self, limit: int = 200) -> int:
        """Process pending outbox rows with atomic claiming.

        Every claim is a find_and_modify: PROCESSING rows are invisible to
        other workers, so two API processes can never double-dispatch the
        same event (duplicate GRNs / payments / stock movements protection).
        Stale claims (crashed worker) are reclaimed after 60s.
        """
        from datetime import timedelta

        stale_cutoff = (utcnow() - timedelta(seconds=60)).isoformat()
        processed = 0
        for _ in range(limit):
            row = await db.db.outbox_events.find_one_and_update(
                {"$or": [
                    {"status": "PENDING"},
                    {"status": "PROCESSING",
                     "claimed_at": {"$lt": stale_cutoff}},
                ]},
                {"$set": {"status": "PROCESSING", "claimed_at": now_iso()}},
                sort=[("created_at", 1)],
            )
            if not row:
                break
            try:
                await self._dispatch(row["name"], row["payload"],
                                     row.get("actor"))
            except Exception as e:
                # Handler failure must NOT be marked DONE (P0 7.2): retry with
                # backoff until the attempt cap, then FAILED (DLQ-able).
                attempts = row.get("attempts", 0) + 1
                status = "FAILED" if attempts >= 5 else "PENDING"
                await db.db.outbox_events.update_one(
                    {"_id": row["_id"]},
                    {"$set": {"status": status, "last_error": str(e)[:500]},
                     "$inc": {"attempts": 1}},
                )
                processed += 1
                continue
            await db.db.outbox_events.update_one(
                {"_id": row["_id"], "status": "PROCESSING"},
                {"$set": {"status": "DONE", "processed_at": now_iso()},
                 "$inc": {"attempts": 1}},
            )
            processed += 1
        return processed

    async def start_pump(self):
        if self._running:
            return
        self._running = True

        async def _loop():
            while self._running:
                try:
                    await self.pump_once()
                except Exception:
                    log.exception("outbox pump error")
                await asyncio.sleep(2)

        self._pump_task = asyncio.create_task(_loop())

    async def stop_pump(self):
        self._running = False
        if self._pump_task:
            self._pump_task.cancel()
            self._pump_task = None


bus = EventBus()


def subscribe_many(mapping: Dict[str, List[Handler]]):
    for name, handlers in mapping.items():
        for h in handlers:
            bus.subscribe(name, h)
