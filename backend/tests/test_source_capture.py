"""Audit-time cited-source capture: bounded, SSRF-guarded, never raising.

The confidence score's dominant drags are citation coverage and verification
strength, and the verifier can only judge what the run captured. These tests
pin the enrichment pass: thin cited URLs get fetched, rich ones are left
alone, unsafe report links are refused before any socket opens, and every
budget (count, wall-clock, failures) degrades instead of raising — a capture
failure must never break an otherwise-completed run.
"""
from __future__ import annotations

import asyncio

import pytest

from app.research import source_capture
from app.research.url_security import UnsafeURLError
from app.retrieval.searchkit.fetch import FetchOutcome


class _Settings:
    def __init__(self, **overrides):
        self.audit_fetch_max_urls = 16
        self.audit_fetch_timeout_sec = 15.0
        self.search_fetch_retry_attempts = 1
        self.max_parallel_fetch = 4
        for key, value in overrides.items():
            setattr(self, key, value)


@pytest.fixture()
def fetch_env(monkeypatch):
    """Patch the lazily-imported fetch/validate seams with deterministic ones."""
    import app.research.url_security as url_security
    import app.retrieval.client as client_mod

    calls: list = []
    state = {"respond": None, "validate": lambda url: url}

    async def _fake_fetch(client, url, *, max_attempts, timeout):
        calls.append(url)
        return state["respond"](url)

    monkeypatch.setattr(client_mod, "_fetch_content_outcome", _fake_fetch)
    monkeypatch.setattr(url_security, "validate_url", lambda url, **kw: state["validate"](url))
    return state, calls


def _capture(captured, report, settings=None, **kwargs):
    return asyncio.run(
        source_capture.capture_cited_source_text(
            captured, report, settings or _Settings(**kwargs)
        )
    )


_REPORT = (
    "## Findings\n"
    "A sufficiently long sentence about the first source content here "
    "([A](https://a.test/page)). Another sufficiently long sentence citing "
    "the second source material ([B](https://b.test/new)). A third "
    "sufficiently long sentence about the rich source ([C](https://c.test/rich)).\n"
)


def test_thin_cited_urls_are_fetched_and_rich_ones_left_alone(fetch_env):
    state, called = fetch_env
    state["respond"] = lambda url: FetchOutcome(
        text="Fetched page body about the topic, comfortably long.", reason="ok"
    )
    captured = [
        {"url": "https://a.test/page", "snippet": "short snippet", "search_type": "general"},
        {"url": "https://c.test/rich", "content": "x" * 900, "search_type": "general"},
    ]
    stats = _capture(captured, _REPORT)
    assert stats["considered"] == 3
    assert stats["fetched"] == 2
    assert stats["failed"] == 0
    assert set(called) == {"https://a.test/page", "https://b.test/new"}
    by_url = {entry["url"]: entry for entry in captured}
    # The snippet entry was enriched in place; the report-only URL was appended.
    assert by_url["https://a.test/page"]["is_content_fetched"] is True
    assert "Fetched page body" in by_url["https://a.test/page"]["content"]
    assert "Fetched page body" in by_url["https://b.test/new"]["content"]
    # The rich entry is untouched and was never fetched.
    assert by_url["https://c.test/rich"]["content"] == "x" * 900


def test_unsafe_cited_url_is_refused_before_any_fetch(fetch_env):
    state, called = fetch_env

    def _validator(url):
        if "169.254" in url:
            raise UnsafeURLError("non-public address")
        return url

    state["validate"] = _validator
    state["respond"] = lambda url: FetchOutcome(text="body", reason="ok")
    captured = []
    report = (
        "## Findings\n"
        "A long enough sentence that cites an internal metadata address "
        "([M](http://169.254.169.254/meta)) and a normal one ([B](https://b.test/new)).\n"
    )
    stats = _capture(captured, report)
    assert stats["skipped_unsafe"] == 1
    assert called == ["https://b.test/new"]
    assert all("169.254" not in entry["url"] for entry in captured)


def test_max_urls_bounds_the_batch(fetch_env):
    state, called = fetch_env
    state["respond"] = lambda url: FetchOutcome(text="body", reason="ok")
    report = (
        "## Findings\n"
        "A long enough sentence citing the first page ([A](https://a.test/1)). "
        "A long enough sentence citing the second page ([B](https://b.test/2)). "
        "A long enough sentence citing the third page ([C](https://c.test/3)).\n"
    )
    stats = _capture([], report, audit_fetch_max_urls=1)
    assert stats["fetched"] == 1
    assert stats["skipped_budget"] == 2
    assert len(called) == 1


def test_fetch_failures_leave_the_snippet_in_place_and_never_raise(fetch_env):
    state, _ = fetch_env

    def _respond(url):
        if url.endswith("/page"):
            raise RuntimeError("connection exploded")
        return FetchOutcome(text="", reason="timeout")

    state["respond"] = _respond
    captured = [
        {"url": "https://a.test/page", "snippet": "short snippet"},
        {"url": "https://c.test/rich", "content": "x" * 900},
    ]
    stats = _capture(captured, _REPORT)
    # One raise + one empty-text outcome: both counted failed, none raised out.
    assert stats["fetched"] == 0
    assert stats["failed"] == 2
    by_url = {entry["url"]: entry for entry in captured}
    # The failed URL keeps its snippet text and gains no empty content field.
    assert by_url["https://a.test/page"].get("content", "") == ""
    assert by_url["https://a.test/page"]["snippet"] == "short snippet"
    # The report-only URL was never appended (nothing usable to store).
    assert "https://b.test/new" not in by_url


def test_empty_report_and_zero_budget_are_noops(fetch_env):
    state, called = fetch_env
    state["respond"] = lambda url: FetchOutcome(text="body", reason="ok")
    stats = _capture([], "")
    assert stats["considered"] == 0 and called == []
    stats = _capture([], _REPORT, audit_fetch_max_urls=0)
    assert stats["considered"] == 0 and called == []
