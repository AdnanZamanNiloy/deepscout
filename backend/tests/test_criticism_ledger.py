"""Criticism ledger (app/core/criticism_ledger.py): critic findings become
tracked research tasks whose resolution is VERIFIED, not assumed.

These tests lock the contract the whole feature exists for:
  * every critic gate-failure/gap becomes a structured task,
  * a task whose condition no longer holds is marked resolved,
  * a task re-raised past its attempt budget becomes exhausted,
  * unresolved criticisms are disclosed as limitations,
  * the ledger drives the depth controller (active -> expand, exhausted -> stop),
  * the ledger is run-scoped, total (never raises) and bounded.
"""
from __future__ import annotations

from app.core.criticism_ledger import (
    STATUS_ATTEMPTED,
    STATUS_EXHAUSTED,
    STATUS_OPEN,
    STATUS_RESOLVED,
    active_tasks,
    exhausted_limitations,
    has_actionable,
    resolved_count,
    summary,
    unresolved_targets,
    update,
)

FACT = {"claim": "x is y", "source": "https://a.org", "verified": True}


def _critique(gate_failures=(), gaps=()):
    return {"gate_failures": list(gate_failures), "gaps": list(gaps)}


def test_gate_failure_becomes_a_task():
    led = update({}, _critique(["planned_axis_uncovered=cost"]), [FACT], iteration=1)
    assert len(led) == 1
    (entry,) = led.values()
    assert entry["kind"] == "uncovered_axis"
    assert entry["target"] == "cost"
    assert entry["status"] == STATUS_OPEN
    assert entry["first_seen_iteration"] == 1


def test_resolved_when_condition_stops_being_raised():
    led = update({}, _critique(["verified=0"]), [FACT], iteration=1)
    # next pass: the critic no longer raises it -> resolved
    led = update(led, _critique([]), [FACT], iteration=2)
    (entry,) = led.values()
    assert entry["status"] == STATUS_RESOLVED
    assert entry["resolved_iteration"] == 2
    assert resolved_count(led) == 1
    assert not has_actionable(led)


def test_repeated_failure_bumps_attempts_then_exhausts():
    led = update({}, _critique(["domains=1<2"]), [FACT], iteration=1)
    led = update(led, _critique(["domains=1<2"]), [FACT], iteration=2)
    (entry,) = led.values()
    assert entry["status"] == STATUS_ATTEMPTED
    assert entry["attempts"] == 1
    # DEFAULT_MAX_ATTEMPTS=2: one more re-raise exhausts it
    led = update(led, _critique(["domains=1<2"]), [FACT], iteration=3)
    (entry,) = led.values()
    assert entry["status"] == STATUS_EXHAUSTED
    assert not has_actionable(led)


def test_exhausted_task_surfaces_as_limitation():
    led = {}
    for i in range(1, 4):
        led = update(led, _critique(["domains=1<2"]), [FACT], iteration=i)
    lines = exhausted_limitations(led)
    assert len(lines) == 1
    assert "domains=1<2" in lines[0]
    assert "unresolved" in lines[0]


def test_gap_creates_unsourced_angle_task():
    led = update({}, _critique(gaps=["no evidence for angle: policy"]), [FACT], iteration=1)
    (entry,) = led.values()
    assert entry["kind"] == "unsourced_angle"
    assert entry["target"] == "policy"


def test_multiple_failures_tracked_independently():
    led = update(
        {},
        _critique(
            [
                "planned_axis_uncovered=cost",
                "planned_axis_uncovered=policy",
                "verified=0",
            ]
        ),
        [FACT],
        iteration=1,
    )
    assert summary(led)["total"] == 3
    # Only one axis is resolved next pass; the others persist.
    led = update(led, _critique(["planned_axis_uncovered=policy", "verified=0"]), [FACT], iteration=2)
    s = summary(led)
    assert s[STATUS_RESOLVED] == 1
    assert active_tasks(led)  # policy + verified still actionably open


def test_unresolved_targets_are_search_ready_tokens():
    led = update(
        {},
        _critique(["planned_axis_uncovered=cost_of_capital", "domains=1<2"]),
        [FACT],
        iteration=1,
    )
    targets = unresolved_targets(led)
    assert "cost of capital" in targets
    # a failure with no named target does not invent one
    assert "" not in targets


def test_ledger_is_bounded():
    from app.core.criticism_ledger import MAX_TASKS

    led = {}
    for i in range(MAX_TASKS + 10):
        led = update(led, _critique([f"planned_axis_uncovered=axis{i}"]), [FACT], iteration=i + 1)
    assert len(led) <= MAX_TASKS


def test_garbage_input_is_total_never_raises():
    # None, wrong types, malformed entries — the ledger must survive all.
    assert update(None, None, None, iteration=1) == {}
    assert update({}, {}, [], iteration=1) == {}
    led = update({"bad": "not-a-dict", "x::y": {"kind": ""}}, _critique(["verified=0"]), [], iteration=1)
    assert isinstance(led, dict)
    assert all(isinstance(v, dict) for v in led.values())


def test_does_not_mutate_input():
    original = {"verified::": {"kind": "no_verified_evidence", "target": "", "detail": "verified=0",
                               "status": STATUS_OPEN, "attempts": 0, "max_attempts": 2,
                               "first_seen_iteration": 1, "last_seen_iteration": 1,
                               "resolved_iteration": None}}
    snapshot = {k: dict(v) for k, v in original.items()}
    update(original, _critique(["verified=0"]), [FACT], iteration=2)
    assert original == snapshot


def test_semantic_gap_only_critique_makes_no_tasks_but_is_total():
    # A definitional gap is a task (no_definitional_claim) — but an empty
    # critique must create nothing.
    assert update({}, _critique(), [], iteration=1) == {}
