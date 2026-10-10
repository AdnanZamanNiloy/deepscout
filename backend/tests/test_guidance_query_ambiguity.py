"""Regression for the guidance-query ambiguity defect (2026-10).

Live failure: "Suggest me highly demanding research topics in computer science."

The intent layer treats "demanding" as an under-specified term with two useful
readings ("Stressful or difficult (high strain)" and "Requiring high skill or
responsibility (high complexity)"). Those readings are a JOB-market taxonomy,
but the term match is context-free, so the detector fired on a research-topics
request too. The ambiguity policy then chose `separate` with an assumption, and
`_intent_research_senses` replaced the plan's actual subject with the reading
labels. Every planned sub-question became "stressful or difficult definition
explanation overview" / "requiring high skill or responsibility statistics …":
the computer-science subject was deleted, no on-topic evidence could ever be
retrieved, the critic correctly reported gaps every round, and the run degraded.

The same misfire happens with any multi-reading modifier inside a guidance
request ("recommend the best programming language" -> "Best fit for a use case").

These tests pin the fix at the decision point: a guidance/recommendation request
is not made ambiguous by a modifier term, so the plan keeps the real subject.
Genuine job queries and homonyms keep their readings.
"""
from __future__ import annotations

import re

from app.agents.ambiguity import decide_ambiguity
from app.agents.intent import heuristic_intent
from app.agents.planning.dimensions import _heuristic_dimensions
from app.agents.planning.plan import fallback_plan


def _plan_questions(query: str, n: int = 4) -> list:
    intent = heuristic_intent(query).to_dict()
    intent["ambiguity_policy"] = decide_ambiguity(query, intent).to_dict()
    return [str(c.get("question", "") or "") for c in fallback_plan(query, n, intent=intent)]


# ---------------------------------------------------------------------------
# 1. The policy no longer splits a guidance request on its modifier term
# ---------------------------------------------------------------------------


def test_guidance_query_proceeds_without_ambiguity_split():
    for query in (
        "Suggest me highly demanding research topics in computer science.",
        "Recommend the best programming language to learn",
        "Give me some ideas for popular research areas",
    ):
        policy = decide_ambiguity(query, heuristic_intent(query).to_dict())
        assert policy.action == "proceed", f"{query!r} -> {policy.action}: {policy.reason}"
        assert policy.interpretations == []
        assert policy.assumption == ""


def test_genuine_job_query_keeps_its_readings():
    """The fix is scoped to guidance requests: a real 'most demanding job'
    question still yields the difficulty readings and research them."""
    policy = decide_ambiguity(
        "what is the most demanding job in 2027",
        heuristic_intent("what is the most demanding job in 2027").to_dict(),
    )
    assert policy.action in ("assume", "separate")
    labels = " ".join(policy.interpretations).lower()
    assert "stressful" in labels or "high skill" in labels


def test_homonym_senses_are_not_suppressed_in_a_guidance_query():
    """Only under-specification interpretations are suppressed. A genuine
    homonym the user typed keeps its distinct meanings."""
    q = "recommend some transformer research topics"
    intent = heuristic_intent(q).to_dict()
    intent["senses"] = [
        {"label": "Transformer neural network architecture", "domain": "machine_learning",
         "probability": 0.7},
        {"label": "Electrical transformer (AC voltage device)", "domain": "engineering",
         "probability": 0.3},
    ]
    intent["ambiguity"] = True
    policy = decide_ambiguity(q, intent)
    assert policy.action != "proceed"
    assert any("transformer" in str(x).lower() for x in policy.interpretations)


# ---------------------------------------------------------------------------
# 2. The plan keeps the real subject (the actual user-visible defect)
# ---------------------------------------------------------------------------


def test_recommendation_plan_keeps_the_subject_and_never_leads_with_a_reading():
    query = "Suggest me highly demanding research topics in computer science."
    questions = _plan_questions(query)
    blob = " ".join(questions).lower()
    assert "computer" in blob and "science" in blob
    # No planned question may open with a reading label ("stressful or difficult …").
    for question in questions:
        low = question.lower()
        assert not low.startswith("stressful or difficult")
        assert not low.startswith("requiring high skill")


def test_recommendation_concept_strips_the_guidance_verb():
    from app.agents.planning.contracts import _query_concept

    assert _query_concept(
        "Suggest me highly demanding research topics in computer science."
    ) == "highly demanding research topics in computer science"
    assert _query_concept("Recommend the best programming language") == (
        "best programming language"
    )


# ---------------------------------------------------------------------------
# 3. Planning is shaped for recommendations, not surveys
# ---------------------------------------------------------------------------


def test_recommendation_heuristic_names_option_dimensions():
    dims = " ".join(
        _heuristic_dimensions(
            "Suggest me highly demanding research topics in computer science."
        )
    ).lower()
    assert "candidate options" in dims
    assert "selection criteria" in dims


def test_recommendation_fallback_plan_is_option_shaped():
    """The degraded fallback plan must also produce recommendation-shaped
    contracts, not a "what is <topic>" survey."""
    questions = " ".join(_plan_questions(
        "Suggest me highly demanding research topics in computer science."
    )).lower()
    assert "candidate" in questions
    assert "selection" in questions or "criteria" in questions


# ---------------------------------------------------------------------------
# 4. The reported regression shape is impossible now
# ---------------------------------------------------------------------------


def test_no_reading_label_leaks_as_a_planned_question_prefix():
    for query in (
        "Suggest me highly demanding research topics in computer science.",
        "Recommend demanding MSc research topics in computer science.",
    ):
        intent = heuristic_intent(query).to_dict()
        policy = decide_ambiguity(query, intent)
        intent["ambiguity_policy"] = policy.to_dict()
        readings = [
            re.sub(r"\s*\([^)]*\)", "", str(x)).strip().lower()
            for x in (policy.interpretations or [])
        ]
        for question in _plan_questions(query):
            low = question.lower()
            for reading in readings:
                assert not low.startswith(reading), (query, question, reading)


# ---------------------------------------------------------------------------
# 5. A recommendation request is not definitional: the run must not be stopped
#    early by the "no definitional claim" semantic-gap gate (the premature-stop
#    defect observed live: finalize at iteration 1 with axes uncovered).
# ---------------------------------------------------------------------------


def test_recommendation_query_is_not_classified_factual():
    from app.agents.orchestrator import classify_query_type

    for query in (
        "suggest me some highly demanding research topic in computer science",
        "Recommend the best programming language to learn",
        "Give me some ideas for popular research areas",
    ):
        assert classify_query_type(query) != "factual", query
        assert classify_query_type(query) == "exploratory", query


def test_genuine_definition_queries_stay_factual():
    from app.agents.orchestrator import classify_query_type

    for query in (
        "What is retrieval augmented generation?",
        "Explain how TCP handles packet loss.",
        "What is a transformer?",
    ):
        assert classify_query_type(query) == "factual", query


def test_critic_does_not_require_a_definition_for_a_recommendation_query(
    monkeypatch,
):
    """The critic's definition gate must not fire for a recommendation request.

    Live defect: the query is typed `factual`, the gate demands an "X is Y"
    claim that recommendation evidence never contains, emits
    `no definitional claim`, and `is_semantic_gap` then finalizes the run at
    iteration 1 with planned axes still uncovered.
    """
    import asyncio

    from app.agents.critic import critic_agent

    class FakeLLM:
        class settings:  # noqa: N801
            max_iterations = 3

        async def generate_json(self, *a, **k):
            return {"is_sufficient": False, "reason": "gaps", "improved_queries": [],
                    "confidence": 0.4}

    query = "suggest me some highly demanding research topic in computer science"
    facts = [{
        "claim": "Fault-tolerant quantum error correction requires threshold engineering.",
        "source": "https://cs.example/1",
        "sub_question": "candidate topics",
    }]
    result = asyncio.run(critic_agent(
        FakeLLM(), query, facts, iteration=1, max_iterations=3,
        query_type="factual",
    ))
    assert "no definitional claim" not in result.get("gate_failures", [])
    assert result.get("semantic_gap") is False


def test_genuine_factual_query_still_requires_a_definition():
    """The gate is scoped, not removed: a real "what is X" query still owes a
    definitional claim."""
    import asyncio

    from app.agents.critic import critic_agent

    class FakeLLM:
        class settings:  # noqa: N801
            max_iterations = 3

        async def generate_json(self, *a, **k):
            return {"is_sufficient": False, "reason": "gaps", "improved_queries": [],
                    "confidence": 0.4}

    facts = [{
        "claim": "Quantum error correction reduces logical error rates substantially.",
        "source": "https://cs.example/1",
        "sub_question": "definition",
    }]
    result = asyncio.run(critic_agent(
        FakeLLM(), "What is quantum error correction?",
        facts, iteration=1, max_iterations=3, query_type="factual",
    ))
    assert "no definitional claim" in result.get("gate_failures", [])
