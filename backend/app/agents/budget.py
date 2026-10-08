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
    "audit": 1.2,
    "redteam": 1.2,
    "executive": 2.0,
    "deep": 2.5,
}


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
            max_seconds=float(getattr(settings, "max_research_seconds", 300.0) or 300.0) * multiplier,
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
        """Would another operation fit the operational ceilings?"""
        if self.remaining_seconds <= 0.0:
            self._refuse(stage, "time budget exhausted")
            return False
        if self.llm_calls + calls > self.max_llm_calls:
            self._refuse(stage, f"call ceiling {self.max_llm_calls} reached")
            return False
        return True

    def afford_pass(self, sub_questions: int, avg_prompt_chars: int = 6000) -> bool:
        """Can one more research pass run within the call/time ceilings?"""
        return self.can_afford(stage="pass", calls=max(1, sub_questions) + 1)

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
