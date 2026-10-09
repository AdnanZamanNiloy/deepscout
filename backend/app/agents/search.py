"""Search Agent — multi-provider search, fetch and ranking.

Refactor note
-------------
The helper layers (result type, text overlap, scoring, ranking, content fetch,
SearXNG parsing and provider feed parsers) now live in the `app.agents.searchkit`
package. This module is the stable facade: it defines `SearchClient` (the public
class) and re-exports every helper name the rest of the codebase imports from
`app.agents.search`, so the import surface is UNCHANGED.
"""
from __future__ import annotations

import asyncio
import html  # noqa: F401
import random  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from typing import Any, Dict, List, Optional, Sequence, Union  # noqa: F401
from urllib.parse import quote  # noqa: F401
from xml.etree import ElementTree  # noqa: F401

import httpx

from app.core.cache import cache_key, get_cache
from app.core.config import Settings
from app.core.logging import get_logger
from app.agents.planner import SubQuestion
from app.agents.searchkit.cache_mixin import CacheMixin
from app.agents.searchkit.diversity import aggregate_reports as _aggregate_diversity
from app.agents.searchkit.providers_mixin import ProviderSearchMixin
from app.agents.searchkit.content_mixin import ContentMixin
from app.agents.reliability import (
    RetryPolicy,
    call_protected,  # noqa: F401
    gather_bounded,
    get_breaker,  # noqa: F401
    retry_after_from_headers,  # noqa: F401
)
from app.agents.retrieval_health import (
    DomainRegistry,
    FailedFetchLog,
    RetrievalHealth,
    classify_fetch_failure,  # noqa: F401
    failure_cools_host,  # noqa: F401
    failure_is_transient,  # noqa: F401
)
from app.agents.sources import (
    TOPICALITY_AUTHORITY_FLOOR,  # noqa: F401
    build_dimension_primary_query,  # noqa: F401
    build_substitution_query,  # noqa: F401
    canonical_url,  # noqa: F401
    classify_source,  # noqa: F401
    documentary_authority,  # noqa: F401
    extract_domain as _host,  # noqa: F401
    freshness_score,  # noqa: F401
    is_primary_source,  # noqa: F401
    is_topically_irrelevant,  # noqa: F401
    partition_site_targets,
    topical_engagement,  # noqa: F401
    topicality_floor_applies,  # noqa: F401
)

from app.agents.searchkit.types import (  # noqa: F401
    SearchResult,
)
from app.agents.searchkit.text import (  # noqa: F401
    _normalize_text,
    _semantic_overlap,
    _is_semantic_duplicate,
    _domain,
)
from app.agents.searchkit.scoring import (  # noqa: F401
    BLOCKED_DOMAINS,
    _is_blocked,
    PREFERRED_HOST_BONUS,
    PREFERRED_FAMILY_BONUS,
    _preferred_domain_bonus,
    _score_result,
)
from app.agents.searchkit.ranking import (  # noqa: F401
    ORIGIN_CAP,
    _apply_topical_floor,
    _deduplicate_and_rank,
)
from app.agents.searchkit.fetch import (  # noqa: F401
    _WIKI_USER_AGENT,
    _BROWSER_USER_AGENT,
    MAX_FETCH_BYTES,
    _READABLE_TYPES,
    _VisibleTextExtractor,
    _clean_html,
    BLOCK_PAGE_PHRASES,
    _looks_like_block_page,
    _extract_pdf_text,
    FETCH_RETRY_AFTER_CAP_SEC,
    _retry_after_header,
    FetchOutcome,
    _fetch_once,
    _domain_of,
)
from app.agents.searchkit.searxng import (  # noqa: F401
    _SITE_OPERATOR_RE,
    _SEARXNG_MAX_QUERY_CHARS,
    _searxng_enabled,
    _searxng_endpoint,
    _searxng_search_type_params,
    _prepare_searxng_query,
    _searxng_published,
    _searxng_to_results,
)
from app.agents.searchkit.queries import (  # noqa: F401
    _split_query,
    contract_queries,
)
from app.agents.searchkit.providers import (  # noqa: F401
    _arxiv_to_results,
    _crossref_to_results,
    _wiki_search_to_results,
)

logger = get_logger(__name__)


async def _fetch_content(url: str, client: httpx.AsyncClient | None = None):
    """Fetch + clean page text. Returns (text, last_modified_header_or_empty).

    A shared client (connection pooling) is passed on the hot path; when None, a
    throwaway client is created so unit tests and one-off callers keep working.

    Kept as the single public fetch primitive (tests and one-off callers patch
    it); `_fetch_content_outcome` below wraps it with status/reason accounting.
    """
    try:
        if client is None:
            async with httpx.AsyncClient(
                timeout=12,
                follow_redirects=True,
                headers={"User-Agent": _BROWSER_USER_AGENT},
            ) as owned:
                return await _fetch_with_client(owned, url)
        return await _fetch_with_client(client, url)
    except Exception as exc:
        logger.warning("[Search] content fetch failed for %s: %s", url[:80], exc, exc_info=exc)
    return "", ""

async def _fetch_with_client(client: httpx.AsyncClient, url: str):
    """One page fetch with redirects, type filtering and a size ceiling."""
    outcome = await _fetch_once(client, url)
    return outcome.text, outcome.last_modified

async def _fetch_content_outcome(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_attempts: int = 2,
    timeout: float = 12.0,
    on_retry=None,
) -> "FetchOutcome":
    """Health-aware fetch: classify the failure, retry transients, cap attempts.

    Uses `_fetch_content` as the single-fetch primitive (so the existing
    monkeypatch seam and status-aware `_fetch_once` both work) and returns a
    structured outcome. A fetch that returns empty text with no status and no
    exception is reported as an unclassified failure, not a success.
    """
    attempts = max(1, int(max_attempts))
    outcome = FetchOutcome(reason="other")
    for attempt in range(1, attempts + 1):
        # Prefer the status-aware primitive; fall back to the legacy tuple
        # primitive (which a test or older caller may have patched in).
        if _fetch_content is not _fetch_content_original:
            text, last_modified = await _fetch_content(url, client)
            retry_after = 0.0
            reason = "ok" if text else "other"
            status = None
        else:
            raw = await _fetch_once(client, url)
            text, last_modified = raw.text, raw.last_modified
            retry_after = raw.retry_after
            reason = raw.reason
            status = raw.status
        outcome = FetchOutcome(text=text, last_modified=last_modified,
                               status=status, reason=reason, attempts=attempt,
                               retry_after=retry_after)
        if outcome.ok or not failure_is_transient(outcome.reason):
            return outcome
        if attempt >= attempts:
            return outcome
        if on_retry is not None:
            try:
                on_retry(attempt, outcome.reason)
            except Exception:  # pragma: no cover - observer must not break flow
                pass
        delay = None
        if outcome.reason == "rate_limited" and outcome.retry_after:
            delay = outcome.retry_after
        if delay is None:
            raw_delay = min(6.0, 0.5 * (2 ** (attempt - 1)))
            delay = random.uniform(0.0, raw_delay)
        logger.info(
            "[Search] transient fetch failure (%s) for %s; retry %d/%d in %.2fs",
            outcome.reason, url[:80], attempt, attempts - 1, delay,
        )
        await asyncio.sleep(delay)
    return outcome

_fetch_content_original = _fetch_content


SEARCH_CACHE_VERSION = "search-v4"


class SearchClient(CacheMixin, ProviderSearchMixin, ContentMixin):
    """Multi-provider search with per-provider fault isolation.

    Concurrency is bounded twice: `semaphore` limits whole sub-question searches
    (as before) and a separate fetch limit bounds page downloads, so a
    contract that ranks 10 fetchable pages cannot monopolize the event loop or
    the socket pool.
    """
    def __init__(self, settings: Settings):
        self.settings = settings
        self._search_limit = max(1, int(getattr(settings, "max_parallel_search", 3) or 3))
        self.semaphore = asyncio.Semaphore(self._search_limit)
        self._fetch_limit = max(1, int(getattr(settings, "max_parallel_fetch", 4) or 4))
        self._timeout = float(getattr(settings, "search_timeout_sec", 20) or 20)
        self._retry = RetryPolicy(
            attempts=int(getattr(settings, "search_retry_attempts", 3) or 3),
            base_delay=0.5,
            max_delay=6.0,
            timeout=self._timeout,
        )
        self.provider_stats: Dict[str, Dict[str, int]] = {}
        # Per-query retrieval-diversity audits (engine counts vs publisher
        # counts, plus SearXNG's own unresponsive-engine list). Bounded: this is
        # telemetry, and an unbounded per-query log inside a long run is the
        # same shape of leak as the old module-level RUNTIME_STATE. Once it is
        # full the newest reading replaces the oldest, so the snapshot always
        # reflects recent retrieval rather than growing without limit.
        self.diversity_stats: List[Dict[str, Any]] = []

        # Retrieval access hardening (run-scoped, LRU-bounded). The domain
        # registry cools hard-blocked/rate-limited hosts; the failed-fetch log
        # remembers dead documents; the health counters make retrieval failure
        # distinguishable from genuinely thin evidence.
        self.domain_registry = DomainRegistry(
            cooldown_sec=float(getattr(settings, "search_domain_cooldown_sec", 90.0) or 90.0),
            failure_threshold=int(getattr(settings, "search_domain_failure_threshold", 3) or 3),
            max_domains=int(getattr(settings, "search_domain_registry_max", 512) or 512),
        )
        self.failed_fetches = FailedFetchLog(
            max_urls=int(getattr(settings, "search_failed_url_memory_max", 2048) or 2048)
        )
        self.health = RetrievalHealth()
    async def run_search(
        self, sub_questions: List[Union[SubQuestion, str, tuple]]
    ) -> List[Dict[str, Any]]:
        """Search a batch of delegation contracts (or raw strings).

        Bounded by `max_parallel_search` through `gather_bounded`, which does not
        allocate every coroutine up front — the previous `asyncio.gather` over
        all contracts created every provider client immediately and relied on an
        inner semaphore to throttle them.
        """
        contracts = list(sub_questions or [])
        if not contracts:
            return []

        # Budget accounting (Feature 12): every provider-backed contract
        # search counts against the run ledger when one is active. Recorded
        # before execution so a mid-batch failure still shows the spend.
        try:
            from app.core.usage import get_run_usage

            usage = get_run_usage()
            if usage is not None:
                usage.record_search("multi", stage="search", count=len(contracts))
        except Exception:
            pass

        factories = [(lambda c=c: self._search(c)) for c in contracts]
        batches = await gather_bounded(factories, self._search_limit)

        results: List[Dict[str, Any]] = []
        for contract, batch in zip(contracts, batches):
            if isinstance(batch, BaseException):
                logger.warning(
                    "[Search] contract failed: %s", type(batch).__name__, exc_info=batch
                )
                continue
            if isinstance(contract, str):
                question_text = contract
            elif isinstance(contract, dict):
                question_text = str(contract.get("question", "")).strip() or str(contract)
            else:
                question_text, _ = _split_query(contract)
            for r in batch or []:
                r.sub_question = question_text
                results.append(r.to_dict())

        return results
    async def run_grounding_search(self, query: str) -> List[Dict[str, Any]]:
        """Cheap single-query search for the planner's terminology grounding.

        Deliberately NOT `run_search`. A grounding search exists to hand the
        planner a handful of `title: snippet` pairs, and it paid for a full
        research retrieval to get them: as a bare string it was treated as a
        contract with no `primary_source_query`, so `contract_queries` built
        one and the call fanned out to 2-3 site-scoped queries AND downloaded
        page bodies — every one of which was discarded unread. Measured ~20s
        against a 0.28s LLM budget, of which the fetches and their retries were
        the bulk.

        Snippets come from the provider response, so dropping the fetch costs
        nothing here. Evidence extraction is unaffected: it goes through
        `run_search`, which still fetches bodies for the summarizer.
        """
        batch = await self._search(query, fetch_content=False)
        return [r.to_dict() for r in batch]
    async def _search(self, query, *, fetch_content: bool = True) -> List[SearchResult]:
        settings = self.settings
        question_text, search_type = _split_query(query)
        if not question_text:
            return []

        queries = contract_queries(
            query,
            max_queries=int(getattr(settings, "max_queries_per_contract", 3) or 3),
        )
        max_results = int(getattr(settings, "search_max_results", 10) or 10)

        # Cache key now includes the search_type, a version tag, and whether
        # page bodies were fetched. Without the type, a news contract and an
        # encyclopedia contract for the same words shared one entry; without the
        # version, a shape change served stale payloads for a full TTL.
        #
        # `fetch_content` is load-bearing and was missing: the planner's
        # grounding search stores content-free results, so with one shared key a
        # later evidence search for the same query hit that entry and reached
        # the summarizer with snippets only and no page bodies — for a whole
        # TTL. A test caught this the day it was written; the flag keeps the two
        # populations in separate entries.
        key = cache_key(
            SEARCH_CACHE_VERSION,
            "search_query",
            (search_type or "general").lower(),
            "bodies" if fetch_content else "snippets_only",
            _normalize_text(" | ".join(queries)),
        )
        cache = None
        try:
            cache = get_cache(settings)
            cached = cache.get(key)
        except Exception as exc:
            logger.warning("[Search] cache read failed: %s", exc, exc_info=exc)
            cached = None

        if cached is not None:
            logger.info("[Search] cache hit for query: %s", question_text[:60])
            return self._decode_cached(cached)

        logger.info(
            "[Search] cache miss; %d query variant(s) for: %s",
            len(queries), question_text[:60],
        )

        async with self.semaphore:
            # Query variants are independent — fan them out concurrently
            # instead of awaiting one at a time (AGENTS.md 4.6). The
            # semaphore still wraps the WHOLE contract, so this shortens
            # latency without changing how many contracts share the search
            # bulkhead. `_providers_for` already absorbs per-provider
            # failures into [], so gather only needs the exception guard for
            # an unexpected hard failure.
            per_query = await asyncio.gather(
                *(self._providers_for(q, search_type) for q in queries),
                return_exceptions=True,
            )
            collected: List[SearchResult] = []
            for batch in per_query:
                if isinstance(batch, BaseException):
                    logger.warning(
                        "[Search] query failed: %s", type(batch).__name__,
                        exc_info=batch,
                    )
                    continue
                collected.extend(batch or [])

        if not collected:
            return []

        from app.agents.evidence_type import classify_evidence_need

        # Publishers the queries steered toward but did not hard-filter on.
        # Recovered from the queries actually issued, so the preference always
        # describes what was asked rather than what was intended. The
        # contract's own `preferred_domains` seeds it: the planner computes that
        # list from the question's jurisdiction and evidence type, and it was
        # write-only until now — stored on every contract, documented in the
        # planner prompt, asserted by tests, and read by nothing. Since the
        # planner's steering stopped being a hard provider filter (see
        # `partition_site_targets`), the ranking preference is where this
        # preference belongs.
        preferred: List[str] = []
        if isinstance(query, dict):
            for term in query.get("preferred_domains") or ():
                text_term = str(term or "").strip().lower().lstrip(".")
                if text_term and text_term not in preferred:
                    preferred.append(text_term)
        for q in queries:
            for term in partition_site_targets(q).soft:
                if term not in preferred:
                    preferred.append(term)

        ranked = _deduplicate_and_rank(
            collected, question_text, max_results, search_type,
            need=classify_evidence_need(question_text),
            preferred=tuple(preferred),
        )
        if fetch_content:
            await self._attach_content(ranked)

        try:
            if cache is not None:
                cache.set(
                    key,
                    [self._to_cache(r) for r in ranked],
                    expire=getattr(settings, "cache_ttl_sec", 3600),
                )
        except Exception as exc:
            logger.warning("[Search] cache write failed, continuing uncached: %s", exc, exc_info=exc)
        return ranked
    def health_snapshot(self) -> Dict[str, Any]:
        """Retrieval-health telemetry for this run (additive; read by the
        benchmark/ledger). Includes the provider success/failure table and the
        currently-cooling domains so a caller can attribute a thin run to
        retrieval access rather than to absent evidence."""
        snapshot = self.health.snapshot()
        snapshot["providers"] = {
            name: dict(bucket) for name, bucket in self.provider_stats.items()
        }
        snapshot["cooling_domains"] = self.domain_registry.cooling_domains()
        snapshot["failed_urls_remembered"] = len(self.failed_fetches)
        snapshot["diversity"] = {
            "per_query": list(self.diversity_stats),
            "aggregate": _aggregate_diversity(self.diversity_stats),
        }
        return snapshot
