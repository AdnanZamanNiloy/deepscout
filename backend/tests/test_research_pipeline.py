"""Tests for the first-party research pipeline (app.research).

Covers the pieces with real logic that a regression could silently break:
prompt/report-type mapping, BM25 context filtering, retriever result shaping,
scraper content guards, review-loop sentinels, and an end-to-end graph smoke
test driven by fakes (no live provider).
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.research.config import load_config
from app.research.context.lexical import bm25_scores, rank_written_sections, tokenize
from app.research.context.select import resolve_context_filter, select_context
from app.research.enums import ReportKind
from app.research.prompts import PromptKit, builder_for
from app.research.retrieval.base import SearchClientRetriever
from app.agents.routing import (
    is_plan_approval,
    is_none_accept_response,
    route_evidence_audit,
    route_review_gate,
)


# --- prompts -----------------------------------------------------------------


def test_report_kind_method_mapping_resolves():
    assert builder_for("deep_dive", PromptKit).__name__ == "generate_report_prompt"
    assert builder_for("section", PromptKit).__name__ == "generate_section_prompt"
    # Unknown kinds fall back to the deep-dive builder, never None.
    fallback = builder_for("nonsense", PromptKit)
    assert fallback.__name__ == "generate_report_prompt"


def test_search_queries_prompt_forbids_operators():
    prompt = PromptKit.generate_search_queries_prompt("q", "", ReportKind.DeepDive.value, 3)
    assert "site:" in prompt and "search-engine operators" in prompt


def test_all_report_prompts_render_without_error():
    """Every prompt generator must build a non-empty string (AGENTS.md 4.1).

    A bad f-string reference (e.g. date.date()) raises at call time; this drives
    each generator so that failure is caught here, not mid-run.
    """
    from app.research.enums import Tone

    report = PromptKit.generate_report_prompt(
        "q", "ctx", "web", report_format="APA", target_words=500,
        tone=Tone.Objective, language="english",
    )
    assert report and "References" in report and date_marker_ok(report)

    subtopic = PromptKit.generate_section_prompt(
        "sub", [], [], "main", "ctx", tone=Tone.Objective,
    )
    assert subtopic and "sub" in subtopic

    intro = PromptKit.generate_report_introduction("q", "summary")
    assert intro and "q" in intro

    conclusion = PromptKit.generate_report_conclusion("q", "report body")
    assert conclusion and "q" in conclusion


def date_marker_ok(text: str) -> bool:
    """The report prompt must state a concrete current date."""
    import re

    return bool(re.search(r"\d{1,2} \w+ \d{4}", text))


# --- context -----------------------------------------------------------------


def test_tokenize_stems_plurals():
    assert "battery" in tokenize("batteries and costs")
    assert "cost" in tokenize("batteries and costs")


def test_bm25_ranks_relevant_passage_first():
    passages = ["unrelated text about cooking", "solar panel cost declined sharply in 2024"]
    scores = bm25_scores("solar panel cost", passages)
    assert scores[1] > scores[0]


def test_resolve_context_filter_defaults_to_keyword():
    assert resolve_context_filter("auto") == "keyword"
    assert resolve_context_filter("none") == "none"
    assert resolve_context_filter("bogus") == "keyword"


async def test_select_context_returns_all_when_small():
    pages = [{"raw_content": "short page", "url": "http://a.test", "title": "A"}]
    cfg = load_config()
    out = await select_context("q", pages, cfg, max_results=10)
    assert "http://a.test" in out


def test_rank_written_sections_finds_overlap():
    sections = [
        {"section_title": "costs", "written_content": "solar panel cost data 2024"},
        {"section_title": "cooking", "written_content": "how to bake bread"},
    ]
    ranked = rank_written_sections("solar cost", sections, max_results=5)
    assert any("solar" in r for r in ranked)


# --- retrieval ---------------------------------------------------------------


def test_retriever_shapes_results_and_respects_limit():
    results = [
        {"url": "http://a.test", "title": "A", "content": "body", "is_content_fetched": True},
        {"url": "http://b.test", "title": "B", "content": "snip", "is_content_fetched": False},
    ]
    shaped = SearchClientRetriever._to_engine_results(results, max_results=10)
    assert shaped[0] == {"url": "http://a.test", "href": "http://a.test", "title": "A", "raw_content": "body"}
    # Non-fetched results carry no raw_content (the pipeline will fetch them).
    assert "raw_content" not in shaped[1]


def test_retriever_source_sink_is_restorable():
    collected: list = []
    previous = SearchClientRetriever.bind_source_sink(collected.extend)
    assert previous is None
    SearchClientRetriever._record_sources([{"url": "http://a.test"}])
    assert collected and collected[0]["url"] == "http://a.test"
    SearchClientRetriever.bind_source_sink(previous)
    collected.clear()
    SearchClientRetriever._record_sources([{"url": "http://b.test"}])
    assert collected == []


def test_retriever_source_sink_failure_is_swallowed():
    def _boom(_):
        raise RuntimeError("down")

    SearchClientRetriever.bind_source_sink(_boom)
    try:
        SearchClientRetriever._record_sources([{"url": "http://a.test"}])  # no raise
    finally:
        SearchClientRetriever.bind_source_sink(None)


# --- scraper guards ----------------------------------------------------------


def test_scraper_rejects_block_and_wordlist_pages():
    from app.research.scraping.fetcher import (
        _looks_like_block_page,
        _looks_like_unextracted_pdf,
        _looks_like_word_list,
    )

    assert _looks_like_block_page("Attention Required! | Cloudflare")
    assert not _looks_like_block_page("A normal article about the weather.")
    assert _looks_like_word_list("word " * 100_000)
    assert not _looks_like_word_list("Real prose. With sentences! And structure? " * 1000)
    assert _looks_like_unextracted_pdf("%PDF-1.7 ... endobj endstream")


# --- routing sentinels -------------------------------------------------------


def test_none_accept_response_is_exact():
    assert is_none_accept_response("None")
    assert is_none_accept_response('"none"')
    # Critical notes that merely contain "none" are NOT acceptance.
    assert not is_none_accept_response("None of the criteria are met")
    assert not is_none_accept_response("")


def test_human_plan_approval_is_exact():
    assert is_plan_approval("no")
    assert not is_plan_approval("not enough cost data")


def test_route_review_gate_accepts_none_feedback():
    assert route_review_gate({"human_feedback": None}) == "accept"
    assert route_review_gate({"human_feedback": "change this", "plan_revision_count": 0}) == "revise"


def test_route_evidence_audit_accepts_clean_draft():
    assert route_evidence_audit({"fact_check_notes": None}) == "accept"
    assert route_evidence_audit({"fact_check_notes": "bad", "fact_check_revision_count": 0}) == "revise"


# --- end-to-end graph smoke --------------------------------------------------


class _FakeLLM:
    async def _generate_with_fallback(self, system_prompt, user_prompt):
        return "Synthesised body text."

    async def generate_json(self, system_prompt, user_prompt, retries=3, response_model=None, empty_ok=False):
        return {
            "title": "Test Report",
            "sections": ["Topic A", "Topic B"],
            "table_of_contents": "- A\n- B",
            "introduction": "Intro.",
            "conclusion": "Conclusion.",
            "sources": [],
        }


class _FakeResearcher:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def conduct_research(self):
        return "Collected evidence that is sufficiently long to be usable."

    async def write_report(self, **kwargs):
        return "## Topic A\nBody with a citation ([source](http://a.test))."


def test_graph_runs_end_to_end_with_fakes():
    from app.agents.team import build_research_team
    from app.research.llm import LLMFunnel

    cfg = load_config()
    chief = build_research_team(
        task={"query": "q", "max_sections": 2},
        llm=LLMFunnel(_FakeLLM()),
        cfg=cfg,
        researcher_factory=lambda **kw: _FakeResearcher(**kw),
    )
    graph = chief.build_graph().compile()

    async def _run():
        return await graph.ainvoke(
            {"task": {"query": "q", "max_sections": 2}}
        )

    result = asyncio.run(_run())
    assert result.get("sections") == ["Topic A", "Topic B"]
    assert result.get("report")
    assert "Test Report" in result["report"]


# --- runner wiring -----------------------------------------------------------


def test_pipeline_runner_facade_delegates_audit_capture(monkeypatch):
    """The route-facing facade must expose every method the routes call.

    Regression: routes call ``runner.capture_cited_sources()`` on the
    PipelineRunner facade, but the method was added only to the wrapped
    ResearchRunner — every completed live run then died with AttributeError at
    the final frame. The facade is a seam; a method on one side is not a method
    on the other.
    """
    import app.runtime.orchestrator as orchestrator

    called: list = []

    class _StubRunner:
        def __init__(self, **kwargs):
            pass

        async def capture_cited_sources(self):
            called.append("capture")

        def final_state(self):
            return {"stub": True}

    monkeypatch.setattr(orchestrator, "ResearchRunner", _StubRunner)
    facade = orchestrator.PipelineRunner(llm=None, search_client=None, query="q")
    asyncio.run(facade.capture_cited_sources())
    assert called == ["capture"]
    assert facade.final_state() == {"stub": True}


def test_runner_fails_loudly_without_search_client():
    """A run with no retrieval client must fail, not silently return no sources.

    Regression: the runtime handle was stored on the wrong object, so the
    runner received search_client=None. Every section then retrieved nothing and
    the run produced an abstaining report that looked like empty search rather
    than a misconfiguration.
    """
    from app.research.runner import ResearchRunner

    runner = ResearchRunner(
        llm=_FakeLLM(),
        search_client=None,
        query="q",
        mode="quick",
    )

    async def _drive():
        async for _ in runner.run():
            pass

    with pytest.raises(RuntimeError, match="no search client"):
        asyncio.run(_drive())


def test_report_prose_cleanup_strips_markers_and_rules():
    """The delivered report carries no inline [N] markers or --- scaffolding."""
    from app.research.runner import _clean_report_prose

    raw = "# T\n\n## S\n\nA claim [1][6].\n\n---\n\nA point\u2014made\u2014here."
    out = _clean_report_prose(raw)
    assert "[1]" not in out and "[6]" not in out
    assert "---" not in out
    assert "\u2014" not in out
    # References survive (not a machine section).
    ref = "# T\n\nBody [2].\n\n## References\n- [x](http://a.test)"
    assert "## References" in _clean_report_prose(ref)


def test_report_prose_cleanup_never_empties_a_report():
    """A cleaner failure or empty result must fall back to the input."""
    from app.research.runner import _clean_report_prose

    assert _clean_report_prose("") == ""
    assert _clean_report_prose("plain body") == "plain body"


def test_section_prior_context_feeds_titles_and_content():
    """Later waves see the titles and drafts of already-written sections."""
    from app.agents.architect import ResearchArchitect

    written = [
        {"section_title": "Section A", "draft": {"Section A": "A's written body."}},
    ]
    ctx = ResearchArchitect._prior_context(written, ["Section A", "Section B"])
    assert ctx["existing_headers"][0]["subtopic task"] == "Section A"
    assert ctx["relevant_written_contents"][0]["written_content"] == "A's written body."


def test_section_prior_context_empty_for_first_wave():
    from app.agents.architect import ResearchArchitect

    ctx = ResearchArchitect._prior_context([], ["Section A", "Section B"])
    assert ctx["existing_headers"] == []
    assert ctx["relevant_written_contents"] == []


def test_section_input_carries_prior_content():
    from app.agents.architect import ResearchArchitect

    prior = {
        "existing_headers": [{"subtopic task": "A", "note": "x"}],
        "relevant_written_contents": [{"section_title": "A", "written_content": "body"}],
    }
    inp = ResearchArchitect._section_input({"task": {}}, "B", "Title", ["A", "B"], prior)
    assert inp["existing_headers"] == prior["existing_headers"]
    assert inp["relevant_written_contents"] == prior["relevant_written_contents"]
    assert inp["sibling_sections"] == ["A"]


# --- source relevance gate ---------------------------------------------------


def _conductor():
    from app.research.skills.researcher import EvidenceGatherer

    class _R:
        query = "The most demanding skills in 2027"

    rc = EvidenceGatherer.__new__(EvidenceGatherer)
    rc.researcher = _R()
    rc.logger = logging.getLogger("test.relevance")
    return rc


def test_relevance_gate_drops_off_topic_sources():
    """A solar-wind / piano study must not reach a labor-market report."""
    rc = _conductor()
    pages = [
        {"url": "http://arxiv.org/bz4cast", "title": "Bz4Cast Verification",
         "raw_content": "A benchmark for solar wind and ionospheric Bz forecasting."},
        {"url": "http://europepmc.org/skills", "title": "In-demand skills 2027",
         "raw_content": "This study examines the most demanding skills in the 2027 labor market and workforce."},
        {"url": "http://arxiv.org/piano", "title": "Piano Skill Assessment Dataset",
         "raw_content": "A MIDI dataset for assessing piano performance."},
    ]
    kept = rc._filter_by_relevance(pages)
    titles = [p["title"] for p in kept]
    assert "In-demand skills 2027" in titles
    assert "Bz4Cast Verification" not in titles
    assert "Piano Skill Assessment Dataset" not in titles


def test_relevance_gate_keeps_all_when_nothing_relevant():
    """The gate must never empty a sub-query's context."""
    rc = _conductor()
    pages = [{"url": "http://a.test", "title": "Odd", "raw_content": "unrelated text"}]
    assert rc._filter_by_relevance(pages) == pages


def test_relevance_gate_passes_single_page():
    rc = _conductor()
    pages = [{"url": "http://a.test", "title": "X", "raw_content": "anything"}]
    assert rc._filter_by_relevance(pages) == pages
