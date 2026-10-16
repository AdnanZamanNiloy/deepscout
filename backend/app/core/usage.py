"""Per-run usage ledger: token/call counts and wall-clock for one research run.

The LLMClient is app-scoped (one instance per process), but usage is per-run.
Threading a ledger object through every agent signature would touch a dozen
call sites and the LangGraph state schema; instead the ledger rides a
ContextVar — the same pattern degradation flags already use — so the LLM
client, the search layer and the API routes can all read the same per-run
accounting with zero signature changes.

Cost and budget accounting were removed: the ledger reports OPERATIONAL counts
(LLM calls, tokens, wall-clock, cache hits) for observability only. No prices,
no dollar figures, no spend ceilings.

Starlette serves each request inside its own task context, so concurrent
research runs never see each other's ledgers.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any, Dict, Optional

from app.infra.budget import ResearchBudget
from app.infra.ladder import DegradationLadder, soft_limit_for
from app.common.logging import get_logger

logger = get_logger(__name__)


class RunUsage:
    """One research run's usage: operational limits + cache counters + timing."""

    __slots__ = (
        "request_id", "budget", "mode", "started_at",
        "cache_hits", "cache_misses", "search_calls",
        "stage_hint", "ladder", "database_url",
    )

    def __init__(
        self,
        request_id: str,
        budget: ResearchBudget,
        mode: str = "standard",
        database_url: str = "",
    ):
        self.request_id = request_id
        self.budget = budget
        self.mode = mode
        # Where this run's checkpoint is written, if any. Empty means "no
        # durable checkpoint" (tests, bench, a bare run) and checkpointing is
        # simply skipped rather than failing.
        self.database_url = str(database_url or "")
        self.started_at = time.time()
        self.cache_hits = 0
        self.cache_misses = 0
        self.search_calls = 0
        self.stage_hint = ""
        # The steering wheel in front of the budget brake: decides what
        # OPTIONAL work to shed before the hard cap, in a fixed order. Shares
        # the run's clock so its decisions and the budget's refusal agree.
        self.ladder = DegradationLadder(
            soft_limit_s=soft_limit_for(budget.max_seconds, mode),
            hard_cap_s=budget.max_seconds,
            mode=mode,
        )

    # -- recording (called from llm.py / search.py) ------------------------

    def record_llm(
        self,
        stage: str,
        prompt: str = "",
        completion: str = "",
        *,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        model: Optional[str] = None,
        cached: bool = False,
    ) -> None:
        """Record one LLM call and its approximate token counts."""
        self.budget.record_llm(
            stage,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
        )
        if cached:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def record_search(self, provider: str, stage: str = "search", count: int = 1) -> None:
        self.search_calls += max(1, count)
        self.budget.record_search(provider, stage=stage, count=count)

    # -- decisions ----------------------------------------------------------

    @property
    def exhausted(self) -> bool:
        return self.budget.exhausted

    def can_afford(self, *, stage: str = "llm", calls: int = 1) -> bool:
        """Whether another model call fits this run's ceilings."""
        return self.budget.can_afford(stage=stage, calls=calls)

    def can_afford_search(self, count: int = 1) -> bool:
        """Whether another search batch fits this run's ceilings."""
        return self.budget.can_afford_search(count=count)

    @property
    def refusals(self) -> list:
        """What the governor declined, for the trace/degradation reasons."""
        return list(self.budget.refusals)

    # -- reporting ----------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        snap = self.budget.snapshot()
        snap.update({
            "request_id": self.request_id,
            "mode": self.mode,
            "wall_sec": round(time.time() - self.started_at, 2),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": (
                round(self.cache_hits / (self.cache_hits + self.cache_misses), 4)
                if (self.cache_hits + self.cache_misses) else 0.0
            ),
            "search_calls": self.search_calls,
        })
        return snap


_RUN_USAGE: ContextVar[Optional[RunUsage]] = ContextVar("deepscout_run_usage", default=None)


def start_run_usage(request_id: str, settings: Any, mode: str = "standard") -> RunUsage:
    """Install a fresh ledger for this run (call at request start)."""
    budget = ResearchBudget.from_settings(settings, mode=mode)
    usage = RunUsage(
        request_id=request_id,
        budget=budget,
        mode=mode,
        database_url=str(getattr(settings, "database_url", "") or ""),
    )
    _RUN_USAGE.set(usage)
    return usage


def get_run_usage() -> Optional[RunUsage]:
    """The current run's ledger, or None outside a research request."""
    return _RUN_USAGE.get()


def get_run_ladder():
    """The current run's degradation ladder, or None outside a run.

    Agents call this at the point optional work would run; outside a run there
    is nothing to shed and every optional capability proceeds.
    """
    usage = _RUN_USAGE.get()
    return usage.ladder if usage is not None else None


def checkpoint_run(state: dict, completed_nodes: list) -> None:
    """Best-effort durable checkpoint of the current run, if one is active.

    Called by the runner after each completed node. Reads the run's ledger to
    find the database; outside a run (or with no database configured) it is a
    no-op, so the runner needs no new constructor argument. Fire-and-forget:
    the caller must not await this, and a failure must never affect the run.
    Writes are queued latest-wins (``queue_checkpoint``) so a snapshot from an
    earlier node can never overwrite a newer one out of order.
    """
    usage = _RUN_USAGE.get()
    if usage is None or not usage.database_url:
        return
    try:
        from app.db.store.checkpoints import queue_checkpoint

        queue_checkpoint(usage.database_url, usage.request_id, state, completed_nodes)
    except Exception as exc:  # never let persistence touch the run
        logger.warning("[checkpoint] schedule failed: %s", exc)


async def flush_checkpoints() -> None:
    """Wait (bounded) for queued checkpoint writes to land. Never raises."""
    try:
        from app.db.store.checkpoints import flush_checkpoints as _flush

        await _flush()
    except Exception as exc:
        logger.warning("[checkpoint] flush failed: %s", exc)


async def load_resume_checkpoint() -> Optional[Dict[str, Any]]:
    """The active run's saved checkpoint, or None.

    Called by the runner at start: when a checkpoint exists the run resumes
    from it (skipping nodes already in the state) instead of starting over.
    Returns None outside a run, when no database is configured, or when the
    checkpoint is absent/corrupt — every failure degrades to a fresh run.
    """
    usage = _RUN_USAGE.get()
    if usage is None or not usage.database_url:
        return None
    try:
        from app.db.store.checkpoints import load_checkpoint

        return await load_checkpoint(usage.database_url, usage.request_id)
    except Exception as exc:
        logger.warning("[checkpoint] load failed: %s", exc)
        return None


def clear_run_usage() -> None:
    _RUN_USAGE.set(None)


def set_stage_hint(stage: str) -> None:
    """Best-effort stage label so ledger entries attribute correctly
    (planner/summarizer/critic/synthesizer) even though the LLM client's
    public API doesn't carry a stage parameter."""
    usage = _RUN_USAGE.get()
    if usage is not None:
        usage.stage_hint = str(stage or "")
