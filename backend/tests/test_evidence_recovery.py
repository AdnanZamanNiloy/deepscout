"""Evidence-recovery regressions: a validation failure must not go straight to
answer generation.

Two failure cases, both taken from live runs:

A) MSc research-topic recommendations — a search for "demanding M.Sc CS research
   topics" retrieved an education-paper abstract, a rip-current benchmark, a
   poultry-feed survey and an esports bibliometric analysis. The critic
   correctly reported `drift=0.50` and `uncovered_angles=3`, but the next pass
   re-searched the same ground, so the run kept failing the same gate.

B) 2027 skill-demand forecasting — every fact came from one publisher, so the
   critic reported `domains=1<2` and `concentration_on=...`. The follow-ups it
   generated were generic ("official statistics data") and did not seek an
   independent publisher, so the same concentration recurred.

What these tests pin:
  1. a validated defect is DIAGNOSED with the defect's own kind and target,
  2. replacement queries TARGET that defect (not a generic rephrase),
  3. concentration produces a query aimed at an equivalent INDEPENDENT
     publisher, explicitly excluding the dominant domain,
  4. drift re-scopes onto the plan axes that produced nothing,
  5. a recovery NEVER re-issues a search the run already executed,
  6. recovery rides the critique so the trace names the defect per query,
  7. an active validation failure blocks finalization while budget remains —
     the loop recovers instead of generating an answer.
"""
from __future__ import annotations

from app.core.depth.controller import decide_with_checks
from app.core.evidence_recovery import diagnose, replacement_queries


# --------------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------------

MSC_PLAN = [
    {
        "axis": "research_frontier_subfields",
        "question": "Which computer science subfields are most demanding in 2026?",
        "search_type": "academic",
    },
    {
        "axis": "master's_feasibility_and_scope",
        "question": "Which MSc-level projects are feasible in 12-18 months on shared compute?",
        "search_type": "academic",
    },
]

MSC_CRITIQUE = {
    "gate_failures": [
        "uncovered_angles=3",
        "planned_axis_uncovered=master's_feasibility_and_scope",
        "missing_primary_source=primary_source_for:research_frontier_subfields",
        "drift=0.50",
    ],
    "gaps": ["no evidence for angle: Which computer science subfields are most demanding in 2026?"],
    "stats": {"distinct_domains": 4},
    "is_sufficient": False,
}

# The live contamination: an education abstract, a rip-current benchmark and a
# poultry-feed survey retrieved for a computer-science topic query.
MSC_OFF_TOPIC_FACTS = [
    {"claim": "Differentiated learning affects outcomes among high school students",
     "source": "https://doi.org/10.47191/ijcsrr/v8-i3-29", "sub_question": "q"},
    {"claim": "Rip current detection is a benchmark task for coastal safety",
     "source": "https://arxiv.org/abs/2511.00001", "sub_question": "q"},
    {"claim": "Poultry feed protein sources show sustainability trends",
     "source": "https://ncbi.nlm.nih.gov/pmc/0001", "sub_question": "q"},
]

SKILLS_PLAN = [
    {
        "axis": "demand_signals",
        "question": "What will be the most in-demand technology skills in 2027?",
        "search_type": "statistical",
    },
    {
        "axis": "labour_market_evidence",
        "question": "What do labour-market surveys say about 2027 skill demand?",
        "search_type": "statistical",
    },
]

SKILLS_CRITIQUE = {
    "gate_failures": [
        "domains=1<2",
        "concentration_on=demand_signals=0.62",
        "missing_primary_source=primary_source_for:demand_signals",
    ],
    "gaps": [],
    "stats": {"distinct_domains": 1},
    "is_sufficient": False,
}

# Every fact from one publisher — the exact concentration the gate named.
SKILLS_SINGLE_DOMAIN_FACTS = [
    {"claim": f"cloud and AI skills dominate 2027 postings claim {i}",
     "source": f"https://thesame.example.org/insight{i}"}
    for i in range(6)
]


# --------------------------------------------------------------------------
# Case A — MSc research topics (irrelevant evidence / drift)
# --------------------------------------------------------------------------


def test_drift_is_diagnosed_with_its_own_kind():
    defects = diagnose(MSC_CRITIQUE, MSC_OFF_TOPIC_FACTS, plan=MSC_PLAN)
    kinds = {d["kind"] for d in defects}
    assert "drift" in kinds


def test_drift_recovery_re_scopes_onto_the_uncovered_plan_axis():
    """The recovery must aim at the axis that produced NOTHING, in the plan's
    own words — not reword the query that already drifted."""
    queries = replacement_queries(
        MSC_CRITIQUE, MSC_OFF_TOPIC_FACTS, plan=MSC_PLAN,
        query="suggest highly demanding research topic for M.sc in computer science",
    )
    texts = [q["query"] for q in queries]
    joined = " ".join(texts).lower()
    assert "msc-level projects" in joined, texts
    assert "most demanding" in joined or "subfields" in joined, texts


def test_drift_recovery_prioritises_primary_sources():
    queries = replacement_queries(
        MSC_CRITIQUE, MSC_OFF_TOPIC_FACTS, plan=MSC_PLAN,
        query="suggest highly demanding research topic for M.sc in computer science",
    )
    # At least one query must be publisher-scoped to primary/official domains.
    assert any("site:" in q["query"] for q in queries), queries


def test_recovery_queries_avoid_the_off_topic_publishers():
    """A drift recovery must not be aimed at the publishers whose pages were
    the contamination."""
    queries = replacement_queries(
        MSC_CRITIQUE, MSC_OFF_TOPIC_FACTS, plan=MSC_PLAN,
        query="suggest highly demanding research topic for M.sc in computer science",
    )
    joined = " ".join(q["query"].lower() for q in queries)
    assert "ncbi.nlm.nih.gov" not in joined
    assert "ijcsrr" not in joined


def test_every_recovery_query_carries_a_reason():
    queries = replacement_queries(
        MSC_CRITIQUE, MSC_OFF_TOPIC_FACTS, plan=MSC_PLAN,
        query="suggest highly demanding research topic for M.sc in computer science",
    )
    assert queries
    for item in queries:
        assert item["reason"], item
        assert item["kind"], item


# --------------------------------------------------------------------------
# Case B — 2027 skill-demand (concentration in a single domain)
# --------------------------------------------------------------------------


def test_concentration_is_diagnosed_from_the_measured_pool():
    defects = diagnose(SKILLS_CRITIQUE, SKILLS_SINGLE_DOMAIN_FACTS, plan=SKILLS_PLAN)
    concentration = [d for d in defects if d["kind"] == "concentration"]
    assert concentration, defects
    # The target must be the REAL publisher, never the `_unassigned` placeholder.
    assert concentration[0]["target"] == "thesame.example.org"


def test_concentration_recovery_seeks_an_independent_publisher():
    queries = replacement_queries(
        SKILLS_CRITIQUE, SKILLS_SINGLE_DOMAIN_FACTS, plan=SKILLS_PLAN,
        query="most in-demand tech skills 2027",
    )
    assert queries
    joined = " ".join(q["query"] for q in queries)
    assert "site:" in joined, "a concentration recovery must be publisher-scoped"
    assert "2027" in joined, queries


def test_concentration_and_single_domain_do_not_duplicate_queries():
    """`concentration_on=` and `domains=1<2` describe one defect for recovery,
    so they must not emit the same substitution twice under two labels."""
    queries = replacement_queries(
        SKILLS_CRITIQUE, SKILLS_SINGLE_DOMAIN_FACTS, plan=SKILLS_PLAN,
        query="most in-demand tech skills 2027",
    )
    texts = [q["query"] for q in queries]
    assert len(texts) == len(set(texts)), texts


def test_missing_primary_axis_gets_a_primary_source_query():
    queries = replacement_queries(
        SKILLS_CRITIQUE, SKILLS_SINGLE_DOMAIN_FACTS, plan=SKILLS_PLAN,
        query="most in-demand tech skills 2027",
    )
    primary = [q for q in queries if q["kind"] == "missing_primary"]
    assert primary, queries
    assert any("site:" in q["query"] for q in primary)


# --------------------------------------------------------------------------
# Cost control — a recovery must never re-spend an executed search
# --------------------------------------------------------------------------


def test_recovery_never_reissues_an_executed_search():
    first = replacement_queries(
        SKILLS_CRITIQUE, SKILLS_SINGLE_DOMAIN_FACTS, plan=SKILLS_PLAN,
        query="most in-demand tech skills 2027",
    )
    executed = [q["query"] for q in first]
    again = replacement_queries(
        SKILLS_CRITIQUE, SKILLS_SINGLE_DOMAIN_FACTS, plan=SKILLS_PLAN,
        query="most in-demand tech skills 2027",
        executed=executed,
    )
    assert not again, f"recovery re-issued executed searches: {[q['query'] for q in again]}"


def test_recovery_is_bounded():
    """A bounded per-pass query count is what keeps the recovery from becoming
    its own cost blow-out."""
    many = {
        "gate_failures": [
            "uncovered_angles=8",
            "domains=1<2",
            "drift=0.62",
            "concentration_on=x=0.62",
            "severe_conflicts=2",
            "facts=3<4",
            "planned_axis_uncovered=a",
            "planned_axis_uncovered=b",
            "planned_axis_uncovered=c",
            "missing_primary_source=primary_source_for:a",
            "missing_primary_source=primary_source_for:b",
        ],
        "gaps": ["no evidence for angle: x", "no evidence for angle: y", "no evidence for angle: z"],
        "stats": {"distinct_domains": 1},
        "is_sufficient": False,
    }
    queries = replacement_queries(many, SKILLS_SINGLE_DOMAIN_FACTS, plan=SKILLS_PLAN, limit=4)
    assert len(queries) <= 4


# --------------------------------------------------------------------------
# The loop must recover, not finalize, while the criticism is actionable
# --------------------------------------------------------------------------


def _state(critique, ledger=None, *, iteration=1, max_iterations=4, sufficient=False):
    return {
        "query": "suggest highly demanding research topic for M.sc in computer science",
        "critique": critique,
        "criticism_ledger": ledger or {},
        "iteration": iteration,
        "max_iterations": max_iterations,
        "confidence": 0.45,
        "facts": [],
        "sub_questions": MSC_PLAN,
        "is_sufficient": sufficient,
    }


def test_validation_failure_blocks_finalization_while_budget_remains():
    """The decisive contract: a run whose evidence failed validation must not
    proceed to answer generation while it can still search."""
    state = _state(MSC_CRITIQUE, iteration=1, max_iterations=4)
    # The critic's gate failures are tracked as actionable criticisms.
    from app.core.criticism_ledger import update as update_ledger

    state["criticism_ledger"] = update_ledger({}, MSC_CRITIQUE, [], iteration=0)
    decision, checks = decide_with_checks(state)
    assert decision == "expand", "validation failure must recover, not finalize"
    assert checks["active_criticism_count"] > 0


def test_validation_failure_still_stops_at_the_hard_wall():
    """Bounded: the recovery never runs past the iteration ceiling."""
    state = _state(MSC_CRITIQUE, iteration=4, max_iterations=4)
    from app.core.criticism_ledger import update as update_ledger

    state["criticism_ledger"] = update_ledger({}, MSC_CRITIQUE, [], iteration=0)
    decision, _ = decide_with_checks(state)
    assert decision == "finalize", "the hard wall must still bound the recovery"
