"""A well-formed EMPTY result is an ANSWER, not a failure.

Root cause of the persistent `degraded: ['summarizer']` runs, measured live:
the model returned `{"facts": []}` — valid JSON saying "these sources contain
nothing relevant" — and `generate_json`'s empty-payload check treated it as
the model having produced NOTHING. So the summarizer:

  * walked its whole excerpt-shrink ladder re-asking the SAME off-topic
    sources (48 empty-object events in one run alongside 3 successful
    extractions of 11/42/13 facts),
  * degraded the run and capped confidence,
  * then ran the extractive fallback over those same off-topic sources —
    which is how unrelated claims ("rip current detection", "poultry feed")
    reached the final report.

The provider was healthy throughout. There was never a size, quota or
reasoning-budget problem: the model was correctly refusing to extract from
irrelevant pages, and the app punished it for it.

These tests pin the distinction between:
  * `{"facts": []}`            -> honest "nothing relevant here"  (an ANSWER)
  * `{}` / all-default fields   -> model produced nothing          (a FAILURE)
  * `{"reason": "no match"}`    -> carries prose                  (an ANSWER)
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.llmkit.jsonparse import (
    payload_is_empty_container,
    payload_says_nothing,
)
from app.core.schemas import SummarizerFactsModel


# --------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------


def test_empty_facts_list_is_an_empty_container():
    assert payload_is_empty_container({"facts": []}) is True


def test_all_default_payload_is_not_an_empty_container():
    # Every field default is the FAILURE shape, not an answer.
    assert payload_is_empty_container({}) is False
    assert payload_is_empty_container({"facts": [], "reason": None}) is True


def test_prose_explanation_is_not_an_empty_container():
    # A model that says WHY it found nothing carries information.
    assert payload_is_empty_container({"facts": [], "reason": "no sources match"}) is False


def test_populated_facts_is_not_an_empty_container():
    assert payload_is_empty_container({"facts": [{"claim": "x is y"}]}) is False


def test_empty_container_still_says_nothing_but_is_classified_apart():
    payload = {"facts": []}
    # It still "says nothing" in the information sense...
    assert payload_says_nothing(payload) is True
    # ...but the caller can now tell it apart from a real failure.
    assert payload_is_empty_container(payload) is True


# --------------------------------------------------------------------------
# generate_json honours empty_ok
# --------------------------------------------------------------------------


class _FakeLLM:
    """Minimal LLMClient stand-in returning a fixed raw text."""

    def __init__(self, text: str):
        self._text = text
        self.settings = None

    async def _generate_with_fallback(self, system_prompt, user_prompt):
        return self._text


def _bind_generate_json():
    """Run the REAL `LLMClient.generate_json` with a stubbed transport."""
    from app.core.llm import LLMClient

    async def _call(text, *, response_model=None, empty_ok=False):
        class _Client(LLMClient):
            def __init__(self):
                pass  # skip provider setup entirely

            async def _generate_with_fallback(self, system_prompt, user_prompt):
                return text

        return await _Client().generate_json(
            "s", "u", response_model=response_model, empty_ok=empty_ok
        )

    return _call


async def test_empty_facts_returned_when_empty_ok():
    call = _bind_generate_json()
    payload = await call('{"facts": []}', response_model=SummarizerFactsModel, empty_ok=True)
    assert payload == {"facts": []}


async def test_empty_facts_raises_without_empty_ok():
    call = _bind_generate_json()
    with pytest.raises(ValueError, match="empty object"):
        await call('{"facts": []}', response_model=SummarizerFactsModel, empty_ok=False)


async def test_all_default_payload_still_raises_even_with_empty_ok():
    """empty_ok must NOT excuse a model that emitted no fields at all."""
    call = _bind_generate_json()
    with pytest.raises(ValueError, match="empty object"):
        await call("{}", response_model=SummarizerFactsModel, empty_ok=True)


# --------------------------------------------------------------------------
# The summarizer: an honest "no facts" ends the pass without degrading
# --------------------------------------------------------------------------


class _EmptyAnswerLLM:
    """The live shape: HTTP 200, valid JSON, honestly empty."""

    def __init__(self, tmp_path):
        from app.core.config import Settings

        self.settings = Settings(
            groq_api_key="k", database_url=str(tmp_path / "e.db"), _env_file=None
        )
        self.calls = 0

    async def generate_json(self, *a, **k):
        self.calls += 1
        return {"facts": []}


def test_summarizer_does_not_degrade_on_an_honest_empty(tmp_path, monkeypatch):
    """The decisive regression test.

    A model that correctly reports "these sources hold nothing relevant" must
    NOT: re-ask via the shrink ladder, record a fallback, or run the
    extractive fallback over the same off-topic sources.
    """
    from app.agents.summarizer import summarizer_agent

    class _LLM(_EmptyAnswerLLM):
        pass

    llm = _LLM(tmp_path)
    # emulate generate_json's empty_ok-aware behaviour for the real path
    async def _gen(system_prompt, user_prompt, response_model=None, empty_ok=False, **kw):
        llm.calls += 1
        return {"facts": []}

    llm.generate_json = _gen  # type: ignore[method-assign]

    results = [{
        "title": "Rip current detection benchmark",
        "url": "https://example-arxiv.org/abs/1",
        "snippet": "",
        "content": (
            "Rip current detection and segmentation is a benchmark task for "
            "coastal safety using video frames."
        ),
        "sub_question": "Which CS subfields are most demanding in 2026?",
    }]

    from app.core.degradation import clear_fallbacks, fallback_reasons, reset_fallbacks

    reset_fallbacks()
    try:
        facts = asyncio.run(
            summarizer_agent(
                llm,
                "suggest highly demanding research topics in computer science",
                results,
            )
        )
    finally:
        clear_fallbacks()

    # Nothing extracted (correct — the source is off-topic)...
    assert facts == []
    # ...no fallback was recorded (i.e. the run is NOT degraded)...
    assert not fallback_reasons(), "an honest empty answer must not degrade the run"
    # ...and the extractive fallback never ran, so no contamination.
    claims = " ".join(str(f.get("claim", "")) for f in facts).lower()
    assert "rip current" not in claims


def test_summarizer_does_not_retry_a_fresh_call_per_rung(tmp_path):
    """Before the fix each shrink rung made a NEW provider call; now one honest
    empty answer ends the pass."""
    from app.agents.summarizer import summarizer_agent

    class _LLM(_EmptyAnswerLLM):
        pass

    llm = _LLM(tmp_path)
    calls = {"n": 0}

    async def _gen(system_prompt, user_prompt, response_model=None, empty_ok=False, **kw):
        calls["n"] += 1
        return {"facts": []}

    llm.generate_json = _gen  # type: ignore[method-assign]

    results = [{
        "title": "t", "url": "https://example.org/a", "snippet": "",
        "content": "Rip currents are hazardous nearshore flows.",
        "sub_question": "Which CS subfields are most demanding?",
    }]

    asyncio.run(
        summarizer_agent(
            llm, "suggest highly demanding research topics in computer science", results
        )
    )
    assert calls["n"] <= 2, f"expected at most the main call, got {calls['n']}"
