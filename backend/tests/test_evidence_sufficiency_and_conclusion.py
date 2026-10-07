"""Evidence-sufficiency and conclusion-gate fixes on `evidence-sufficiency`.

Four defects, each pinned by a test that fails on the pre-fix code:

1. The supported-cluster wording was unreachable in production. Both renderers
   accepted a `cluster=` argument that the live call sites never passed, so a
   run whose evidence supported a shortlist fell through to "the evidence
   supports no answer" — the gate over-refusing instead of naming the group.

2. A degraded run could not stop. DEGRADED_CAP (0.55) sits below every mode
   target (0.60-0.85), so the confidence term of `sufficiency_met` was
   permanently false and each pass re-extracted the same sources until the
   iteration or budget ceiling intervened.

3. `defensible_number_one` was a tautology (`x or False`) with no consumer.

4. Two "evidence gaps remain" predicates with different scopes read like
   duplicates, and `evidence_sufficient` was computed but read by nothing.
"""
from app.agents import convergence as conv_mod
from app.agents.convergence import FundamentalGap, render_convergence_contract
from app.agents.report_consistency import (
    STATUS_CLUSTER,
    STATUS_NO_NUMBER_ONE,
    assess_report_consistency,
    render_consistency_contract,
    report_status,
)
from app.agents.synthesizer import _basis_candidates
from app.core import depth_controller
from app.core.config import Settings
from app.core.confidence import DEGRADED_CAP, compute_confidence

SINGLE_WINNER_QUERY = "Which database is best for a startup: PostgreSQL or MongoDB?"


def _settings(**kw):
    return Settings(groq_api_key="k", _env_file=None, **kw)


# ---------------------------------------------------------------------------
# 1. Supported cluster is live
# ---------------------------------------------------------------------------

SHORTLIST_BASIS = {
    "verdict": "shortlist",
    "candidates": ["PostgreSQL", "MongoDB", "SQLite"],
    "has_comparison": False,
    "reason": "the evidence gives examples but does not compare them",
}
CONVERGED = {
    "identified": True,
    "rounds": 2,
    "reason": "no source compares them on a common measure",
    "signature": "no source compares",
    "missing_evidence": "a head-to-head benchmark",
}


def test_basis_candidates_reads_the_flat_shape():
    assert _basis_candidates(SHORTLIST_BASIS) == ["PostgreSQL", "MongoDB", "SQLite"]


def test_basis_candidates_reads_the_nested_shape():
    # The router/audit path persists the basis nested; the synthesis path reads
    # it flat. Both must yield the cluster or the fix only works on one path.
    assert _basis_candidates({"basis": SHORTLIST_BASIS}) == [
        "PostgreSQL", "MongoDB", "SQLite",
    ]


def test_basis_candidates_dedupes_and_drops_junk():
    messy = {
        "verdict": "shortlist",
        "candidates": ["PostgreSQL", "postgresql", "  ", None, "", "x" * 200],
    }
    assert _basis_candidates(messy) == ["PostgreSQL"]


def test_basis_candidates_tolerates_non_dict_and_bad_types():
    assert _basis_candidates("not a dict") == []
    assert _basis_candidates(None) == []
    assert _basis_candidates({"candidates": "a string"}) == []
    assert _basis_candidates({"candidates": 7}) == []
    assert _basis_candidates({}) == []


def test_cluster_reaches_the_consistency_contract():
    """The headline fix: a supported shortlist renders as a cluster."""
    status = report_status(
        SINGLE_WINNER_QUERY,
        convergence=CONVERGED,
        ranking_basis=SHORTLIST_BASIS,
        cluster=_basis_candidates(SHORTLIST_BASIS),
    )
    contract = render_consistency_contract(status)
    assert status.status == STATUS_NO_NUMBER_ONE
    assert status.cluster == ["PostgreSQL", "MongoDB", "SQLite"]
    assert "SUPPORTED CLUSTER" in contract
    assert "PostgreSQL" in contract
    # The over-refusing fallback must NOT be what a supported cluster gets.
    assert "supports no answer" not in contract
    # Ranking language is still forbidden either way.
    assert "DO NOT use ranking language" in contract


def test_cluster_reaches_the_convergence_contract():
    gap = FundamentalGap(
        identified=True,
        rounds=2,
        reason=CONVERGED["reason"],
        signature=CONVERGED["signature"],
        missing_evidence=CONVERGED["missing_evidence"],
    )
    contract = render_convergence_contract(
        SINGLE_WINNER_QUERY, gap, cluster=_basis_candidates(SHORTLIST_BASIS)
    )
    assert "supported CLUSTER" in contract
    assert "PostgreSQL" in contract
    assert "supports no answer" not in contract
    # The winner prohibition is unconditional and must survive the cluster path.
    assert "do NOT name a winner" in contract


def test_without_candidates_the_no_answer_fallback_still_fires():
    """Guard against over-correcting into inventing a group."""
    empty = {"verdict": "shortlist", "candidates": []}
    status = report_status(
        SINGLE_WINNER_QUERY, convergence=CONVERGED, ranking_basis=empty, cluster=[]
    )
    contract = render_consistency_contract(status)
    assert status.cluster == []
    assert "supports no answer" in contract
    assert "SUPPORTED CLUSTER" not in contract


def test_shortlist_basis_alone_yields_the_cluster_status():
    """No convergence diagnosis, but the basis says shortlist -> STATUS_CLUSTER."""
    status = report_status(
        SINGLE_WINNER_QUERY,
        convergence={"identified": False},
        ranking_basis=SHORTLIST_BASIS,
        cluster=_basis_candidates(SHORTLIST_BASIS),
    )
    assert status.status == STATUS_CLUSTER
    assert status.forbids_ranking is True
    assert status.allowed_ranking is False
    assert "SUPPORTED CLUSTER" in render_consistency_contract(status)


def test_ranked_basis_still_allows_ranking():
    """The cluster wiring must not disturb the one verdict that permits order."""
    status = report_status(
        SINGLE_WINNER_QUERY,
        convergence={"identified": False},
        ranking_basis={"verdict": "ranked"},
        cluster=["A", "B"],
    )
    assert status.forbids_ranking is False
    assert status.allowed_ranking is True
    assert render_consistency_contract(status) == ""


def test_audit_records_the_cluster_it_measured_against():
    """A report that correctly presented a supported cluster must not be
    audited as if it had invented one."""
    audit = assess_report_consistency(
        SINGLE_WINNER_QUERY,
        [("Comparison", "PostgreSQL and MongoDB form a supported cluster of "
                        "options with comparable evidence, not a ranking.")],
        convergence=CONVERGED,
        ranking_basis=SHORTLIST_BASIS,
        cluster=_basis_candidates(SHORTLIST_BASIS),
    )
    assert audit["status"]["cluster"] == ["PostgreSQL", "MongoDB", "SQLite"]


def test_audit_still_flags_a_manufactured_winner():
    audit = assess_report_consistency(
        SINGLE_WINNER_QUERY,
        [("Comparison", "PostgreSQL is the strongest candidate for a startup.")],
        convergence=CONVERGED,
        ranking_basis=SHORTLIST_BASIS,
        cluster=_basis_candidates(SHORTLIST_BASIS),
    )
    assert audit["violations"], "ranking language must still be caught with a cluster present"


# ---------------------------------------------------------------------------
# 2. A degraded run terminates instead of looping to the wall
# ---------------------------------------------------------------------------


def _fact(claim, source, dim, **kw):
    base = {"claim": claim, "source": source, "verified": True, "sub_question": dim,
            "is_primary": True}
    base.update(kw)
    return base


def _complete_state(**overrides):
    """Two planned axes, both covered by corroborated primary facts.

    This is the evidence shape the fix is scoped to: nothing structural is
    missing, so the ONLY thing holding confidence under target is the cap.
    """
    base = {
        "query": "cost and mechanism of X",
        "iteration": 2,
        "max_iterations": 4,
        "confidence": 0.55,
        "confidence_history": [0.8, 0.7, 0.55],
        "mode": "standard",
        # The critic keeps hedging, which is what a run judging unrewritten
        # extracted text looks like: no structural gap, but no verdict either.
        "critique": {"is_sufficient": False, "improved_queries": ["more evidence"],
                     "reason": "extracted claims are unrewritten"},
        "contradictions": [],
        "sub_questions": [
            {"axis": "evidence", "question": "what is the cost data",
             "minimum_sources": 1},
            {"axis": "mechanism", "question": "how does the mechanism works",
             "minimum_sources": 1},
        ],
        "search_results": [
            {"url": "https://gov.uk/x", "sub_question": "what is the cost data"},
            {"url": "https://who.int/y", "sub_question": "how does the mechanism works"},
        ],
        "facts": [
            _fact("Global spending reached 200 billion dollars in 2025",
                  "https://gov.uk/x", "what is the cost data",
                  corroborating_sources=["https://gov.uk/x", "https://who.int/d"],
                  corroboration_count=2),
            _fact("The mechanism works via a multi-stage pipeline process",
                  "https://who.int/y", "how does the mechanism works"),
        ],
    }
    base.update(overrides)
    return base


def test_engine_reports_degraded_capped_only_for_the_degradation_cap():
    facts = _complete_state()["facts"]
    clean = compute_confidence(facts=facts, critique={}, iteration=1, max_iterations=3,
                              degraded=[], provider_degraded=False)
    degraded = compute_confidence(facts=facts, critique={}, iteration=1, max_iterations=3,
                                  degraded=["synthesizer"], provider_degraded=False)
    assert degraded["degraded_capped"] is True
    assert clean["degraded_capped"] is False


def test_degraded_cap_keeps_confidence_below_sufficiency():
    """The cap must keep doing its job — this fix changes termination only."""
    facts = _complete_state()["facts"]
    result = compute_confidence(facts=facts, critique={}, iteration=1, max_iterations=3,
                                degraded=["synthesizer"], provider_degraded=False)
    assert result["overall"] <= DEGRADED_CAP
    assert result["overall"] < 0.75, "a degraded run must never read as sufficient"


def test_degraded_run_finalizes_instead_of_looping():
    state = _complete_state(confidence_degraded_capped=True)
    checks = depth_controller.evaluate(state, _settings())
    assert checks["degraded_capped"] is True
    assert checks["degraded_stop"] is True
    assert depth_controller.decide(state, _settings()) == "finalize"


def test_same_state_without_the_degraded_flag_still_expands():
    """Proves the stop is caused by the cap, not by the fixture being easy."""
    state = _complete_state()
    checks = depth_controller.evaluate(state, _settings())
    assert checks["degraded_stop"] is False
    assert depth_controller.decide(state, _settings()) == "expand"


def test_degraded_stop_does_not_apply_when_confidence_reached_target():
    state = _complete_state(confidence=0.9, confidence_degraded_capped=True)
    checks = depth_controller.evaluate(state, _settings())
    assert checks["degraded_stop"] is False, "nothing to excuse once target is met"


def test_degraded_stop_does_not_override_an_uncovered_axis():
    state = _complete_state(
        confidence_degraded_capped=True,
        sub_questions=[
            {"axis": "evidence", "question": "q1", "minimum_sources": 1},
            {"axis": "mechanism", "question": "q2", "minimum_sources": 1},
            {"axis": "outlook", "question": "q3", "minimum_sources": 1},
        ],
    )
    checks = depth_controller.evaluate(state, _settings())
    assert checks["degraded_stop"] is False
    assert depth_controller.decide(state, _settings()) == "expand"


def test_degraded_stop_does_not_override_an_uncorroborated_claim():
    state = _complete_state(confidence_degraded_capped=True)
    state["facts"] = [dict(state["facts"][0], corroboration_count=1,
                           corroborating_sources=None)]
    checks = depth_controller.evaluate(state, _settings())
    assert checks["degraded_stop"] is False
    assert depth_controller.decide(state, _settings()) == "expand"


def test_degraded_stop_does_not_override_a_severe_contradiction():
    state = _complete_state(confidence_degraded_capped=True)
    state["contradictions"] = [
        {"claim_a": "spending reached 200 billion dollars in 2025",
         "claim_b": "spending fell in 2025", "severity": 0.9, "resolved": False},
    ]
    checks = depth_controller.evaluate(state, _settings())
    assert checks["degraded_stop"] is False


def test_degraded_stop_reason_is_honest():
    state = _complete_state(confidence_degraded_capped=True)
    decision, checks = depth_controller.decide_with_checks(state, _settings())
    assert decision == "finalize"
    assert "degraded extraction" in checks["decision_reason"]


def test_confidence_cap_shortfall_is_explained_in_decision_reasons():
    """A stop must never be explained as 'evidence sufficient' when the cap is
    what stopped it."""
    state = _complete_state(confidence_degraded_capped=True)
    checks = depth_controller.evaluate(state, _settings())
    joined = " ".join(checks["decision_reasons"])
    assert "degraded-extraction cap" in joined


def test_hard_walls_still_win_over_the_degraded_stop():
    state = _complete_state(confidence_degraded_capped=True, iteration=9, max_iterations=4)
    assert depth_controller.hard_wall_reached(state, _settings()) is True


# ---------------------------------------------------------------------------
# 3. Dead logic removed
# ---------------------------------------------------------------------------


def test_defensible_number_one_is_gone():
    assert not hasattr(FundamentalGap, "defensible_number_one")
    assert "defensible_number_one" not in FundamentalGap(
        identified=True, rounds=2
    ).to_dict()
    source = open(conv_mod.__file__, encoding="utf-8").read()
    assert "defensible_number_one" not in source


def test_fundamental_gap_rounds_comment_matches_its_value():
    """The constant and the comment that argued for a different number."""
    source = open(conv_mod.__file__, encoding="utf-8").read()
    head = source[: source.index("_SINGLE_WINNER_RE")]
    assert "FUNDAMENTAL_GAP_ROUNDS = 2" in head
    # The stale justification ("Three: ... third confirms") must be gone.
    assert "the third confirms" not in head


# ---------------------------------------------------------------------------
# 4. Scope of the two "gaps remain" predicates
# ---------------------------------------------------------------------------


def test_router_gate_is_documented_as_the_narrower_subset():
    from app.graph import evidence as graph_evidence

    source = open(graph_evidence.__file__, encoding="utf-8").read()
    doc = source[source.index("def _evidence_gaps_remain"): source.index("facts = [")]
    assert "SUBSET" in doc
    assert "evidence_sufficient" in doc


def test_evidence_sufficient_is_reported_not_silently_dropped():
    """It has no decision consumer by design; the decision REASON must carry
    the shortfall so the trace cannot claim 'evidence sufficient'."""
    state = _complete_state(confidence=0.4)
    checks = depth_controller.evaluate(state, _settings())
    assert checks["evidence_sufficient"] is False
    assert checks["decision_reason"] != "evidence sufficient"
