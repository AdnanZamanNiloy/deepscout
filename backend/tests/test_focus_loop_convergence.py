"""The review → follow-up-search loop must CLOSE, not restate the gap.

The failure this file pins: the reviewer reported missing dimensions every
round, but nothing converted that report into research tasks. On an expansion
pass the planner seeded its required axes from the axes ALREADY researched and
appended only what the model volunteered, so the next plan was drawn from the
direction that had already been explored. The gaps stayed textual — a sentence
in `critique_feedback` the next LLM call was free to ignore — and the run
re-reported the same holes while search kept returning the same material.

The regression is direct: given a state whose focus report names 3 missing
dimensions, the next `planner` node must produce sub-questions whose axes ARE
those dimensions, so `search` executes them and the following coverage
measurement can see them filled.

Domain-agnosticism is asserted explicitly: the fixture subject is deliberately
NOT artificial-intelligence, and one test greps for AI terms in the generated
questions.
"""

import asyncio


from app.core.config import Settings
from app.graph import workflow as wf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# An exploratory, NON-AI question. Anything AI-shaped in an assertion below is a
# hardcoded-taxonomy regression, not a subject this fixture supplies.
QUERY = "How is the global coffee supply chain adapting to climate change?"


def _settings(**kw):
    base = dict(groq_api_key="k", database_url=":memory:", _env_file=None)
    base.update(kw)
    return Settings(**base)


class _DeadLLM:
    """Forces the deterministic path.

    The failure being fixed is structural — the gap never became a task — so it
    must reproduce with NO model involvement. If the fix only worked when an LLM
    happened to volunteer the right dimensions, it would not be a fix.
    """

    def __init__(self, settings):
        self.settings = settings

    async def generate_json(self, *args, **kwargs):
        raise RuntimeError("no provider")

    async def generate(self, *args, **kwargs):
        raise RuntimeError("no provider")


def _planned_state(missing, *, existing_axis="technical_benchmarks", thin=()):
    """A mid-run expansion state whose focus report names `missing` dimensions.

    `existing` carries one dimension with verified evidence — the already-covered
    direction the loop used to keep re-searching.
    """
    existing_question = "coffee yield measured outcomes under warming"
    plan = [
        {"id": 1, "axis": existing_axis, "question": existing_question,
         "search_type": "academic", "minimum_sources": 1, "variants": []},
    ]
    for offset, dimension in enumerate(missing, start=2):
        plan.append(
            {"id": offset, "axis": dimension, "question": f"{dimension} coverage question",
             "search_type": "news", "minimum_sources": 1, "variants": []}
        )
    return {
        "query": QUERY,
        "iteration": 1,               # expansion pass
        "max_iterations": 4,
        "mode": "standard",
        "sub_questions": plan[:1],    # only the covered dimension has a contract
        "facts": [
            {"claim": "Coffee yields fell in the measured regions",
             "source": "https://agri.example/study", "sub_question": existing_question,
             "axis": existing_axis, "verified": True},
            {"claim": "Warming reduced arabica suitability",
             "source": "https://agri.example/study-2", "sub_question": existing_question,
             "axis": existing_axis, "verified": True},
        ],
        "search_results": [{"url": "https://agri.example/study",
                            "sub_question": existing_question}],
        "critique": {"is_sufficient": False, "improved_queries": [], "reason": "gaps"},
        "confidence": 0.5,
        "intent": {"query_type": "exploratory", "domain": "general"},
        "orchestration": {"target_agents": 5, "target_sub_questions": 5,
                          "min_sources_per_axis": 2},
        "focus": {
            "report": {
                "query": QUERY,
                "coverage": 0.25,
                "concentration": 0.9,
                "dominant_dimension": existing_axis,
                "drift": 0.58,          # the value from the live report
                "off_query_share": 0.58,
                "source_diversity": 1,
                "host_concentration": 1.0,
                "missing": list(missing),
                "thin": list(thin),
                "concentrated": True,
                "drifted": True,
                "is_broad": True,
                "is_narrow": False,
            },
            "queries": [],
        },
    }


async def _run_planner(settings, state):
    graph = wf.create_workflow(_DeadLLM(settings), search_client=None)
    return await graph.nodes["planner"].ainvoke(state)


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------


def test_missing_dimensions_become_searchable_contracts_next_iteration():
    """3 missing dimensions → 3 contracts on those axes, in the NEXT plan.

    This is the exact behaviour that was absent: the reviewer named the gaps and
    the following iteration searched something else.
    """
    settings = _settings()
    missing = ["enterprise_adoption", "energy_environment", "labor_social_impact"]
    state = _planned_state(missing)

    out = asyncio.run(_run_planner(settings, state))
    plan = out["sub_questions"]
    axes = {str(c.get("axis", "")) for c in plan}

    for dimension in missing:
        assert dimension in axes, (
            f"missing dimension '{dimension}' produced no contract; next pass "
            f"cannot search it. axes={sorted(axes)}"
        )


def test_gap_contracts_are_marked_with_a_distinct_search_question():
    """A gap dimension must get its OWN search string, not a copy of the one
    already researched — otherwise search returns the same pages again."""
    settings = _settings()
    missing = ["regulation", "supply_shocks", "smallholder_adaptation"]
    state = _planned_state(missing)
    out = asyncio.run(_run_planner(settings, state))

    covered_question = state["sub_questions"][0]["question"]
    gap_questions = [
        c["question"] for c in out["sub_questions"]
        if str(c.get("axis", "")) in set(missing)
    ]
    assert len(gap_questions) == len(missing)
    for question in gap_questions:
        assert question != covered_question
        assert question.strip()


def test_gap_contracts_survive_the_agent_cap():
    """Gap-closing contracts are the reason the pass exists.

    They are injected after the plan is capped and excluded from the cap; if they
    were truncated away, the loop would return to re-reporting the same gaps.
    """
    settings = _settings()
    missing = ["a_dimension", "b_dimension", "c_dimension", "d_dimension"]
    state = _planned_state(missing)
    # A cap smaller than the number of gaps must not drop any of them.
    state["orchestration"]["target_agents"] = 2
    out = asyncio.run(_run_planner(settings, state))
    axes = {str(c.get("axis", "")) for c in out["sub_questions"]}
    assert set(missing) <= axes


def test_gap_contracts_are_not_duplicated_when_the_axis_already_exists():
    """Re-issuing a question that already exists returns the same pages."""
    settings = _settings()
    state = _planned_state(["regulation"])
    first = asyncio.run(_run_planner(settings, state))
    state["sub_questions"] = first["sub_questions"]
    second = asyncio.run(_run_planner(settings, state))
    questions = [c["question"] for c in second["sub_questions"]]
    assert len(questions) == len(set(questions)), "duplicate contracts issued"


def test_no_gap_contracts_when_nothing_is_missing():
    """A complete pass must not invent work — that is the drift being fixed."""
    settings = _settings()
    state = _planned_state([])
    state["focus"]["report"]["missing"] = []
    state["focus"]["report"]["thin"] = []
    out = asyncio.run(_run_planner(settings, state))
    axes = {str(c.get("axis", "")) for c in out["sub_questions"]}
    assert "enterprise_adoption" not in axes
    assert "regulation" not in axes


# ---------------------------------------------------------------------------
# Drift must reach the decision
# ---------------------------------------------------------------------------


def test_high_drift_forces_a_redirect_decision():
    """Drift 0.58 must expand with a redirect reason, not finalize.

    The live report carried drift=0.58 and round 3 still repeated itself; the
    depth controller has to act on the number.
    """
    from app.core.depth_controller import decide_with_checks

    state = _planned_state(["enterprise_adoption", "regulation"])
    state.update({
        "confidence": 0.9,                      # high confidence must NOT win
        "confidence_history": [0.6, 0.8, 0.9],
        "critique": {"is_sufficient": True, "improved_queries": [], "reason": "ok"},
    })
    decision, checks = decide_with_checks(state)
    assert checks["focus"]["drifted"] is True
    assert checks["focus"]["redirect"] is True
    assert decision == "expand"
    assert "drift" in checks["decision_reason"].lower()


def test_drift_redirect_beats_a_sufficient_critic():
    """Size and confidence look healthy on a drifted run — that is the trap."""
    from app.core.depth_controller import decide
    from app.core.usage import clear_run_usage, start_run_usage

    settings = _settings()
    start_run_usage("focus-drift-test", settings, mode="standard")
    try:
        state = _planned_state(["enterprise_adoption"])
        state.update({
            "confidence": 0.95,
            "critique": {"is_sufficient": True, "improved_queries": [], "reason": "ok"},
        })
        assert decide(state) == "expand"
    finally:
        clear_run_usage()


def test_concentration_alone_redirects_a_broad_question():
    """A broad question answered from one dimension is concentrated, not done."""
    from app.core.depth_controller import decide_with_checks

    state = _planned_state(["regulation"])
    state["focus"]["report"].update({
        "drift": 0.0, "off_query_share": 0.0, "drifted": False,
        "concentration": 0.95, "concentrated": True, "is_narrow": False,
    })
    _, checks = decide_with_checks(state)
    assert checks["focus"]["concentrated"] is True
    assert checks["focus"]["redirect"] is True


def test_a_narrow_answered_question_is_not_redirected():
    """Proportionality: one dimension is a complete answer to a narrow question."""
    from app.core.depth_controller import decide_with_checks

    state = _planned_state([])
    state["focus"]["report"].update({
        "drift": 0.0, "drifted": False, "off_query_share": 0.0,
        "concentration": 1.0, "concentrated": False, "is_narrow": True,
    })
    _, checks = decide_with_checks(state)
    assert checks["focus"]["redirect"] is False


# ---------------------------------------------------------------------------
# Domain agnosticism
# ---------------------------------------------------------------------------


def test_generated_gap_questions_contain_no_hardcoded_topics():
    """The mechanism must work for a coffee question without naming AI.

    A hardcoded taxonomy is the original bug; this asserts the gap path cannot
    reintroduce one, because the question text is built from the dimension label
    and the query's own concept.
    """
    settings = _settings()
    missing = ["enterprise_adoption", "energy_environment", "regulation",
               "open_source_competition", "labor_social_impact"]
    state = _planned_state(missing)
    out = asyncio.run(_run_planner(settings, state))

    banned = ("ai ", "ai(", "artificial intelligence", "gpu", "datacenter",
              "frontier model", "llm", "benchmark")
    for contract in out["sub_questions"]:
        text = str(contract.get("question", "")).lower()
        for term in banned:
            assert term not in text, f"hardcoded topic '{term}' in: {text}"

    # And the questions must still be ABOUT the user's question.
    gap_texts = [
        str(c["question"]).lower() for c in out["sub_questions"]
        if str(c.get("axis", "")) in set(missing)
    ]
    assert gap_texts
    assert any("coffee" in t for t in gap_texts)


def test_gap_contracts_for_a_completely_different_domain():
    """Same code path, unrelated subject — no taxonomy carries over."""
    settings = _settings()
    state = _planned_state(["harmonic_analysis", "performance_practice"])
    state["query"] = "How did sonata form develop in the classical period?"
    state["focus"]["report"]["query"] = state["query"]
    out = asyncio.run(_run_planner(settings, state))
    axes = {str(c.get("axis", "")) for c in out["sub_questions"]}
    assert {"harmonic_analysis", "performance_practice"} <= axes


# ---------------------------------------------------------------------------
# The gap must survive into what search actually issues
# ---------------------------------------------------------------------------


def test_search_node_issues_the_gap_questions():
    """End of the chain: the gap contracts must be searched, not just planned.

    A contract that never reaches `search_node` is indistinguishable from the
    textual gap this change replaces.
    """
    queried = []

    class _RecordingSearch:
        def __init__(self, settings):
            self.settings = settings
            self.health = None

        async def run_search(self, queries, **kwargs):
            # queries arrive as (text, search_type) pairs.
            queried.append([
                q[0] if isinstance(q, (tuple, list)) else str(q) for q in queries
            ])
            return []

    settings = _settings()
    missing = ["enterprise_adoption", "energy_environment", "labour_impact"]
    state = _planned_state(missing)
    planned = asyncio.run(_run_planner(settings, state))
    state.update(planned)

    graph = wf.create_workflow(_DeadLLM(settings), search_client=_RecordingSearch(settings))
    asyncio.run(graph.nodes["search"].ainvoke(state))

    issued = " ".join(" ".join(batch) for batch in queried).lower()
    for dimension in missing:
        assert dimension.replace("_", " ") in issued, (
            f"gap dimension '{dimension}' was planned but never searched"
        )
