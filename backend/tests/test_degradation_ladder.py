"""The degradation ladder sheds optional work in a fixed, announced order.

Contract under test:
  * a run comfortably inside its budget sheds nothing;
  * when the projected finish would exceed the soft limit, the ladder sheds —
    and it sheds the diagram BEFORE the second audit pass, because presentation
    costs the reader less than verification;
  * a shed is reported as a run degradation, so the delivered report says what
    it left out;
  * outside a run there is no ladder, and optional work always proceeds.

Deterministic: the clock is injected, so these never depend on wall time.
"""
from __future__ import annotations

import asyncio

import pytest

from app.common.config import Settings
from app.core.degradation import fallback_reasons, reset_fallbacks, take_fallbacks
from app.core.usage import clear_run_usage, get_run_ladder, start_run_usage
from app.infra.ladder import (
    MANDATORY_STAGES,
    SHED_ORDER,
    DegradationLadder,
    soft_limit_for,
)


class _Clock:
    """A clock we control, so a 'slow run' is exact rather than sleep-based."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture(autouse=True)
def _clean():
    clear_run_usage()
    reset_fallbacks()
    yield
    clear_run_usage()
    reset_fallbacks()


# --- the shedding order is the decision, so it is pinned --------------------


def test_the_shed_order_puts_presentation_before_verification():
    assert SHED_ORDER.index("diagram") < SHED_ORDER.index("second_audit_pass")


# --- a healthy run sheds nothing -------------------------------------------


def test_a_run_inside_its_budget_sheds_nothing():
    clock = _Clock()
    ladder = DegradationLadder(soft_limit_s=150, hard_cap_s=180, mode="quick", clock=clock)
    remaining = ["narrative_writer", "evidence_auditor", "diagram_designer", "report_assembler"]

    assert ladder.evaluate(remaining, capability="diagram") is True
    assert ladder.evaluate(
        ["narrative_writer", "evidence_auditor", "report_assembler"],
        capability="second_audit_pass", extra_s=20,
    ) is True
    assert ladder.summary()["shed"] == []


# --- a slow run sheds, in order --------------------------------------------


def test_a_slow_run_sheds_the_diagram_first_then_the_second_pass():
    clock = _Clock()
    ladder = DegradationLadder(soft_limit_s=100, hard_cap_s=180, mode="quick", clock=clock)
    clock.advance(95)  # almost at the soft limit

    diagram_ok = ladder.evaluate(
        ["narrative_writer", "evidence_auditor", "diagram_designer", "report_assembler"],
        capability="diagram",
    )
    audit_ok = ladder.evaluate(
        ["narrative_writer", "evidence_auditor", "report_assembler"],
        capability="second_audit_pass", extra_s=20,
    )

    assert diagram_ok is False, "a run with no room must not spend a call on visuals"
    assert audit_ok is False
    assert ladder.summary()["shed"] == ["diagram", "second_audit_pass"]


def test_shedding_leaves_the_mandatory_path_intact():
    """The ladder may only ever drop optional capabilities."""
    for cap in SHED_ORDER:
        assert cap not in MANDATORY_STAGES


# --- the soft limit leaves room to finish ----------------------------------


def test_the_soft_limit_is_below_the_hard_cap_by_a_mode_reserve():
    # Shedding starts at the soft limit, and must leave room to finish the
    # mandatory tail, so it is always strictly below the enforced hard cap.
    for mode, cap in (("quick", 180.0), ("standard", 360.0), ("deep", 720.0)):
        soft = soft_limit_for(cap, mode)
        assert 0 < soft < cap, f"{mode}: soft limit {soft} must be inside the cap {cap}"


# --- it reports what it shed ------------------------------------------------


def test_shed_reasons_name_every_shed_capability():
    clock = _Clock()
    ladder = DegradationLadder(soft_limit_s=1, hard_cap_s=180, mode="quick", clock=clock)
    ladder.evaluate(["diagram_designer"], capability="diagram")
    reasons = ladder.shed_reasons()
    assert set(reasons) == set(ladder.shed)
    assert all(isinstance(v, str) and v for v in reasons.values())


# --- integration: the ladder rides the run, and reports a degradation -------


def test_the_ladder_rides_the_run_and_sheds_are_degradations():
    s = Settings(groq_api_key="k", database_url=":memory:", _env_file=None)
    usage = start_run_usage("ladder-1", s, mode="quick")

    assert get_run_ladder() is usage.ladder, "the run must expose its own ladder"
    # Force the run far past its soft limit and let the diagram agent decide.
    usage.ladder.started_at -= 10_000

    from app.agents.report import DiagramDesigner

    class _LLM:
        async def create_chat_completion(self, **kw):  # pragma: no cover - must not run
            raise AssertionError("the diagram LLM call must not happen when shed")

    agent = DiagramDesigner(_LLM(), cfg=None, sink=None)
    out = asyncio.run(agent.run({"task": {}}))

    assert out["diagrams"] == []
    assert "ladder:diagram" in take_fallbacks()
    assert fallback_reasons().get("ladder:diagram")


def test_without_a_run_optional_work_proceeds():
    clear_run_usage()
    assert get_run_ladder() is None

    from app.agents.report import DiagramDesigner

    class _LLM:
        async def create_chat_completion(self, **kw):
            return "flowchart TD\n  A --> B"

    agent = DiagramDesigner(_LLM(), cfg=None, sink=None)
    out = asyncio.run(agent.run({"task": {}}))
    assert out["diagrams"], "outside a run the diagram must still be produced"
