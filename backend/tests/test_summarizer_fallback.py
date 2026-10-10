"""Summarizer heuristic-fallback relevance (v14): the fallback must rank
candidates by query/sub-question overlap instead of taking the first
sentences that clear a bare threshold.

Regression: a fully-degraded run on a Bangladesh nuclear-vs-solar query
surfaced a UN transcript's Malawi electrification paragraph as a top claim —
the sentence cleared a 0.15 whole-query overlap while being about a
different country entirely.
"""

import asyncio


from app.agents.summarizer import summarizer_agent
from app.core.config import Settings
from app.core.degradation import clear_fallbacks, reset_fallbacks


class ExplodingLLM:
    def __init__(self, tmp_path):
        self.settings = Settings(
            groq_api_key="k", database_url=str(tmp_path / "t.db"), _env_file=None
        )

    async def generate_json(self, *a, **k):
        raise RuntimeError("providers down")


def _results():
    return [
        {
            "title": "Bangladesh power sector outlook",
            "url": "https://example-power.gov.bd/outlook",
            "snippet": "",
            "content": (
                "Solar power in Bangladesh reached 3.2 percent of generation in 2025. "
                "The Rooppur nuclear plant dominates the capital budget with two reactors. "
                "Levelized costs for new solar fell below grid parity last year. "
                "Officials expect tariff pressure to ease after commissioning."
            ),
            "sub_question": "What are the levelized cost values for nuclear and solar power in Bangladesh?",
        },
        {
            # Same source quality, but its text is about a different region.
            "title": "Regional electrification meeting transcript",
            "url": "https://example-transcript.un.org/asset/k1u/x",
            "snippet": "",
            "content": (
                "The government is implementing key programs such as the Malawi Rural "
                "Electrification Program while scaling up mini grids to reach underserved "
                "communities across the lake region. Delegates discussed cookstove adoption. "
                "The chair closed the session thanking interpreters and delegates."
            ),
            "sub_question": "What are the levelized cost values for nuclear and solar power in Bangladesh?",
        },
    ]


def test_fallback_prefers_topically_matching_sentences(tmp_path):
    reset_fallbacks()
    try:
        facts = asyncio.run(
            summarizer_agent(ExplodingLLM(tmp_path), "Compare the economics of nuclear vs solar energy in Bangladesh", _results())
        )
    finally:
        clear_fallbacks()

    assert facts, "fallback must still produce claims"
    claims = " ".join(str(f.get("claim", "")) for f in facts).lower()
    # On-topic sentences from the power-sector source survive.
    assert "solar" in claims or "nuclear" in claims
    # The off-topic regional transcript contributes nothing about Malawi.
    assert "malawi" not in claims
    assert "cookstove" not in claims
    assert "interpreter" not in claims


def test_fallback_ranks_better_sentences_first(tmp_path):
    reset_fallbacks()
    try:
        facts = asyncio.run(
            summarizer_agent(ExplodingLLM(tmp_path), "Compare the economics of nuclear vs solar energy in Bangladesh", _results())
        )
    finally:
        clear_fallbacks()

    power_source_facts = [f for f in facts if "power.gov.bd" in str(f.get("source", ""))]
    assert power_source_facts, "on-topic source must contribute claims"
    # The highest-ranked claims should be the tariff/LCOE/nuclear sentences,
    # not the generic closing sentence.
    top = str(power_source_facts[0].get("claim", "")).lower()
    assert any(term in top for term in ("solar", "nuclear", "cost", "tariff")), top


def test_failed_llm_result_not_cached(tmp_path):
    """An empty result from a failed LLM call must not poison the cache:
    after a provider blip, the next call retries the LLM instead of serving
    the cached [] for a full TTL hour (extended degradation past recovery)."""
    from app.core.cache import get_cache

    class FlakyLLM:
        def __init__(self):
            self.settings = Settings(
                groq_api_key="k", database_url=str(tmp_path / "t.db"), _env_file=None
            )
            self.calls = 0

        async def generate_json(self, *a, **k):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider blip")
            return {"facts": [
                {"claim": "Solar power in Bangladesh reached grid parity for new utility projects",
                 "source": "https://power.gov.bd/outlook", "confidence": 0.9},
            ]}

    llm = FlakyLLM()
    results = [{
        "title": "Bangladesh power sector outlook",
        "url": "https://power.gov.bd/outlook",
        "snippet": "",
        "content": "Solar power in Bangladesh reached grid parity for new utility projects.",
        "sub_question": "What are the levelized cost values for solar power in Bangladesh?",
    }]
    reset_fallbacks()
    try:
        first = asyncio.run(summarizer_agent(llm, "Compare the economics of nuclear vs solar energy in Bangladesh", results))
        assert first == [] or all("solar" in f["claim"].lower() for f in first)
        cache = get_cache(llm.settings)
        # The failed call wrote nothing usable; a recovered provider must
        # be attempted again on the next run.
        second = asyncio.run(summarizer_agent(llm, "Compare the economics of nuclear vs solar energy in Bangladesh", results))
        assert llm.calls == 2, "empty LLM result was cached — provider recovery is blocked"
        assert any("grid parity" in f["claim"] for f in second)
    finally:
        clear_fallbacks()


def test_empty_model_object_is_weak_evidence_not_a_provider_outage(tmp_path):
    """A model that returns an empty object degraded the run as
    "provider-transient" ("the provider was temporarily unavailable (rate
    limit, timeout or outage)"), which is FALSE: every provider answered, the
    model just produced nothing usable. The reason must classify as evidence
    weakness, not a transport failure — the two must never be conflated
    (app/core/degradation.py, reliability #4)."""
    from app.core.degradation import EVIDENCE_WEAK, fallback_reasons

    class EmptyObjectLLM:
        def __init__(self):
            self.settings = Settings(
                groq_api_key="k",
                database_url=str(tmp_path / "empty_object.db"),
                _env_file=None,
            )
            self.calls = 0

        async def generate_json(self, *a, **k):
            # The exact live failure: HTTP 200, every field default.
            self.calls += 1
            raise ValueError("model returned an empty object; every field is default")

    llm = EmptyObjectLLM()
    results = [{
        "title": "Bangladesh power sector outlook",
        "url": "https://power.gov.bd/empty-object-probe",
        "snippet": "",
        "content": "Solar power in Bangladesh reached grid parity for new utility projects.",
        "sub_question": "What are the levelized cost values for solar power in Bangladesh?",
    }]
    reset_fallbacks()
    try:
        asyncio.run(summarizer_agent(
            llm, "Compare the economics of nuclear vs solar energy in Bangladesh", results
        ))
        reasons = fallback_reasons()
        assert reasons.get("summarizer") == EVIDENCE_WEAK, (
            f"empty-object degradation misclassified: {reasons!r}"
        )
    finally:
        clear_fallbacks()


def test_fallback_uses_the_same_relevance_floor_as_the_llm_path(tmp_path):
    """The extractive fallback must not admit claims the LLM path would reject.

    Regression (live MSc-topic run): when the free model returned an empty
    object on large prompts, the fallback ran with a 0.2 overlap floor — half
    the LLM path's MIN_QUERY_OVERLAP — so a degraded run admitted off-topic
    fragments the LLM path would have filtered ("rip current detection" on a
    computer-science topic query). A degraded run must be WEAKER, never less
    on-topic.
    """
    from app.agents.evidence.cleaning import MIN_QUERY_OVERLAP
    import app.agents.summarizer as _sum
    # Fix A: the fallback floor is now the LLM-path floor, not a looser one.
    assert _sum.MIN_FALLBACK_OVERLAP == MIN_QUERY_OVERLAP    # The behavioural contract: an off-topic sentence does NOT enter the pool.
    results = [
        {
            "title": "Rip current detection",
            "url": "https://example-arxiv.org/abs/1",
            "snippet": "",
            "content": (
                "Rip current detection and segmentation is a newly proposed "
                "benchmark task for coastal safety using video frames. "
                "The dataset contains annotated shorebreak imagery."
            ),
            "sub_question": "What are demanding MSc research topics in computer science?",
        }
    ]
    reset_fallbacks()
    try:
        facts = asyncio.run(
            summarizer_agent(
                ExplodingLLM(tmp_path),
                "suggest highly demanding research topic for M.sc in computer science",
                results,
            )
        )
    finally:
        clear_fallbacks()
    claims = " ".join(str(f.get("claim", "")) for f in facts).lower()
    assert "rip current" not in claims and "shorebreak" not in claims, (
        "an off-topic fragment must not survive the degraded extractive fallback"
    )
