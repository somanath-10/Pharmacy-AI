"""Human-readable sequential IDs (PO-2026-00001) via atomic Mongo counters."""
import asyncio

from app.core.database import db, utcnow

_lock = asyncio.Lock()

_PREFIX_MONTHS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}


async def next_id(name: str, prefix: str | None = None, width: int = 5) -> str:
    """Atomic counter: counters.find_one_and_update({name}, {$inc}).
    Falls back to in-process lock when a transaction is not available."""
    coll = db.db.counters
    async with _lock:
        doc = await coll.find_one_and_update(
            {"name": name},
            {"$inc": {"seq": 1}, "$set": {"updated_at": utcnow()}},
            upsert=True,
            return_document=True,
        )
        seq = int(doc["seq"])
    year = utcnow().year
    p = prefix if prefix is not None else name.upper()
    return f"{p}-{year}-{seq:0{width}d}"


async def next_movement_id() -> str:
    return await next_id("movement", "MOV", 6)


async def peek_seq(name: str) -> int:
    doc = await db.db.counters.find_one({"name": name})
    return int(doc["seq"]) if doc else 0
