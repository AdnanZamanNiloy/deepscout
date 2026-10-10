"""Answer format adapts to the QUERY TYPE, not a fixed template.

The synthesis layer chooses a presentation strategy from the question and the
evidence (`build_blueprint`), tells the writer that strategy, and the
conformance gate verifies the emitted prose actually does the work the type
requires. These tests pin the contract for the families the refactor must
support — including `recommendation` ("suggest me some research topics"), the
family a live run got wrong (it answered with a definitional survey and a
`## Executive Summary` skeleton instead of actionable options).

Deterministic and LLM-free: `build_blueprint` / `infer_query_type` are pure,
and the conformance gate measures prose without a model call.
"""
from __future__ import annotations

import asyncio

from app.agents.answer_conformance import check_answer_conformance
from app.agents.outline import AnswerBlueprint, build_blueprint, build_outline
from app.agents.synthesizer import SYNTHESIZER_SYSTEM_PROMPT, synthesize

# ---------------------------------------------------------------------------
# The families the refactor must distinguish, with a representative question.
# ---------------------------------------------------------------------------

_QUERY_TYPES = {
    "recommendation": "suggest me some highly demanding research topic for M.sc in computer science and engineering",
    "comparison": "Compare React and Vue for a large dashboard application",
    "causal": "Why did AI capital expenditure rise in 2025?",
    "howto": "How do I configure a CI build pipeline?",
    "definition": "What is a transformer neural network?",
    "forecast": "What will the AI market be worth in 2030?",
}


def _facts():
    return [
        {"claim": "Global AI capital expenditure reached 300 billion dollars in 2025.",
         "source": "https://a.example/x", "confidence": 0.85, "verified": True,
         "sub_question": "spending", "axis": "evidence"},
        {"claim": "Training runs doubled in compute year over year.",
         "source": "https://b.example/y", "confidence": 0.8, "verified": True,
         "sub_question": "scaling", "axis": "mechanism"},
    ]


def _blueprint(query: str) -> AnswerBlueprint:
    outline = build_outline(query, _facts(), [])
    return build_blueprint(query, _facts(), [], outline=outline)


def test_every_query_family_gets_its_own_strategy():
    """No two families collapse to the same strategy, and none is `general`."""
    strategies = {name: _blueprint(q).strategy for name, q in _QUERY_TYPES.items()}
    for name, strategy in strategies.items():
        assert strategy == name, (name, strategy)
    assert len(set(strategies.values())) == len(strategies)


def test_recommendation_framework_names_method_and_feasibility():
    """The reader asked for topics they can act on, so the strategy must ask
    for each option's method and a feasibility note."""
    framework = _blueprint(_QUERY_TYPES["recommendation"]).framework.lower()
    assert "method" in framework
    assert "feasib" in framework
    # And it must forbid the survey shape the live run produced.
    assert "survey" in framework


def test_recommendation_is_not_misread_as_list_or_decision():
    """'suggest some topics' is a ranked suggestion request, not a bare
    enumeration and not a trade-off decision."""
    assert _blueprint("suggest some research topics in AI").strategy == "recommendation"
    assert _blueprint("recommend projects I could start this year").strategy == "recommendation"
    assert _blueprint("what should I study to work on robotics?").strategy == "recommendation"


def test_declared_intent_wins_over_shape_detection():
    bp = build_blueprint("Tell me about X", _facts(), [],
                         intent={"query_type": "recommendation"})
    assert bp.strategy == "recommendation"


def test_depth_is_question_shaped_not_evidence_shaped():
    """A definition stays concise; a broad/decision question goes deeper."""
    assert _blueprint(_QUERY_TYPES["definition"]).depth == "concise"
    assert _blueprint(_QUERY_TYPES["comparison"]).depth in ("standard", "deep")


# ---------------------------------------------------------------------------
# Conformance: the emitted prose must do the work its type requires.
# ---------------------------------------------------------------------------

def test_survey_answer_fails_a_recommendation_query():
    """The live defect: a survey of open problems instead of actionable topics."""
    survey = (
        "Research in computer science has shifted toward machine learning. "
        "A 2021 survey of split computing identifies open challenges. "
        "Cold start latency in serverless computing remains unresolved. "
        "Several papers discuss these problems at length [1][2]."
    )
    report = check_answer_conformance(survey, _QUERY_TYPES["recommendation"])
    assert report.query_type == "recommendation"
    assert report.shape_conformant is False
    assert any("suggest" in f.lower() for f in report.failures)


def test_actionable_recommendation_passes():
    good = (
        "Here are three demanding M.Sc topics. First, serverless cold-start "
        "systems: the method is to build a cross-provider benchmark, which is "
        "feasible with public cloud credits and distributed-systems skills. "
        "Second, carbon-aware scheduling, which needs energy telemetry data and "
        "about six months to a first result. Choose the topic whose data you can "
        "access within your supervision window [1][2]."
    )
    report = check_answer_conformance(good, _QUERY_TYPES["recommendation"])
    assert report.shape_conformant is True
    assert report.failures == []


def test_comparison_needs_a_verdict_and_a_causal_answer_needs_a_mechanism():
    no_verdict = (
        "React uses a virtual DOM. Vue uses a reactivity system. "
        "Both are popular choices for web applications [1]."
    )
    comp = check_answer_conformance(no_verdict, _QUERY_TYPES["comparison"])
    assert comp.shape_conformant is False

    no_mechanism = (
        "AI capital expenditure rose in 2025. Investments grew across the sector. "
        "Many firms expanded their data-centre capacity [1]."
    )
    causal = check_answer_conformance(no_mechanism, _QUERY_TYPES["causal"])
    assert causal.shape_conformant is False


# ---------------------------------------------------------------------------
# The strategy reaches the writer prompt (integration, not just the helper).
# ---------------------------------------------------------------------------

class _RecordingWriter:
    def __init__(self):
        self.prompts = []

    async def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.prompts.append(f"{system_prompt}\n{user_prompt}")
        return {"answer": "A concrete recommended topic with its method [1]."}


def test_recommendation_strategy_reaches_the_writer_prompt():
    writer = _RecordingWriter()
    asyncio.run(
        synthesize(writer, _QUERY_TYPES["recommendation"], _facts(),
                   {"intent": {}, "mode": "standard"}, compress_context=False)
    )
    combined = "\n".join(writer.prompts).lower()
    assert writer.prompts, "the writer must have been called"
    assert "presentation strategy" in combined
    # The recommendation shape, not the fixed skeleton.
    assert "ranked" in combined
    assert "survey the field" in combined


# ---------------------------------------------------------------------------
# JSON schema compatibility — the refactor must not change the output contract.
# ---------------------------------------------------------------------------

def test_llm_output_schema_is_unchanged():
    from app.agents.synthesis.orchestrator import SynthesizerAnswerModel

    fields = set(SynthesizerAnswerModel.model_fields)
    assert fields == {"answer"}
    # The system prompt documents exactly that schema.
    assert '"answer"' in SYNTHESIZER_SYSTEM_PROMPT
