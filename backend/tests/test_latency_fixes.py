"""Latency remediation: independent I/O must actually run concurrently.

Evidence (baseline run 0bfd5f9e, 236s total): the search node alone took
111s — with Tavily's circuit open every contract query paid a serial
DDG text→news rescue, and primary-fallback substitution queries ran one
awaited round at a time. Section-wise synthesis wrote each section with a
serial awaited LLM call, and the router ran AFTER the intent gather instead
of overlapping the grounding search.

Each test pins concurrency with an in-flight peak counter or completion
ordering — never bare wall-clock thresholds (those flake under load).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI

from app.agents.intent import heuristic_intent
from app.agents.outline import build_outline
from app.agents.search import SearchClient, SearchResult
from app.agents.synthesizer import synthesize
from app.api.routes import limiter, router as api_router
from app.core.config import Settings


def _settings(**over) -> Settings:
    base = {"groq_api_key": "k", "_env_file": None}
    base.update(over)
    return Settings(**base)


def _result(url: str, *, sub_question: str = "what is a transformer",
            search_type: str = "general") -> SearchResult:
    return SearchResult(
        title="Result", url=url, snippet="snippet text",
        sub_question=sub_question, search_type=search_type, provider="ddg_text",
    )


def _sub_questions():
    return [
        {"question": "AI definition", "axis": "definition"},
        {"question": "AI market data", "axis": "evidence"},
        {"question": "AI risks", "axis": "criticism"},
        {"question": "AI outlook", "axis": "outlook"},
    ]


def _facts():
    return [
        {"claim": "Artificial intelligence simulates human intelligence in machines.",
         "axis": "definition", "source": "https://a.example/x", "confidence": 0.8,
         "verified": True, "sub_question": "AI definition"},
        {"claim": "Global AI spending reached 200 billion dollars in 2025.",
         "axis": "evidence", "source": "https://b.example/y", "confidence": 0.7,
         "verified": True, "sub_question": "AI market data"},
        {"claim": "AI systems can encode societal bias at scale.",
         "axis": "criticism", "source": "https://c.example/z", "confidence": 0.6,
         "verified": True, "sub_question": "AI risks"},
        {"claim": "Adoption is expected to keep rising through 2026.",
         "axis": "outlook", "source": "https://d.example/w", "confidence": 0.5,
         "verified": True, "sub_question": "AI outlook"},
    ]


# ---------------------------------------------------------------------------
# Section-wise synthesis: writes overlap (peak >= 2), order preserved
# ---------------------------------------------------------------------------

class _PeakSectionLLM:
    def __init__(self):
        self.in_flight = 0
        self.peak = 0
        self.calls = 0

    async def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            return {"answer": "Section body with a cited claim [1]."}
        finally:
            self.in_flight -= 1


def test_section_wise_writes_run_concurrently():
    """Sections are independent I/O and must be gathered, not awaited one
    by one: with a serial loop the peak in-flight count stays at 1."""
    outline = build_outline("What is the current trend of AI?", _facts(), _sub_questions())
    llm = _PeakSectionLLM()
    result = asyncio.run(
        synthesize(llm, "What is the current trend of AI?", _facts(),
                   {"intent": {}, "sub_questions": _sub_questions()},
                   outline=outline, section_wise=True, compress_context=False)
    )
    assert result is not None
    assert llm.calls >= 3  # exec summary + at least two section writes
    assert llm.peak >= 2, "section writes did not overlap"
    # Submission order = outline order regardless of completion order.
    for section in outline.sections:
        assert f"## {section.title}" in result.answer


# ---------------------------------------------------------------------------
# Tavily -> DDG rescue: text and news overlap
# ---------------------------------------------------------------------------

async def test_searxng_and_primary_legs_run_concurrently():
    """The provider fan-out must stay concurrent: SearXNG plus the free
    primary legs (arXiv/Crossref/Wikipedia) are independent HTTP calls, so
    awaiting them one at a time would serialise the search node.

    This replaces the old DuckDuckGo text+news concurrency test, which no
    longer had a subject once DDG was replaced by a single self-hosted
    aggregate.
    """
    from app.agents.search import SearchClient

    state = {"in_flight": 0, "peak": 0}
    settings = Settings(groq_api_key="k", _env_file=None)
    client = SearchClient(settings)

    async def _slow(name, search_type="academic"):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.05)
            return [_result(f"https://{name}.example/a", search_type=search_type)]
        finally:
            state["in_flight"] -= 1

    client._searxng_search = lambda q, search_type="": _slow("searxng", search_type)
    client._arxiv = lambda q: _slow("arxiv")
    client._crossref = lambda q: _slow("crossref")

    results = await client._providers_for("some query", "academic")
    assert state["peak"] >= 3, "provider legs ran serially"
    assert {r.url for r in results} == {
        "https://searxng.example/a", "https://arxiv.example/a", "https://crossref.example/a"
    }


# ---------------------------------------------------------------------------
# Primary fallback: substitution queries issue concurrently
# ---------------------------------------------------------------------------

async def test_primary_fallback_queries_run_concurrently(monkeypatch):
    settings = _settings(
        search_primary_fallback_enabled=True,
        search_primary_fallback_max=2,
        search_fetch_top_n=4,
    )
    client = SearchClient(settings)
    state = {"in_flight": 0, "peak": 0}

    async def slow_providers(self, query, search_type):
        assert "site:" in query
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.05)
            return []
        finally:
            state["in_flight"] -= 1

    monkeypatch.setattr(SearchClient, "_providers_for", slow_providers)
    ranked = [
        _result("https://blocked-a.com/x"),
        _result("https://blocked-b.com/y"),
    ]
    await client._primary_fallback(ranked, ["blocked-a.com", "blocked-b.com"])
    assert state["peak"] >= 2, "primary-fallback queries ran serially"
    assert client.health.snapshot()["fallback_queries_issued"] == 2


# ---------------------------------------------------------------------------
# Intent node: router overlaps the grounding search
# ---------------------------------------------------------------------------

async def test_route_overlaps_grounding_search(monkeypatch):
    """route_query must finish BEFORE the grounding search begins.

    The invariant used to be "overlap" — the search ran concurrently with the
    intent/route LLM calls. It is now stronger: the route decision GATES the
    search, because the search's only consumer is the planner and a
    conversation/direct turn never reaches one. So route must complete first
    even on the research branch.
    """
    import app.graph.workflow as wf
    from app.core.llm import LLMClient

    stamps: dict = {}
    settings = Settings(groq_api_key="k", _env_file=None)
    llm = LLMClient(settings)

    class _SlowSearch:
        async def run_search(self, sub_questions):
            await asyncio.sleep(0.35)
            if "search" not in stamps:
                stamps["search"] = time.monotonic()
            return []

    # Class bodies resolve via LOAD_NAME (global/builtins only) — attach
    # the settings after the block instead of `settings = settings` inside.
    _SlowSearch.settings = settings

    async def fake_classify(llm_arg, query, context_snippets=None):
        await asyncio.sleep(0.10)
        stamps["classify"] = time.monotonic()
        return heuristic_intent(query)

    class _RouteStub:
        def to_dict(self):
            return {"path": "research", "reason": "test", "confidence": 0.9,
                    "origin": "llm", "signals": {}}

    async def fake_route(llm_arg, query, intent=None):
        await asyncio.sleep(0.10)
        stamps["route"] = time.monotonic()
        return _RouteStub()

    async def fake_planner(**kwargs):
        return [{
            "id": 1, "question": "transformer neural network architecture definition",
            "axis": "definition", "search_type": "encyclopedia", "priority": 1,
            "depends_on": [], "domain": "machine_learning", "minimum_sources": 2,
            "coverage_goal": "", "stop_condition": "", "variants": [], "agent": "",
            "tools": ["web_search"], "scope": [], "output_format": "structured_findings",
            "specialist": "technical", "preferred_domains": [], "primary_source_query": "",
            "wave": 0, "sense": "",
        }]

    async def fake_summarizer(llm_arg, query, search_results, specialist_role="general",
                              prior_findings=None, sense=""):
        return []

    async def fake_critic(**kwargs):
        return {"is_sufficient": True, "reason": "enough", "improved_queries": [],
                "confidence": 0.8}

    async def fake_synthesizer(llm=None, query=None, facts=None, context=None):
        return "## Executive Summary\n\nAnswer."

    monkeypatch.setattr(wf, "classify_intent", fake_classify)
    monkeypatch.setattr(wf, "route_query", fake_route)
    monkeypatch.setattr(wf, "planner_agent", fake_planner)
    monkeypatch.setattr(wf, "summarizer_agent", fake_summarizer)
    monkeypatch.setattr(wf, "critic_agent", fake_critic)
    monkeypatch.setattr(wf, "synthesizer_agent", fake_synthesizer)

    graph = wf.create_workflow(llm, _SlowSearch())
    state = wf.build_initial_state("What is transformer?", 3, mode="quick")
    async for _snap in graph.astream(state, stream_mode="values"):
        pass

    assert {"classify", "route", "search"} <= set(stamps)
    # classify+route (0.20s) complete before the search starts, so a
    # conversation/direct turn never pays for it at all.
    assert stamps["route"] < stamps["search"], (
        "route_query must complete before the grounding search is issued"
    )


# ---------------------------------------------------------------------------
# Grounding search must not pay for page bodies it discards
# ---------------------------------------------------------------------------


async def test_grounding_search_does_not_fetch_page_bodies(monkeypatch):
    """The planner's grounding search reads six `title: snippet` pairs and
    throws every page body away. Fetching them was ~20s of pure latency."""
    from app.agents.search import SearchClient
    import app.agents.search as search_mod

    settings = Settings(groq_api_key="k", database_url=":memory:", _env_file=None)
    client = SearchClient(settings)
    fetched: list = []

    async def fake_providers(self, q, stype):
        return [
            SearchResult(
                title="Grounding hit", url="https://example.org/a",
                snippet="terminology from the web", provider="test",
            )
        ]

    async def fake_fetch(url, client=None):
        fetched.append(url)
        return "full page body", ""

    monkeypatch.setattr(SearchClient, "_providers_for", fake_providers)
    monkeypatch.setattr(search_mod, "_fetch_content", fake_fetch)

    grounding = await client.run_grounding_search("transformer architecture")
    assert grounding, "grounding search returned nothing"
    assert not fetched, "grounding search downloaded page bodies it discards"
    # The snippet it keeps is exactly what it was asked for.
    assert "terminology from the web" in grounding[0]["snippet"]


async def test_evidence_search_still_fetches_page_bodies(monkeypatch):
    """The grounding optimisation must not starve the summarizer, which reads
    the bodies."""
    from app.agents.search import SearchClient
    import app.agents.search as search_mod

    settings = Settings(
        groq_api_key="k", database_url=":memory:", _env_file=None,
        search_fetch_top_n=3,
    )
    client = SearchClient(settings)
    fetched: list = []

    async def fake_providers(self, q, stype):
        return [
            SearchResult(
                title=f"Hit {i}", url=f"https://example{i}.org/a",
                snippet="snippet text", provider="test",
            )
            for i in range(3)
        ]

    async def fake_fetch(url, client=None):
        fetched.append(url)
        return "full page body", ""

    monkeypatch.setattr(SearchClient, "_providers_for", fake_providers)
    monkeypatch.setattr(search_mod, "_fetch_content", fake_fetch)

    await client.run_search(["transformer architecture"])
    assert fetched, "evidence search stopped fetching bodies"


async def test_grounding_search_does_not_poison_the_evidence_cache(monkeypatch):
    """Regression: the cache key omitted whether bodies were fetched, so the
    grounding search's content-free entry was served to a later evidence search
    and the summarizer got snippets with no page bodies — for a whole TTL."""
    from app.agents.search import SearchClient
    import app.agents.search as search_mod

    settings = Settings(
        groq_api_key="k", database_url=":memory:", _env_file=None,
        cache_ttl_sec=3600,
    )
    client = SearchClient(settings)
    fetched: list = []

    async def fake_providers(self, q, stype):
        return [
            SearchResult(
                title="Grounding hit", url="https://cache-poison.example/a",
                snippet="terminology", provider="test",
            )
        ]

    async def fake_fetch(url, client=None):
        fetched.append(url)
        return "full page body", ""

    monkeypatch.setattr(SearchClient, "_providers_for", fake_providers)
    monkeypatch.setattr(search_mod, "_fetch_content", fake_fetch)

    await client.run_grounding_search("cache poisoning probe query")
    assert not fetched
    # Same query, evidence path: must NOT be served the content-free entry.
    await client.run_search(["cache poisoning probe query"])
    assert fetched, "evidence search was served the grounding search's cache entry"


def test_breaker_cooldown_is_configurable_and_short():
    """The cooldown was a literal 60.0 at four sites. A healthy direct answer is
    ~2-3s, so one upstream 503 parked the chain for a minute — observed as a
    68s wait for a one-line answer."""
    from app.core.llm import LLMClient

    settings = Settings(groq_api_key="k", _env_file=None)
    client = LLMClient(settings)
    assert client.groq_breaker.cooldown_sec == settings.llm_breaker_cooldown_sec
    assert client.groq_breaker.threshold == settings.llm_breaker_threshold
    # Chain breakers must not silently keep the old literal.
    chain = client._chain_breaker({"endpoint": "e", "model": "m"})
    assert chain.cooldown_sec == settings.llm_breaker_cooldown_sec
    # Sized against a ~2-3s request, not a minute.
    assert settings.llm_breaker_cooldown_sec <= 30.0


# ---------------------------------------------------------------------------
# Intent node: a greeting costs no LLM call at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query", ["hi", "thanks", "hello there", "goodbye", "hey"]
)
async def test_conversation_turn_makes_no_llm_call(monkeypatch, query):
    """`conversation_kind` is a pure function and `route_query` already
    returns that decision without a model call — but the node used to run
    `classify_intent` FIRST, so "hi" paid a full LLM round-trip to classify
    the intent of a greeting. Under Groq's rate limiter that is the whole
    latency budget of a greeting."""
    import app.graph.workflow as wf
    from app.core.llm import LLMClient

    calls = {"intent": 0, "route": 0, "search": 0}
    settings = Settings(groq_api_key="k", _env_file=None)
    llm = LLMClient(settings)

    class _Search:
        async def run_grounding_search(self, query):
            calls["search"] += 1
            return []

        async def run_search(self, sub_questions):
            calls["search"] += 1
            return []

    async def classify(*a, **k):
        calls["intent"] += 1
        raise AssertionError("classify_intent must not run for a greeting")

    async def route(*a, **k):
        calls["route"] += 1
        raise AssertionError("route_query must not run for a greeting")

    monkeypatch.setattr(wf, "classify_intent", classify)
    monkeypatch.setattr(wf, "route_query", route)

    graph = wf.create_workflow(llm, _Search())
    state = wf.build_initial_state(query, 3, mode="quick")
    final: dict = {}
    async for snap in graph.astream(state, stream_mode="values"):
        final = snap

    assert calls == {"intent": 0, "route": 0, "search": 0}
    # The reply still reaches the user.
    assert str(final.get("direct_answer") or "").strip(), "no reply delivered"
    assert str(final.get("route", {}).get("answer_sketch") or "").strip()


async def test_conversation_shortcut_matches_the_full_route_decision():
    """The shortcut must be byte-identical to what route_query would have
    returned, or it is a behaviour change dressed up as an optimisation."""
    from app.agents.intent import heuristic_intent
    from app.agents.router import deterministic_route, route_query
    from app.core.llm import LLMClient

    llm = LLMClient(Settings(groq_api_key="k", _env_file=None))
    for query in ("hi", "thanks", "hello there", "goodbye"):
        intent = heuristic_intent(query).to_dict()
        fast = deterministic_route(query, intent=intent).to_dict()
        slow = (await route_query(llm, query, intent=intent)).to_dict()
        assert fast == slow, query
        assert fast["path"] == "conversation"
        assert fast["answer_sketch"], "conversation needs its fixed reply"


# ---------------------------------------------------------------------------
# Intent node: the grounding search is gated on the research branch
# ---------------------------------------------------------------------------


async def _run_with_route(monkeypatch, path: str, query: str = "transformer?") -> tuple[int, dict]:
    """Run the graph with the router stubbed to `path`; return (search_calls,
    planner_kwargs).

    The default query must NOT be a conversational turn: those now short-circuit
    before the router is consulted at all, which would make every research-path
    assertion here vacuous."""
    import app.graph.workflow as wf
    from app.core.llm import LLMClient

    calls = {"search": 0}
    planner_kwargs: dict = {}
    settings = Settings(groq_api_key="k", _env_file=None)
    llm = LLMClient(settings)

    class _CountingSearch:
        async def run_grounding_search(self, query):
            calls["search"] += 1
            await asyncio.sleep(0.01)
            return [
                {"title": "Grounding hit", "snippet": "terminology from the web",
                 "url": "https://example.org/a", "content": "body",
                 "reliability_score": 0.9}
            ]

        async def run_search(self, sub_questions):
            calls["search"] += 1
            await asyncio.sleep(0.01)
            return [
                {"title": "Grounding hit", "snippet": "terminology from the web",
                 "url": "https://example.org/a", "content": "body",
                 "reliability_score": 0.9}
            ]

    async def fake_classify(llm_arg, query, context_snippets=None):
        return heuristic_intent(query)

    class _RouteStub:
        def to_dict(self):
            return {
                "path": path, "reason": "test", "confidence": 0.95,
                "origin": "llm", "signals": {},
                # conversation_node reads exactly this key.
                "answer_sketch": "Hello! What would you like to research?",
            }

    async def fake_route(llm_arg, query, intent=None):
        return _RouteStub()

    async def fake_planner(**kwargs):
        planner_kwargs.update(kwargs)
        return [{
            "id": 1, "question": "q", "axis": "definition",
            "search_type": "encyclopedia", "priority": 1, "depends_on": [],
            "domain": "machine_learning", "minimum_sources": 2,
            "coverage_goal": "", "stop_condition": "", "variants": [],
            "agent": "", "tools": ["web_search"], "scope": [],
            "output_format": "structured_findings", "specialist": "technical",
            "preferred_domains": [], "primary_source_query": "", "wave": 0,
            "sense": "",
        }]

    async def fake_summarizer(*a, **k):
        return []

    async def fake_critic(**k):
        return {"is_sufficient": True, "reason": "enough",
                "improved_queries": [], "confidence": 0.8}

    async def fake_synthesizer(**k):
        return "## Executive Summary\n\nAnswer."

    class _DirectStub:
        usable = True
        needs_research = False
        confidence = 0.9
        answer = "Paris."

        def to_dict(self):
            return {"answer": self.answer, "confidence": self.confidence,
                    "needs_research": False, "usable": True, "reason": "stable"}

    async def fake_direct(*a, **k):
        return _DirectStub()

    # Without this the direct path REFUSES (the test's fake Groq key 401s) and
    # falls through to the planner, which legitimately searches — so the test
    # would measure the fallthrough rather than the fast path.
    monkeypatch.setattr(wf, "direct_answer_agent", fake_direct)
    monkeypatch.setattr(wf, "classify_intent", fake_classify)
    monkeypatch.setattr(wf, "route_query", fake_route)
    monkeypatch.setattr(wf, "planner_agent", fake_planner)
    monkeypatch.setattr(wf, "summarizer_agent", fake_summarizer)
    monkeypatch.setattr(wf, "critic_agent", fake_critic)
    monkeypatch.setattr(wf, "synthesizer_agent", fake_synthesizer)

    graph = wf.create_workflow(llm, _CountingSearch())
    state = wf.build_initial_state(query, 3, mode="quick")
    async for _snap in graph.astream(state, stream_mode="values"):
        pass
    return calls["search"], planner_kwargs


@pytest.mark.parametrize("path", ["conversation", "direct"])
async def test_fast_paths_never_pay_for_the_grounding_search(monkeypatch, path):
    """A greeting took ~20s because the grounding search ran before the route
    was known, and its only consumer — the planner — is never reached on these
    branches. Both fast paths must now issue zero searches."""
    calls, planner_kwargs = await _run_with_route(monkeypatch, path)
    assert calls == 0, f"{path} path paid for the grounding search"
    assert planner_kwargs == {}, f"{path} path unexpectedly reached the planner"


async def test_research_path_still_receives_grounding_snippets(monkeypatch):
    """Behaviour preservation: gating the search must not starve the planner,
    which is the one component that reads context_snippets."""
    calls, planner_kwargs = await _run_with_route(monkeypatch, "research")
    # >= 1, not == 1: search_node legitimately searches again after the plan.
    # What matters is that the intent node's grounding search still happened.
    assert calls >= 1, "research path lost its grounding search"
    snippets = planner_kwargs.get("context_snippets") or []
    assert snippets, "planner received no grounding snippets"
    assert any("Grounding hit" in s for s in snippets)


async def test_unknown_route_path_still_researches_with_grounding(monkeypatch):
    """`route_after_intent` treats anything that is not conversation/direct as
    research. The gate must agree, or the fail-safe direction loses grounding."""
    calls, planner_kwargs = await _run_with_route(monkeypatch, "something-garbage")
    assert calls >= 1
    assert planner_kwargs.get("context_snippets")


# ---------------------------------------------------------------------------
# Trace instrumentation: started_at is a real superstep boundary
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()

