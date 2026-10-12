"""Worker pool for concurrent scraping.

Bounds how many pages are fetched at once and applies an optional global rate
limit shared across every pool in the process, so several concurrent research
sections cannot together overwhelm a rate-limited host.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager


class _GlobalRateLimiter:
    """A process-wide minimum interval between requests.

    A single instance is shared by all pools (see :func:`get_global_rate_limiter`)
    so the configured delay applies across concurrent researchers, not per pool.
    """

    def __init__(self) -> None:
        self._delay = 0.0
        self._last = 0.0
        self._lock = asyncio.Lock()

    def configure(self, delay: float) -> None:
        self._delay = max(0.0, float(delay or 0.0))

    async def wait_if_needed(self) -> None:
        if self._delay <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._delay - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


_global_limiter = _GlobalRateLimiter()


def get_global_rate_limiter() -> _GlobalRateLimiter:
    return _global_limiter


class WorkerPool:
    """Concurrency + rate limiting for one research section's scraping.

    ``max_workers`` caps simultaneous fetches within this pool; the global rate
    limiter caps request frequency across all pools.
    """

    def __init__(self, max_workers: int, rate_limit_delay: float = 0.0):
        self.max_workers = max(1, int(max_workers or 1))
        self.rate_limit_delay = rate_limit_delay
        self.semaphore = asyncio.Semaphore(self.max_workers)
        get_global_rate_limiter().configure(rate_limit_delay)

    @asynccontextmanager
    async def throttle(self):
        async with self.semaphore:
            await get_global_rate_limiter().wait_if_needed()
            yield
