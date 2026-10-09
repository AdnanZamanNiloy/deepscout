"""Retrieval-engine diversity vs publisher diversity.

The failure these guard against is subtle and expensive: ONE engine returning
results from FIFTY different domains looks like superb diversity in any
count-that-only-measures-domains view. And the mirror case — twenty results from
one publisher — is a single source however many pages it fills.

So the two questions are counted separately and never added together:
  * engine diversity    = how many independent indexes answered
  * publisher diversity = how many distinct sites/organisations were cited
"""
from app.agents.searchkit.diversity import (
    aggregate_reports,
    diversity_report,
    engine_diversity,
    publisher_diversity,
    unresponsive_engines,
)
from app.agents.searchkit.types import SearchResult


def _r(domain, engine, url=None, publisher=""):
    return SearchResult(
        title="t",
        url=url or f"https://{domain}/p",
        snippet="s",
        source_domain=domain,
        publisher=publisher,
        retrieval_provider="searxng" if engine else "",
        retrieval_engine=engine,
        retrieval_engines=[engine] if engine else [],
    )


def test_one_engine_many_domains_is_NOT_engine_diversity():
    """The exact illusion this module exists to break."""
    results = [_r(f"site{i}.com", "google cse") for i in range(10)]
    engines = engine_diversity(results)
    publishers = publisher_diversity(results)

    assert engines["unique_engines"] == 1
    assert engines["top_engine"] == "google cse"
    assert engines["top_engine_share"] == 1.0
    assert engines["concentrated"] is True

    # The domains genuinely are ten, and the report must say so without that
    # number ever making the retrieval look diversified.
    assert publishers["unique_source_domains"] == 10


def test_many_engines_few_domains_is_still_one_publisher():
    results = [
        _r("mdpi.com", "google cse"),
        _r("mdpi.com", "duckduckgo"),
        _r("www.mdpi.com", "mwmbl"),
    ]
    assert publisher_diversity(results)["unique_source_domains"] == 1
    assert engine_diversity(results)["unique_engines"] == 3
    # Three engines, one publisher: this is an independence problem the engine
    # count alone would have hidden.


def test_healthy_mix_is_not_concentrated():
    results = [
        _r("a.com", "google cse"), _r("b.com", "google cse"),
        _r("c.com", "duckduckgo"), _r("d.com", "mwmbl"),
    ]
    report = diversity_report(results)
    assert report["engines"]["unique_engines"] == 3
    assert report["engines"]["concentrated"] is False
    assert report["concentration_cause"] == "none"


def test_single_healthy_looking_engine_is_still_concentrated():
    """Two domains from one engine is not diversification."""
    results = [_r("a.com", "mwmbl"), _r("b.com", "mwmbl")]
    assert engine_diversity(results)["concentrated"] is True


def test_concentration_cause_is_attributed_to_the_upstream_outage():
    """When engines failed upstream, concentration is the outage's fault — and
    the report must say that rather than implying a configuration problem."""
    results = [_r(f"s{i}.com", "mwmbl") for i in range(5)]
    report = diversity_report(
        results,
        unresponsive=[["brave", "too many requests"], ["duckduckgo", "CAPTCHA"]],
    )
    assert report["engines"]["concentrated"] is True
    assert report["concentration_cause"] == "upstream_outage"
    assert report["upstream_failure_count"] == 2
    assert {u["engine"] for u in report["unresponsive_engines"]} == {
        "brave", "duckduckgo",
    }


def test_multi_engine_result_counts_toward_each_engine():
    """A page two engines found is credited to both, so the engine count cannot
    be inflated by splitting or deflated by taking only the primary."""
    results = [_r("a.com", "google cse"), _r("a.com", "google cse")]
    results[0].retrieval_engines = ["google cse", "duckduckgo"]
    engines = engine_diversity(results)
    assert engines["counts_by_engine"]["google cse"] == 2
    assert engines["counts_by_engine"]["duckduckgo"] == 1


def test_aggregator_name_is_never_counted_as_an_engine():
    """'searxng' retrieved nothing on its own; counting it would inflate the
    engine count with the aggregator's own name."""
    results = [_r("a.com", ""), _r("b.com", "")]
    for r in results:
        r.provider = "searxng"
    assert engine_diversity(results)["unique_engines"] == 0


def test_legacy_rows_still_report_their_engine():
    """Rows persisted before the new fields carry only provider='searxng:x'."""
    result = SearchResult(title="t", url="https://a.com/p", snippet="s",
                          provider="searxng:google cse")
    assert engine_diversity([result])["counts_by_engine"] == {"google cse": 1}


def test_publisher_diversity_recognises_named_publishers():
    results = [
        _r("doi.org", "crossref", publisher="Springer Nature"),
        _r("doi.org", "crossref", publisher="Springer Nature"),
        _r("doi.org", "crossref", publisher="Elsevier"),
    ]
    pubs = publisher_diversity(results)
    assert pubs["unique_publishers"] == 2
    assert pubs["unique_source_domains"] == 1


def test_undomained_results_are_reported_not_hidden():
    """A result whose URL yields no domain must be visible as a blind spot, not
    silently dropped so the domain count looks clean. (source_domain empty is
    what the mapper produces for a malformed URL.)"""
    good = _r("a.com", "google cse")
    bad = _r("", "google cse", url="not-a-url")
    stats = publisher_diversity([good, bad])
    assert stats["unique_source_domains"] == 1
    assert stats["undomained_results"] == 1


def test_undomained_count_is_per_result_not_per_domain():
    """Three results on one domain is zero blind spots. The naive
    `len(results) - len(domains)` would report two phantom ones."""
    results = [_r("mdpi.com", "google cse") for _ in range(3)]
    assert publisher_diversity(results)["undomained_results"] == 0


def test_unresponsive_engines_tolerates_odd_shapes():
    assert unresponsive_engines([["brave", "429"]]) == [{"engine": "brave", "reason": "429"}]
    assert unresponsive_engines(["mojeek"]) == [{"engine": "mojeek", "reason": ""}]
    assert unresponsive_engines(None) == []


def test_aggregate_does_not_invent_a_ratio():
    """Per-query counts are summed; unique counts are maxed. Averaging the
    ratios would describe no query in the run."""
    reports = [
        {"results": 10, "counts_by_engine": {"a": 5, "b": 5}, "unique_engines": 2,
         "unique_source_domains": 8, "unique_publishers": 8, "concentrated": False,
         "unresponsive_engines": []},
        {"results": 6, "counts_by_engine": {"a": 6}, "unique_engines": 1,
         "unique_source_domains": 3, "unique_publishers": 3, "concentrated": True,
         "unresponsive_engines": [{"engine": "x", "reason": "timeout"}]},
    ]
    agg = aggregate_reports(reports)
    assert agg["queries_audited"] == 2
    assert agg["counts_by_engine"] == {"a": 11, "b": 5}
    assert agg["unique_engines"] == 2
    assert agg["max_unique_source_domains_in_one_query"] == 8
    assert agg["concentrated_queries"] == 1
    # Not every query was concentrated, so the run is not wholly concentrated.
    assert agg["concentrated"] is False
    assert agg["upstream_failure_count"] == 1


def test_empty_input_is_safe():
    report = diversity_report([])
    assert report["results"] == 0
    assert report["engines"]["unique_engines"] == 0
    assert report["engines"]["concentrated"] is False
    assert aggregate_reports([])["queries_audited"] == 0

def test_searxng_success_rate_is_over_engines_not_results():
    """One engine returning 20 hits is not twenty successes. Weighting by result
    count would let the surviving engine hide an outage behind its volume."""
    results = [_r(f"s{i}.com", "mwmbl") for i in range(20)]
    report = diversity_report(
        results,
        unresponsive=[["brave", "429"], ["duckduckgo", "CAPTCHA"], ["mojeek", "denied"]],
    )
    sx = report["searxng"]
    assert sx["responsive_count"] == 1
    assert sx["unresponsive_count"] == 3
    assert sx["engine_success_rate"] == 0.25
    assert sx["responsive_engines"] == ["mwmbl"]


def test_success_rate_is_none_when_nothing_was_audited():
    assert diversity_report([])["searxng"]["engine_success_rate"] is None


def test_cross_engine_dedup_is_detected_and_reported():
    """The ranker keeps one hit per canonical URL, so a second engine's copy of
    the same page disappears. That is a real source loss and must be visible."""
    from app.agents.searchkit.diversity import cross_engine_collisions

    results = [
        _r("example.com", "google cse", url="https://example.com/a"),
        _r("example.com", "duckduckgo", url="https://example.com/a?utm_source=x"),
        _r("other.com", "mwmbl", url="https://other.com/b"),
    ]
    stats = cross_engine_collisions(results)
    assert stats["result_rows"] == 3
    assert stats["unique_canonical_urls"] == 2
    assert stats["duplicate_url_rows"] == 1
    assert stats["overwritten_across_engines"] == 1


def test_same_engine_duplicates_are_not_reported_as_cross_engine_loss():
    from app.agents.searchkit.diversity import cross_engine_collisions

    results = [
        _r("example.com", "google cse", url="https://example.com/a"),
        _r("example.com", "google cse", url="https://example.com/a#frag"),
    ]
    assert cross_engine_collisions(results)["overwritten_across_engines"] == 0


def test_report_exposes_dedup_section():
    report = diversity_report([_r("a.com", "google cse")])
    assert report["dedup"]["unique_canonical_urls"] == 1
    assert report["dedup"]["overwritten_across_engines"] == 0
