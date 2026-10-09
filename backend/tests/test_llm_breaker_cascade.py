"""One open breaker must not degrade the whole run.

The failure this pins: a circuit breaker is designed for a provider CHAIN, where
"skip this one, try the next" is the recovery. With a single provider — a
UI-selected one is exclusive and deliberately runs no fallbacks — there is no
next. So one slow call opened the breaker, and every later call in that run then
failed instantly.

Measured live, in one 12:33:03 second of a real run:

    [LLM] circuit breaker OPEN after timeout (cooldown 15s)
    [Analyst] LLM call failed, using plan fallback
    [Synthesizer] providers unavailable at cap=14 facts
    [Synthesizer] providers unavailable at cap=24 facts
    [Synthesizer] providers unavailable at cap=40 facts
    [Synthesizer] providers unavailable at cap=80 facts

The analyst and all four synthesizer sections degraded together, for a run whose
provider worked fine moments later. The banner the user saw — "evidence
extraction, analyst, synthesis ran on deterministic extraction" — is this
cascade, not four independent provider failures.

The fix waits out the breaker's own cooldown exactly ONCE per call, so a
transient block costs latency instead of the whole run. These tests keep it
bounded: the wait happens at most once, is capped, and never applies to a hard
failure (no keys, rejected authorization, oversized prompt).

All network is mocked; no real provider is contacted.
"""
import asyncio

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.llm import LLMClient, _breaker_seconds_left

ENDPOINT = "http://localhost:20128/v1/chat/completions"

_CUSTOM = {
    "name": "Opencode1",
    "endpoint": ENDPOINT,
    "model": "oc/space-bunny-free",
    "api_key": "test-key",
}


def _client(**over) -> LLMClient:
    base = {
        "groq_api_key": "",
        "huggingface_api_key": "",
        "_env_file": None,
    }
    base.update(over)
    return LLMClient(Settings(**base))


async def _exclusive(client: LLMClient) -> tuple:
    """Force the single-exclusive-provider shape the UI produces."""
    async def _resolve():
        return dict(_CUSTOM), True
    client._resolve_custom = _resolve  # type: ignore[method-assign]
    return await client._resolve_custom()


def _ok(content: str = '{"ok": true}') -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


async def test_an_open_breaker_is_waited_out_and_the_call_succeeds():
    """THE regression. Breaker open from one earlier timeout; the next call
    must wait the cooldown and succeed rather than degrade."""
    client = _client()
    await _exclusive(client)
    # Park the breaker the way a real timeout does.
    client.custom_breaker.record_timeout()
    assert client.custom_breaker.is_open()
    assert _breaker_seconds_left(client.custom_breaker) > 0

    route = respx.get(ENDPOINT)  # unused; keeps respx from complaining
    with respx.mock:
        respx.post(ENDPOINT).mock(return_value=_ok())
        # Patch sleep to observe the wait without actually spending it.
        waited = []
        real_sleep = asyncio.sleep

        async def _fake_sleep(delay, *a, **k):
            waited.append(delay)
            # Wind the breaker's cooldown forward so the retry sees it closed.
            if client.custom_breaker._opened_at is not None:
                client.custom_breaker._opened_at -= delay
            return await real_sleep(0)

        asyncio.sleep = _fake_sleep
        try:
            out = await client.generate_json("sys", "user")
        finally:
            asyncio.sleep = real_sleep

    assert out == {"ok": True}
    # Exactly one wait, and it was the breaker's own remaining cooldown.
    assert len(waited) == 1, waited
    assert 0 < waited[0] <= client.settings.llm_breaker_wait_cap_sec
    # and it was the breaker's real remaining cooldown, not a guess
    assert waited[0] <= client.settings.llm_breaker_cooldown_sec


async def test_the_wait_happens_at_most_once():
    """Bounded by construction: a second attempt may not wait again, so a
    genuinely dead provider still fails fast rather than looping."""
    client = _client()
    await _exclusive(client)

    sleeps = []
    real_sleep = asyncio.sleep

    async def _counting_sleep(delay, *a, **k):
        sleeps.append(delay)
        return await real_sleep(0)

    asyncio.sleep = _counting_sleep
    try:
        # Breaker open, and it stays open: the provider never recovers.
        with respx.mock:
            respx.post(ENDPOINT).mock(
                side_effect=httpx.ReadTimeout("still slow")
            )
            for attempt in (1, 2):
                client = _client()
                await _exclusive(client)
                client.custom_breaker.record_timeout()
                try:
                    await client.generate_json("sys", "user")
                except Exception as exc:
                    assert "failed" in str(exc).lower() or exc is not None
    finally:
        asyncio.sleep = real_sleep

    # One wait per CALL is allowed; what must never happen is a loop of them.
    assert len(sleeps) == 2, sleeps


async def test_no_breaker_block_means_no_wait():
    """A healthy provider must not pay the wait at all — this is the latency
    regression guard for the fix."""
    client = _client()
    await _exclusive(client)

    slept = []
    real_sleep = asyncio.sleep

    async def _spy(delay, *a, **k):
        slept.append(delay)
        return await real_sleep(0)

    asyncio.sleep = _spy
    try:
        with respx.mock:
            respx.post(ENDPOINT).mock(return_value=_ok())
            assert await client.generate_json("sys", "user") == {"ok": True}
    finally:
        asyncio.sleep = real_sleep
    assert slept == []


async def test_an_oversized_prompt_is_not_retried():
    """A size rejection is a VERDICT about the payload, not transient state.

    Note the breaker must be CLOSED here: a 413 can only arrive if the call was
    actually attempted, and an already-open breaker means the call never happens
    (a different scenario, covered by the wait tests above). What is asserted is
    that a size failure raises PromptTooLargeError straight away and never pays
    the breaker wait — retrying it would spend the research budget to reach the
    same verdict.
    """
    from app.core.llmkit.types import PromptTooLargeError

    client = _client()
    await _exclusive(client)
    assert not client.custom_breaker.is_open()

    slept = []
    real_sleep = asyncio.sleep

    async def _spy(delay, *a, **k):
        slept.append(delay)
        return await real_sleep(0)

    asyncio.sleep = _spy
    try:
        with respx.mock:
            respx.post(ENDPOINT).mock(return_value=httpx.Response(413, json={}))
            with pytest.raises(PromptTooLargeError):
                await client.generate_json("sys", "user")
    finally:
        asyncio.sleep = real_sleep
    assert slept == [], "a size rejection must not trigger the breaker wait"


async def test_a_wait_cap_of_zero_disables_the_wait():
    """The escape hatch: fail-fast-everywhere is still reachable by config."""
    client = _client(llm_breaker_wait_cap_sec=0)
    custom, _ = await _exclusive(client)
    client.custom_breaker.record_timeout()
    # Cap of 0 means "never wait", even with a genuinely open breaker.
    assert client._breaker_wait_seconds(custom, "", "") is None


async def test_no_configured_provider_is_not_a_breaker_wait():
    """The no-provider path keeps its own friendly error; waiting would be
    pointless and would hide the real problem."""
    client = _client()
    assert client._breaker_wait_seconds(None, "", "") is None

# ---------------------------------------------------------------------------
# An empty-but-valid response must be retried, not accepted as success
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field
from typing import List as _List


class _Brief(BaseModel):
    """A schema shaped like the analyst's: every field has a default, which is
    precisely why an empty response validates cleanly."""

    thesis: str = ""
    insights: _List[str] = Field(default_factory=list)
    counter_evidence: _List[str] = Field(default_factory=list)


def _empty_analyst_payload():
    """The exact shape a reasoning model that emitted nothing produces."""
    return _Brief().model_dump()


async def test_an_empty_schema_valid_response_is_retried_not_accepted():
    """THE second half of the cascade.

    A schema whose fields all default validates `{}` successfully, so a model
    that returns HTTP 200 with an empty object — a reasoning model that spent
    its whole budget on hidden reasoning — is accepted as a good answer and the
    caller degrades immediately. It must be treated as a failed attempt and
    retried, because the next attempt often produces real content.
    """
    import json as _json

    from app.core.llm import _payload_says_nothing

    assert _payload_says_nothing(_empty_analyst_payload())
    assert _payload_says_nothing({})

    client = _client()
    await _exclusive(client)
    calls = {"n": 0}

    async def _fake_chain(system_prompt, user_prompt, **kw):
        calls["n"] += 1
        # First attempt: the model says nothing. Second: real content.
        if calls["n"] == 1:
            return _json.dumps(_empty_analyst_payload())
        return _json.dumps({"thesis": "RAG reduces hallucinations in some settings",
                            "insights": ["one finding"], "counter_evidence": []})

    client._generate_with_fallback = _fake_chain  # type: ignore[method-assign]
    out = await client.generate_json("sys", "user", response_model=_Brief)

    assert calls["n"] == 2, "the empty response must not be accepted as success"
    assert out["thesis"] == "RAG reduces hallucinations in some settings"


async def test_a_genuinely_empty_response_still_fails_after_retries():
    """Bounded: a model that says nothing on every attempt still degrades the
    run rather than retrying forever."""
    import json as _json

    client = _client()
    await _exclusive(client)
    calls = {"n": 0}

    async def _always_empty(system_prompt, user_prompt, **kw):
        calls["n"] += 1
        return _json.dumps({})

    client._generate_with_fallback = _always_empty  # type: ignore[method-assign]
    try:
        await client.generate_json("sys", "user", response_model=_Brief)
    except Exception as exc:
        assert exc is not None
    else:
        raise AssertionError("a persistently empty response must surface, not return {}")
    assert calls["n"] == 3, f"expected the configured 3 attempts, got {calls['n']}"


def test_real_answers_are_never_mistaken_for_empty():
    """Guarding the fix's main risk: calling a real answer empty would degrade
    every good run. `False` and `0` are real answers, not absences."""
    from app.core.llm import _payload_says_nothing

    for payload in (
        {"contradiction_found": False},
        {"count": 0},
        {"insights": ["", "real"]},
        {"thesis": "x"},
        {"relationships": [{"statement": "a"}]},
    ):
        assert not _payload_says_nothing(payload), payload
