from __future__ import annotations

from tenacity import wait_exponential_jitter

from typing import Awaitable
from typing import Callable
import asyncio
import httpx
import re
from typing import Any, List



def _attempt_timeout(base: float) -> httpx.Timeout:
    """Per-attempt read-gap timeout (httpx 0.28 has no total cap)."""
    return httpx.Timeout(base)


async def _with_total_cap(coro_factory: Callable[[], Awaitable[Any]], base: float) -> Any:
    """Run one provider attempt under a HARD total wall-clock cap.

    read=base bounds the inter-chunk gap only. Without a total, a
    drip-feeding proxy (bytes every few seconds, never a 60s gap) holds a
    call open far past the intended budget — measured live: a 16-MINUTE
    planner call against a stalled proxy whose read gaps never tripped the
    read timeout. The wall-clock cap is base * 1.5 at asyncio level; the
    raised TimeoutError is classified as a timeout everywhere (fail-fast,
    breaker-recorded as a timeout)."""
    return await asyncio.wait_for(coro_factory(), timeout=base * 1.5)


def _real_key(value: Any) -> str:
    """Settings may carry example placeholders (your_...); treat those as
    unset so we skip the provider instead of burning calls on 401s."""
    text = str(value or "").strip()
    if not text or text.lower().startswith("your_"):
        return ""
    return text


def _retry_after_hint(exc: BaseException | None) -> float:
    """The provider's own retry hint, in seconds, or 0 when none given.

    Groq puts a numeric header OR a 'Please try again in X.XXs' body line
    on TPM-limit 429s; honoring the provider's number beats guessing."""
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response is None:
        return 0.0
    raw = exc.response.headers.get("retry-after") or ""
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    match = re.search(r"try again in ([0-9.]+)s", exc.response.text.lower())
    return float(match.group(1)) if match else 0.0


def _wait_with_retry_after(retry_state) -> float:
    """Honor the provider's Retry-After on 429s; exponential jitter otherwise.

    The first retry of a call may wait up to 20s when the provider says so:
    free-tier TPM windows are the difference between an LLM-written report
    and an extractive fallback, and a rolling per-minute window usually
    clears within one patient wait. Later retries cap at 3s so a sustained
    wall fails fast to the next provider instead of stalling the run."""
    outcome = getattr(retry_state, "outcome", None)
    exc = outcome.exception() if outcome is not None else None
    if (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response is not None
        and exc.response.status_code == 429
    ):
        delay = _retry_after_hint(exc)
        if delay:
            cap = 20.0 if getattr(retry_state, "attempt_number", 1) <= 1 else 3.0
            return min(max(delay, 1.0), cap)
    return wait_exponential_jitter(initial=0.4, max=3)(retry_state)


HF_FALLBACK_MODELS: List[str] = [
    "Qwen/Qwen2.5-7B-Instruct",
    "HuggingFaceH4/zephyr-7b-beta",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
]
