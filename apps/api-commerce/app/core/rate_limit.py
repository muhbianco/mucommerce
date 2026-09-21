from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from app.core.exceptions import RateLimitedError
from app.core.redis import get_redis


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiter:
    """Fixed-window counter. Redis (INCR + EXPIRE, atomic via pipeline) or memory.

    Fixed window is enough for abuse protection at this scale and is trivially
    correct across workers. Keys carry the window start so a stale key can
    never leak into the next window.
    """

    def __init__(self) -> None:
        self._memory: dict[str, int] = defaultdict(int)

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = int(time.time())
        window_start = now - (now % window_seconds)
        bucket = f"ratelimit:{key}:{window_start}"
        retry_after = window_seconds - (now - window_start)

        redis = get_redis()
        if redis is not None:
            try:
                async with redis.pipeline(transaction=True) as pipe:
                    pipe.incr(bucket)
                    pipe.expire(bucket, window_seconds + 1)
                    count, _ = await pipe.execute()
            except Exception:
                # Redis down must not take the API down; fail open and log upstream.
                return RateLimitResult(True, limit, retry_after)
        else:
            self._memory[bucket] += 1
            count = self._memory[bucket]
            if len(self._memory) > 10_000:
                for stale in [k for k in self._memory if k.split(":")[-1] < str(window_start)]:
                    self._memory.pop(stale, None)

        allowed = int(count) <= limit
        return RateLimitResult(allowed, max(limit - int(count), 0), retry_after)


rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """Client address as seen by Traefik.

    Safe only because Traefik runs without `forwardedHeaders.insecure`/`trustedIPs`: it drops
    any X-Forwarded-For sent by the client and writes the real peer address. If a CDN or
    another proxy is ever put in front, trust only its hops here instead of the first entry.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(
    scope: str,
    limit: int,
    window_seconds: int,
    key_fn: Callable[[Request], str] | None = None,
) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency factory. Default key = client IP."""

    async def dependency(request: Request) -> None:
        key = key_fn(request) if key_fn else client_ip(request)
        result = await rate_limiter.hit(f"{scope}:{key}", limit, window_seconds)
        if not result.allowed:
            raise RateLimitedError(retry_after_seconds=result.retry_after_seconds, limit=limit)

    return dependency
