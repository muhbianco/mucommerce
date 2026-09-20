from __future__ import annotations

import json
import time
from typing import Any

from app.core.redis import get_redis


class TtlCache:
    """Small JSON cache: Redis when configured, process memory otherwise.

    Used for tenant resolution by host. Values are plain JSON-serialisable dicts.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._memory: dict[str, tuple[float, Any]] = {}

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> Any | None:
        redis = get_redis()
        if redis is not None:
            try:
                raw = await redis.get(self._key(key))
            except Exception:
                raw = None
            return json.loads(raw) if raw else None
        entry = self._memory.get(self._key(key))
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._memory.pop(self._key(key), None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        redis = get_redis()
        if redis is not None:
            try:
                await redis.set(self._key(key), json.dumps(value, default=str), ex=ttl_seconds)
            except Exception:
                return
            return
        self._memory[self._key(key)] = (time.monotonic() + ttl_seconds, value)

    async def delete(self, key: str) -> None:
        redis = get_redis()
        if redis is not None:
            try:
                await redis.delete(self._key(key))
            except Exception:
                return
            return
        self._memory.pop(self._key(key), None)

    def clear_memory(self) -> None:
        self._memory.clear()
