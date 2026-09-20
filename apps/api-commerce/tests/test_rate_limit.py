from __future__ import annotations

from app.core.rate_limit import RateLimiter


async def test_memory_limiter_blocks_after_limit() -> None:
    limiter = RateLimiter()
    results = [await limiter.hit("k", limit=3, window_seconds=60) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].retry_after_seconds > 0
    assert results[0].remaining == 2


async def test_memory_limiter_isolates_keys() -> None:
    limiter = RateLimiter()
    await limiter.hit("a", limit=1, window_seconds=60)
    assert (await limiter.hit("a", limit=1, window_seconds=60)).allowed is False
    assert (await limiter.hit("b", limit=1, window_seconds=60)).allowed is True
