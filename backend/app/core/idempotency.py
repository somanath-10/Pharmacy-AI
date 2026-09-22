"""Idempotency: critical writes are safe to retry.

Usage:
    async with idempotent("GRN", key) as (first_time, prior_result):
        if not first_time: return prior_result
        ... do work ...
        await store_result(...)
"""
from typing import Any, Optional

from app.core.database import db, now_iso
from app.core.errors import IdempotencyConflict


async def begin(key: str, request_digest: str = "") -> dict:
    """Returns {'first_time': bool, 'result': prior result or None}."""
    if not key:
        return {"first_time": True, "result": None}
    existing = await db.db.idempotency_keys.find_one({"key": key})
    if existing:
        if request_digest and existing.get("digest") and existing["digest"] != request_digest:
            raise IdempotencyConflict(
                f"Idempotency key {key} reused with different payload",
            )
        return {"first_time": False, "result": existing.get("result")}
    await db.db.idempotency_keys.insert_one(
        {"key": key, "digest": request_digest, "result": None,
         "status": "IN_FLIGHT", "created_at": now_iso()}
    )
    return {"first_time": True, "result": None}


async def complete(key: str, result: Any):
    if not key:
        return
    await db.db.idempotency_keys.update_one(
        {"key": key},
        {"$set": {"result": result, "status": "COMPLETED", "completed_at": now_iso()}},
    )


async def release(key: str):
    if not key:
        return
    await db.db.idempotency_keys.delete_one({"key": key})


class idempotent:
    """Async context manager wrapper around begin/complete/release.

    The gate object returned by __aenter__ is a mutable dict with helpers:
        async with idempotent("GRN", key) as gate:
            if not gate["first_time"]:
                return gate["result"]
            result = ...do work...
            gate.store(result)   # persisted for replay on retry
            return result
    Failure (exception) releases the key so the operation can be retried.
    """

    class _Gate(dict):
        def __init__(self, state: dict, cm_key: Optional[str]):
            super().__init__(state)
            self._cm_key = cm_key

        def store(self, result):
            self["result"] = result
            return result

    def __init__(self, scope: str, key: Optional[str], request_digest: str = ""):
        self.scope = scope
        self.key = f"{scope}:{key}" if key else None
        self.digest = request_digest
        self._gate = None

    async def __aenter__(self):
        self.state = await begin(self.key, self.digest)
        self._gate = idempotent._Gate(self.state, self.key)
        return self._gate

    async def __aexit__(self, exc_type, exc, tb):
        if self.key and exc_type is not None:
            await release(self.key)  # allow retry after failure
        elif self.key and self._gate is not None and self._gate.get("result") is not None:
            await complete(self.key, self._gate["result"])
        return False
