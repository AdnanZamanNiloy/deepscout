"""Ambiguity: preserve meaning, prefer researching, ask only as a last resort.

Three failures these pin, all of which produced a confident wrong answer rather
than an obvious error:

  1. MEANING COLLAPSE. "demanding" (difficult/heavy) and "demand" (sought-after)
     reduce to the same string under any -ing stem, so a query about demanding
     jobs scored a perfect semantic fit against readings about labour shortage
     and the system answered "which jobs are hardest to staff" — a question
     nobody asked. The stemmer could not have caught it alone; the reading is
     simply about a different word.

  2. OVER-EAGER CLARIFICATION. Three divergent readings of "demanding" tripped
     the coverage limit of 2 and stopped the run. Researching three readings IS
     the job — one report can rank jobs on strain, skill and hours together,
     which beats both a single answer and a question.

  3. DEAD GUARD. A meaning filter written into the policy layer tripped the
     project's no-subject-strings rule. The taxonomy belongs to the intent layer,
     which owns it; the policy consumes it as data.

Every assertion here is domain-agnostic. The intent layer supplies the readings
and the boundary data; the tests never assert on the policy module's internals.
"""

from app.agents.ambiguity import (
    ASK,
    ASSUME,
    PROCEED,
    SEPARATE,
    _stem,
    decide_ambiguity,
    readings_would_diverge,
)
from app.agents.intent import heuristic_intent, meaning_boundaries


def _policy_for(query, readings, probabilities=None):
    probs = list(probabilities or [])
    intent = {
        "senses": [
            {"label": label, "probability": probs[i] if i < len(probs) else 0.5}
            for i, label in enumerate(readings)
        ],
        "interpretations": [{"label": label} for label in readings],
        "meaning_boundaries": meaning_boundaries(query),
    }
    return decide_ambiguity(query, intent)


# ---------------------------------------------------------------------------
# 1. Meaning preservation
# ---------------------------------------------------------------------------

def test_gerund_forms_are_not_stemmed_onto_a_different_word():
    """The morphological half of the fix, asserted on its own.

    No suffix rule can know that "demand" is a noun and "demanding" an
    adjective, so these forms are curated. Both the policy layer's stemmer and
    the core semantic engine must agree, or dedup would merge them anyway.
    """
    from app.core.semantic import _stem as core_stem

    for stemmer in (_stem, core_stem):
        assert stemmer("demanding") != stemmer("demand")
        assert stemmer("demanding") == "demanding"
        # Ordinary inflections still collapse — the guard must not disable
        # stemming, or related forms would read as unrelated sources.
        assert stemmer("demands") == "demand"


def test_off_meaning_reading_is_never_adopted_as_the_assumption():
    """The heart of it: a reading about a different word is not a weak candidate
    for this one, it is a different question."""
    query = "most demanding jobs in 2027"
    policy = _policy_for(
        query,
        [
            "Roles with the heaviest workload or pressure",
            "Roles employers struggle to staff because supply is short",
        ],
        [0.5, 0.45],
    )
    assert policy.action in (ASSUME, SEPARATE)
    assert policy.should_stop is False
    if policy.action == ASSUME:
        assert "staff" not in policy.assumption.lower()
        assert "supply" not in policy.assumption.lower()


def test_a_reading_about_the_other_word_is_still_shown_not_hidden():
    """Rejected as an interpretation, but the user is TOLD the two words differ.

    Silently dropping it would leave the user believing their question was
    unambiguous; showing it costs nothing because it is researched separately and
    never adopted.
    """
    query = "most demanding jobs in 2027"
    policy = _policy_for(
        query,
        [
            "Roles with the heaviest workload or pressure",
            "Roles employers struggle to staff because supply is short",
        ],
        [0.5, 0.45],
    )
    assert len(policy.interpretations) == 2


def test_the_boundary_is_silent_when_the_user_wrote_the_other_word():
    """Asymmetric on purpose: asking about in-demand jobs must keep the
    labour-market readings, or the guard would fix one confusion by creating
    another."""
    query = "most in-demand jobs in 2027"
    policy = _policy_for(
        query,
        [
            "Hard to fill because supply is short",
            "Fastest growing in openings",
        ],
        [0.55, 0.4],
    )
    assert len(policy.interpretations) == 2
    if policy.action == ASSUME:
        assert "supply" in policy.assumption.lower() or "growing" in policy.assumption.lower()


def test_hyphenated_and_spaced_spellings_are_the_same_term():
    """"in-demand" and "in demand" are one word, written two ways. Matching only
    the spaced form meant the common spelling produced no readings at all."""
    from app.agents.intent import _detect_interpretations

    hyphenated = _detect_interpretations("most in-demand jobs in 2027")
    spaced = _detect_interpretations("most in demand jobs in 2027")
    assert hyphenated and [i.label for i in hyphenated] == [i.label for i in spaced]


def test_the_two_words_never_produce_each_others_readings():
    """The table itself: asking about one term must not yield the other's."""
    from app.agents.intent import _detect_interpretations

    demanding = [i.label for i in _detect_interpretations("most demanding jobs in 2027")]
    in_demand = [i.label for i in _detect_interpretations("most in-demand jobs in 2027")]
    assert demanding and in_demand
    assert demanding != in_demand
    # The labour-market reading belongs to the other word only.
    assert not any("fill" in label.lower() or "growing" in label.lower() for label in demanding)
    assert not any("strain" in label.lower() or "complexity" in label.lower()
                   for label in in_demand)


def test_the_policy_layer_names_no_subject():
    """Architecture guard: the policy consumes the boundary as DATA. A subject
    taxonomy hardcoded here is what the project's no-subject-strings rule
    exists to prevent (and ambiguity.py must stay domain-agnostic)."""
    from app.agents.ambiguity import _violates_meaning_preservation

    boundaries = [{"word": "alpha", "excludes": ("beta",)}]
    assert _violates_meaning_preservation("alpha question", "about beta", boundaries)
    assert not _violates_meaning_preservation("alpha question", "about alpha", boundaries)
    # No boundary data -> the guard does nothing at all.
    assert not _violates_meaning_preservation("alpha question", "about beta", [])


# ---------------------------------------------------------------------------
# 2. Prefer researching over asking
# ---------------------------------------------------------------------------

def test_three_divergent_readings_are_researched_not_asked():
    """THE regression: a coverage limit of 2 stopped research on three readings
    of one word. Three axes of one question belong in one report."""
    policy = _policy_for(
        "most demanding jobs",
        [
            "Roles with the heaviest physical workload",
            "Roles requiring the most specialised expertise",
            "Roles with the longest hours and least autonomy",
        ],
        [0.4, 0.33, 0.27],
    )
    assert policy.action in (ASSUME, SEPARATE), policy.reason
    assert policy.should_stop is False


def test_two_readings_never_stop_the_run():
    for readings in (
        ["Roles employers cannot staff", "Roles with the highest injury rate"],
        ["Highest quality", "Best fit for a particular case"],
    ):
        policy = _policy_for("tell me about the most demanding job in 2027", readings)
        assert policy.action in (ASSUME, SEPARATE), policy.reason
        assert policy.should_stop is False


def test_multiple_interpretations_alone_never_block_research():
    """Requirement: ambiguity guides strategy, it does not gate it."""
    queries = [
        "what can be the most demanding job in 2027",
        "which approach is most effective",
        "what is the best option here",
        "the most affordable way to deploy this",
    ]
    for query in queries:
        policy = _policy_for(query, ["Reading one", "Reading two"])
        assert policy.action != ASK, f"{query!r} -> {policy.action}"
        assert policy.should_stop is False


def test_ask_stays_reachable_for_a_genuinely_unanswerable_question():
    """Guarding against over-correction: the clarification path must not become
    dead code just because asking is now rare.

    Four unrelated subjects, none echoing the query's word, tied on likelihood —
    covering all of them would be four research programmes for one question.
    """
    policy = _policy_for(
        "meridian",
        [
            "Astronomical observation programme",
            "Culinary certification track",
            "Maritime navigation licence",
            "Agricultural machinery course",
        ],
        [0.25, 0.25, 0.25, 0.25],
    )
    assert policy.action == ASK, policy.reason
    assert policy.should_stop is True
    assert policy.question.strip()
    assert readings_would_diverge(policy.interpretations)


def test_separate_researches_and_assume_researches_one():
    """Both non-stopping outcomes remain reachable.

    When one reading is off-meaning the policy may ASSUME the surviving one — a
    reasonable interpretation, stated, with research proceeding — or SEPARATE.
    Either is correct; what must never happen is assuming the off-meaning one.
    """
    separated = _policy_for(
        "tell me about the most demanding job",
        ["Roles employers cannot staff", "Roles with the highest injury rate"],
    )
    assert separated.action in (ASSUME, SEPARATE), separated.reason
    assert separated.should_stop is False
    assert separated.assumption
    assert "staff" not in separated.assumption.lower()

    assumed = _policy_for(
        "what can be the most demanding job in 2027",
        ["Roles with the highest injury rate", "Roles with the longest hours"],
        [0.9, 0.05],
    )
    assert assumed.action == ASSUME, assumed.reason
    assert "injury rate" in assumed.assumption.lower()


# ---------------------------------------------------------------------------
# 3. End to end, through the real intent layer
# ---------------------------------------------------------------------------

def test_target_query_researches_the_intended_meaning():
    """The reported query, through the shipped deterministic path."""
    query = "most demanding jobs in 2027"
    intent = heuristic_intent(query)
    payload = intent.to_dict()
    policy = decide_ambiguity(query, payload)

    assert policy.action in (ASSUME, SEPARATE), policy.reason
    assert policy.should_stop is False
    assert policy.assumption
    assert not policy.question
    # Whatever is assumed, it is a reading of the word the user actually wrote.
    assert "staff" not in policy.assumption.lower()
    assert "supply" not in policy.assumption.lower()


def test_genuinely_ambiguous_queries_still_research():
    """A real homonym keeps both senses and researches them; it does not ask."""
    for query in ("what is a transformer", "python performance benchmarks"):
        payload = heuristic_intent(query).to_dict()
        policy = decide_ambiguity(query, payload)
        assert policy.action in (ASSUME, SEPARATE, PROCEED), f"{query!r} -> {policy.reason}"
        assert policy.should_stop is False, f"{query!r} must not be blocked"


def test_evidence_safeguards_are_untouched():
    """The meaning guard constrains INTERPRETATION choice only. It must not
    weaken evidence accounting, which is what keeps a report honest."""
    from app.agents.ambiguity import assess_reading_evidence, evidence_balance_guidance

    policy = {
        "action": ASSUME,
        "assumption": "reading one",
        "interpretations": ["reading one", "reading two"],
    }
    evidence = assess_reading_evidence(
        [
            {"claim": "a", "source": "https://x.com/1", "reading": "reading one"},
            {"claim": "b", "source": "https://y.com/2", "reading": "reading two"},
            {"claim": "c", "source": "https://z.com/3", "reading": "reading two"},
            {"claim": "d", "source": "https://w.com/4", "reading": "reading two"},
        ],
        policy,
    )
    # The safeguard still measures the gap and still reports it: the chosen
    # reading is under-evidenced relative to the one not taken.
    assert evidence.chosen == "reading one"
    assert evidence.chosen_count == 1
    assert evidence.alternative_count == 3
    assert evidence.chosen_is_thin
    assert evidence_balance_guidance(evidence)

# ---------------------------------------------------------------------------
# 4. The graph: non-stopping actions must actually reach research
# ---------------------------------------------------------------------------

def _run_graph(query):
    """Drive the real graph with no LLM, and return the final state.

    Copied in spirit from the existing policy tests: the point is to prove the
    ROUTING, which no amount of unit testing `decide_ambiguity` can do.
    """
    import asyncio

    from app.core.config import Settings
    from app.graph import workflow as wf

    settings = Settings(
        groq_api_key="k", database_url=":memory:", _env_file=None,
        research_timeout_sec=20,
    )

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
    return final or {}


def test_the_target_query_reaches_research_through_the_graph():
    """ASSUME/SEPARATE must fall through to the planner, not short-circuit."""
    final = _run_graph("most demanding jobs in 2027")
    ambiguity = final.get("ambiguity") or {}
    assert ambiguity.get("action") in (ASSUME, SEPARATE, PROCEED), ambiguity
    assert final.get("sub_questions"), "research must be planned, not blocked"
    # No clarifying question was put to the user.
    assert not str((final.get("direct_answer_meta") or {}).get("kind", "")) == "clarification"


def test_a_genuinely_unanswerable_question_stops_with_a_question():
    """The other half: ASK really does stop the run and deliver a question.

    The policy is injected rather than produced, because ASK needs four
    readings and the deterministic intent layer can only supply two — reaching it
    honestly requires an LLM, which these tests deliberately do not use. What is
    under test here is the ROUTING, and routing is exactly what a unit test of
    `decide_ambiguity` cannot cover.

    Guarding both directions: a system that never stops is as broken as one that
    always does.
    """
    import app.agents.ambiguity as ambiguity_mod

    from app.agents.ambiguity import AmbiguityPolicy, clarification_question

    labels = [
        "Astronomical observation programme",
        "Culinary certification track",
        "Maritime navigation licence",
        "Agricultural machinery course",
    ]
    original = ambiguity_mod.decide_ambiguity
    ambiguity_mod.decide_ambiguity = lambda query, intent=None, **kw: AmbiguityPolicy(
        action=ASK,
        query=query,
        interpretations=labels,
        question=clarification_question(query, labels),
        reason="injected for the routing test",
    )
    try:
        final = _run_graph("meridian")
    finally:
        ambiguity_mod.decide_ambiguity = original

    meta = final.get("direct_answer_meta") or {}
    assert meta.get("kind") == "clarification", final.get("ambiguity")
    assert final.get("direct_answer", "").strip()
    assert not final.get("sub_questions"), "an ASK must not also plan research"


# ---------------------------------------------------------------------------
# 5. The reported regression: four readings must not stop the run
# ---------------------------------------------------------------------------

def test_four_readings_about_the_asked_subject_are_researched():
    """A real run returned this. The model produced four readings for one query
    and the coverage threshold (3) was crossed, so the run stopped and asked.

    The threshold was never the right instrument: models routinely return four
    senses for one word, so any count low enough to be interesting is also low
    enough to be crossed by an ordinary query. Three of these four readings name
    the very subject the user asked about, so the work was plainly doable and the
    question was an excuse not to do it.
    """
    query = "what can be the most demanding jobs in 2027"
    labels = [
        "Most demanding/high-pressure occupations in the labor market",
        "Most in-demand jobs / fastest-growing occupations",
        "Demanding jobs requiring advanced technical credentials",
        "Stressful or difficult (high strain)",
    ]
    policy = _policy_for(query, labels, [0.3, 0.3, 0.3, 0.3])
    assert policy.action != ASK, policy.reason
    assert policy.should_stop is False
    assert not policy.question.strip()
    assert policy.assumption, "a reading is researched with its assumption stated"


def test_a_plethora_of_on_subject_readings_still_researches():
    """Count must never be the deciding factor while the readings are on-subject."""
    query = "most demanding jobs in 2027"
    labels = [
        "Jobs with the heaviest physical workload",
        "Jobs requiring the most specialised expertise",
        "Jobs with the longest hours and least autonomy",
        "Jobs carrying the greatest legal responsibility",
    ]
    policy = _policy_for(query, labels)
    assert policy.action != ASK, policy.reason
    assert policy.should_stop is False


def test_asking_requires_that_no_reading_is_about_the_question():
    """The gate itself, asserted directly.

    If one reading is recognisably about what was asked, the system can research
    it and say so. Only when none is does the question genuinely lack a subject,
    and asking becomes the honest response rather than an evasion.
    """
    from app.agents.ambiguity import _any_reading_on_subject

    query = "what can be the most demanding jobs in 2027"
    # One on-subject reading is enough, even when its siblings are not: the
    # system can research that one and state the assumption.
    assert _any_reading_on_subject(query, ["Most in-demand jobs"]) is True
    # A reading that names none of the query's content words is off-subject on
    # its own — the point is that ANY on-subject reading clears the gate.
    assert _any_reading_on_subject(query, ["Roles with the heaviest workload"]) is False
    assert _any_reading_on_subject(
        query, ["Roles with the heaviest workload", "Most in-demand jobs"]
    ) is True
    # Not one reading is recognisably about the question -> genuinely
    # undetermined, and asking is the honest response.
    assert _any_reading_on_subject(
        "meridian",
        ["Astronomical observation programme", "Culinary certification track"],
    ) is False


def test_ask_remains_reachable_when_nothing_is_on_subject():
    """Over-correction guard: four unrelated subjects still earn a question."""
    policy = _policy_for(
        "meridian",
        [
            "Astronomical observation programme",
            "Culinary certification track",
            "Maritime navigation licence",
            "Agricultural machinery course",
        ],
        [0.25, 0.25, 0.25, 0.25],
    )
    assert policy.action == ASK, policy.reason
    assert policy.should_stop is True
    assert policy.question.strip()


# ---------------------------------------------------------------------------
# 6. The report must report the reading the pipeline actually assumed
# ---------------------------------------------------------------------------

def test_the_report_announces_the_assumed_reading_not_the_classifier_order():
    """Both disambiguation renderers must lead with the DECISION, not the raw
    sense order. Measured live, they disagreed: the pipeline assumed "Stressful
    or difficult" while the report announced "meaning 1 ... taken to mean the
    skills carrying the heaviest employer demand" — the prose, which is all the
    user reads, contradicted the decision that produced it.
    """
    from app.agents.synthesis.context_blocks import _render_ambiguity_block
    from app.agents.synthesis.postprocess import _deterministic_disambiguation

    intent = {
        "ambiguity": True,
        "recommended_action": "research_dominant",
        "senses": [
            {"label": "Most in-demand skill in 2027", "note": "what employers want"},
            {"label": "Stressful or difficult (high strain)", "note": "burnout"},
            {"label": "Requiring high skill (high complexity)", "note": "expertise"},
        ],
    }
    policy = {"action": "assume", "assumption": "Stressful or difficult (high strain)"}

    from app.agents.synthesis.context_blocks import _render_interpretations_block

    # The two renderers that consume `senses`.
    for render in (_deterministic_disambiguation, _render_ambiguity_block):
        text = render(intent, policy)
        # Indentation differs per renderer; match on the stripped line.
        first = next(
            l.strip() for l in text.splitlines() if l.strip().startswith("1)")
        )
        assert "Stressful or difficult" in first, f"{render.__name__}: {first!r}"

    # The interpretations block is the one the WRITER mirrors: it must list the
    # assumed reading first too, or the prose follows the wrong reading while the
    # prompt states the right one.
    interp_intent = {
        "interpretations": [
            {"label": "Most in-demand skill in 2027", "description": "what employers want"},
            {"label": "Stressful or difficult (high strain)", "description": "burnout"},
        ]
    }
    text = _render_interpretations_block(interp_intent, policy)
    assert text.index("Stressful or difficult") < text.index("Most in-demand skill")

    # Without a policy (older payload / no decision) the raw order is still used
    # rather than crashing.
    assert _deterministic_disambiguation(intent)
