"""Source identity vs retrieval provenance.

The bug: SearXNG results were labelled `searxng:<engine>` in a single `provider`
string, and that string was rendered as the source. Every Google-CSE hit therefore
appeared to come from a publisher called "google cse" — including papers on
arxiv.org and mdpi.com. The retrieval INDEX had become the SOURCE IDENTITY.

These tests pin the separation itself, not just the new field names:
  * a Google-CSE hit from example.com says `example.com`;
  * the engine is still recorded, as provenance;
  * the legacy `provider` value is unchanged, so caches and old readers keep
    working (backward compatibility is a requirement, not an accident);
  * `www.`, subdomains, duplicate URLs and malformed URLs canonicalize sanely;
  * results from different engines keep their own provenance;
  * missing metadata can NEVER make the engine name surface as the publisher.
"""
import pytest

from app.agents.searchkit.identity import (
    canonical_source_domain,
    normalize_publisher,
    source_identity,
)
from app.agents.searchkit.searxng import _searxng_to_results
from app.agents.searchkit.types import SearchResult


def _row(**over):
    row = {
        "url": "https://www.mdpi.com/2227-7390/13/5/856",
        "title": "Hallucination Mitigation for RAG",
        "content": "Retrieval-augmented generation leverages...",
        "engine": "google cse",
        "engines": ["google cse"],
    }
    row.update(over)
    return row


# --- identity: canonicalization ------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/a", "example.com"),
        ("https://www.example.com/a", "example.com"),
        ("http://WWW.EXAMPLE.COM/a", "example.com"),
        ("https://user:pw@example.com:8443/a", "example.com"),
        ("https://news.bbc.co.uk/story", "news.bbc.co.uk"),
        ("https://arxiv.org/pdf/2301.1", "arxiv.org"),
        ("example.com", "example.com"),
        # Malformed / hostile input must degrade to "", never raise and never
        # render as a source label.
        ("", ""),
        ("not a url", ""),
        ("https:///no-host", ""),
        ("javascript:alert(1)", ""),
        ("https://", ""),
        ("https://..", ""),
    ],
)
def test_canonical_source_domain(url, expected):
    assert canonical_source_domain(url) == expected


def test_duplicate_urls_collapse_to_one_domain():
    """Tracking params, fragments and trailing slashes name one source."""
    variants = [
        "https://example.com/a?utm_source=x",
        "https://example.com/a#section",
        "https://example.com/a/",
        "https://www.example.com/a",
        "http://example.com:80/a",
    ]
    assert len({canonical_source_domain(u) for u in variants}) == 1


@pytest.mark.parametrize("raw", ["", None, "  ", "None", "null", "N/A", "-", "<html>"])
def test_unusable_publisher_normalizes_to_empty(raw):
    """A third-party feed must not be able to inject a fake source name."""
    assert normalize_publisher(raw) == ""


def test_publisher_is_taken_from_the_provider_not_invented():
    assert source_identity("https://example.com/x", "Nature Portfolio") == (
        "example.com",
        "Nature Portfolio",
    )
    # No publisher stated -> "", and the caller falls back to the domain. The
    # domain is never prettified into a name ("example.com" is not "Example").
    assert source_identity("https://example.com/x") == ("example.com", "")


# --- searxng mapping -------------------------------------------------------

def test_google_cse_hit_from_example_com_reports_example_com():
    result = _searxng_to_results(
        {"results": [_row(url="https://example.com/paper")]}, "q"
    )[0]
    assert result.source_domain == "example.com"
    assert result.retrieval_provider == "searxng"


def test_retrieval_provenance_still_identifies_google_cse():
    result = _searxng_to_results({"results": [_row()]}, "q")[0]
    assert result.retrieval_engine == "google cse"
    assert result.retrieval_engines == ["google cse"]


def test_legacy_provider_value_is_unchanged():
    """Backward compatibility: caches and existing readers depend on this."""
    result = _searxng_to_results({"results": [_row()]}, "q")[0]
    assert result.provider == "searxng:google cse"


def test_results_from_different_engines_keep_their_own_provenance():
    rows = [
        _row(url="https://a.com/1", engine="google cse", engines=["google cse"]),
        _row(url="https://b.com/2", engine="duckduckgo", engines=["duckduckgo"]),
        _row(url="https://c.com/3", engine="mwmbl", engines=["mwmbl"]),
    ]
    results = _searxng_to_results({"results": rows}, "q")
    got = {r.source_domain: r.retrieval_engine for r in results}
    assert got == {
        "a.com": "google cse",
        "b.com": "duckduckgo",
        "c.com": "mwmbl",
    }
    # No engine leaked into any domain or publisher field.
    for r in results:
        assert "cse" not in r.source_domain
        assert "duckduckgo" not in r.source_domain


def test_multi_engine_hit_keeps_every_credited_engine():
    """A metasearch can return one page several engines found; dropping the
    extras understates diversity and misattributes the page."""
    result = _searxng_to_results(
        {"results": [_row(engine="google cse", engines=["google cse", "duckduckgo"])]},
        "q",
    )[0]
    assert result.retrieval_engines == ["google cse", "duckduckgo"]


def test_engine_name_never_becomes_the_publisher():
    """Missing metadata must not let the engine masquerade as a publisher."""
    result = _searxng_to_results(
        {"results": [_row(url="https://example.com/x", publisher=None)]}, "q"
    )[0]
    assert result.publisher == ""
    assert result.source_domain == "example.com"
    assert result.retrieval_engine == "google cse"


def test_malformed_url_yields_empty_domain_but_keeps_provenance():
    result = _searxng_to_results({"results": [_row(url="not-a-url")]}, "q")[0]
    assert result.source_domain == ""
    assert result.retrieval_engine == "google cse"


def test_plugin_prefixed_engine_is_stripped():
    result = _searxng_to_results(
        {"results": [_row(engine="plugin:host", engines=["plugin:host"])]}, "q"
    )[0]
    assert result.retrieval_engine == "host"
    assert result.retrieval_engines == ["host"]


def test_to_dict_carries_identity_and_provenance_separately():
    payload = SearchResult(
        title="T",
        url="https://example.com/x",
        snippet="s",
        source_domain="example.com",
        publisher="Example Org",
        retrieval_provider="searxng",
        retrieval_engine="google cse",
        retrieval_engines=["google cse", "duckduckgo"],
    ).to_dict()
    assert payload["source_domain"] == "example.com"
    assert payload["publisher"] == "Example Org"
    assert payload["retrieval_provider"] == "searxng"
    assert payload["retrieval_engine"] == "google cse"
    assert payload["retrieval_engines"] == ["google cse", "duckduckgo"]


# --- the API's source label ------------------------------------------------

def test_api_source_label_prefers_domain_over_engine():
    from app.api.routes import _source_label

    label = _source_label({
        "url": "https://www.example.com/paper",
        "source_domain": "example.com",
        "publisher": "",
        "retrieval_engine": "google cse",
        "provider": "searxng:google cse",
    })
    assert label == "example.com"
    assert "cse" not in label


def test_api_source_label_never_falls_back_to_the_engine():
    """No domain, no publisher, a malformed URL: the label is empty, not the
    engine. An empty label beats a wrong one — the UI falls back to the host."""
    from app.api.routes import _source_label

    label = _source_label({
        "url": "not-a-url",
        "retrieval_engine": "google cse",
        "provider": "searxng:google cse",
    })
    assert label == ""
    assert "cse" not in label


def test_api_source_label_derives_domain_from_url_when_column_missing():
    """Rows persisted before source_domain existed must still label correctly."""
    from app.api.routes import _source_label

    assert _source_label({
        "url": "https://www.weforum.org/stories/1",
        "provider": "searxng:mwmbl",
    }) == "weforum.org"