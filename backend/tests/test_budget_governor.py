"""The budget governor must actually bite.

Root cause this locks: ``ResearchBudget.can_afford`` and
``RunUsage.exhausted`` existed for a full release with ZERO callers — the
ceilings were computed, reported, and never consulted, so a stalled provider
could hold a run open to the deployment timeout while the UI claimed the run
was "bounded". A test that only asserts the numbers are computed would have
passed throughout that bug. These tests assert the ENFORCEMENT: the funnel and
the search client must take their deterministic fallback once the ceiling is
spent, and must keep working when there is no ledger at all.

They are written against the real seam (``LLMFunnel``, ``SearchClient.run_search``)
rather than a helper, so removing the check from the seam fails the test.
"""
from __future__ import annotations

import asyncio

import pytest

from app.common.config import Settings
from app.core.usage import clear_run_usage, get_run_usage, start_run_usage
from app.infra.budget import ResearchBudget, mode_timeout_seconds


@pytest.fixture(autouse=True)
def _no_ledger_leak():
    """Every test starts and ends with no run ledger installed."""
    clear_run_usage()
    yield
    clear_run_usage()


def _settings() -> Settings:
    return Settings(groq_api_key="test-key", database_url=":memory:", _env_file=None)


# --- the ceiling is mode-shaped and enforced == the reported one ------------


def test_each_mode_gets_an_absolute_hard_cap_below_the_deployment_base():
    s = _settings()
    quick = mode_timeout_seconds(s, "quick")
    standard = mode_timeout_seconds(s, "standard")
    deep = mode_timeout_seconds(s, "deep")

    assert quick < standard < deep, "modes must differ in wall-clock allowance"
    # Every cap sits under the deployment's absolute maximum.
    assert deep <= s.research_timeout_sec
    # And well under the old flat 1000s that let a hang run for ~17 minutes.
    assert deep < 1000


def test_the_budget_reports_the_same_seconds_the_route_enforces():
    """One source of truth: budget.max_seconds == mode_timeout_seconds."""
    s = _settings()
    for mode in ("quick", "standard", "deep", "unknown_mode"):
        budget = ResearchBudget.from_settings(s, mode)
        assert budget.max_seconds == mode_timeout_seconds(s, mode), mode


# --- the governor refuses once a ceiling is spent ---------------------------


def test_llm_call_ceiling_is_refused_and_recorded():
    s = _settings()
    usage = start_run_usage("t1", s, mode="quick")  # 21 calls
    budget = usage.budget

    # Spend exactly the ceiling.
    for _ in range(budget.max_llm_calls):
        assert budget.can_afford(stage="llm") is True
        budget.record_llm("plan")

    # The next call is refused, and the refusal is visible in the trace.
    assert budget.can_afford(stage="llm") is False
    assert any("ceiling" in r for r in budget.refusals)


def test_wall_clock_ceiling_is_refused_and_recorded():
    s = _settings()
    usage = start_run_usage("t2", s, mode="standard")
    budget = usage.budget
    budget.max_seconds = 0.0  # already spent

    assert budget.can_afford(stage="llm") is False
    assert budget.can_afford_search(1) is False
    assert any("time budget" in r for r in budget.refusals)


def test_search_does_not_consume_the_llm_ceiling():
    """Retrieval must not be able to starve generation.

    A run whose search spend equalled its LLM ceiling would refuse every writer
    call and produce an empty report; search is bounded by wall-clock instead.
    """
    s = _settings()
    usage = start_run_usage("t3", s, mode="standard")
    budget = usage.budget
    for _ in range(200):
        budget.record_search("web")

    assert budget.llm_calls == 0
    assert budget.can_afford(stage="llm") is True


# --- the seam enforces it (this is what a "numbers only" test would miss) ---

def test_the_funnel_takes_its_fallback_when_the_ceiling_is_spent():
    """With the ceiling spent, the funnel must NOT reach the provider."""
    from app.research.llm import LLMFunnel

    called = {"n": 0}

    class _Client:
        async def _generate_with_fallback(self, system, user):
            called["n"] += 1
            return "should not be reached"

        async def generate_json(self, system, user, retries=3):
            called["n"] += 1
            return {"reached": True}

    s = _settings()
    usage = start_run_usage("t4", s, mode="quick")
    usage.budget.max_llm_calls = 0  # nothing affordable

    funnel = LLMFunnel(_Client())
    text = asyncio.run(funnel.create_chat_completion([{"role": "user", "content": "hi"}]))
    obj = asyncio.run(funnel.call_model_json([{"role": "user", "content": "hi"}]))

    assert text == ""
    assert obj == {}
    assert called["n"] == 0, "provider was called despite an exhausted budget"
    # The refusal is on the ledger, so the trace can explain the stop.
    assert any("ceiling" in r or "time budget" in r for r in get_run_usage().budget.refusals)


def test_the_funnel_is_transparent_outside_a_run():
    """No ledger (probes, tests, bench without a run) must not block calls."""
    from app.research.llm import LLMFunnel

    calls = {"n": 0}

    class _Client:
        async def _generate_with_fallback(self, system, user):
            calls["n"] += 1
            return "ok"

        async def generate_json(self, system, user, retries=3):
            calls["n"] += 1
            return {"ok": True}

    clear_run_usage()
    funnel = LLMFunnel(_Client())
    assert asyncio.run(funnel.create_chat_completion([{"role": "user", "content": "x"}])) == "ok"
    assert asyncio.run(funnel.call_model_json([{"role": "user", "content": "x"}]))["ok"] is True
    assert calls["n"] == 2


def test_the_search_seam_returns_empty_when_the_wall_clock_is_spent():
    """Retrieval must stop, not raise, when the run is out of time."""
    from app.retrieval.client import SearchClient

    s = _settings()
    usage = start_run_usage("t5", s, mode="standard")
    usage.budget.max_seconds = 0.0

    client = SearchClient(s)
    results = asyncio.run(client.run_search(["a query"]))
    assert results == []
    assert any("time budget" in r for r in usage.budget.refusals)
