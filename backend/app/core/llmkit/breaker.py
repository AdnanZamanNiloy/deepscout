from __future__ import annotations

import time
from app.core.logging import get_logger

logger = get_logger(__name__)


logger = get_logger(__name__)


class CircuitBreaker:
    """Per-provider failure counter with a cooldown window.

    After `threshold` consecutive failures the provider is skipped for
    `cooldown_sec` instead of being retried on every call.
    """

    def __init__(self, threshold: int = 3, cooldown_sec: float = 60.0):
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if (time.monotonic() - self._opened_at) >= self.cooldown_sec:
            # Cooldown elapsed — allow one probe attempt through.
            self._reset()
            return False
        return True

    def record_success(self) -> None:
        self._reset()

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.warning(
                "[LLM] circuit breaker OPEN after %d consecutive failures (cooldown %.0fs)",
                self._consecutive_failures,
                self.cooldown_sec,
            )

    def record_timeout(self) -> None:
        """Open immediately on a single per-attempt timeout. A timeout
        already burned llm_timeout_sec (25s) of the 90s research budget,
        so waiting for `threshold` failures like fast errors do would let
        one slow provider eat the whole run — including re-runs on every
        outer generate_json retry. Cooldown still re-probes afterwards."""
        self._consecutive_failures += 1
        if self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.warning(
                "[LLM] circuit breaker OPEN after timeout (cooldown %.0fs)",
                self.cooldown_sec,
            )

    def record_rate_limit(self) -> None:
        """A 429 is a THROTTLE, not an outage: it must not open the cooldown
        breaker. The provider is reachable and a rolling per-minute window
        usually clears within the tenacity retry's own backoff (up to 20s on
        the first retry). Counting it toward the breaker's threshold made a
        burst of free-tier 429s skip a healthy provider for a full 60s and
        degrade the run — exactly the conflation of "throttled" with "down"
        this class must avoid. A short per-provider cooldown would still add
        a second wait on top of tenacity's, so this only logs; the retry
        policy + caller ladders own the back-off."""
        logger.warning(
            "[LLM] provider rate-limited (429); not opening breaker — "
            "retry/backoff owns the wait"
        )

    def _reset(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
