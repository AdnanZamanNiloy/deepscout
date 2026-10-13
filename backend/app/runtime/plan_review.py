"""Human-in-the-loop plan-approval registry.

When a run is started with interactive review enabled, it pauses at the plan
stage and waits for the user to approve (or edit) the proposed outline. This
module is the handshake between the paused run and the approval endpoint:

* the run calls :func:`await_plan_decision` and blocks on an ``asyncio.Event``;
* ``POST /api/research/{run_id}/plan`` calls :func:`submit_plan_decision`,
  which stores the decision and sets the event, releasing the run.

State is per-run and bounded: :func:`clear_plan_decision` is called in the run's
``finally`` so nothing accumulates (AGENTS.md 4.3 — no unbounded module state).
A module-level dict is required here because the run coroutine and the separate
HTTP request are different tasks; the cleanup path is what keeps it safe.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PlanDecision:
    """The user's answer to a plan-review prompt."""

    action: str  # "approve" | "revise"
    # The outline the user approved (possibly edited), used on approve.
    sections: List[str] = field(default_factory=list)
    # Free-text direction for the run ("focus on costs and policy").
    feedback: str = ""


@dataclass
class _Pending:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Optional[PlanDecision] = None


_pending: Dict[str, _Pending] = {}


def _key(request_id: str) -> str:
    return str(request_id)


def register_plan_wait(request_id: str) -> None:
    """Arm the wait for ``request_id``. Overwrites any stale entry."""
    _pending[_key(request_id)] = _Pending()


def submit_plan_decision(request_id: str, decision: PlanDecision) -> bool:
    """Deliver a decision to a waiting run. Returns False if none is waiting."""
    entry = _pending.get(_key(request_id))
    if entry is None:
        return False
    entry.decision = decision
    entry.event.set()
    return True


async def await_plan_decision(request_id: str, timeout: Optional[float]) -> Optional[PlanDecision]:
    """Wait for the user's decision, or ``None`` on timeout.

    ``None`` means "no answer in time" — the caller then falls back to the
    automatic approval so a run can never hang on an absent user.
    """
    entry = _pending.get(_key(request_id))
    if entry is None:
        return None
    try:
        await asyncio.wait_for(entry.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    return entry.decision


def clear_plan_decision(request_id: str) -> None:
    """Drop the run's wait state. Safe to call more than once."""
    _pending.pop(_key(request_id), None)


def has_pending(request_id: str) -> bool:
    """True when a run is currently waiting for a decision."""
    return _key(request_id) in _pending
