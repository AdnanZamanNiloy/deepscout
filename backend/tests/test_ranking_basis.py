"""A superlative needs a comparative basis. Without one, name no winner.

The failure these tests pin: the evidence holds a well-sourced strain finding
about seafaring AND a well-sourced strain finding about obstetrics. Nothing
compares them. The report calls them the "strongest candidates" — a superlative
manufactured by inference from two unrelated qualitative signals.

A superlative asserts a COMPARISON. Two unrelated findings are not one. Applied
to every "most / best / highest / worst" question:

  RANKED       comparable evidence supports ordering the candidates
  SHORTLIST    evidence supports candidates as examples but does not order them
  UNDETERMINED neither is justified

No test asserts on a real subject or metric; the stand-ins below exist to make
the assertions concrete, not because the code knows them.
"""

import re
import tokenize
from io import StringIO

from app.agents.ranking_basis import (
    RANKED,
    SHORTLIST,
    UNDETERMINED,
    assess_answer_ranking,
    assess_comparative_basis,
    is_superlative_query,
    ranking_violation,
    render_ranking_contract,
    winner_claims,
)

QUERY = "what is the most demanding job in 2027"

# Two separate findings. Neither compares the other.
SEPARATE = [
    {"claim": "Seafaring shows high injury and fatality rates", "verified": True},
    {"claim": "Obstetrics has high burnout rates among clinicians", "verified": True},
    {"claim": "Seafaring workers report long deployments", "verified": True},
    {"claim": "Obstetrics requires frequent night shifts", "verified": True},
]
# A comparison study: the same measure across both, with different values.
COMPARED = [
    {"claim": "Seafaring has a higher fatality rate than Obstetrics", "verified": True},
    {"claim": "Seafaring recorded 24 deaths per 100k workers", "verified": True},
    {"claim": "Obstetrics recorded 3 deaths per 100k workers", "verified": True},
]


# ---------------------------------------------------------------------------
# Recognising the question shape
# ---------------------------------------------------------------------------


def test_superlative_questions_are_recognised():
    for query in (
        "what is the most demanding job in 2027",
        "which option is best",
        "the highest risk country",
        "the worst performing sector",
        "which is the largest market",
    ):
        assert is_superlative_query(query), query


def test_ordinary_questions_are_not_superlative():
    for query in (
        "what is the population of Malawi in 2024",
        "how does TCP congestion control work",
        "explain the water cycle",
    ):
        assert not is_superlative_query(query), query


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def test_separate_findings_yield_a_shortlist_not_a_ranking():
    """The headline rule: examples do not become an ordering."""
    basis = assess_comparative_basis(QUERY, SEPARATE)
    assert basis.verdict == SHORTLIST
    assert basis.may_rank is False
    # And the candidates ARE identified, as examples.
    assert "Seafaring" in basis.candidates and "Obstetrics" in basis.candidates


def test_a_real_comparison_permits_a_ranking():
    basis = assess_comparative_basis(QUERY, COMPARED)
    assert basis.verdict == RANKED
    assert basis.may_rank is True
    assert basis.has_comparison is True


def test_a_shared_metric_across_candidates_counts_as_a_comparison():
    """A real comparison states the same measure for each candidate.

    The candidates recur across the pool (as they do in any study that reports
    more than one figure), and the values differ.
    """
    facts = [
        {"claim": "Seafaring recorded 24 deaths per 100k workers", "verified": True},
        {"claim": "Obstetrics recorded 3 deaths per 100k workers", "verified": True},
        {"claim": "Seafaring also led on injury severity", "verified": True},
        {"claim": "Obstetrics reported lower severity scores", "verified": True},
    ]
    basis = assess_comparative_basis(QUERY, facts)
    assert basis.verdict == RANKED
    assert basis.shared_metric


def test_identical_figures_are_not_a_comparison():
    """"Both studied in 2024" is not a basis for ordering anyone."""
    facts = [
        {"claim": "Seafaring recorded 2024 data on strain", "verified": True},
        {"claim": "Obstetrics recorded 2024 data on strain", "verified": True},
    ]
    basis = assess_comparative_basis(QUERY, facts)
    assert basis.verdict != RANKED


def test_a_single_mention_is_an_anecdote_not_a_candidate():
    """One passing reference must not become a shortlist entry."""
    facts = [
        {"claim": "Seafaring shows high strain, according to one account", "verified": True},
        {"claim": "Burnout is common in many occupations", "verified": True},
    ]
    basis = assess_comparative_basis(QUERY, facts)
    assert "Seafaring" not in basis.candidates


def test_no_evidence_is_undetermined():
    basis = assess_comparative_basis(QUERY, [])
    assert basis.verdict == UNDETERMINED
    assert basis.may_rank is False


def test_a_non_superlative_query_is_not_ranked_by_this_rule():
    basis = assess_comparative_basis("what is the population of Malawi", SEPARATE)
    assert basis.verdict == UNDETERMINED


# ---------------------------------------------------------------------------
# The writer contract
# ---------------------------------------------------------------------------


def test_the_shortlist_contract_forbids_superlatives():
    contract = render_ranking_contract(assess_comparative_basis(QUERY, SEPARATE), QUERY)
    low = contract.lower()
    assert "shortlist only" in low
    for phrase in ("strongest candidate", "likely winner", "leading"):
        assert phrase in low
    assert "example" in low
    assert "never be promoted into a ranking" in low
    assert "superlative" in low


def test_the_ranked_contract_permits_ranking_and_asks_for_the_basis():
    contract = render_ranking_contract(assess_comparative_basis(QUERY, COMPARED), QUERY)
    assert "RANKING PERMITTED" in contract
    assert "basis" in contract.lower()


def test_the_undetermined_contract_says_so():
    contract = render_ranking_contract(assess_comparative_basis(QUERY, []), QUERY)
    assert "CANNOT DETERMINE" in contract


# ---------------------------------------------------------------------------
# Detecting a violation in a delivered answer
# ---------------------------------------------------------------------------


def test_naming_a_strongest_candidate_is_a_violation_on_a_shortlist():
    answer = (
        "Overall, seafaring and obstetrics are the strongest candidates for the "
        "most demanding job in 2027."
    )
    basis = assess_comparative_basis(QUERY, SEPARATE)
    assert ranking_violation(answer, basis)
    assert "strongest candidates" in winner_claims(answer)


def test_presenting_examples_without_ranking_is_not_a_violation():
    answer = (
        "Both seafaring and obstetrics are examples of highly demanding work. "
        "No study compares them, so a ranking cannot be supported."
    )
    basis = assess_comparative_basis(QUERY, SEPARATE)
    assert ranking_violation(answer, basis) == []


def test_winner_language_is_allowed_when_a_comparison_exists():
    answer = "Seafaring has the higher fatality rate, so it leads on this measure."
    basis = assess_comparative_basis(QUERY, COMPARED)
    assert basis.may_rank is True
    assert ranking_violation(answer, basis) == []


def test_the_full_assessment_reports_basis_and_violation_together():
    bad = "Seafaring is the leading candidate for the most demanding job."
    report = assess_answer_ranking(bad, QUERY, SEPARATE)
    assert report["query_is_superlative"] is True
    assert report["basis"]["verdict"] == SHORTLIST
    assert report["violation"], "a superlative on a shortlist must be flagged"

    clean = assess_answer_ranking(
        "Seafaring and obstetrics are both examples of demanding work; no study "
        "compares them, so the ranking cannot be determined.",
        QUERY,
        SEPARATE,
    )
    assert clean["violation"] == []


def test_assessment_is_total_on_garbage():
    for answer, query, facts in (
        ("", QUERY, SEPARATE),
        (None, QUERY, None),
        ("text", "", []),
        ("text", QUERY, ["not a mapping"]),
    ):
        report = assess_answer_ranking(answer, query, facts)
        assert isinstance(report, dict)
        assert "basis" in report


# ---------------------------------------------------------------------------
# The writer is actually told
# ---------------------------------------------------------------------------


def test_the_synthesizer_renders_the_ranking_contract():
    import inspect

    from app.agents import synthesizer

    source = inspect.getsource(synthesizer.synthesize)
    assert "render_ranking_contract" in source
    assert "is_superlative_query" in source


def test_the_ranking_basis_reaches_the_audit():
    import inspect

    from app.agents import synthesizer

    source = inspect.getsource(synthesizer._finalize)
    assert "assess_answer_ranking" in source
    assert "ranking_audit" in source


def test_the_ranking_module_names_no_subject():
    """Domain agnosticism: the rules are about comparisons, not topics."""
    import ast

    source = open("app/agents/ranking_basis.py").read()
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                docstring_lines.update(range(body.lineno, (body.end_lineno or 0) + 1))

    topics = ("seafaring", "obstetrics", "demanding", "job", "burnout", "surgeon",
              "ai", "cybersecurity", "finance", "malawi")
    offenders = []
    for tok in tokenize.generate_tokens(StringIO(source).readline):
        if tok.type != tokenize.STRING or tok.start[0] in docstring_lines:
            continue
        for topic in topics:
            if re.search(rf"\b{topic}\b", tok.string, re.IGNORECASE):
                offenders.append((tok.start[0], topic))
    assert not offenders, f"executable subject strings in ranking_basis.py: {offenders}"


def test_the_audits_reach_the_caller_context():
    """An audit the caller cannot read is inert.

    `synthesize` rebinds ctx to a copy, so the measurements written inside it
    must be mirrored onto the caller's dict like the other machine-owned
    provenance.
    """
    from app.agents.synthesizer import _mirror_machine_notes

    inner = {
        "synthesis_machine_notes": ["n"],
        "definition_audit": {"locked": True},
        "ranking_audit": {"basis": {"verdict": SHORTLIST}},
        "ranking_basis": {"verdict": SHORTLIST},
        "definition_lock": {"term": "t"},
    }
    outer = {}
    _mirror_machine_notes(inner, outer)
    assert outer.get("ranking_audit")
    assert outer.get("definition_audit")
    assert outer.get("ranking_basis")
    assert outer.get("definition_lock")
    assert outer.get("synthesis_machine_notes") == ["n"]
