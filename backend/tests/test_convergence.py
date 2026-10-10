"""Convergence on a fundamental gap, and no manufactured winners.

The failure these tests pin, in two parts:

1. The loop reopened for an unsourced planned angle AFTER the reviewer had
   already established, twice, that no source provides the requested ranking. The
   angle was unsourced because the evidence does not exist — more searching
   cannot change that — so the loop must converge.

2. Asked for a #1, the answer reached for an analogy, a proxy or an unrelated
   finding and presented it as the winner. A proxy may explain context; it may
   never substitute for the requested ranking. If the evidence supports no
   defensible #1, the answer says so.

Flow asserted here:
  Query -> Interpretation Lock -> Research -> Evidence Review ->
  Fundamental Gap Check -> Converge -> Answer

No test asserts on a real subject; the stand-ins exist to make the assertions
concrete, not because the code knows them.
"""

import re
import tokenize
from io import StringIO

from app.agents.convergence import (
    FUNDAMENTAL_GAP_ROUNDS,
    FundamentalGap,
    assess_convergence,
    assess_fundamental_gap,
    fundamental_gap_language,
    is_single_winner_query,
    manufactured_winner,
    render_convergence_contract,
)
from app.core.depth.controller import decide_with_checks

QUERY = "what is the most demanding job in 2027"

# The reviewer's repeated conclusion: the KIND of evidence needed is absent.
GAP_REVIEW = (
    "no source provides a ranking of occupations by strain; the remaining "
    "evidence is indirect and contextual"
)
# A coverage complaint, which searching CAN fix. Must never converge on this.
COVERAGE_REVIEW = "2 uncovered angles remain and one claim needs corroboration"


# ---------------------------------------------------------------------------
# Recognising the question shape
# ---------------------------------------------------------------------------


def test_single_winner_questions_are_recognised():
    for query in (
        "what is the most demanding job in 2027",
        "which is best",
        "the highest risk country",
        "what is the single best option",
        "who is the number one provider",
    ):
        assert is_single_winner_query(query), query


def test_ordinary_questions_do_not_demand_a_ranking():
    for query in (
        "what is the population of Malawi in 2024",
        "how does TCP congestion control work",
        "explain the water cycle",
    ):
        assert not is_single_winner_query(query), query


# ---------------------------------------------------------------------------
# Fundamental gap vs coverage gap — the distinction that makes convergence safe
# ---------------------------------------------------------------------------


def test_the_required_kind_of_evidence_being_absent_is_a_fundamental_gap():
    for text in (
        "no source provides the requested ranking",
        "the evidence is only indirect and contextual",
        "cannot be ranked because there is no common measure",
        "no study compares the two",
        "the sources do not rank these occupations",
    ):
        assert fundamental_gap_language(text), text


def test_a_coverage_complaint_is_not_a_fundamental_gap():
    """An unsourced angle may still be findable; it must not stop the loop."""
    for text in (
        "2 uncovered angles remain",
        "the evidence is thin on one dimension",
        "this claim needs corroboration from a second publisher",
        "angle under-sourced (1/2): grid regulation",
    ):
        assert not fundamental_gap_language(text), text


def test_a_single_round_does_not_converge():
    """One round is a slow start, not a property of the question."""
    gap = assess_fundamental_gap(QUERY, [GAP_REVIEW])
    assert gap.identified is False
    assert gap.rounds == 1


def test_the_same_gap_in_consecutive_rounds_converges():
    first = assess_fundamental_gap(QUERY, [GAP_REVIEW])
    second = assess_fundamental_gap(QUERY, [GAP_REVIEW], history=[first.signature])
    assert second.identified is True
    assert second.rounds >= FUNDAMENTAL_GAP_ROUNDS
    assert second.should_converge is True


def test_alternating_gaps_do_not_converge():
    """Different gaps on different rounds is a changing picture, not a settled one."""
    first = assess_fundamental_gap(QUERY, ["no source provides the requested ranking"])
    other = assess_fundamental_gap(
        QUERY, ["the evidence is only indirect"],
        history=[first.signature],
    )
    # The second round reports a DIFFERENT kind of gap, so no repeat yet.
    assert other.identified is False


def test_a_coverage_review_never_converges_however_often_repeated():
    history = ["uncovered angle"] * 5
    gap = assess_fundamental_gap(QUERY, [COVERAGE_REVIEW], history=history)
    assert gap.identified is False


def test_the_gap_names_the_missing_evidence():
    first = assess_fundamental_gap(QUERY, [GAP_REVIEW])
    gap = assess_fundamental_gap(QUERY, [GAP_REVIEW], history=[first.signature])
    assert gap.missing_evidence
    assert "ranking" in gap.missing_evidence.lower() or "ranks" in gap.missing_evidence.lower()


def test_assessment_is_total_on_garbage():
    for query, reviews, history in (
        ("", [], []),
        (QUERY, [], []),
        (QUERY, [None, ""], [None]),
        (None, ["text"], ["text"]),
    ):
        gap = assess_fundamental_gap(query, reviews, history=history)
        assert isinstance(gap, FundamentalGap)


# ---------------------------------------------------------------------------
# The writer contract
# ---------------------------------------------------------------------------


def _converged():
    first = assess_fundamental_gap(QUERY, [GAP_REVIEW])
    return assess_fundamental_gap(QUERY, [GAP_REVIEW], history=[first.signature])


def test_the_contract_forbids_manufacturing_a_winner():
    contract = render_convergence_contract(QUERY, _converged())
    low = contract.lower()
    assert "no defensible #1" in low
    assert "do not name a winner" in low
    assert "analogy" in low and "proxy" in low and "inference" in low
    assert "may never substitute for the requested ranking" in low


def test_the_contract_offers_a_cluster_when_one_exists():
    contract = render_convergence_contract(
        QUERY, _converged(), cluster=["seafaring", "obstetrics"]
    )
    assert "seafaring" in contract and "obstetrics" in contract
    assert "not as a ranking" in contract.lower()


def test_the_contract_requires_removing_irrelevant_evidence():
    contract = render_convergence_contract(QUERY, _converged())
    low = contract.lower()
    assert "remove irrelevant evidence" in low
    assert "off-target" in low


def test_the_contract_names_the_missing_evidence_and_what_would_settle_it():
    contract = render_convergence_contract(QUERY, _converged())
    assert "missing" in contract.lower()
    assert "settle" in contract.lower()


def test_no_contract_when_the_gap_is_not_established():
    assert render_convergence_contract(QUERY, FundamentalGap()) == ""


# ---------------------------------------------------------------------------
# Detecting a manufactured winner
# ---------------------------------------------------------------------------


def test_naming_a_winner_after_converging_is_a_violation():
    answer = "The most demanding job is deep-sea fishing, based on the closest comparable data."
    assert manufactured_winner(answer, QUERY, _converged())


def test_stating_no_defensible_winner_is_not_a_violation():
    answer = (
        "No defensible #1 can be established: no source ranks occupations by "
        "strain. Deep-sea fishing and midwifery are both examples of high-strain "
        "work, but nothing compares them."
    )
    assert manufactured_winner(answer, QUERY, _converged()) == []


def test_no_violation_before_the_gap_is_established():
    """Before convergence the ranking gate governs, not this check."""
    answer = "The most demanding job is deep-sea fishing."
    assert manufactured_winner(answer, QUERY, FundamentalGap()) == []


def test_the_full_assessment_reports_gap_and_violation_together():
    first = assess_fundamental_gap(QUERY, [GAP_REVIEW])
    history = [first.signature]
    report = assess_convergence(
        "The most demanding job is deep-sea fishing.", QUERY, [GAP_REVIEW], history=history
    )
    assert report["single_winner_question"] is True
    assert report["gap"]["identified"] is True
    assert report["manufactured_winner"]

    clean = assess_convergence(
        "No defensible #1 can be established from the available evidence.",
        QUERY, [GAP_REVIEW], history=history,
    )
    assert clean["manufactured_winner"] == []


# ---------------------------------------------------------------------------
# The decision engine converges
# ---------------------------------------------------------------------------


def _loop_state(**over):
    state = {
        "query": QUERY,
        "iteration": 2,
        "max_iterations": 5,
        "mode": "standard",
        "confidence": 0.5,
        "confidence_history": [0.45, 0.5],
        "sub_questions": [
            {"id": 1, "axis": "a", "question": "qa", "minimum_sources": 1},
            {"id": 2, "axis": "b", "question": "qb", "minimum_sources": 1},
        ],
        "facts": [
            {"claim": "a finding", "source": "https://x.example/1", "verified": True,
             "sub_question": "qa"}
        ],
        "search_results": [],
        "focus": {"report": {}},
        "critique": {"is_sufficient": False, "improved_queries": ["more"],
                     "reason": "gaps", "confidence": 0.4},
    }
    state.update(over)
    return state


def test_a_converged_fundamental_gap_finalizes():
    state = _loop_state(convergence={
        "identified": True, "rounds": 2,
        "reason": "no source ranks occupations by strain",
    })
    decision, checks = decide_with_checks(state)
    assert decision == "finalize"
    # The reason is the gap's own wording, which names the finding directly.
    assert "ranks" in checks["decision_reason"].lower() or "gap" in checks["decision_reason"].lower()


def test_the_convergence_check_preempts_an_uncovered_angle():
    """An unsourced angle must NOT reopen the loop once the gap is established.

    This is the reported failure: a planned angle remains unsourced, so the run
    searches again even though the reviewer has already concluded the evidence
    does not exist.
    """
    state = _loop_state(convergence={"identified": True, "rounds": 2, "reason": GAP_REVIEW})
    state["sub_questions"] = [
        {"id": 1, "axis": "a", "question": "qa", "minimum_sources": 1},
    ]
    state["facts"] = []  # nothing covered -> uncovered_axes would force an expand
    state["focus"] = {"report": {}}
    decision, _ = decide_with_checks(state)
    assert decision == "finalize"


def test_an_evidence_gap_still_expands_without_convergence():
    """Regression guard: convergence must not swallow ordinary expansion."""
    state = _loop_state(convergence={})
    decision, _ = decide_with_checks(state)
    assert decision == "expand"


# ---------------------------------------------------------------------------
# The instruction reaches the writer
# ---------------------------------------------------------------------------


def test_the_synthesizer_renders_the_convergence_contract():
    import inspect

    from app.agents import synthesizer

    source = inspect.getsource(synthesizer.synthesize)
    assert "render_convergence_contract" in source


def test_the_convergence_reaches_the_writer_context():
    source = open("app/graph/workflow.py").read()
    assert '"convergence": state.get("convergence")' in source


def test_the_convergence_module_names_no_subject():
    """Domain agnosticism: the rules are about evidence, not topics."""
    import ast

    source = open("app/agents/convergence.py").read()
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                docstring_lines.update(range(body.lineno, (body.end_lineno or 0) + 1))

    topics = ("demanding", "job", "seafaring", "obstetrics", "strain", "burnout",
              "surgeon", "ai", "cybersecurity", "finance", "malawi")
    offenders = []
    for tok in tokenize.generate_tokens(StringIO(source).readline):
        if tok.type != tokenize.STRING or tok.start[0] in docstring_lines:
            continue
        for topic in topics:
            if re.search(rf"\b{topic}\b", tok.string, re.IGNORECASE):
                offenders.append((tok.start[0], topic))
    assert not offenders, f"executable subject strings in convergence.py: {offenders}"
