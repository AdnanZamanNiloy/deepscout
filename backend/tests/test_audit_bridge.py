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
        "content": (
            "The technique improves factual accuracy over a pure parametric "
            "model for knowledge-intensive tasks."
        ),
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
        # Both cited claims are supported by the captured source text: the
        # honest reconstruction verifies them (instead of stamping True).
        assert f["verified"] is True
        assert 0.0 <= f["verification_score"] <= 1.0
        assert f["verification_reason"].startswith("verified")
        assert 0.0 <= f["confidence"] <= 1.0
        assert f["source"].startswith("http")


def test_multi_cited_sentence_verifies_against_the_union_of_its_sources():
    """A sentence citing two sources is judged against both, not just the first.

    Each source alone holds only part of the claim's vocabulary — verification
    against the first citation only (the old behavior) fails the overlap band
    on exactly the cross-source synthesis sentences the writer is told to
    produce (same bug class as the answer-support synthesis fix).
    """
    captured = [
        {"url": "https://a.test/willow", "content": "The Willow chip demonstrated error correction."},
        {"url": "https://b.test/scaling", "content": (
            "Scalable quantum computing promises require many physical qubits "
            "working together."
        )},
    ]
    report = (
        "## Findings\n"
        "The Willow chip unlocks scalable quantum computing promises "
        "([Willow](https://a.test/willow)) and this is confirmed by further "
        "analysis of the error correction results "
        "([Scaling](https://b.test/scaling)).\n"
    )
    index = build_source_index(captured, [], report)
    facts = build_facts(report, index, "quantum computing")
    assert facts
    union_fact = next(f for f in facts if f.get("sources"))
    assert union_fact["sources"] == ["https://a.test/willow", "https://b.test/scaling"]
    assert union_fact["verified"] is True, union_fact["verification_reason"]
    # Neither source alone would have passed: the first lacks the second
    # half's vocabulary and vice versa.
    first_only = build_facts(
        report,
        build_source_index([captured[0]], [], report),
        "quantum computing",
    )
    assert first_only and first_only[-1]["verified"] is False


def test_multi_cited_sentence_grounds_numbers_across_the_union():
    """A figure carried by the SECOND cited source counts as grounded."""
    captured = [
        {"url": "https://a.test/overview", "content": "An overview of the chip program."},
        {"url": "https://b.test/chip", "content": (
            "Google announced the 105-qubit Willow chip in 2024 with improved "
            "error correction results."
        )},
    ]
    report = (
        "## Findings\n"
        "Google announced the 105-qubit Willow chip with improved error "
        "correction results, according to the program overview "
        "([Overview](https://a.test/overview)) and the chip announcement "
        "([Chip](https://b.test/chip)).\n"
    )
    index = build_source_index(captured, [], report)
    facts = build_facts(report, index, "Willow chip")
    fact = next(f for f in facts if f.get("sources"))
    assert fact["verified"] is True, fact["verification_reason"]


def test_verification_census_is_logged(capfd):
    """The audit bridge logs a one-line census of why claims failed."""
    captured = [{"url": "https://a.test/p", "title": "P"}]  # no text at all
    report = (
        "## Findings\n"
        "A sufficiently long sentence citing a pageless source "
        "([P](https://a.test/p)).\n"
    )
    build_facts(report, build_source_index(captured, [], report), "q")
    out = capfd.readouterr().out
    assert "[AuditBridge] verification:" in out
    assert "0/1 verified" in out
    assert "no_text=1" in out


def test_build_facts_flags_number_hallucination():
    """A figure absent from the cited source is a hard verification failure."""
    captured = [{
        "url": "https://a.test/stats",
        "title": "Stats",
        "content": "Adoption grew 4% in 2024 across the surveyed sector.",
    }]
    report = (
        "## Findings\n"
        "Adoption grew 40% in 2024 across the surveyed sector "
        "([Stats](https://a.test/stats)).\n"
    )
    index = build_source_index(captured, [], report)
    facts = build_facts(report, index, "adoption growth")
    assert facts
    assert facts[0]["verified"] is False
    assert "figure" in facts[0]["verification_reason"]
    # Overlap itself is high (one digit differs); only the numeric check fails.
    assert facts[0]["verification_score"] > 0.35


def test_build_facts_unverifiable_without_source_text():
    """A cited source with no captured text is explicitly unverified, not assumed."""
    captured = [{"url": "https://a.test/p", "title": "P"}]
    report = "## Findings\nA sufficiently long sentence that cites this page ([P](https://a.test/p)).\n"
    index = build_source_index(captured, [], report)
    facts = build_facts(report, index, "q")
    assert facts
    assert facts[0]["verified"] is False
    assert facts[0]["verification_score"] == 0.0
    assert "unavailable" in facts[0]["verification_reason"]


def test_build_facts_stamps_plan_section_on_claims():
    index = build_source_index(CAPTURED, [], REPORT)
    facts = build_facts(REPORT, index, "q", sections=["Summary"])
    assert facts
    assert all(f["sub_question"] == "Summary" for f in facts)


def test_build_facts_attaches_cross_publisher_corroboration():
    """A claim restated by a different publisher's source gets corroborated.

    Uses realistic paraphrases, not identical text: the band is defined on the
    engine's ``claim_similarity``, and judging pairs by any other metric
    (e.g. the hybrid) silently misses restatements that band is calibrated for.
    """
    captured = [
        {
            "url": "https://www.who.int/topics/willow",
            "title": "Willow qubit count",
            "content": "Google's Willow chip has 105 physical qubits on one die.",
        },
        {
            "url": "https://www.nature.com/articles/willow",
            "title": "Willow review",
            "content": "The Willow processor from Google contains 105 physical qubits in total.",
        },
    ]
    report = (
        "## Summary\n"
        "Google's Willow chip has 105 physical qubits on one die "
        "([WHO](https://www.who.int/topics/willow)). The Willow processor from "
        "Google contains 105 physical qubits in total "
        "([Nature](https://www.nature.com/articles/willow)).\n"
    )
    index = build_source_index(captured, [], report)
    facts = build_facts(report, index, "q")
    corroborated = [
        f for f in facts if int(f.get("corroboration_count", 1) or 1) >= 2
    ]
    assert corroborated, "restated cross-publisher claim was not corroborated"


def test_build_facts_does_not_corroborate_within_one_publisher():
    same_publisher = [
        {"url": "https://blog.example.com/a", "content": "Wind capacity in the region doubled during 2025."},
        {"url": "https://blog.example.com/b", "content": "Wind capacity in the region doubled during 2025."},
    ]
    report = (
        "## Summary\n"
        "Wind capacity in the region doubled during 2025 ([A](https://blog.example.com/a)). "
        "Wind capacity in the region doubled during 2025 ([B](https://blog.example.com/b)).\n"
    )
    index = build_source_index(same_publisher, [], report)
    facts = build_facts(report, index, "wind capacity")
    assert facts
    assert all(int(f.get("corroboration_count", 1) or 1) == 1 for f in facts)


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


def test_compute_audit_uses_passed_critique_and_plan():
    """The run's real verdict and plan must reach the confidence signals.

    Without them critic_survival is a constant hedge (0.6) and axis_coverage
    never runs — the two wiring gaps that ceilinged live confidence.
    """
    index = build_source_index(CAPTURED, [], REPORT)
    facts = build_facts(REPORT, index, "q", sections=["Summary"])
    audit = compute_audit(
        query="q",
        report=REPORT,
        sources=index,
        facts=facts,
        critique={"is_sufficient": True},
        sub_questions=[{"question": "Summary", "axis": "Summary"}],
    )
    breakdown = audit["confidence_breakdown"]
    assert breakdown["critic_survival"] == 1.0
    assert breakdown["axis_coverage"] == 1.0


def test_audit_critique_maps_verdicts():
    from app.research.runner import _audit_critique

    assert _audit_critique({}) == {}
    assert _audit_critique({"fact_check_notes": None}) == {"is_sufficient": True}
    assert _audit_critique({"fact_check_notes": "   "}) == {}
    fail = _audit_critique({"fact_check_notes": "Section 2 misstates the figure"})
    assert fail["is_sufficient"] is False and fail["gaps"] == ["Section 2 misstates the figure"]


def test_audit_critique_prices_each_named_issue_separately():
    """N bullets → N gaps: the critic signal prices each issue (0.6 - 0.02N).

    Collapsing the list into one gap made every rejection score 0.58
    regardless of whether the auditor named one problem or five.
    """
    from app.research.runner import _audit_critique

    notes = "- Section 2 misstates the 40% figure\n* Missing source for claim X\n3) Contradicts the WHO page"
    critique = _audit_critique({"fact_check_notes": notes})
    assert critique["is_sufficient"] is False
    assert critique["gaps"] == [
        "Section 2 misstates the 40% figure",
        "Missing source for claim X",
        "Contradicts the WHO page",
    ]


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


# A pool that scores well ABOVE the cap on its own, so a cap is observable.
STRONG_REPORT = (
    "# Grid storage economics\n\n"
    "Grid-scale lithium storage costs fell to 140 dollars per kilowatt-hour "
    "in 2025 across three major markets according to two independent "
    "operator datasets ([1]). Sodium-ion chemistry reached commercial "
    "deployment in grid storage during 2025 with lower material costs ([2]).\n\n"
    "[1] https://example.com/a\n"
    "[2] https://example.org/b\n"
)
STRONG_FACTS = [
    {
        "claim": "Grid-scale lithium storage costs fell to 140 dollars per kilowatt-hour in 2025 across three major markets",
        "source": "https://example.com/a", "confidence": 0.9, "agent": "pipeline",
        "verified": True, "verification_score": 0.86,
        "verification_reason": "weighted overlap 0.86", "corroboration_count": 2,
    },
    {
        "claim": "Sodium-ion chemistry reached commercial deployment in grid storage during 2025 with lower material costs",
        "source": "https://example.org/b", "confidence": 0.85, "agent": "pipeline",
        "verified": True, "verification_score": 0.81,
        "verification_reason": "weighted overlap 0.81", "corroboration_count": 2,
    },
]
STRONG_SOURCES = [{"url": "https://example.com/a"}, {"url": "https://example.org/b"}]


def _audit(**over):
    from app.evidence.audit import compute_audit

    return compute_audit(
        query="grid storage economics",
        report=STRONG_REPORT,
        sources=STRONG_SOURCES,
        facts=[dict(f) for f in STRONG_FACTS],
        **over,
    )


def test_a_degraded_run_is_actually_capped_through_the_audit_bridge():
    """The degraded cap must fire on the LIVE path, not only in unit tests.

    `compute_confidence` is the only place that applies DEGRADED_CAP, and
    `compute_audit` is its only production caller. When that call site omitted
    `degraded=`, the guard was dead — a run assembled without a model could
    score as though it had been researched. The docs promised a cap nothing
    applied.

    A synthesizer fallback is a degraded PRODUCT whatever the evidence looks
    like, so it caps unconditionally; that is the case this asserts.
    """
    from app.core.confidence import DEGRADED_CAP

    healthy = _audit()
    assert healthy["confidence"] > DEGRADED_CAP, (
        "the fixture must score above the cap, or the assertion is vacuous"
    )

    capped = _audit(degraded=["synthesizer"])
    assert capped["confidence"] <= DEGRADED_CAP, (
        f"a synthesizer-fallback run scored {capped['confidence']}, above the "
        f"{DEGRADED_CAP} cap — the degraded input is not reaching the engine"
    )
    assert any("capped" in n.lower() for n in capped.get("confidence_notes", [])), (
        "the cap must be explained, not silently applied"
    )


def test_a_well_corroborated_summarizer_fallback_is_not_capped():
    """The evidence-conditional half of the rule, so the fix cannot over-cap.

    A summarizer that fell back to extractive text is capped ONLY when the
    pool is genuinely weak. A verified, independently corroborated extractive
    pool is a real (if blunt) answer and must not be scored down for it — the
    engine says so explicitly instead of capping silently.
    """
    from app.core.confidence import DEGRADED_CAP

    assert _audit(degraded=["summarizer"])["confidence"] > DEGRADED_CAP
    notes = _audit(degraded=["summarizer"]).get("confidence_notes", [])
    assert any("corroborated" in n and "no cap applied" in n for n in notes), notes


def test_a_provider_outage_caps_even_a_strong_pool():
    """A transport failure preserves no evidence signal, so it caps outright."""
    from app.core.confidence import DEGRADED_CAP

    out = _audit(degraded=[], provider_degraded=True)
    assert out["confidence"] <= DEGRADED_CAP


def test_the_audit_bridge_defaults_to_the_live_degradation_record():
    """With no explicit arguments the bridge reads the request's own state.

    Otherwise a future caller that forgets the arguments silently re-disables
    the cap — which is exactly how it was disabled the first time.
    """
    from app.core.confidence import DEGRADED_CAP
    from app.core.degradation import record_fallback, reset_fallbacks

    reset_fallbacks()
    try:
        record_fallback("synthesizer", "provider-transient")
        out = _audit()
        assert out["confidence"] <= DEGRADED_CAP, (
            "ambient degradation must reach the engine without being passed"
        )
    finally:
        reset_fallbacks()
