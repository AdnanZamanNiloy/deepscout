"""Research-run operational limits (wall-clock and call ceilings).

This module used to be a full cost/token governor with dollar pricing, model
price tables and affordability accounting. All of that was removed: the product
no longer tracks or caps spend, and the UI no longer reports cost.

What remains is what actually protects liveness and the 8GB/free-tier host:

* a per-run WALL-CLOCK ceiling, so a stalled provider cannot hold a run past
  the interactive deadline (the "provider-timeout trap" fix depends on it);
* a CALL ceiling, a coarse runaway guard on the fan-out (sub-questions x
  passes x agents).

Token counts are still recorded for observability (see `app.core.usage`), but
they are NOT a limit and carry no price.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


# Per-mode multiplier for the operational ceilings. Deep modes are allowed more
# wall-clock and more calls — that depth is the product — but the multiplier is
# explicit and bounded rather than emergent from a loop count.
MODE_LIMIT_MULTIPLIER: Dict[str, float] = {
    "quick": 0.35,
    "standard": 1.0,
    "deep": 2.5,
}


# Absolute per-mode wall-clock caps (seconds), independent of the deployment
# base. These are the "hard cap" from the mode profiles: what actually stops a
# stalled run. Set well above measured p90 (see bench/results/MODE_BASELINE.md:
# quick p90 98s, standard 77s, deep 41s) so a healthy run is never cut, but far
# below the old flat 1000s so a hang is caught in minutes, not tens of minutes.
MODE_HARD_CAP_SECONDS: Dict[str, float] = {
    "quick": 180.0,
    "standard": 360.0,
    "deep": 720.0,
}


def mode_timeout_seconds(settings: Any, mode: str) -> float:
    """The hard wall-clock cap for one run, chosen by its mode.

    Order of precedence:
      1. the mode's explicit hard cap (MODE_HARD_CAP_SECONDS), clamped to the
         deployment's absolute ``research_timeout_sec`` so an operator can
         still lower the global maximum;
      2. the deployment base scaled by the mode multiplier, for an unknown mode.

    One source of truth for "how long may this mode take": the route's
    ``asyncio.timeout`` and the budget's ``max_seconds`` both derive from this,
    so the enforced stop and the reported ceiling cannot disagree.
    """
    base = float(getattr(settings, "research_timeout_sec", 1000.0) or 1000.0)
    key = str(mode or "standard").lower()
    cap = MODE_HARD_CAP_SECONDS.get(key)
    if cap is not None:
        return min(base, cap)
    multiplier = MODE_LIMIT_MULTIPLIER.get(key, 1.0)
    return base * multiplier


@dataclass
class ResearchBudget:
    """Per-run operational ceilings: wall-clock and total LLM calls.

    Kept under the historical name so existing call sites do not change. No
    dollars, no tokens-as-a-limit, no pricing.
    """

    max_llm_calls: int = 60
    max_seconds: float = 300.0

    llm_calls: int = field(default=0, init=False)
    search_calls: int = field(default=0, init=False)
    spent_tokens: int = field(default=0, init=False)
    started_at: float = field(default_factory=time.monotonic, init=False)
    refusals: List[str] = field(default_factory=list, init=False)

    @classmethod
    def from_settings(cls, settings: Any, mode: str = "standard") -> "ResearchBudget":
        multiplier = MODE_LIMIT_MULTIPLIER.get(str(mode or "standard").lower(), 1.0)
        return cls(
            max_llm_calls=int(float(getattr(settings, "max_llm_calls", 60) or 60) * multiplier),
            # Same function the route's asyncio.timeout uses, so the ceiling the
            # governor reports is exactly the one that will stop the run.
            max_seconds=mode_timeout_seconds(settings, mode),
        )

    # -- state -------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed)

    @property
    def exhausted(self) -> bool:
        return self.llm_calls >= self.max_llm_calls or self.remaining_seconds <= 0.0

    def utilization(self) -> float:
        """Worst-case fraction consumed across the two ceilings (0-1)."""
        ratios = [
            self.llm_calls / self.max_llm_calls if self.max_llm_calls > 0 else 0.0,
            self.elapsed / self.max_seconds if self.max_seconds > 0 else 0.0,
        ]
        return round(min(1.0, max(ratios)), 4)

    # -- decisions ---------------------------------------------------------

    def can_afford(self, *, stage: str = "llm", calls: int = 1, **_ignored: Any) -> bool:
        """Would another operation fit the operational ceilings?

        This is the run's actual brake. It is called before every model call
        and every search batch; when it returns False the caller takes its
        deterministic fallback and the run keeps going on the work already
        done, rather than expanding without limit on a stalled or rate-limited
        provider.

        Refusals are recorded once per (stage, reason) so the trace can explain
        WHY a run stopped expanding — a silent cut-off reads as a broken run.
        """
        if self.remaining_seconds <= 0.0:
            self._refuse(stage, "time budget exhausted")
            return False
        if self.llm_calls + calls > self.max_llm_calls:
            self._refuse(stage, f"call ceiling {self.max_llm_calls} reached")
            return False
        return True

    def can_afford_search(self, count: int = 1) -> bool:
        """Search spend is counted separately from LLM calls.

        Search does not consume the LLM ceiling (a retrieval-heavy run must not
        be able to starve its own writers), but it is still bounded by the same
        wall-clock ceiling, which is what actually protects liveness on an 8GB
        host against a provider that hangs.
        """
        if self.remaining_seconds <= 0.0:
            self._refuse("search", "time budget exhausted")
            return False
        return True

    def _refuse(self, stage: str, reason: str) -> None:
        message = f"{stage}: {reason}"
        if message not in self.refusals:
            self.refusals.append(message)

    # -- accounting --------------------------------------------------------

    def record_llm(
        self,
        stage: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        **_ignored: Any,
    ) -> None:
        """Record one LLM call and its token counts (observability only)."""
        self.llm_calls += 1
        self.spent_tokens += max(0, int(input_tokens)) + max(0, int(output_tokens))

    def record_search(self, provider: str, stage: str = "search", count: int = 1) -> None:
        self.search_calls += max(1, count)

    # -- reporting ---------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        return {
            "spent_tokens": self.spent_tokens,
            "llm_calls": self.llm_calls,
            "max_llm_calls": self.max_llm_calls,
            "search_calls": self.search_calls,
            "elapsed_sec": round(self.elapsed, 2),
            "max_seconds": self.max_seconds,
            "utilization": self.utilization(),
            "exhausted": self.exhausted,
            "refusals": list(self.refusals),
        }
