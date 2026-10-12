"""Audit bridge: reconstruct the retained-core fact pool from a run's output.

The research pipeline emits a markdown report and a list of retrieved sources,
but the retained audit core (confidence, contradictions, evidence grades,
quality/citation-support) was built for the built-in pipeline's structured fact
pool. These tests pin the bridge's contract:

* sources are parsed out of the report's citation strings and merged with the
  sources actually captured during retrieval;
* claims are extracted deterministically from the report's prose and attributed
  to the URL each sentence cites;
* the reconstructed facts drive the retained core so confidence/evidence-grades
  come out non-empty and sane;
* every step is total — empty or malformed input yields empty audit fields, not
  an exception.
"""
from __future__ import annotations

from app.evidence.audit import (
    build_facts,
    build_source_index,
    compute_audit,
    parse_source_citations,
)


CAPTURED = [
    {
        "url": "https://www.who.int/topics/rag",
        "title": "WHO RAG overview",
        "content": "Retrieval augmented generation grounds answers in evidence.",
        "published_at": "2023-05-01",
        "search_type": "general",
        "is_primary": True,
    },
    {
        "url": "https://arxiv.org/abs/2005.11401",
        "title": "RAG paper",
        "published_at": "2020-05-22",
        "search_type": "academic",
    },
]

REPORT = """# Retrieval Augmented Generation

## Summary
Retrieval augmented generation combines a retriever with a generator to ground
answers in evidence ([WHO RAG overview](https://www.who.int/topics/rag)). The
technique improves factual accuracy over a pure parametric model
([RAG paper](https://arxiv.org/abs/2005.11401)).

## Sources
[1] RAG paper — https://arxiv.org/abs/2005.11401
"""


def test_parse_source_citations_pulls_urls_from_citation_strings():
    urls = parse_source_citations([
        "-  WHO, 2023, WHO [source](https://www.who.int/topics/rag)",
        "https://arxiv.org/abs/2005.11401",
    ])
    assert "https://www.who.int/topics/rag" in urls
    assert "https://arxiv.org/abs/2005.11401" in urls


def test_parse_source_citations_dedupes():
    urls = parse_source_citations([
        "[a](https://x.test/p)",
        "[b](https://x.test/p)",
    ])
    assert urls == ["https://x.test/p"]


def test_build_source_index_prefers_captured_metadata():
    index = build_source_index(CAPTURED, [], REPORT)
    by_url = {s["url"]: s for s in index}
    who = by_url["https://www.who.int/topics/rag"]
    assert who["title"] == "WHO RAG overview"
    assert who["published_at"] == "2023-05-01"
    # A URL cited only in the report (not captured) is still included.
    assert all("url" in s for s in index)


def test_build_source_index_includes_report_only_urls():
    report = "Text with a link https://only-in-report.test/x here."
    index = build_source_index([], [], report)
    assert any(s["url"] == "https://only-in-report.test/x" for s in index)


def test_build_facts_attributes_claims_to_cited_sources():
    index = build_source_index(CAPTURED, [], REPORT)
    facts = build_facts(REPORT, index, "What is retrieval augmented generation?")
    assert len(facts) >= 2
    sources = {f["source"] for f in facts}
    assert "https://www.who.int/topics/rag" in sources
    assert "https://arxiv.org/abs/2005.11401" in sources
    for f in facts:
        assert f["claim"]
        assert f["verified"] is True
        assert 0.0 <= f["confidence"] <= 1.0
        assert f["source"].startswith("http")


def test_build_facts_does_not_glue_headings_onto_claims():
    index = build_source_index(CAPTURED, [], REPORT)
    facts = build_facts(REPORT, index, "What is retrieval augmented generation?")
    # "Retrieval Augmented Generation Summary ..." (heading glued to sentence)
    assert not any(f["claim"].startswith("Retrieval Augmented Generation Summary") for f in facts)


def test_build_facts_empty_on_empty_input():
    assert build_facts("", [], "q") == []
    assert build_facts(REPORT, [], "q") == []


def test_compute_audit_populates_confidence_and_grades():
    index = build_source_index(CAPTURED, [], REPORT)
    facts = build_facts(REPORT, index, "What is retrieval augmented generation?")
    audit = compute_audit(
        query="What is retrieval augmented generation?",
        report=REPORT,
        sources=index,
        facts=facts,
    )
    assert 0.0 <= audit["confidence"] <= 1.0
    assert audit["confidence_breakdown"]
    # Two sources, two claims: at least one evidence grade must be recorded.
    assert sum(audit["evidence_distribution"].values()) >= 1


def test_compute_audit_is_total_on_empty_facts():
    audit = compute_audit(query="q", report="", sources=[], facts=[])
    # No evidence -> a near-zero score (the confidence engine's floor from
    # critic-survival alone), never a crash and never an inflated number.
    assert audit["confidence"] < 0.1
    assert audit["facts"] == []
    assert audit["contradictions"] == []
    assert audit["evidence_distribution"] == {}


def test_retriever_source_sink_is_per_run_and_restorable():
    """The source collector binds to the retriever class and restores cleanly."""
    from app.research.retrieval.base import SearchClientRetriever

    collected: list = []

    class _Probe(SearchClientRetriever):
        pass

    previous = _Probe.bind_source_sink(collected.extend)
    assert previous is None
    _Probe._record_sources([{"url": "https://a.test/x", "title": "A"}])
    assert collected and collected[0]["url"] == "https://a.test/x"

    # Restoring the previous (None) sink clears the binding: no cross-run leak.
    _Probe.bind_source_sink(previous)
    collected.clear()
    _Probe._record_sources([{"url": "https://b.test/y"}])
    assert collected == []


def test_retriever_source_sink_failure_does_not_raise():
    """A broken collector must never break retrieval (AGENTS.md 4.4)."""
    from app.research.retrieval.base import SearchClientRetriever

    class _Probe(SearchClientRetriever):
        pass

    def _boom(_):
        raise RuntimeError("collector down")

    _Probe.bind_source_sink(_boom)
    try:
        _Probe._record_sources([{"url": "https://a.test/x"}])  # no exception
    finally:
        _Probe.bind_source_sink(None)
