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
    assert policy.get("action") == "assume"
    chosen = str(policy.get("assumption", "") or "")
    assert chosen
    # Every planned contract researches the chosen reading.
    senses = {
        str(c.get("sense", "") or "")
        for c in (final.get("sub_questions") or [])
        if isinstance(c, dict)
    }
    senses -= {""}
    assert senses == {chosen}, f"contracts researched {senses}, expected {chosen}"


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
