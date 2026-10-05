"""SearXNG backend, scraper hygiene, and PDF extraction.

SearXNG replaces the Tavily/DuckDuckGo provider layer as the primary web
search. The contract it has to honour is the EXISTING one, not a new one:
`contract_queries` still fans out base question + planner variants + the
reserved primary-source query; every query still fans out across providers in
parallel; canonical-URL dedup, domain diversity, content fetching and the whole
scoring formula are untouched. Only the thing that talks to the web changed.

Response shape is grounded in the SearXNG source (see `searx/webutils.py`
`get_json_response` and `searx/result_types/_base.py` `MainResult.as_dict`):
`{"query", "results": [...], "answers", "corrections", "infoboxes",
"suggestions", "unresponsive_engines"}` with per-result `title`, `content`,
`url`, `publishedDate`, `engine`, plus paper fields `journal`/`doi`/`publisher`.

No test here needs a running SearXNG instance: the transport is exercised with
respx, and the mapping is a pure function.
"""

import httpx
import pytest
import respx

from app.agents import search as search_mod
from app.agents.search import (
    SearchClient,
    _clean_html,
    _extract_pdf_text,
    _looks_like_block_page,
    _prepare_searxng_query,
    _searxng_to_results,
)
from app.core.config import Settings

SEARXNG_URL = "http://searxng.test:8080/search"


def _settings(**over):
    base = {
        "groq_api_key": "k",
        "_env_file": None,
        "searxng_url": "http://searxng.test:8080",
    }
    base.update(over)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit breakers are module-level singletons (`reliability._BREAKERS`).

    Without this, the failure tests below trip the `searxng` and `wikipedia`
    breakers and every later test in the file is silently skipped by the
    half-open guard -- which is how the previous provider suite came to depend
    on test ordering. Reset per test so each one is independent.
    """
    from app.agents import reliability as rel

    rel._BREAKERS.clear()
    yield
    rel._BREAKERS.clear()


def _payload(**over):
    """A minimal but shape-accurate SearXNG JSON response."""
    row = {
        "url": "https://example.org/a",
        "title": "A result",
        "content": "Some extracted description of the page.",
        "engine": "duckduckgo",
        "publishedDate": "2026-03-04T10:00:00+00:00",
    }
    row.update(over.pop("row", {}))
    body = {
        "query": "q",
        "results": [row],
        "answers": [],
        "corrections": [],
        "infoboxes": [],
        "suggestions": [],
        "unresponsive_engines": [],
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# Response mapping
# ---------------------------------------------------------------------------


def test_searxng_payload_mapping():
    results = _searxng_to_results(_payload(), "q")
    assert len(results) == 1
    r = results[0]
    assert r.url == "https://example.org/a"
    assert r.title == "A result"
    assert r.snippet.startswith("Some extracted description")
    assert r.provider == "searxng:duckduckgo"
    assert r.published_at == "2026-03-04T10:00:00+00:00"
    assert r.matched_query == "q"


def test_searxng_records_the_upstream_engine():
    """A metasearch aggregates many indexes, so which engine produced a hit is
    diagnostic information a single-provider API never had to carry."""
    assert _searxng_to_results(
        _payload(row={"engine": "mojeek"}), "q"
    )[0].provider == "searxng:mojeek"
    # No engine reported -> still attributable to searxng.
    assert _searxng_to_results(_payload(row={"engine": ""}), "q")[0].provider == "searxng"
    # Plugin prefixes are stripped.
    assert _searxng_to_results(
        _payload(row={"engine": "plugin:host"}), "q"
    )[0].provider == "searxng:host"


def test_searxng_carries_paper_metadata_into_the_snippet():
    """`detect_primary_refs` reads a DOI out of the snippet to recognise the
    ORIGINAL of a study. A paper result's DOI must survive the mapping, or
    arXiv/Crossref-style results arriving through SearXNG lose their primacy
    signal."""
    r = _searxng_to_results(
        _payload(row={"template": "paper.html", "doi": "10.1234/abcd",
                      "journal": "Nature", "publisher": "Springer"}),
        "q",
    )[0]
    assert "10.1234/abcd" in r.snippet
    assert "Nature" in r.snippet


def test_searxng_skips_rows_without_a_url():
    kept = _searxng_to_results(
        {"results": [{"title": "no url"}, "not a dict", {"url": "https://ok.example/x"}]},
        "q",
    )
    assert [r.url for r in kept] == ["https://ok.example/x"]


def test_searxng_tolerates_a_non_list_results_field():
    assert _searxng_to_results({"results": None}, "q") == []
    assert _searxng_to_results({"results": {}}, "q") == []
    assert _searxng_to_results("not a dict", "q") == []


def test_searxng_strips_html_from_titles():
    r = _searxng_to_results(_payload(row={"title": "<b>Bold</b> title"}), "q")[0]
    assert r.title == "Bold title"


def test_searxng_applies_negative_site_exclusions():
    """SearXNG has no `-site:` request parameter, so exclusions are applied to
    the mapped results. Corroboration queries depend on this to obtain an
    INDEPENDENT publisher."""
    payload = {"results": [
        {"url": "https://reuters.com/a", "title": "A"},
        {"url": "https://news.reuters.com/b", "title": "B"},
        {"url": "https://worldbank.org/c", "title": "C"},
    ]}
    kept = _searxng_to_results(payload, "q", exclude_domains=["reuters.com"])
    assert [r.url for r in kept] == ["https://worldbank.org/c"]


def test_searxng_published_date_falls_back_across_fields():
    """`MainResult.publishedDate` is the real field; paper results carry
    `date_of_publication`; `pubdate` is the older string form."""
    def pub(row):
        return _searxng_to_results(_payload(row=row), "q")[0].published_at
    assert pub({"publishedDate": None, "date_of_publication": "2025-01-02",
                "pubdate": "2024-01-01"}) == "2025-01-02"
    assert pub({"publishedDate": None, "date_of_publication": None,
                "pubdate": "2024-01-01"}) == "2024-01-01"
    assert pub({"publishedDate": None}) == ""
    # A list date (some engines) takes its first element.
    assert pub({"publishedDate": ["2026-05-06T00:00:00"]}) == "2026-05-06T00:00:00"


# ---------------------------------------------------------------------------
# Query preparation
# ---------------------------------------------------------------------------


def test_searxng_query_requests_json_format():
    """`searx/webapp.py` reads `q` and `format` from the request form and
    returns HTML unless `format=json` is asked for."""
    _, params = _prepare_searxng_query("population of Malawi", "statistical", _settings())
    assert params["q"] == "population of Malawi"
    assert params["format"] == "json"
    assert params["pageno"] == 1


def test_searxng_keeps_hard_site_operators_in_the_query():
    """SearXNG forwards `site:` to its engines natively, so a jurisdiction-
    grounded target rides along in the query text."""
    text, params = _prepare_searxng_query(
        "malawi yield site:gov.mw OR site:worldbank.org", "statistical", _settings()
    )
    assert params["q"] == text
    assert "site:gov.mw" in text


def test_searxng_never_filters_on_an_unguessed_site_hint():
    """An ungrounded hint must not reach the provider. Passing it as a hard
    filter is what excluded the authoritative source in the first place; it
    stays a ranking preference instead."""
    text, params = _prepare_searxng_query(
        "retrieval benchmarks site:arxiv.org", "academic", _settings()
    )
    assert "site:arxiv.org" not in text
    assert "site:" not in text


def test_searxng_categories_track_the_search_type():
    settings = _settings()
    _, academic = _prepare_searxng_query("transformer", "academic", settings)
    assert academic["categories"] == "science"
    _, news = _prepare_searxng_query("grid auction", "news", settings)
    assert "news" in news["categories"]
    assert news["time_range"] == "month"
    _, stats = _prepare_searxng_query("population", "statistical", settings)
    assert stats["categories"] == "general,science"


def test_searxng_sends_no_result_count_parameter():
    """SearXNG has no per-request result limit -- per-engine counts live in the
    instance's settings.yml. Inventing a parameter would be silently ignored
    upstream and would look like it worked."""
    _, params = _prepare_searxng_query("q", "general", _settings(searxng_max_results=30))
    assert "searxng_max_results" not in params
    assert "max_results" not in params


def test_searxng_truncates_long_queries():
    text, params = _prepare_searxng_query("word " * 200, "general", _settings())
    assert len(params["q"]) <= 400


def test_searxng_empty_query_is_never_sent():
    text, params = _prepare_searxng_query("   ", "general", _settings())
    assert not text.strip()


# ---------------------------------------------------------------------------
# The transport
# ---------------------------------------------------------------------------


async def test_searxng_is_the_primary_web_search(monkeypatch, tmp_path):
    settings = _settings(database_url=str(tmp_path / "t.db"))
    client = SearchClient(settings)
    seen = []

    async def fake_providers(self, q, stype):
        # _providers_for is called once per contract query, so collect rather
        # than overwrite: contract_queries still fans out base + variants +
        # the reserved primary-source query and that must not change.
        seen.append(q)
        return _searxng_to_results(_payload(row={"url": f"https://e{len(seen)}.org/x"}), q)

    async def empty_wiki(self, query):
        return []

    monkeypatch.setattr(SearchClient, "_providers_for", fake_providers)
    ranked = await client._search("searxng probe query")
    assert seen[0] == "searxng probe query", "the base question must lead"
    assert len(seen) >= 2, "contract_queries must still fan out"
    assert any("site:" in q for q in seen), "the primary-source slot must survive"
    assert ranked


async def test_searxng_sends_a_clean_query_and_json_accept(monkeypatch):
    settings = _settings(searxng_url="http://searxng.test:8080")
    client = SearchClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("http://searxng.test:8080/search").mock(
            return_value=httpx.Response(200, json=_payload())
        )
        results = await client._searxng_search("population of Malawi", "statistical")

    assert route.called
    request = route.calls[0].request
    assert "site:" not in str(request.url)
    assert request.url.params["format"] == "json"
    assert request.url.params["q"] == "population of Malawi"
    assert request.headers["accept"] == "application/json"
    assert [(r.url, r.provider) for r in results] == [
        ("https://example.org/a", "searxng:duckduckgo")
    ]


async def test_searxng_content_counts_as_fetched(monkeypatch, tmp_path):
    """Provider-supplied body counts as read: no second fetch per URL."""
    settings = _settings(database_url=str(tmp_path / "t.db"))
    client = SearchClient(settings)
    calls = []

    async def fake_searxng(self, query, search_type=""):
        return _searxng_to_results(
            {"results": [{"title": "t", "url": "https://a.com/x",
                          "content": "already have full text here",
                          "engine": "duckduckgo"}]},
            query,
        )

    async def boom_fetch(url, client=None):
        calls.append(url)
        raise AssertionError("must not refetch provider content")

    async def empty_wiki(self, query):
        return []

    monkeypatch.setattr(SearchClient, "_searxng_search", fake_searxng)
    monkeypatch.setattr(SearchClient, "_wiki", empty_wiki)
    monkeypatch.setattr(search_mod, "_fetch_content", boom_fetch)
    ranked = await client._search("searxng skip probe query")
    assert len(ranked) == 1
    assert ranked[0].is_content_fetched is True
    assert calls == []


async def test_searxng_applies_the_result_cap(monkeypatch):
    """`searxng_max_results` is a client-side cap, because SearXNG offers no
    request parameter for it."""
    settings = _settings(searxng_max_results=2)
    client = SearchClient(settings)
    rows = [{"url": f"https://e{i}.org/x", "title": f"T{i}", "content": "c",
             "engine": "duckduckgo"} for i in range(6)]

    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://searxng.test:8080/search").mock(
            return_value=httpx.Response(200, json={"results": rows})
        )
        results = await client._searxng_search("cap probe", "general")
    assert len(results) == 2


async def test_searxng_empty_results_degrade_to_nothing(monkeypatch):
    """An aggregate returning nothing usually means misconfigured engines. That
    must surface as a provider failure, not be mistaken for "no such document
    exists"."""
    settings = _settings()
    client = SearchClient(settings)

    async def boom(self):
        raise AssertionError("wiki must not run in this test")

    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://searxng.test:8080/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        results = await client._searxng_search("nothing here", "general")
    assert results == []
    assert client.health_snapshot()["providers"]["searxng"]["fail"] >= 1


async def test_searxng_http_error_degrades_to_nothing(monkeypatch):
    settings = _settings()
    client = SearchClient(settings)
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://searxng.test:8080/search").mock(
            return_value=httpx.Response(500)
        )
        assert await client._searxng_search("boom", "general") == []


async def test_searxng_disabled_makes_no_request(tmp_path):
    settings = _settings(searxng_enabled=False, database_url=str(tmp_path / "t.db"))
    client = SearchClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("http://searxng.test:8080/search").mock(
            return_value=httpx.Response(200, json=_payload())
        )
        await client._search("disabled probe query")
    assert not route.called


async def test_searxng_missing_endpoint_makes_no_request(tmp_path):
    settings = _settings(searxng_url="", database_url=str(tmp_path / "t.db"))
    client = SearchClient(settings)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("http://searxng.test:8080/search").mock(
            return_value=httpx.Response(200, json=_payload())
        )
        await client._search("no endpoint probe")
    assert not route.called


async def test_searxng_keeps_the_free_primary_source_legs(monkeypatch, tmp_path):
    """arXiv / Crossref / Wikipedia are untouched and still run alongside."""
    settings = _settings(database_url=str(tmp_path / "t.db"))
    client = SearchClient(settings)
    called = []

    async def fake_searxng(self, query, search_type=""):
        called.append(("searxng", search_type))
        return _searxng_to_results(_payload(), query)

    async def fake_arxiv(self, query):
        called.append(("arxiv", ""))
        return []

    async def fake_crossref(self, query):
        called.append(("crossref", ""))
        return []

    async def fake_wiki(self, query):
        called.append(("wiki", ""))
        return []

    monkeypatch.setattr(SearchClient, "_searxng_search", fake_searxng)
    monkeypatch.setattr(SearchClient, "_arxiv", fake_arxiv)
    monkeypatch.setattr(SearchClient, "_crossref", fake_crossref)
    monkeypatch.setattr(SearchClient, "_wiki", fake_wiki)

    await client._providers_for("academic query", "academic")
    assert {n for n, _ in called} == {"searxng", "arxiv", "crossref"}

    called.clear()
    await client._providers_for("general query", "general")
    assert {n for n, _ in called} == {"searxng", "wiki"}


async def test_unresponsive_upstream_engines_are_logged_not_fatal(monkeypatch, caplog):
    settings = _settings()
    client = SearchClient(settings)
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://searxng.test:8080/search").mock(
            return_value=httpx.Response(
                200, json=_payload(unresponsive_engines=[{"name": "bing", "error": "timeout"}])
            )
        )
        results = await client._searxng_search("partial engines", "general")
    assert len(results) == 1, "one dead upstream engine must not lose the batch"


async def test_slow_searxng_degrades_instead_of_stalling(monkeypatch):
    """The aggregate can be slow (many upstream engines). A timeout must
    degrade to no results and be counted, not stall the contract."""
    settings = _settings(searxng_timeout_sec=0.2)
    client = SearchClient(settings)

    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://searxng.test:8080/search").mock(
            side_effect=httpx.ReadTimeout("too slow")
        )
        assert await client._searxng_search("slow probe", "general") == []
    assert client.health_snapshot()["providers"]["searxng"]["fail"] >= 1


async def test_searxng_uses_the_configured_timeout():
    """The aggregate is legitimately slower than a single-provider API, so the
    timeout is generous and configurable rather than the 20s fetch timeout."""
    import inspect

    settings = _settings(searxng_timeout_sec=45.0)
    client = SearchClient(settings)
    src = inspect.getsource(type(client)._searxng_search)
    assert "searxng_timeout_sec" in src
    assert float(getattr(settings, "searxng_timeout_sec")) == 45.0


# ---------------------------------------------------------------------------
# Provider-agnostic behaviour (carried over verbatim from the Tavily suite)
# ---------------------------------------------------------------------------


def test_block_page_detection():
    assert _looks_like_block_page("Access denied. Please verify you are human to continue.") is True
    assert _looks_like_block_page("Enable JavaScript to view this content") is True
    # Long articles mentioning captchas are NOT block pages.
    long_article = ("This paper surveys captcha designs across two decades of research. " * 20
                    + "Access denied patterns are compared in section four.")
    assert _looks_like_block_page(long_article) is False
    assert _looks_like_block_page("") is False


def test_pdf_extraction_reads_pages(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Transfer learning reuses pretrained models for new tasks.")
    pdf_bytes = doc.tobytes()
    doc.close()
    text = _extract_pdf_text(pdf_bytes, "https://x.com/paper.pdf")
    assert "Transfer learning reuses pretrained models" in text


def test_pdf_missing_lib_skips(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("pymupdf", "fitz"):
            raise ImportError("no pdf lib")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert _extract_pdf_text(b"%PDF-1.4 junk", "https://x.com/a.pdf") == ""


def test_clean_html_drops_nonvisible_markup():
    html = (
        "<html><head><title>Ignored title</title>"
        "<script>var tracker = 1;</script>"
        "<style>.hidden { display: none; }</style></head>"
        "<body><nav>Home | Search</nav>"
        "<article><h1>Visible finding</h1><p>First paragraph.</p>"
        "<p>Second paragraph.</p></article></body></html>"
    )
    text = _clean_html(html)

    assert "Visible finding" in text
    assert "First paragraph." in text
    assert "Second paragraph." in text
    assert "tracker" not in text
    assert "display: none" not in text
    assert "<" not in text
    assert len(text) <= 6000


async def test_wiki_sends_user_agent():
    """Wikipedia 403s script default UAs; _wiki must identify itself or
    every call degrades to [] (regression: all wiki queries failed live
    with a JSON decode error on the 403 page)."""
    settings = _settings()
    client = SearchClient(settings)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("https://en.wikipedia.org/w/api.php").mock(
            return_value=httpx.Response(200, json={
                "query": {"search": [{
                    "title": "Transformer",
                    "snippet": "A <span>transformer</span> is a device",
                    "timestamp": "2026-01-01T00:00:00Z",
                }]},
            }))
        results = await client._wiki("transformer")
    assert route.called
    assert "user-agent" in route.calls[0].request.headers
    assert "python-httpx" not in route.calls[0].request.headers["user-agent"]
    assert [(row.title, row.provider) for row in results] == [("Transformer", "wikipedia")]


async def test_fetch_content_sends_browser_ua():
    """Generic page fetch identifies as a browser (upstream scraper parity):
    publishers that block script UAs would otherwise yield block pages."""
    from app.agents import search as search_mod

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get("https://example.com/article").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/html"},
                text="<html><body><p>Article body text.</p></body></html>"))
        text, _ = await search_mod._fetch_content("https://example.com/article")
    assert route.called
    assert route.calls[0].request.headers["user-agent"].startswith("Mozilla/")
    assert "Article body text." in text


def test_split_query_shapes():
    from app.agents.search import _split_query

    assert _split_query("plain text") == ("plain text", "")
    assert _split_query(("pair text", "news")) == ("pair text", "news")
    assert _split_query({"question": "  contract q  ", "search_type": "news"}) == ("contract q", "news")
    assert _split_query({"question": "", "search_type": "news"}) == ("", "news")
    assert _split_query("") == ("", "")
