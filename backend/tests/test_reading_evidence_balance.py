"""Evidence availability must not redefine the question.

The failure these tests pin: the run picks the natural reading of an ambiguous
query, that reading turns out to be hard to evidence, a different reading is
easier to find, and the answer quietly becomes about the easier one. The meaning
of a question is decided BEFORE research; the search results cannot change it.

The concrete example: "most demanding job in 2027" defines "demanding" as hard to
fill, then researches it. If stress studies turn out to be more plentiful, the
report must say the chosen reading is thin — not become a report about stress.

Every fixture is domain-agnostic in the sense that matters: the mechanism reads
reading labels and evidence tags, never a subject. The labels below exist to make
the assertions concrete, not because the code knows them.
"""

import asyncio
import re
import tokenize
from io import StringIO

from app.agents.ambiguity import (
    ALTERNATIVE_EVIDENCE_RATIO,
    ReadingEvidence,
    assess_reading_evidence,
    evidence_balance_guidance,
)
from app.agents.planner import _assign_intent_senses, _intent_research_senses
from app.core.config import Settings
from app.graph import workflow as wf
from app.graph.evidence import _measured_coverage_gaps

# The chosen reading, plus a competing one that happens to be easier to evidence.
CHOSEN = "hard to fill"
COMPETING = "stressful or difficult"

POLICY = {
    "action": "assume",
    "assumption": CHOSEN,
    "interpretations": [CHOSEN, COMPETING],
}


def _facts(sense, n):
    return [{"claim": f"finding {i}", "sense": sense, "verified": True} for i in range(n)]


# ---------------------------------------------------------------------------
# The balance assessment
# ---------------------------------------------------------------------------


def test_a_better_evidenced_competing_reading_does_not_become_the_answer():
    """The headline rule: more evidence for another reading is NOT a switch.

    The chosen reading stays the one answered; the assessment only reports the
    imbalance so the writer can handle it explicitly.
    """
    evidence = assess_reading_evidence(_facts(COMPETING, 6), POLICY)
    assert evidence.chosen == CHOSEN  # the reading is unchanged
    assert evidence.chosen_count == 0
    assert evidence.alternative == COMPETING
    assert evidence.alternative_count == 6
    assert evidence.chosen_is_thin is True
    assert evidence.alternative_is_better_evidenced is True


def test_the_guidance_forbids_switching_and_requires_reporting_the_gap():
    evidence = assess_reading_evidence(_facts(COMPETING, 6), POLICY)
    guidance = evidence_balance_guidance(evidence)
    low = guidance.lower()
    assert "evidence gap" in low, "the chosen reading's shortfall must be reported"
    # And it must explicitly forbid the silent switch.
    assert "do not" in low and ("different reading" in low or "another reading" in low)
    assert CHOSEN in guidance


def test_a_better_evidenced_reading_may_only_be_an_alternative():
    evidence = assess_reading_evidence(_facts(COMPETING, 6), POLICY)
    guidance = evidence_balance_guidance(evidence)
    low = guidance.lower()
    assert "alternative" in low
    # It must be labelled and kept out of the main answer.
    assert "label" in low or "clearly labelled" in low or "separate subsection" in low
    assert "never merge" in low or "not the reading" in low
    assert COMPETING in guidance


def test_a_well_evidenced_chosen_reading_produces_no_warning():
    """No imbalance, no guidance — the writer is left alone."""
    evidence = assess_reading_evidence(_facts(CHOSEN, 5), POLICY)
    assert evidence.chosen_is_thin is False
    assert evidence.alternative_is_better_evidenced is False
    assert evidence_balance_guidance(evidence) == ""


def test_untagged_evidence_is_not_reported_as_a_thin_reading():
    """A pool that simply is not sense-labelled must not read as a thin reading.

    Telling the writer to hedge on good evidence is its own failure.
    """
    untagged = [{"claim": f"finding {i}", "verified": True} for i in range(5)]
    evidence = assess_reading_evidence(untagged, POLICY)
    assert evidence.chosen_is_thin is False
    assert evidence_balance_guidance(evidence) == ""


def test_a_marginally_better_alternative_is_not_surfaced():
    """Only a SUBSTANTIALLY better evidenced reading is worth offering."""
    facts = _facts(CHOSEN, 2) + _facts(COMPETING, 2)
    evidence = assess_reading_evidence(facts, POLICY)
    assert evidence.alternative_is_better_evidenced is False
    assert "alternative" not in evidence_balance_guidance(evidence).lower()


def test_the_ratio_threshold_is_respected():
    facts = _facts(CHOSEN, 2) + _facts(COMPETING, 3)
    below = assess_reading_evidence(facts, POLICY)
    assert below.alternative_is_better_evidenced is False  # 3 < 2*2

    facts = _facts(CHOSEN, 2) + _facts(COMPETING, 4)
    at = assess_reading_evidence(facts, POLICY)
    assert at.alternative_is_better_evidenced is True  # 4 >= 2*2
    assert ALTERNATIVE_EVIDENCE_RATIO == 2.0


def test_balance_assessment_is_total_on_garbage():
    for facts, policy in (
        (None, None),
        ([], {}),
        (["not a mapping"], POLICY),
        ([{"claim": "x"}], {"assumption": ""}),
    ):
        result = assess_reading_evidence(facts, policy)
        assert isinstance(result, ReadingEvidence)
        # Every entry point must yield a usable string, never raise.
        assert isinstance(evidence_balance_guidance(result), str)


# ---------------------------------------------------------------------------
# The chosen reading is the one the plan researches
# ---------------------------------------------------------------------------


def test_the_chosen_reading_is_stamped_on_planned_contracts():
    """Facts carry the reading they were gathered for, so attribution works.

    Without this the under-specified path produced NO sense tags at all, and the
    evidence could not be attributed to a reading downstream.
    """
    intent = {
        "ambiguity_policy": dict(POLICY),
    }
    research = _intent_research_senses(intent)
    assert research and research[0][0] == CHOSEN

    plan = [{"id": 1, "axis": "evidence", "question": "q1"},
            {"id": 2, "axis": "other", "question": "q2"}]
    stamped = _assign_intent_senses(plan, intent)
    assert all(str(c.get("sense", "")) == CHOSEN for c in stamped)


def test_separate_action_tags_the_chosen_reading_first():
    intent = {"ambiguity_policy": {"action": "separate", "assumption": CHOSEN,
                                   "interpretations": [CHOSEN, COMPETING]}}
    research = _intent_research_senses(intent)
    assert research[0][0] == CHOSEN
    assert COMPETING in [label for label, _ in research]


def test_no_ambiguity_policy_means_no_sense_tagging():
    """Unambiguous queries are unaffected — exactly the previous behaviour."""
    assert _intent_research_senses({"senses": []}) == []
    assert _intent_research_senses(None) == []


# ---------------------------------------------------------------------------
# The gap reaches the Limitations section
# ---------------------------------------------------------------------------


def _gap_state(facts):
    return {
        "query": "the most demanding job in 2027",
        "facts": facts,
        "ambiguity": dict(POLICY),
        "intent": {},
        "investigation_state": {},
        "contradictions": [],
    }


def test_a_thin_chosen_reading_is_reported_as_a_limitation():
    gaps = _measured_coverage_gaps(_gap_state(_facts(COMPETING, 6)))
    joined = " ".join(gaps).lower()
    assert "thin on evidence" in joined
    assert CHOSEN.lower() in joined
    # And it must say the question is answered under that reading regardless.
    assert "regardless" in joined


def test_a_better_evidenced_reading_is_named_as_an_alternative_limitation():
    gaps = _measured_coverage_gaps(_gap_state(_facts(COMPETING, 6)))
    joined = " ".join(gaps).lower()
    assert "alternative" in joined
    assert "not as the answer" in joined


def test_no_limitation_when_the_chosen_reading_is_well_evidenced():
    gaps = _measured_coverage_gaps(_gap_state(_facts(CHOSEN, 5)))
    joined = " ".join(gaps).lower()
    assert "thin on evidence" not in joined


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def _run_graph(query):
    settings = Settings(groq_api_key="k", database_url=":memory:", _env_file=None,
                        research_timeout_sec=20)

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
    return final


def test_the_chosen_reading_is_fixed_before_research():
    """The plan is shaped by the chosen reading, not by what search returns."""
    final = _run_graph("what can be the most demanding job in 2027")
    policy = final.get("ambiguity") or {}
    assert policy.get("action") in ("assume", "separate")
    chosen = str(policy.get("assumption", "") or "")
    assert chosen
    # Every planned contract researches a reading of the question, and the
    # chosen reading is among them. On `separate` both readings of "demanding"
    # are researched (each tagged), which is the point — but NO contract may
    # research a different TERM's meaning (labour-market demand).
    senses = {
        str(c.get("sense", "") or "")
        for c in (final.get("sub_questions") or [])
        if isinstance(c, dict)
    }
    senses -= {""}
    assert chosen in senses, f"chosen reading not researched: {senses}"
    for sense in senses:
        assert "demand" not in sense.lower() or "demanding" in sense.lower(), (
            f"a labour-demand reading was researched for a 'demanding' question: {sense}"
        )


def test_the_ambiguity_module_names_no_subject():
    """Domain agnosticism: the balance logic cannot contain a topic taxonomy."""
    import ast

    source = open("app/agents/ambiguity.py").read()
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                docstring_lines.update(range(body.lineno, (body.end_lineno or 0) + 1))

    topics = ("demanding", "job", "stress", "burnout", "ai", "artificial intelligence",
              "cybersecurity", "finance")
    offenders = []
    for tok in tokenize.generate_tokens(StringIO(source).readline):
        if tok.type != tokenize.STRING or tok.start[0] in docstring_lines:
            continue
        for topic in topics:
            if re.search(rf"\b{topic}\b", tok.string, re.IGNORECASE):
                offenders.append((tok.start[0], topic))
    assert not offenders, f"executable subject strings in ambiguity.py: {offenders}"


# ---------------------------------------------------------------------------
# Interpretation SCORING: semantic/contextual fit must dominate evidence
# ---------------------------------------------------------------------------


def _candidates(*pairs):
    from app.agents.ambiguity import ReadingCandidate

    return [ReadingCandidate(label=lbl, description=desc) for lbl, desc in pairs]


def test_selection_scores_all_three_dimensions():
    """Every reading is scored on semantic fit, contextual fit and evidence."""
    from app.agents.ambiguity import select_reading

    cands = _candidates(("difficulty", "effortful, high-pressure work"),
                        ("labour demand", "roles employers cannot staff"))
    _, scored = select_reading(cands, "what is the most demanding job",
                               evidence_counts={"labour demand": 10})
    assert len(scored) == 2
    for c in scored:
        assert 0.0 <= c.semantic_fit <= 1.0
        assert 0.0 <= c.contextual_fit <= 1.0
        assert 0.0 <= c.evidence <= 1.0
        assert "semantic_fit" in c.to_dict() and "evidence" in c.to_dict()


def test_wording_decides_even_when_the_other_reading_has_all_the_evidence():
    """THE RULE: a reading that echoes the user's word wins on zero evidence.

    "Demanding work" (0 facts) must beat "High labour demand" (500 facts),
    because 500 facts about a different term do not make it the question.
    """
    from app.agents.ambiguity import select_reading

    cands = _candidates(("Demanding work", "effortful, high-pressure roles"),
                        ("High labour demand", "roles employers struggle to staff"))
    chosen, scored = select_reading(
        cands, "what is the most demanding job",
        evidence_counts={"Demanding work": 0, "High labour demand": 500},
    )
    assert chosen is not None
    assert chosen.label == "Demanding work", [c.to_dict() for c in scored]
    by_label = {c.label: c for c in scored}
    assert by_label["High labour demand"].evidence == 1.0
    assert by_label["Demanding work"].evidence == 0.0
    assert by_label["Demanding work"].semantic_fit > by_label["High labour demand"].semantic_fit


def test_evidence_alone_never_selects_a_reading():
    """A meaning tie with lopsided evidence must NOT be broken by evidence.

    Returning no choice makes the caller separate or ask; letting evidence win
    would let the search results pick the question.
    """
    from app.agents.ambiguity import select_reading

    cands = _candidates(("reading one", "first meaning"),
                        ("reading two", "second meaning"))
    chosen, _ = select_reading(
        cands, "some query", evidence_counts={"reading one": 0, "reading two": 900}
    )
    assert chosen is None, "evidence decided the meaning"


def test_demand_and_demanding_are_not_the_same_word():
    """"demanding" must not be satisfied by a reading about "demand"."""
    from app.agents.ambiguity import _meaning_fit, ReadingCandidate, _subject_tokens

    demand = ReadingCandidate(label="High labour demand",
                              description="roles employers struggle to staff")
    query = "what is the most demanding job"
    semantic, _ = _meaning_fit(demand, query)
    # No literal match: "demanding" is not present as a word in the reading.
    assert "demanding" not in _subject_tokens(demand.label) | _subject_tokens(demand.description)
    assert semantic < 0.5, f"a demand reading claimed the word 'demanding' ({semantic})"


def test_the_intent_prior_is_used_as_context_not_evidence():
    """A correct classifier prior must not be overridden by raw vocabulary.

    "what is a transformer" picked "an electrical device" on vocabulary alone
    (the label contains the word) over the AI sense the classifier scored 0.85.
    The prior IS context and must count.
    """
    from app.agents.ambiguity import select_reading

    intent = {
        "senses": [
            {"label": "a neural network architecture", "probability": 0.85},
            {"label": "an electrical device", "probability": 0.10},
        ],
        "interpretations": [
            {"label": "a neural network architecture"},
            {"label": "an electrical device"},
        ],
    }
    priors = {s["label"]: s["probability"] for s in intent["senses"]}
    from app.agents.ambiguity import _reading_candidates

    cands = _reading_candidates(intent, "what is a transformer")
    chosen, scored = select_reading(cands, "what is a transformer", priors=priors)
    assert chosen is not None and chosen.label == "a neural network architecture", [
        c.to_dict() for c in scored
    ]


def test_a_demanding_question_never_offers_a_labour_demand_reading():
    """The table bug: "demanding" listed a labour-demand reading.

    "demanding" means difficult; "in demand" is a different term. The curated
    table must not put a demand reading under the demanding key.
    """
    from app.agents.ambiguity import _reading_candidates
    from app.agents.intent import heuristic_intent

    q = "what can be the most demanding job in 2027"
    intent = heuristic_intent(q).to_dict()
    labels = [c.label.lower() for c in _reading_candidates(intent, q)]
    assert labels, "the query should still be recognised as underspecified"
    for label in labels:
        assert "demand" not in label or "demanding" in label, (
            f"a labour-demand reading was offered for 'demanding': {label}"
        )


def test_in_demand_has_its_own_readings():
    """"in demand" resolves to the demand sense, under its own term."""
    from app.agents.ambiguity import decide_ambiguity
    from app.agents.intent import heuristic_intent

    q = "which job is most in demand in 2027"
    policy = decide_ambiguity(q, heuristic_intent(q).to_dict())
    assert policy.action in ("assume", "separate")
    assert policy.interpretations, "the demand term should still be ambiguous"


def test_a_reading_that_echoes_the_question_is_not_an_interpretation():
    """An LLM asked for the readings often hands the question back.

    "Most demanding jobs/careers in 2027" for "what can be the most demanding
    job in 2027" covers every content word and adds one rename. It is the
    question, not a reading of it — and as a "reading" it wins selection on
    semantic fit precisely by repeating the user's words, so the policy would
    answer the question with itself.
    """
    from app.agents.ambiguity import _is_restatement, _subject_tokens, _reading_candidates

    query = "what can be the most demanding job in 2027"
    tokens = _subject_tokens(query)
    assert _is_restatement("Most demanding jobs/careers in 2027", tokens)
    assert _is_restatement("demanding job", tokens)

    intent = {
        "senses": [
            {"label": "Most demanding jobs/careers in 2027", "probability": 0.5},
            {"label": "Stressful or difficult (high strain)", "probability": 0.3},
            {"label": "Requiring high skill or responsibility (high complexity)",
             "probability": 0.2},
        ],
        "interpretations": [],
    }
    labels = [c.label for c in _reading_candidates(intent, query)]
    assert "Most demanding jobs/careers in 2027" not in labels
    assert "Stressful or difficult (high strain)" in labels


def test_a_genuine_reading_that_mentions_the_term_is_not_filtered():
    """The echo rule must not eat legitimate readings.

    "Transformer neural network architecture" covers the query's word but adds
    real concepts, so it is a meaning rather than an echo. A one-word query is
    never "fully covered" in the sense the rule needs.
    """
    from app.agents.ambiguity import _is_restatement, _subject_tokens

    tokens = _subject_tokens("what is a transformer")
    assert not _is_restatement("Transformer neural network architecture", tokens)
    assert not _is_restatement("Electrical power transformer", tokens)
    # And a multi-word query keeps its genuine readings too.
    long_tokens = _subject_tokens("what is the best option")
    assert not _is_restatement("Highest quality", long_tokens)
    assert not _is_restatement("Best fit for a use case", long_tokens)
