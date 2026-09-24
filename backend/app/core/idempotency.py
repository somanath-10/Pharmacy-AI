"""Idempotency: critical writes are safe to retry, race-free.

Ownership is claimed atomically via the unique index on `key`:
  - first caller inserts IN_FLIGHT and proceeds
  - concurrent duplicate gets DuplicateKeyError and an explicit
    InProgress response (never a silent None)
  - COMPLETED keys replay the stored result
  - FAILED keys may be reclaimed exactly once (conditional update —
    two concurrent reclaimers race on the same status guard, so only
    one wins the modified_count==1 update)
Keys expire after IDEMPOTENCY_TTL_HOURS (TTL index on expires_at in
ensure_indexes). Transitions are status-guarded:
  IN_FLIGHT --complete--> COMPLETED   (only if still IN_FLIGHT or FAILED)
  IN_FLIGHT --fail-----> FAILED      (only if still IN_FLIGHT)
so a straggler can never clobber a COMPLETED result.
"""
from datetime import timedelta
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.database import db, now_iso, utcnow
from app.core.errors import DomainError, IdempotencyConflict


class IdempotencyInProgress(DomainError):
    status_code = 409
    code = "IDEMPOTENCY_IN_PROGRESS"


def _expiry():
    return utcnow() + timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS)


async def begin(key: str, request_digest: str = "", owner: str = "") -> dict:
    """Atomically claim `key`. Returns first_time/result/status."""
    if not key:
        return {"first_time": True, "result": None, "status": "PASSTHRU"}
    now = utcnow()
    doc = {
        "key": key,
        "digest": request_digest,
        "result": None,
        "status": "IN_FLIGHT",
        "owner": owner,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": _expiry(),
    }
    try:
        # unique index on `key` makes this the atomic claim
        await db.db.idempotency_keys.insert_one(doc)
        return {"first_time": True, "result": None, "status": "IN_FLIGHT"}
    except DuplicateKeyError:
        existing = await db.db.idempotency_keys.find_one({"key": key})
        if not existing:  # extremely unlikely race; treat as first
            return await begin(key, request_digest, owner)
        if (request_digest and existing.get("digest")
                and existing["digest"] != request_digest):
            raise IdempotencyConflict(
                f"Idempotency key {key} reused with different payload")
        status = existing.get("status")
        if status == "COMPLETED":
            return {"first_time": False, "result": existing.get("result"),
                    "status": "COMPLETED"}
        if status == "IN_FLIGHT":
            # explicit in-progress signal — caller must not silently proceed
            raise IdempotencyInProgress(
                f"Operation {key} is already in progress; retry shortly")
        # FAILED (or legacy doc) → conditional reclaim, exactly one winner
        res = await db.db.idempotency_keys.update_one(
            {"key": key, "status": {"$in": ["FAILED", None, ""]}},
            {"$set": {"status": "IN_FLIGHT", "owner": owner,
                      "digest": request_digest, "result": None,
                      "updated_at": now.isoformat(),
                      "expires_at": _expiry()}})
        if res.modified_count == 1:
            return {"first_time": True, "result": None, "status": "IN_FLIGHT"}
        raise IdempotencyInProgress(
            f"Operation {key} is being retried by another request")


async def complete(key: str, result: Any):
    """Status-guarded COMPLETED transition (idempotent, race-safe)."""
    if not key:
        return
    await db.db.idempotency_keys.update_one(
        {"key": key, "status": {"$in": ["IN_FLIGHT", "FAILED"]}},
        {"$set": {"result": result, "status": "COMPLETED",
                  "completed_at": now_iso(), "updated_at": now_iso(),
                  "expires_at": _expiry()}})


async def fail(key: str, error: str = ""):
    """Status-guarded FAILED transition — key retained for digest-conflict
    detection, retryable. Never downgrades a COMPLETED result."""
    if not key:
        return
    await db.db.idempotency_keys.update_one(
        {"key": key, "status": "IN_FLIGHT"},
        {"$set": {"status": "FAILED", "error": str(error)[:300],
                  "updated_at": now_iso()}})


class idempotent:
    """Async context manager: claim → work → complete/fail.

        async with idempotent("GRN", key) as gate:
            if not gate["first_time"]:
                return gate["result"]          # replay of completed result
            result = ...do work...
            gate.store(result)
            return result

    On exception the key is marked FAILED (retryable), never deleted, so a
    different-payload retry still trips the digest conflict check.
    """

    class _Gate(dict):
        def __init__(self, state: dict, cm_key: Optional[str]):
            super().__init__(state)
            self._cm_key = cm_key

        def store(self, result):
            self["result"] = result
            return result

    def __init__(self, scope: str, key: Optional[str], request_digest: str = "",
                 owner: str = ""):
        self.scope = scope
        self.key = f"{scope}:{key}" if key else None
        self.digest = request_digest
        self.owner = owner
        self._gate = None

    async def __aenter__(self):
        self.state = await begin(self.key, self.digest, self.owner)
        self._gate = idempotent._Gate(self.state, self.key)
        return self._gate

    async def __aexit__(self, exc_type, exc, tb):
        if not self.key:
            return False
        if exc_type is not None:
            await fail(self.key, str(exc))
        elif self._gate.get("result") is not None:
            await complete(self.key, self._gate["result"])
        else:
            # block exited without storing a result: treat as failed so the
            # next attempt reclaims instead of replaying None forever
            await fail(self.key, "no result stored")
        return False
