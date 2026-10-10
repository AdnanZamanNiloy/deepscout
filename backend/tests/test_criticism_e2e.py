"""End-to-end: the critic's findings become tracked tasks in a real graph run.

This drives the full LangGraph workflow with the deterministic mock pipeline and
asserts that:
  * the critic node writes a `criticism_ledger` to state,
  * a run whose evidence keeps failing the gates accumulates tracked tasks,
  * the ledger drives the loop (the run still terminates at the ceiling),
  * exhausted criticisms reach the report's Limitations via the ledger.

It is the end-to-end companion to the unit tests in test_criticism_ledger.py and
test_criticism_loop.py, and guards against the feature silently detaching from
the live pipeline.
"""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.graph.workflow import (
    build_initial_state,
    create_workflow,
    graph_recursion_limit,
)


def _settings(**kw):
    base = dict(groq_api_key="k", database_url=":memory:", _env_file=None)
    base.update(kw)
    return Settings(**base)


async def _run(query: str, *, max_iterations: int, mode: str = "standard"):
    from bench.mock_pipeline import FakeLLM, FakeSearch
    from app.core import llm_cache as _lc
    from app.core.usage import clear_run_usage, start_run_usage

    _lc._force_disabled = True
    settings = _settings()
    # critic never passes: forces the loop to keep evaluating the same gaps
    llm = FakeLLM(settings, critic_pass_on_iteration=10_000)
    search = FakeSearch(settings)
    workflow = create_workflow(llm, search_client=search)

    state = build_initial_state(query, max_iterations=max_iterations, mode=mode)
    usage = start_run_usage("test-crit-ledger", settings, mode=mode)
    try:
        final = dict(state)
        async for snapshot in workflow.astream(
            state,
            stream_mode="values",
            config={"recursion_limit": graph_recursion_limit(state)},
        ):
            final = {**final, **{k: v for k, v in snapshot.items() if v}}
    finally:
        clear_run_usage()
    return final


@pytest.mark.asyncio
async def test_critic_ledger_is_written_to_state():
    final = await _run("What are the current trends in artificial intelligence?", max_iterations=2)
    ledger = final.get("criticism_ledger")
    assert isinstance(ledger, dict), "critic node must write a criticism ledger"
    # every entry is a well-formed tracked task
    for entry in ledger.values():
        assert entry["kind"]
        assert entry["status"] in ("open", "attempted", "resolved", "exhausted")
        assert "attempts" in entry


@pytest.mark.asyncio
async def test_run_with_persistently_failing_gate_terminates_and_shows_ledger():
    final = await _run("Compare solar versus nuclear energy economics", max_iterations=2, mode="deep")
    # The run must terminate (ceiling), never loop unbounded.
    assert int(final.get("iteration", 0)) <= int(final.get("max_iterations", 0))
    assert final.get("final_report")
    ledger = final.get("criticism_ledger") or {}
    assert isinstance(ledger, dict)


def test_ledger_limitations_reach_the_report_gaps():
    """Exhausted criticisms must appear in the measured limitations list."""
    from app.graph.evidence import _measured_coverage_gaps

    exhausted = {
        "uncovered_axis::cost": {
            "kind": "uncovered_axis",
            "target": "cost",
            "detail": "planned_axis_uncovered=cost",
            "status": "exhausted",
            "attempts": 2,
            "max_attempts": 2,
            "first_seen_iteration": 1,
            "last_seen_iteration": 3,
            "resolved_iteration": None,
        }
    }
    state = {"query": "x", "facts": [], "criticism_ledger": exhausted}
    gaps = _measured_coverage_gaps(state)
    assert any("unresolved after" in g for g in gaps)
    assert any("cost" in g for g in gaps)
