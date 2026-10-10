"""Convergence: the loop must STOP, not expand forever.

The failure these tests pin: every review produced more research instead of
converging. Source count reached 80 with important gaps still open, `drift` moved
from 0.77 to 0.78 without triggering a correction, and a query could spend many
rounds without reaching sufficient coverage.

Cause: the dimension-coverage channel had no attempt accounting. It regenerated
the same dimension every round with a fresh `(attempt N)` query, which the
executed-query memory could never match, so an unsupported dimension was
re-searched indefinitely. The claim ledger had exhaustion; dimensions did not.

The properties asserted here:
  * a dimension searched to exhaustion stops generating candidates;
  * an exhausted dimension is excluded from EVERY channel, not just one;
  * the depth controller finalizes when every remaining gap is exhausted;
  * high drift forces a redirect while actionable work remains, and stops
    forcing one when it does not;
  * a satisfiable corpus still converges, and does not stop prematurely.
"""

import asyncio

from app.core.config import Settings
from app.core.depth.controller import decide_with_checks
from app.core.investigation_planner import (
    _dimension_entry_key,
    select_investigations,
)
from app.core.investigation_state import (
    record_dimension_attempts,
)
from app.graph import workflow as wf

QUERY = "What are the current trends in artificial intelligence?"


def _settings(**kw):
    base = dict(groq_api_key="k", database_url=":memory:", _env_file=None)
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 1-4: per-dimension scoring, exhaustion, and no indefinite searching
# ---------------------------------------------------------------------------


def test_dimension_attempts_exhaust_after_the_budget():
    """A dimension that never yields evidence is marked, not searched forever."""
    state = {}
    state = record_dimension_attempts(state, ["regulation"], max_attempts=2)
    entry = state[_dimension_entry_key("regulation")]
    assert entry["status"] == "attempted"
    assert entry["attempts"] == 1

    state = record_dimension_attempts(state, ["regulation"], max_attempts=2)
    entry = state[_dimension_entry_key("regulation")]
    assert entry["status"] == "exhausted", "budget spent must exhaust the dimension"


def test_exhausted_dimension_is_excluded_from_every_channel():
    """One channel honouring the ledger is not enough.

    The dimension-coverage channel excluded exhausted dimensions but the
    primary-source channel did not, so an unsupported dimension kept consuming
    budget through the second door.
    """
    state = {
        "query": QUERY,
        "iteration": 2,
        "facts": [
            {"claim": "a capability result", "source": "https://x.example/1",
             "verified": True, "sub_question": "AI capability benchmark results"},
        ],
        "sub_questions": [
            {"id": 1, "axis": "capability", "question": "AI capability benchmark results",
             "minimum_sources": 1, "search_type": "academic"},
            {"id": 2, "axis": "enterprise_adoption",
             "question": "enterprise AI adoption rates", "minimum_sources": 1,
             "search_type": "statistical"},
        ],
        "investigation_state": {
            _dimension_entry_key("capability"): {
                "claim": "capability", "attempts": 2, "queries": [],
                "status": "exhausted", "max_attempts": 2,
            },
        },
        "focus": {"report": {"missing": ["capability", "enterprise_adoption"], "thin": []}},
        "coverage_searched": [],
        "executed_queries": [],
    }
    result = select_investigations(state, budget_cap=8)
    targets = {str(c.get("target", "")).lower() for c in result["selected"]}
    assert not any("capability" in t for t in targets), (
        f"exhausted dimension still selected: {sorted(targets)}"
    )
    assert any("adoption" in t for t in targets), "the actionable gap must be selected"


def test_only_the_highest_value_uncovered_dimension_leads():
    """REVIEW answers 'what is the single most valuable missing evidence?' by
    ranking, and the top candidate is the uncovered dimension."""
    state = {
        "query": QUERY,
        "iteration": 1,
        "facts": [
            {"claim": "a finding", "source": "https://x.example/1", "verified": True,
             "sub_question": "AI capability benchmark results"},
        ],
        "sub_questions": [
            {"id": 1, "axis": "capability", "question": "AI capability benchmark results",
             "minimum_sources": 1, "search_type": "academic"},
            {"id": 2, "axis": "regulation", "question": "AI regulation policy",
             "minimum_sources": 1, "search_type": "news"},
        ],
        "investigation_state": {},
        "focus": {"report": {"missing": ["regulation"], "thin": []}},
        "coverage_searched": [],
        "executed_queries": [],
    }
    result = select_investigations(state, budget_cap=8)
    assert result["selected"], "an uncovered dimension must produce a candidate"
    top = result["selected"][0]
    assert "regulation" in str(top.get("target", "")).lower()
    assert top["expected_gain"] > 0.0


def test_covered_dimension_is_not_researched_for_coverage():
    """Requirement 3: a dimension with sufficient evidence gets no COVERAGE work.

    A covered dimension may still attract a primary-source query when its
    evidence is secondary — that is a genuinely different deficiency — but it
    must never be re-searched just for coverage.
    """
    state = {
        "query": QUERY,
        "iteration": 2,
        "facts": [
            {"claim": "a capability result", "source": "https://www.nature.com/a",
             "verified": True, "sub_question": "AI capability benchmark results",
             "is_primary_source": True},
        ],
        "sub_questions": [
            {"id": 1, "axis": "capability", "question": "AI capability benchmark results",
             "minimum_sources": 1, "search_type": "academic"},
        ],
        "investigation_state": {},
        # Nothing missing and nothing thin: the dimension is satisfied.
        "focus": {"report": {"missing": [], "thin": []}},
        "coverage_searched": [],
        "executed_queries": [],
    }
    result = select_investigations(state, budget_cap=8)
    kinds = {
        (str(c.get("kind", "")), str(c.get("target", "")).lower())
        for c in result["selected"]
    }
    assert not any(kind == "dimension_coverage" for kind, _ in kinds), (
        f"covered dimension produced coverage work: {kinds}"
    )


def test_uncovered_dimension_outranks_a_covered_one():
    """Requirement 4: the missing dimension leads; the covered one does not."""
    state = {
        "query": QUERY,
        "iteration": 1,
        "facts": [
            {"claim": "a capability result", "source": "https://x.example/1",
             "verified": True, "sub_question": "AI capability benchmark results",
             "is_primary_source": True},
        ],
        "sub_questions": [
            {"id": 1, "axis": "capability", "question": "AI capability benchmark results",
             "minimum_sources": 1, "search_type": "academic"},
            {"id": 2, "axis": "regulation", "question": "AI regulation policy",
             "minimum_sources": 1, "search_type": "news"},
        ],
        "investigation_state": {},
        "focus": {"report": {"missing": ["regulation"], "thin": []}},
        "coverage_searched": [],
        "executed_queries": [],
    }
    result = select_investigations(state, budget_cap=8)
    coverage_targets = [
        str(c.get("target", "")).lower()
        for c in result["selected"]
        if c.get("kind") == "dimension_coverage"
    ]
    assert coverage_targets, "the uncovered dimension must be researched"
    assert "regulation" in coverage_targets[0], (
        f"uncovered dimension did not lead: {coverage_targets}"
    )
    assert not any("capability" in t for t in coverage_targets)


# ---------------------------------------------------------------------------
# 5: the hard convergence condition
# ---------------------------------------------------------------------------


def _convergence_state(missing, *, exhausted):
    state = {
        "query": QUERY,
        "iteration": 3,
        "max_iterations": 5,
        "mode": "standard",
        "confidence": 0.6,
        "confidence_history": [0.5, 0.55, 0.6],
        "critique": {"is_sufficient": False, "improved_queries": [],
                     "reason": "missing dimensions"},
        "sub_questions": [
            {"id": i + 1, "axis": a, "question": f"question for {a}",
             "minimum_sources": 1, "variants": []}
            for i, a in enumerate(missing)
        ],
        "facts": [
            {"claim": "a finding", "source": "https://x.example/1", "verified": True,
             "sub_question": "question for " + missing[0]},
        ] if missing else [],
        "search_results": [],
        "investigation_state": {},
        "focus": {"report": {
            "missing": list(missing), "thin": [], "coverage": 0.5,
            "drift": 0.78, "drifted": True, "off_query_share": 0.78,
            "concentration": 0.9, "concentrated": True, "is_narrow": False,
        }},
        "coverage_searched": [],
        "executed_queries": [],
    }
    if exhausted:
        from app.core.investigation_state import record_dimension_attempts

        inv = {}
        for axis in missing:
            inv = record_dimension_attempts(inv, [axis], max_attempts=2)
            inv = record_dimension_attempts(inv, [axis], max_attempts=2)
        state["investigation_state"] = inv
    return state


def test_high_drift_forces_a_redirect_while_action_remains():
    """Requirement 6: drift 0.78 must produce a corrective expand."""
    state = _convergence_state(["enterprise_adoption", "regulation"], exhausted=False)
    decision, checks = decide_with_checks(state)
    assert checks["focus"]["drifted"] is True
    assert checks["focus"]["actionable_left"] is True
    assert decision == "expand"
    assert "drift" in checks["decision_reason"].lower()


def test_high_drift_stops_forcing_a_redirect_once_gaps_are_exhausted():
    """Requirement 5: drift cannot be corrected by re-searching a dead end.

    Continuing to expand because drift is high, when every uncovered dimension has
    already been searched to exhaustion, is exactly the non-convergence being
    fixed.
    """
    state = _convergence_state(["enterprise_adoption", "regulation"], exhausted=True)
    decision, checks = decide_with_checks(state)
    assert checks["focus"]["drifted"] is True
    assert checks["focus"]["actionable_left"] is False
    assert decision == "finalize"
    assert "exhaustion" in checks["decision_reason"].lower()


def test_uncovered_axis_stops_forcing_passes_when_it_is_exhausted():
    """An unsupported axis is a finding, not an endless work order."""
    state = _convergence_state(["enterprise_adoption"], exhausted=True)
    state["focus"]["report"]["drifted"] = False
    state["focus"]["report"]["drift"] = 0.0
    state["focus"]["report"]["concentrated"] = False
    state["critique"] = {"is_sufficient": True, "improved_queries": [], "reason": "ok"}
    decision, checks = decide_with_checks(state)
    assert decision == "finalize", checks["decision_reason"]


# ---------------------------------------------------------------------------
# 6-8: end-to-end convergence
# ---------------------------------------------------------------------------


PLAN = [
    {"id": 1, "axis": "capability", "question": "AI capability benchmark results",
     "search_type": "academic", "minimum_sources": 1, "variants": []},
    {"id": 2, "axis": "enterprise_adoption", "question": "enterprise AI adoption rates",
     "search_type": "statistical", "minimum_sources": 1, "variants": []},
    {"id": 3, "axis": "labour_impact", "question": "AI impact on jobs",
     "search_type": "news", "minimum_sources": 1, "variants": []},
]


def _run_loop(*, corpus_satisfies_all: bool):
    """Drive the real graph and return (search_call_count, final_state)."""
    calls = {"n": 0}
    searched = []

    async def planner(llm, query, critique_feedback="", today="", **kw):
        calls["n"] += 1
        return [dict(c) for c in PLAN] if calls["n"] == 1 else []

    class Search:
        def __init__(self, settings):
            self.settings = settings
            self.health = None

        async def run_search(self, queries, **kw):
            batch = [q[0] if isinstance(q, (tuple, list)) else str(q) for q in queries]
            searched.append(batch)
            out = []
            for text in batch:
                if corpus_satisfies_all or any(
                    k in text.lower() for k in ("capability", "benchmark")
                ):
                    out.append({"url": f"https://t{abs(hash(text)) % 997}.example/p",
                                "title": "t", "snippet": "s", "content": "c",
                                "sub_question": text})
            return out

        async def run_grounding_search(self, query):
            return await self.run_search([query])

    settings = _settings(research_timeout_sec=30)
    # Save and restore the module-global agent seams. They are patched, not
    # mocked per-instance, so leaving them set leaks the stub into every later
    # test in the session (it broke test_perf_optimizations when run together).
    saved = {
        name: getattr(wf, name)
        for name in ("planner_agent", "summarizer_agent", "critic_agent",
                     "synthesizer_agent", "verify_facts")
    }
    wf.planner_agent = planner

    async def summarizer(llm, query, search_results=None, specialist_role="general", **kw):
        return [
            {"claim": f"F {r.get('sub_question', '')[:40]}", "source": r["url"],
             "confidence": 0.9, "verified": True,
             "sub_question": str(r.get("sub_question", "") or ""),
             "corroborating_sources": [r["url"], "https://other.example/ref"]}
            for r in (search_results or [])
        ]

    async def critic(llm, query, facts=None, iteration=1, max_iterations=3,
                     contradictions=None, **kw):
        return {"is_sufficient": False, "reason": "missing", "improved_queries": [],
                "confidence": 0.5}

    async def synthesizer(llm, query, facts, context=None):
        return "answer"

    wf.summarizer_agent = summarizer
    wf.critic_agent = critic
    wf.synthesizer_agent = synthesizer
    wf.verify_facts = lambda facts, search_results: facts

    class _LLM:
        def __init__(self, settings):
            self.settings = settings

    try:
        graph = wf.create_workflow(_LLM(settings), Search(settings))
        state = wf.build_initial_state(QUERY, 3, mode="standard")
        final = None

        async def drive():
            nonlocal final
            async for snap in graph.astream(state, stream_mode="values"):
                final = snap

        asyncio.run(drive())
        return len(searched), final
    finally:
        for name, original in saved.items():
            setattr(wf, name, original)


def test_loop_converges_when_the_corpus_can_satisfy_every_dimension():
    """The normal case: coverage completes and the loop stops on sufficiency."""
    rounds, final = _run_loop(corpus_satisfies_all=True)
    report = (final.get("focus") or {}).get("report") or {}
    assert report.get("missing") == [], f"gaps remained: {report.get('missing')}"
    assert rounds <= 4, f"took {rounds} search rounds to converge"
    ledger = {
        k: v.get("status")
        for k, v in (final.get("investigation_state") or {}).items()
        if k.startswith("dimension::")
    }
    assert not ledger, f"nothing should be exhausted when all gaps closed: {ledger}"


def test_loop_stops_instead_of_expanding_when_a_gap_is_unsatisfiable():
    """The reported failure: an unsupported dimension must not be searched forever.

    Before the fix this ran to the expansion wall (12 passes, 80+ sources) while
    the same gaps stayed open. It must mark the dimension exhausted and stop.
    """
    rounds, final = _run_loop(corpus_satisfies_all=False)
    ledger = {
        k: v.get("status")
        for k, v in (final.get("investigation_state") or {}).items()
        if k.startswith("dimension::")
    }
    exhausted = [k for k, v in ledger.items() if v == "exhausted"]
    assert exhausted, f"unsatisfiable dimensions must be exhausted: {ledger}"
    assert rounds <= 6, f"did not converge: {rounds} search rounds"
    assert rounds < 12, "must stop before the expansion wall, not at it"


def test_convergence_is_domain_agnostic():
    """Same mechanism, unrelated subject: no AI topic is referenced anywhere."""
    source = open("app/core/investigation_planner.py").read()
    executable_topics = ("artificial intelligence", "benchmark", "gpu", "datacenter")
    # The channel names and scores must not mention any subject.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"'):
            continue
        for topic in executable_topics:
            assert topic not in stripped.lower(), f"topic '{topic}' in: {stripped}"
