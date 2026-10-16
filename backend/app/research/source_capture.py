"""Fetch text for the URLs a completed report cites, so verification judges
the PAGE, not the retrieval snippet.

The audit bridge verifies each claim against its cited source's captured text
(``app.evidence.verify``). Live runs showed the captured text for many cited
pages is only the search-result snippet (~200-400 chars) — too short for the
overlap band and numeric grounding, so claims the page itself supports failed
verification (a live report had 37% of its claim sentences carrying digits).
This pass runs once, after the report exists and outside the research-mode
timeout, and enriches the run's captured-source list with real page text.

Bounded by construction (8GB host): at most ``audit_fetch_max_urls`` URLs,
one shared wall-clock budget (``audit_fetch_timeout_sec``), concurrency =
``max_parallel_fetch``, and the fetch primitive's own ~12K-char clean cap.
Every cited URL passes the SSRF/local-file guard first: report links are model
output and are exactly as untrusted as search results. Never raises — audit
enrichment must not break a completed run (AGENTS.md 4.4/4.7).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from app.common.config import get_settings
from app.common.logging import get_logger
from app.evidence.sources import canonical_url

logger = get_logger(__name__)

# Below this many characters the best available text for a cited URL is a
# snippet, not a page: fetching is worth it. Fetched pages clean to ~12K
# chars (fetch._clean_html's cap) — an order of magnitude above any snippet.
AUDIT_FETCH_MIN_TEXT = 800


def _best_text(item: Dict[str, Any]) -> str:
    content = str(item.get("content", "") or "")
    snippet = str(item.get("snippet", "") or "")
    return content if content.strip() else snippet


async def capture_cited_source_text(
    captured: List[Dict[str, Any]],
    report: str,
    settings: Optional[Any] = None,
) -> Dict[str, int]:
    """Best-effort fetch of cited URLs whose captured text is only a snippet.

    Mutates ``captured`` in place (find-by-URL or append) so the audit bridge's
    ``build_source_index`` picks the fetched text up unchanged. Returns a
    census for the caller to log:
    ``{"considered", "fetched", "failed", "skipped_unsafe", "skipped_budget"}``.
    """
    stats = {
        "considered": 0,
        "fetched": 0,
        "failed": 0,
        "skipped_unsafe": 0,
        "skipped_budget": 0,
    }
    try:
        # Imported here so the module has no import-time dependency on the
        # retrieval stack (and tests can monkeypatch the source modules).
        from app.evidence.audit import _extract_urls
        from app.research.url_security import UnsafeURLError, validate_url
        from app.retrieval.client import _fetch_content_outcome
        from app.retrieval.searchkit.fetch import _BROWSER_USER_AGENT

        cfg = settings or get_settings()
        max_urls = max(0, int(getattr(cfg, "audit_fetch_max_urls", 32) or 0))
        if max_urls == 0 or not report.strip():
            return stats

        by_key: Dict[str, Dict[str, Any]] = {}
        for item in captured:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if url:
                by_key.setdefault(url, item)
                by_key.setdefault(canonical_url(url), item)

        targets: List[str] = []
        for url in _extract_urls(report):
            stats["considered"] += 1
            existing = by_key.get(url) or by_key.get(canonical_url(url))
            if existing is not None and len(_best_text(existing)) >= AUDIT_FETCH_MIN_TEXT:
                continue  # already has page-grade text
            if len(targets) >= max_urls:
                stats["skipped_budget"] += 1
                continue
            try:
                validate_url(url)
            except UnsafeURLError:
                stats["skipped_unsafe"] += 1
                logger.warning(
                    "[SourceCapture] skipping non-public URL cited by the report: %s",
                    url[:100],
                )
                continue
            targets.append(url)

        if not targets:
            return stats

        budget = max(1.0, float(getattr(cfg, "audit_fetch_timeout_sec", 20.0) or 20.0))
        retries = max(1, int(getattr(cfg, "search_fetch_retry_attempts", 2) or 2))
        parallel = max(1, int(getattr(cfg, "max_parallel_fetch", 4) or 4))
        per_fetch = min(10.0, budget / 2.0)
        semaphore = asyncio.Semaphore(parallel)

        def _apply(url: str, outcome: Any) -> None:
            text = str(getattr(outcome, "text", "") or "")
            if not text.strip():
                return
            entry = by_key.get(url) or by_key.get(canonical_url(url))
            if entry is None:
                entry = {"url": url, "snippet": "", "search_type": "general"}
                captured.append(entry)
                by_key[url] = entry
                by_key.setdefault(canonical_url(url), entry)
            entry["content"] = text
            entry["is_content_fetched"] = True
            last_modified = str(getattr(outcome, "last_modified", "") or "").strip()
            if last_modified and not str(entry.get("published_at") or "").strip():
                entry["published_at"] = last_modified

        async with httpx.AsyncClient(
            timeout=per_fetch,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            limits=httpx.Limits(max_connections=parallel),
        ) as client:

            async def _one(url: str) -> None:
                async with semaphore:
                    try:
                        outcome = await _fetch_content_outcome(
                            client, url, max_attempts=retries, timeout=per_fetch,
                        )
                    except Exception as exc:  # count it, never bubble (gather swallows silently)
                        stats["failed"] += 1
                        logger.debug(
                            "[SourceCapture] fetch raised for %s: %s", url[:100], exc,
                            exc_info=exc,
                        )
                        return
                if outcome.ok:
                    _apply(url, outcome)
                    stats["fetched"] += 1
                else:
                    stats["failed"] += 1
                    logger.debug(
                        "[SourceCapture] fetch failed (%s): %s",
                        getattr(outcome, "reason", "other"), url[:100],
                    )

            try:
                await asyncio.wait_for(
                    asyncio.gather(*(_one(u) for u in targets), return_exceptions=True),
                    timeout=budget,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[SourceCapture] %.0fs budget exhausted with %d/%d fetched",
                    budget, stats["fetched"], len(targets),
                )
        return stats
    except Exception as exc:  # a capture failure must never fail the run
        logger.warning("[SourceCapture] cited-source capture failed", exc_info=exc)
        return stats
