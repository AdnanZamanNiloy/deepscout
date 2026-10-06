"""Ambiguity handling: resolve it, assume, ask, or separate — never research all.

The failure these tests pin: an underspecified question was acknowledged and then
researched under every reading at once. "What can be the most demanding job in
2027" mixed job growth, openings, burnout, cognitive load, physical strain and
automation risk into one evidence pool; the reviewer correctly reported that none
of them defined "most demanding" and asked for more research, which cannot help.
A semantic gap is not an evidence gap, and searching cannot close it.

The policy (app/agents/ambiguity.py) decides before planning:
  PROCEED   one clear reading                      -> plan normally
  ASSUME    one reading dominates or context picks -> state it, research it
  ASK       readings diverge, nothing chooses      -> ask, STOP
  SEPARATE  readings differ but the question wants -> research each, keep apart

Every test below is domain-agnostic: no test asserts on a hardcoded subject, and
the implementation names none.
"""

import asyncio

from app.agents.ambiguity import (
    ASK,
    ASSUME,
    PROCEED,
    SEPARATE,
    decide_ambiguity,
    is_semantic_gap,
    readings_would_diverge,
)
from app.core.depth_controller import decide_with_checks
from app.core.config import Settings
from app.graph import workflow as wf

# ---------------------------------------------------------------------------
# Helpers — readings are supplied by the INTENT layer, never by a table here
# ---------------------------------------------------------------------------


def _intent_with(readings, probabilities=None):
    """Build the intent shape the policy consumes, from readings given by a test."""
    probs = list(probabilities or [])
    return {
        "senses": [
            {"label": label, "probability": probs[i] if i < len(probs) else 0.5}
            for i, label in enumerate(readings)
        ],
        "interpretations": [{"label": label} for label in readings],
    }


# Labels used by the fixtures. They are deliberately NOT the ones the shipped
# homonym table uses, so a passing test cannot be the table doing the work.
DIVERGENT = ["roles employers cannot staff", "roles with the highest injury rate"]
OVERLAPPING = ["highest quality", "best fit for a particular case"]


# ---------------------------------------------------------------------------
# The four decisions
# ---------------------------------------------------------------------------


def test_an_underspecified_query_is_researched_with_a_stated_assumption():
    """THE DEFAULT: ambiguity guides strategy, it does not block research.

    "What can be the most demanding job in 2027" must be researched under the
    most reasonable reading, with the reading stated — NOT answered by asking the
    user to choose. Stopping to interrogate is the expensive failure here.

    The reading is the one the intent layer lists first, so no probability is
    required for the deterministic path to proceed.
    """
    from app.agents.intent import heuristic_intent

    intent = heuristic_intent("what can be the most demanding job in 2027").to_dict()
    policy = decide_ambiguity("what can be the most demanding job in 2027", intent)
    assert policy.action == ASSUME, policy.reason
    assert policy.should_stop is False
    assert policy.assumption  # a reading is chosen and reported
    assert not policy.question  # and nothing is asked


def test_clarification_requires_many_divergent_readings():
    """ASK is the last resort: substantially different plans AND needlessly broad.

    Only when covering every reading would pad the report does one question beat
    an answer.
    """
    # >2 divergent readings and the question does NOT already ask for a choice
    # (no "which"/"vs"), so covering them would pad the report.
    many = ["staffing shortages", "burnout rates", "automation displacement",
            "wage stagnation", "training requirements"]
    policy = decide_ambiguity("tell me about this subject", _intent_with(many))
    assert policy.action == ASK, policy.reason
    assert policy.should_stop is True
    assert policy.question.strip()
    # The question names the readings and invents no subject of its own.
    for label in many[:2]:
        assert label in policy.question


def test_disambiguating_context_resolves_the_ambiguity():
    """Requirement 3 + 4: the query's own wording picks a reading -> proceed.

    Asking a user to clarify a question they already clarified is its own failure.
    """
    for query in (
        "what can be the most demanding job in 2027 in terms of physical effort",
        "what can be the most demanding job according to injury statistics",
        "the most demanding job specifically by injury rate",
    ):
        policy = decide_ambiguity(query, _intent_with(DIVERGENT))
        assert policy.action == PROCEED, f"{query!r} -> {policy.action}: {policy.reason}"


def test_a_dominant_reading_is_assumed_and_stated():
    """Requirement 4: one clearly dominant reading -> state the assumption, go."""
    policy = decide_ambiguity(
        "what is a transformer",
        _intent_with(
            ["a neural network architecture", "an electrical device"],
            [0.85, 0.10],
        ),
    )
    assert policy.action == ASSUME
    assert policy.assumption == "a neural network architecture"
    assert policy.should_stop is False


def test_lexically_overlapping_readings_do_not_diverge():
    """The divergence test is structural: labels that share vocabulary overlap.

    This is what separates ASSUME from ASK/SEPARATE — readings about the same
    thing in different words are covered by one answer.
    """
    same_thing = ["cost of ownership over five years", "five-year total cost of ownership"]
    assert not readings_would_diverge(same_thing)
    policy = decide_ambiguity(
        "what is the cost of ownership", _intent_with(same_thing, [0.8, 0.1])
    )
    assert policy.action == ASSUME
    assert policy.assumption == same_thing[0]


def test_readings_with_no_shared_vocabulary_diverge():
    """Different subjects => different answers, which is the ASK/SEPARATE case."""
    assert readings_would_diverge(DIVERGENT)


def test_a_comparative_question_separates_rather_than_asks():
    """Requirement 6: when the readings ARE the question, answer each separately.

    A user comparing two things wants both, so asking which they meant would be
    unresponsive. The readings must also stay apart in the answer.
    """
    policy = decide_ambiguity(
        "which job is most demanding: A or B?", _intent_with(DIVERGENT)
    )
    assert policy.action == SEPARATE
    assert policy.needs_separation is True
    assert policy.should_stop is False


def test_a_broad_question_separates_rather_than_asks():
    """A question asking for several things is broad, not ambiguous."""
    policy = decide_ambiguity(
        "how do injury rates and staffing shortages compare across sectors",
        _intent_with(DIVERGENT),
        broad_question=True,
    )
    assert policy.action == SEPARATE


def test_a_clearly_defined_question_proceeds():
    """The common case must be untouched: one reading, plan normally."""
    policy = decide_ambiguity(
        "what is the population of Malawi in 2024",
        _intent_with(["the population of Malawi"]),
    )
    assert policy.action == PROCEED
    assert policy.should_stop is False
    assert policy.interpretations == []


def test_policy_is_total_on_garbage():
    for query, intent in (
        ("", None),
        ("q", {}),
        ("q", {"senses": "not a list"}),
        ("q", {"interpretations": [None, {}, {"label": ""}]}),
    ):
        policy = decide_ambiguity(query, intent)
        assert policy.action == PROCEED


# ---------------------------------------------------------------------------
# Requirement 7 + the stopping condition
# ---------------------------------------------------------------------------


def test_semantic_gap_is_recognised_from_the_critics_own_vocabulary():
    assert is_semantic_gap(["no definitional claim"])
    assert not is_semantic_gap(["facts=2<4"])
    # An uncovered ANGLE is an evidence gap, not a semantic one.
    assert not is_semantic_gap(["uncovered_angles=1"])
    assert not is_semantic_gap([])
    # "ambiguous"/"interpretation" are semantic regardless of the axis name.
    assert is_semantic_gap(["planned_axis_uncovered=interpretation"])


def test_a_definition_blocker_stops_searching():
    """Requirement 7: the remaining problem is meaning, so stop.

    More searching cannot resolve a definition; spending another pass on it is
    exactly how an ambiguous query accumulated research programmes for every
    reading.
    """
    state = {
        "query": "what can be the most demanding job in 2027",
        "iteration": 2,
        "max_iterations": 5,
        "mode": "standard",
        "confidence": 0.7,
        "confidence_history": [0.6, 0.65, 0.7],
        "sub_questions": [
            {"id": 1, "axis": "a", "question": "qa", "minimum_sources": 1}
        ],
        "facts": [
            {"claim": "c", "source": "https://x.example/1", "verified": True,
             "sub_question": "qa"}
        ],
        "search_results": [],
        "focus": {"report": {}},
        "critique": {
            "is_sufficient": False,
            "improved_queries": ["search for more"],
            "reason": "none of these define what is being asked",
            "semantic_gap": True,
            "gate_failures": ["no definitional claim"],
        },
    }
    decision, checks = decide_with_checks(state)
    assert decision == "finalize", checks["decision_reason"]
    assert "definition" in checks["decision_reason"].lower()


def test_an_evidence_blocker_still_expands():
    """The semantic stop must not disable the evidence loop."""
    state = {
        "query": "what is the population of Malawi",
        "iteration": 2,
        "max_iterations": 5,
        "mode": "standard",
        "confidence": 0.4,
        "confidence_history": [0.3, 0.35, 0.4],
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
        "critique": {
            "is_sufficient": False,
            "improved_queries": ["a genuinely new query"],
            "reason": "missing evidence",
            "semantic_gap": False,
            "gate_failures": [],
        },
    }
    decision, _ = decide_with_checks(state)
    assert decision == "expand"


# ---------------------------------------------------------------------------
# End to end: the run must actually stop
# ---------------------------------------------------------------------------


def _run_graph(query):
    settings = Settings(
        groq_api_key="k", database_url=":memory:", _env_file=None,
        research_timeout_sec=20,
    )
    searched = []

    class _LLM:
        def __init__(self):
            self.settings = settings

        async def generate_json(self, *a, **k):
            raise RuntimeError("no provider")

        async def generate(self, *a, **k):
            raise RuntimeError("no provider")

    class _Search:
        def __init__(self):
            self.settings = settings
            self.health = None

        async def run_search(self, queries, **kw):
            searched.append(list(queries))
            return []

        async def run_grounding_search(self, query):
            return []

    graph = wf.create_workflow(_LLM(), _Search())
    state = wf.build_initial_state(query, 3, mode="standard")
    final = None

    async def drive():
        nonlocal final
        async for snap in graph.astream(state, stream_mode="values"):
            final = snap

    asyncio.run(drive())
    return final, searched


def test_ambiguous_query_is_researched_not_blocked():
    """The headline behaviour: an underspecified query still gets researched."""
    final, _ = _run_graph("what can be the most demanding job in 2027")
    ambiguity = final.get("ambiguity") or {}
    assert ambiguity.get("action") == ASSUME, ambiguity
    assert ambiguity.get("assumption"), "a reading must be chosen and stated"
    # Research proceeded: a plan exists.
    assert final.get("sub_questions"), "the query must not be blocked"


def test_context_resolved_query_does_plan_research():
    """The counterpart: context removes the ambiguity, so research proceeds."""
    final, _ = _run_graph(
        "what can be the most demanding job in 2027 in terms of physical effort"
    )
    assert (final.get("ambiguity") or {}).get("action") == PROCEED
    assert final.get("sub_questions"), "a resolved query must still be researched"


def test_no_ambiguity_decision_reaches_the_answer_text():
    """Process metadata stays out of the primary answer (vision contract)."""
    final, _ = _run_graph("what can be the most demanding job in 2027")
    audit = str(final.get("final_audit") or "")
    answer = str(final.get("final_report") or "") or str(final.get("direct_answer") or "")
    # The clarification question IS the answer and may name readings; what must
    # never appear in the answer is the policy reason/vocabulary.
    assert "would produce substantially different answers" not in answer
    assert isinstance(audit, str)


def test_ambiguity_module_names_no_subject():
    """Domain agnosticism: the policy cannot contain a topic taxonomy."""
    import ast
    import io
    import re
    import tokenize

    source = open("app/agents/ambiguity.py").read()
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                docstring_lines.update(range(body.lineno, (body.end_lineno or 0) + 1))

    topics = ("demanding", "job", "ai", "artificial intelligence", "transformer",
              "cybersecurity", "finance", "nursing", "teaching")
    offenders = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.STRING or tok.start[0] in docstring_lines:
            continue
        for topic in topics:
            if re.search(rf"\b{topic}\b", tok.string, re.IGNORECASE):
                offenders.append((tok.start[0], topic))
    assert not offenders, f"executable subject strings in ambiguity.py: {offenders}"


def test_a_reading_that_restates_the_query_is_not_offered_as_a_choice():
    """The first live run asked "Did you mean Most demanding jobs in 2027, ...?"

    A reading whose content words are all already in the query adds no choice;
    offering it back to the user is noise in the one place the UI must be clear.
    A genuine reading introduces vocabulary the query never had.
    """
    from app.agents.ambiguity import _is_restatement, _subject_tokens

    query = "what can be the most demanding job in 2027"
    tokens = _subject_tokens(query)
    assert _is_restatement("Most demanding jobs in 2027", tokens)
    assert not _is_restatement("Hard to fill (high demand)", tokens)
    assert not _is_restatement("Stressful or difficult (high strain)", tokens)
    # Plurality and tense fold, so a restatement is still caught.
    assert _is_restatement("demanding job", tokens)


def test_restatement_is_filtered_from_the_readings():
    policy = decide_ambiguity(
        "what can be the most demanding job in 2027",
        _intent_with(["Most demanding jobs in 2027"] + DIVERGENT),
    )
    assert "Most demanding jobs in 2027" not in policy.interpretations
    assert policy.assumption in DIVERGENT

    # And in the ask case the filtered reading is absent from the question too.
    # The restatement filter is relative to the QUERY, so it is asserted against
    # the query the label restates.
    many = ["Most demanding jobs in 2027", "staffing shortages", "burnout rates",
            "automation displacement", "wage stagnation"]
    asked = decide_ambiguity(
        "what can be the most demanding job in 2027", _intent_with(many)
    )
    assert asked.action == ASK, asked.reason
    assert "Most demanding jobs in 2027" not in asked.question


# ---------------------------------------------------------------------------
# Ambiguity must GUIDE research, never block it
# ---------------------------------------------------------------------------


def test_ambiguity_never_stops_research_for_two_readings():
    """The core correction: asking is reserved for many divergent readings.

    For the ordinary underspecified case the run researches the reasonable
    reading. Every two-reading case must produce a research action (assume or
    separate), never a stop.
    """
    for query in (
        "what can be the most demanding job in 2027",
        "which approach is most effective",
        "what is the best option here",
    ):
        policy = decide_ambiguity(query, _intent_with(DIVERGENT))
        assert policy.action in (ASSUME, SEPARATE), f"{query!r} -> {policy.action}"
        assert policy.should_stop is False
        assert policy.assumption or policy.action == PROCEED


def test_the_most_reasonable_reading_is_the_intent_layers_first():
    """No probabilities needed: the deterministic path still picks a reading."""
    # Genuinely distinct readings and NO probabilities at all — the shape the
    # deterministic homonym path supplies.
    intent = {
        "senses": [{"label": "staffing shortages"}, {"label": "burnout rates"}],
        "interpretations": [{"label": "staffing shortages"}, {"label": "burnout rates"}],
    }
    policy = decide_ambiguity("what does this term mean", intent)
    assert policy.action == ASSUME, policy.reason
    assert policy.assumption == "staffing shortages"


def test_a_genuine_tie_between_two_readings_is_separated_not_asked():
    """Equally plausible AND answerable together -> cover both, keep them apart."""
    policy = decide_ambiguity(
        "tell me about this", _intent_with(DIVERGENT, [0.5, 0.5])
    )
    assert policy.action == SEPARATE
    assert policy.needs_separation is True
    assert policy.should_stop is False


def test_the_assumption_is_required_by_the_writer():
    """The answer must state the reading AND define the term as used."""
    from app.agents.synthesizer import _render_interpretations_block

    intent = {"interpretations": [{"label": "reading one"}, {"label": "reading two"}]}
    block = _render_interpretations_block(
        intent, {"action": "assume", "assumption": "reading one",
                 "interpretations": ["reading one", "reading two"]}
    )
    assert "reading one" in block
    # It must instruct the writer to define the term, not merely name the reading.
    assert "defines the ambiguous term" in block or "define the ambiguous term" in block
