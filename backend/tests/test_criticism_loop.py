"""Depth-controller integration of the criticism ledger.

The critic-ledger gate must make an UNFINISHED criticism block a soft stop
while budget remains, and must NOT block once the criticism is exhausted
(which is disclosed as a limitation instead). This is the "convert each
weakness into a task and verify it is resolved" contract's stopping half.
"""
from __future__ import annotations

from app.core.depth.controller import decide_with_checks


def _state(ledger, *, sufficient=True, iteration=1, max_iterations=3):
    return {
        "query": "compare the economics of solar versus nuclear",
        "critique": {
            "is_sufficient": sufficient,
            "improved_queries": ["cost of capital solar nuclear economics"],
            "gate_failures": [],
        },
        "criticism_ledger": ledger,
        "iteration": iteration,
        "max_iterations": max_iterations,
        "confidence": 0.9,
        "facts": [],
        "sub_questions": [],
    }


def _ledger(status, *, attempts=0, max_attempts=2):
    return {
        "uncovered_axis::cost": {
            "kind": "uncovered_axis",
            "target": "cost",
            "detail": "planned_axis_uncovered=cost",
            "status": status,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "first_seen_iteration": 1,
            "last_seen_iteration": 1,
            "resolved_iteration": None,
        }
    }


def test_active_criticism_forces_expand():
    d, checks = decide_with_checks(_state(_ledger("open")))
    assert d == "expand"
    assert checks["active_criticism_count"] == 1
    assert "unresolved critic-raised" in checks["decision_reason"]


def test_attempted_criticism_still_forces_expand():
    d, _ = decide_with_checks(_state(_ledger("attempted", attempts=1)))
    assert d == "expand"


def test_exhausted_criticism_does_not_block_finalize():
    d, checks = decide_with_checks(_state(_ledger("exhausted", attempts=2)))
    # exhausted -> not actionable -> clean sufficiency stop
    assert d == "finalize"
    assert checks["active_criticism_count"] == 0


def test_resolved_criticism_does_not_block_finalize():
    d, checks = decide_with_checks(_state(_ledger("resolved", attempts=1)))
    assert d == "finalize"
    assert checks["active_criticism_count"] == 0


def test_active_criticism_without_novel_query_finalizes_with_disclosure():
    state = _state(_ledger("open"))
    # no follow-ups left to act on the criticism
    state["critique"]["improved_queries"] = []
    d, checks = decide_with_checks(state)
    assert d == "finalize"
    assert "no novel query" in checks["decision_reason"]


def test_hard_wall_still_overrides_active_criticism():
    # Budget/ceiling are absolute: an active criticism cannot loop past them.
    state = _state(_ledger("open"), iteration=3, max_iterations=3)
    d, checks = decide_with_checks(state)
    assert d == "finalize"


def test_absent_ledger_is_identical_to_before():
    # No ledger key at all -> feature inert (preserves existing behaviour).
    state = _state(None)
    del state["criticism_ledger"]
    d, checks = decide_with_checks(state)
    assert checks["active_criticism_count"] == 0
    assert d == "finalize"
