"""The final answer must preserve the definition research was locked to.

The failure these tests pin: the meaning is locked before research, the run then
writes the answer from whatever evidence came back, and the evidence reaches for
the nearest measurable proxy. Asked for the "most demanding job" with "demanding"
locked to difficulty/strain, the report becomes a ranking by preparation —
because preparation data (an O*NET Job Zone) exist and strain data do not. The
proxy has replaced the requested concept.

Research may narrow or REJECT a hypothesis. It must not REDEFINE the question.
The pipeline is QUERY -> INTERPRETATION LOCK -> RESEARCH -> EVIDENCE CHECK ->
ANSWER, never QUERY -> RESEARCH -> redefine from available evidence -> ANSWER.

No test asserts on a real metric or subject; the proxies below are stand-ins.
"""

import re
import tokenize
from io import StringIO

from app.agents.definition_lock import (
    assess_answer_definition,
    definition_drifted,
    definition_lock,
    insufficiency_handled,
    proxy_promoted,
    render_lock_contract,
)

QUERY = "what can be the most demanding job in 2027"
POLICY = {
    "action": "assume",
    "assumption": "Stressful or difficult (high strain)",
    "interpretations": [
        "Stressful or difficult (high strain)",
        "Requiring high skill or responsibility (high complexity)",
    ],
}


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------


def test_the_lock_identifies_the_disputed_term():
    """The term is the query's content word that no reading accounts for."""
    lock = definition_lock(QUERY, POLICY)
    assert lock.term == "demanding"
    assert lock.definition == "Stressful or difficult (high strain)"
    assert lock.locked is True


def test_the_lock_is_empty_when_the_query_was_not_ambiguous():
    assert definition_lock("what is the capital of France", {}).locked is False
    assert render_lock_contract(definition_lock("q", {})) == ""


def test_the_contract_states_all_four_requirements():
    contract = render_lock_contract(definition_lock(QUERY, POLICY))
    low = contract.lower()
    # The definition is named and frozen.
    assert "locked definition" in low
    assert "Stressful or difficult (high strain)" in contract
    # Proxies may not become the definition.
    assert "proxy rule" in low
    assert "not the definition" in low
    assert "defines" in low and "measures" in low
    # The insufficiency shape: state it, give a labelled partial, name what is
    # missing.
    assert "insufficient" in low
    assert "partial" in low
    assert "missing" in low


# ---------------------------------------------------------------------------
# A proxy must not be promoted into the concept
# ---------------------------------------------------------------------------


def test_a_proxy_promoted_into_the_concept_is_detected():
    """'X is the best measure of demanding' promotes a proxy. Forbidden."""
    answer = (
        "Demanding is defined here as the level of preparation a role requires. "
        "Job Zone is the best measure of how demanding a job is."
    )
    assert proxy_promoted(answer, definition_lock(QUERY, POLICY))


def test_a_proxy_used_as_supporting_evidence_is_allowed():
    """The rule forbids PROMOTION, not the use of adjacent data."""
    answer = (
        "Here 'demanding' means strain and workload. No dataset ranks occupations "
        "by strain, so the evidence is insufficient for the requested ranking. As "
        "PARTIAL evidence, preparation level correlates with responsibility, but "
        "it does not measure strain. The missing data is a burnout index."
    )
    lock = definition_lock(QUERY, POLICY)
    assert not proxy_promoted(answer, lock)
    assert definition_drifted(answer, lock) == []
    assert insufficiency_handled(answer) is True


def test_an_answer_that_redefines_the_term_in_the_opening_is_flagged():
    answer = (
        "## Executive Summary\n"
        "For this report, demanding means the preparation a job requires. "
        "Ranking by that measure gives surgeons first place."
    )
    assert definition_drifted(answer, definition_lock(QUERY, POLICY))


def test_an_answer_that_states_the_lock_is_not_flagged():
    answer = (
        "## Executive Summary\n"
        "Here 'demanding' is taken to mean stressful or difficult work — the "
        "heaviest workloads and burnout risk. On that reading, ..."
    )
    assert definition_drifted(answer, definition_lock(QUERY, POLICY)) == []


def test_an_answer_that_states_the_lock_in_its_own_words_is_not_flagged():
    """The lock is about MEANING, not about repeating the label verbatim."""
    answer = (
        "## Executive Summary\n"
        "This report ranks roles by how demanding they are, where demanding means "
        "strain and pressure on the worker rather than required qualifications."
    )
    assert definition_drifted(answer, definition_lock(QUERY, POLICY)) == []


# ---------------------------------------------------------------------------
# The required insufficiency shape
# ---------------------------------------------------------------------------


def test_insufficiency_is_recognised_from_its_own_vocabulary():
    assert insufficiency_handled("the evidence is insufficient to rank these")
    assert insufficiency_handled("no published dataset measures this")
    assert insufficiency_handled("that is a data gap")
    assert not insufficiency_handled("the ranking is clear and well supported")


def test_the_full_assessment_separates_the_three_verdicts():
    """Drift, proxy promotion and honest-gap are independent findings."""
    bad = (
        "Demanding means the preparation a role requires. O*NET Job Zone is the "
        "best measure of how demanding a job is, so this ranks by preparation."
    )
    report = assess_answer_definition(bad, QUERY, POLICY)
    assert report["locked"] is True
    assert report["drifted"], "redefining the term must be flagged"
    assert report["promoted"], "promoting a proxy must be flagged"
    assert report["honest_gap"] is False

    good = (
        "Here 'demanding' means strain and workload. No dataset ranks occupations "
        "by strain, so the evidence is insufficient for the requested ranking. "
        "This is a PARTIAL answer; the missing data is a strain index."
    )
    clean = assess_answer_definition(good, QUERY, POLICY)
    assert clean["drifted"] == []
    assert clean["promoted"] == []
    assert clean["honest_gap"] is True


def test_assessment_is_total_on_garbage():
    for answer, query, policy in (
        ("", QUERY, POLICY),
        (None, QUERY, POLICY),
        ("text", QUERY, None),
        ("text", "", {}),
    ):
        report = assess_answer_definition(answer, query, policy)
        assert isinstance(report, dict)
        assert "locked" in report


# ---------------------------------------------------------------------------
# The writer is actually told
# ---------------------------------------------------------------------------


def test_the_synthesizer_renders_the_lock_contract():
    """The instruction must reach the writer, not merely exist."""
    import inspect

    from app.agents import synthesizer

    source = inspect.getsource(synthesizer.synthesize)
    assert "render_lock_contract" in source
    assert "definition_lock" in source


def test_the_lock_reaches_the_audit():
    """The delivered answer is measured against the lock, without being rewritten."""
    import inspect

    from app.agents import synthesizer

    # The audit lives in the synthesis finalizer alongside the other
    # observational checks (citation audit, research quality).
    source = inspect.getsource(synthesizer._finalize)
    assert "assess_answer_definition" in source
    assert "definition_audit" in source


def test_the_definition_module_names_no_subject():
    """Domain agnosticism: the rules are about definitions, not topics."""
    import ast

    source = open("app/agents/definition_lock.py").read()
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                docstring_lines.update(range(body.lineno, (body.end_lineno or 0) + 1))

    topics = ("demanding", "job", "onetonet", "o*net", "surgeon", "strain",
              "burnout", "ai", "cybersecurity", "finance")
    offenders = []
    for tok in tokenize.generate_tokens(StringIO(source).readline):
        if tok.type != tokenize.STRING or tok.start[0] in docstring_lines:
            continue
        for topic in topics:
            if re.search(rf"\b{re.escape(topic)}\b", tok.string, re.IGNORECASE):
                offenders.append((tok.start[0], topic))
    assert not offenders, f"executable subject strings in definition_lock.py: {offenders}"


def test_negation_is_not_a_stopword_in_the_lock_tokens():
    """AGENTS.md: negation must never be a stopword in a similarity component."""
    from app.agents.definition_lock import _tokens

    assert "without" in _tokens("countries without access")
    assert "not" in _tokens("this does not measure strain")
