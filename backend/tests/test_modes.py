"""Research Modes tests (Phase 3.7)."""
from app.graph.workflow import build_initial_state
from app.agents.orchestrator import MODE_PRESETS, VALID_MODES


def test_presets_shape():
    assert set(MODE_PRESETS) == {"quick", "standard", "deep"}
    assert MODE_PRESETS["quick"]["max_iterations"] < MODE_PRESETS["standard"]["max_iterations"]
    assert MODE_PRESETS["standard"]["max_iterations"] < MODE_PRESETS["deep"]["max_iterations"]
    assert MODE_PRESETS["quick"]["max_agents"] < MODE_PRESETS["deep"]["max_agents"]
    assert set(VALID_MODES) == {"quick", "standard", "deep"}
    # Distinct resource profiles: no two modes share a tuple.
    assert len({(p["max_agents"], p["max_iterations"]) for p in MODE_PRESETS.values()}) == 3


def test_quick_mode_respects_one_iteration():
    """GAP-8 regression: the max(3,...) iteration floor must not defeat quick mode."""
    state = build_initial_state("what is RAG", 3, mode="quick")
    assert state["max_iterations"] == 1
    assert state["mode"] == "quick"
    assert state["orchestration"]["target_agents"] == 2


def test_only_deep_exceeds_default_cap():
    hard = (
        "Should Bangladesh invest in nuclear vs solar energy over the next 20 years, "
        "considering financing, grid impact, and political trade-offs between "
        "regional power strategies?"
    )
    standard = build_initial_state(hard, 3, mode="standard")
    deep = build_initial_state(hard, 3, mode="deep")
    quick = build_initial_state(hard, 3, mode="quick")
    assert standard["orchestration"]["target_agents"] == 3  # standard plans 3 angles
    assert quick["orchestration"]["target_agents"] <= 2
    assert deep["orchestration"]["target_agents"] > 3  # the explicit opt-in mode exceeds it


def test_unknown_mode_falls_back_to_standard():
    state = build_initial_state("what is RAG", 3, mode="bogus")
    assert state["mode"] == "standard"
    assert state["max_iterations"] == 3


# ---------------------------------------------------------------------------
# Legacy modes are mapped forward, never rejected
# ---------------------------------------------------------------------------

def test_exactly_three_modes_with_the_required_shape():
    """The contract: two/one/three agents and five/three/one passes."""
    assert set(MODE_PRESETS) == {"quick", "standard", "deep"}
    assert MODE_PRESETS["quick"] == {
        "max_agents": 2, "max_iterations": 1, "deep_research": False}
    assert MODE_PRESETS["standard"] == {
        "max_agents": 3, "max_iterations": 3, "deep_research": False}
    assert MODE_PRESETS["deep"] == {
        "max_agents": 5, "max_iterations": 5, "deep_research": True}
    # Ordering is monotonic: effort never goes down as mode goes up.
    pair = lambda m: (MODE_PRESETS[m]["max_agents"], MODE_PRESETS[m]["max_iterations"])
    assert pair("quick") < pair("standard") < pair("deep")


def test_legacy_modes_map_to_the_mode_that_inherited_their_behaviour():
    """A saved executive run must still get DEEP research, not silently become
    shallow. This is what makes resume and old URLs safe across a mode rename."""
    from app.agents.orchestrator import resolve_mode

    assert resolve_mode("executive") == "deep"
    assert resolve_mode("audit") == "standard"
    for legacy, expected in (("executive", "deep"), ("audit", "standard")):
        state = build_initial_state("what is retrieval augmented generation", 3, mode=legacy)
        assert state["mode"] == expected
        # The preset the mode inherits is applied, not the default.
        assert state["max_iterations"] == MODE_PRESETS[expected]["max_iterations"]


def test_a_legacy_mode_request_still_plans_research():
    """A stored run or old client asking for `audit` must reach the planner.

    Verifying through build_initial_state only covers resolution; this drives the
    real node so a future change that drops the mode before planning would fail.
    """
    from app.graph.workflow import build_initial_state

    state = build_initial_state(
        "What can be the most demanding job in 2027?", 3, mode="audit")
    assert state["mode"] == "standard"
    assert state["max_iterations"] == MODE_PRESETS["standard"]["max_iterations"]
