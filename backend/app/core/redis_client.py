"""Redis client with in-process fallback (cache, locks, rate-limit counters)."""
import asyncio
import json
import time
from typing import Any, Optional

from app.core.config import settings


class MemoryCache:
    """Single-process fallback when Redis is unavailable (dev/test)."""

    def __init__(self):
        self._data: dict = {}
        self._expiry: dict = {}
        self._locks: dict = {}
        self._counters: dict = {}

    async def get(self, key: str) -> Optional[str]:
        if key in self._expiry and time.time() > self._expiry[key]:
            self._data.pop(key, None)
            self._expiry.pop(key, None)
        return self._data.get(key)

    async def set(self, key: str, value: str, ttl: Optional[int] = None):
        self._data[key] = value
        if ttl:
            self._expiry[key] = time.time() + ttl

    async def delete(self, key: str):
        self._data.pop(key, None)

    async def acquire_lock(self, name: str, ttl: int = 30) -> bool:
        if name in self._locks and time.time() < self._locks[name]:
            return False
        self._locks[name] = time.time() + ttl
        return True

    async def release_lock(self, name: str):
        self._locks.pop(name, None)

    async def incr(self, key: str, ttl: int = 60) -> int:
        now = time.time()
        window_key = f"{key}:{int(now // ttl)}"
        self._counters[window_key] = self._counters.get(window_key, 0) + 1
        return self._counters[window_key]


class CacheClient:
    """Redis if REDIS_URL set, else in-process. Same async interface either way."""

    def __init__(self):
        self._redis = None
        self._memory = MemoryCache()

    async def connect(self):
        if settings.REDIS_URL:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(
                    settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2
                )
                await self._redis.ping()
            except Exception:
                self._redis = None

    @property
    def is_redis(self) -> bool:
        return self._redis is not None

    async def get(self, key: str) -> Optional[str]:
        if self._redis:
            try:
                return await self._redis.get(key)
            except Exception:
                return await self._memory.get(key)
        return await self._memory.get(key)

    async def set(self, key: str, value: str, ttl: Optional[int] = None):
        if self._redis:
            try:
                await self._redis.set(key, value, ex=ttl)
                return
            except Exception:
                pass
        await self._memory.set(key, value, ttl)

    async def delete(self, key: str):
        if self._redis:
            try:
                await self._redis.delete(key)
                return
            except Exception:
                pass
        await self._memory.delete(key)

    async def get_json(self, key: str) -> Optional[Any]:
        raw = await self.get(key)
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                return None
        return None

    async def set_json(self, key: str, value: Any, ttl: Optional[int] = None):
        await self.set(key, json.dumps(value, default=str), ttl)

    async def acquire_lock(self, name: str, ttl: int = 30) -> bool:
        if self._redis:
            try:
                return bool(await self._redis.set(f"lock:{name}", "1", nx=True, ex=ttl))
            except Exception:
                pass
        return await self._memory.acquire_lock(name, ttl)

    async def release_lock(self, name: str):
        if self._redis:
            try:
                await self._redis.delete(f"lock:{name}")
                return
            except Exception:
                pass
        await self._memory.release_lock(name)

    async def incr_window(self, key: str, ttl: int = 60) -> int:
        if self._redis:
            try:
                n = await self._redis.incr(key)
                if n == 1:
                    await self._redis.expire(key, ttl)
                return n
            except Exception:
                pass
        return await self._memory.incr(key, ttl)


cache = CacheClient()


class DistributedLock:
    """Async context manager for named locks."""

    def __init__(self, name: str, ttl: int = 30):
        self.name = name
        self.ttl = ttl
        self.acquired = False

    async def __aenter__(self):
        for _ in range(20):
            self.acquired = await cache.acquire_lock(self.name, self.ttl)
            if self.acquired:
                return self
            await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        if self.acquired:
            await cache.release_lock(self.name)
