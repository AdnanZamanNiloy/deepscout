from __future__ import annotations

"""SearchClient provider methods, split into a mixin (refactor).

Moved verbatim from `app/agents/search.py`; `SearchClient` inherits this so
behaviour and the class surface are unchanged."""

import asyncio
import re
from typing import Any, Dict, List

import httpx

from app.agents.reliability import RetryPolicy, call_protected, get_breaker
from app.agents.sources import partition_site_targets
from app.core.logging import get_logger
from app.agents.searchkit.types import SearchResult
from app.agents.searchkit.searxng import _SITE_OPERATOR_RE, _prepare_searxng_query, _searxng_enabled, _searxng_endpoint, _searxng_to_results
from app.agents.searchkit.fetch import _WIKI_USER_AGENT
from app.agents.searchkit.providers import _arxiv_to_results, _crossref_to_results, _wiki_search_to_results

logger = get_logger(__name__)

# Per-query diversity audits retained per run. Generous for an interactive run
# (a long pass issues tens of queries, not thousands) and small enough that the
# snapshot cannot grow into the kind of unbounded per-run state AGENTS.md 4.3
# forbids.
_DIVERSITY_LOG_LIMIT = 200


class ProviderSearchMixin:
        async def _providers_for(self, query: str, search_type: str) -> List[SearchResult]:
            """Fan out one query across the providers that suit its search_type.

            Provider choice is now type-driven instead of one-size-fits-all: an
            academic contract queries arXiv and Crossref (which return the papers
            themselves), a statistical contract stays on general web search where
            agency pages live, and Wikipedia always runs because it is free and
            high-trust for background.
            """
            stype = (search_type or "").strip().lower()
            tasks: List[Any] = []

            if _searxng_enabled(self.settings):
                tasks.append(self._searxng_search(query, search_type=stype))

            if stype == "academic":
                tasks.append(self._arxiv(query))
                tasks.append(self._crossref(query))
            elif stype in ("encyclopedia", "", "general"):
                tasks.append(self._wiki(query))
            elif stype == "statistical":
                tasks.append(self._wiki(query))

            batches = await asyncio.gather(*tasks, return_exceptions=True)
            collected: List[SearchResult] = []
            for batch in batches:
                if isinstance(batch, BaseException):
                    logger.warning("[Search] provider error: %s", type(batch).__name__)
                    continue
                collected.extend(batch or [])
            return collected

        async def _searxng_search(self, query: str, search_type: str = "") -> List[SearchResult]:
            """Query the self-hosted SearXNG instance. Primary web search.

            Same reliability contract as the provider it replaces: bounded retries
            with jitter, a circuit breaker, and a deterministic empty result on
            failure so one dead backend degrades a run instead of ending it. Zero
            usable hits counts as a FAILURE, not an answer -- an aggregate that
            returns nothing usually means it is misconfigured or its engines are
            all failing, and that must show up in health rather than be mistaken
            for "no such document exists".
            """
            base = _searxng_endpoint(self.settings)
            if not base:
                logger.warning("[Search] searxng_url is not configured; skipping web search")
                return []

            clean_query, params = _prepare_searxng_query(query, search_type, self.settings)
            if not clean_query:
                return []
            url = f"{base}/search"

            # `-site:` has no SearXNG request parameter, so exclusions are applied
            # to the mapped results instead of to the upstream query.
            excluded = partition_site_targets(query).excluded

            timeout = float(getattr(self.settings, "searxng_timeout_sec", 30.0) or 30.0)

            async def _call() -> List[SearchResult]:
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=True,
                    headers={"Accept": "application/json"},
                ) as client:
                    r = await client.get(url, params=params)
                    r.raise_for_status()
                    payload = r.json()
                if not isinstance(payload, dict):
                    raise ValueError("SearXNG returned a non-object payload")
                mapped = _searxng_to_results(payload, clean_query, excluded)
                cap = int(getattr(self.settings, "searxng_max_results", 30) or 30)
                if cap > 0:
                    mapped = mapped[:cap]
                if not mapped:
                    raise ValueError("SearXNG returned no usable results")
                unresponsive = payload.get("unresponsive_engines") or []
                if unresponsive:
                    logger.info(
                        "[Search] searxng: %d unresponsive upstream engine(s)", len(unresponsive)
                    )
                # Audit the aggregate rather than trusting its own silence: one
                # engine answering everything reads as plenty of results and is
                # actually a single source. Recorded per query so a run's trace
                # can show whether its retrieval was genuinely diversified.
                self._record_diversity(mapped, unresponsive)
                self._count("searxng", "ok")
                return mapped

            async def _fallback() -> List[SearchResult]:
                self._count("searxng", "fail")
                return []

            return await call_protected(
                _call,
                name="searxng",
                policy=self._retry,
                breaker=get_breaker(
                    "searxng",
                    failure_threshold=3,
                    cooldown=float(
                        getattr(self.settings, "search_searxng_cooldown_sec", 60.0) or 60.0
                    ),
                ),
                fallback=_fallback,
            )

        async def _wiki(self, query) -> List[SearchResult]:
            async def _call() -> List[SearchResult]:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": _WIKI_USER_AGENT},
                ) as client:
                    r = await client.get(
                        "https://en.wikipedia.org/w/api.php",
                        params={
                            "action": "query",
                            "list": "search",
                            "srsearch": str(query),
                            "srlimit": 5,
                            "format": "json",
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                self._count("wikipedia", "ok")
                return _wiki_search_to_results(data, str(query))

            async def _empty() -> List[SearchResult]:
                self._count("wikipedia", "fail")
                return []

            return await call_protected(
                _call,
                name="wikipedia",
                policy=RetryPolicy(attempts=2, base_delay=0.5, max_delay=3.0, timeout=self._timeout),
                breaker=get_breaker("wikipedia", failure_threshold=4, cooldown=45.0),
                fallback=_empty,
            )

        async def _arxiv(self, query) -> List[SearchResult]:
            """arXiv Atom API — preprints, free, no key.

            Worth a provider slot because an academic contract that lands on a blog
            summarizing a paper is strictly worse evidence than the paper, and the
            general web search reliably prefers the blog.
            """
            text = re.sub(r"\s+", " ", _SITE_OPERATOR_RE.sub("", str(query))).strip()
            if not text:
                return []

            async def _call() -> List[SearchResult]:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": _WIKI_USER_AGENT},
                ) as client:
                    r = await client.get(
                        "https://export.arxiv.org/api/query",
                        params={
                            "search_query": f"all:{text[:200]}",
                            "start": 0,
                            "max_results": 6,
                            "sortBy": "relevance",
                        },
                    )
                    r.raise_for_status()
                    payload = r.text
                self._count("arxiv", "ok")
                return _arxiv_to_results(payload, text)

            async def _empty() -> List[SearchResult]:
                self._count("arxiv", "fail")
                return []

            return await call_protected(
                _call,
                name="arxiv",
                policy=RetryPolicy(attempts=2, base_delay=1.0, max_delay=4.0, timeout=self._timeout),
                breaker=get_breaker("arxiv", failure_threshold=3, cooldown=90.0),
                fallback=_empty,
            )

        async def _crossref(self, query) -> List[SearchResult]:
            """Crossref works API — DOI metadata and abstracts, free, no key."""
            text = re.sub(r"\s+", " ", _SITE_OPERATOR_RE.sub("", str(query))).strip()
            if not text:
                return []

            async def _call() -> List[SearchResult]:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": _WIKI_USER_AGENT},
                ) as client:
                    r = await client.get(
                        "https://api.crossref.org/works",
                        params={
                            "query.bibliographic": text[:300],
                            "rows": 5,
                            "select": "DOI,URL,title,abstract,container-title,issued",
                            "sort": "relevance",
                        },
                    )
                    r.raise_for_status()
                    payload = r.json()
                self._count("crossref", "ok")
                return _crossref_to_results(payload, text)

            async def _empty() -> List[SearchResult]:
                self._count("crossref", "fail")
                return []

            return await call_protected(
                _call,
                name="crossref",
                policy=RetryPolicy(attempts=2, base_delay=1.0, max_delay=4.0, timeout=self._timeout),
                breaker=get_breaker("crossref", failure_threshold=3, cooldown=90.0),
                fallback=_empty,
            )

        def _record_diversity(
            self, results: List[SearchResult], unresponsive: Any = None
        ) -> None:
            """Store one per-query retrieval-diversity audit.

            Deliberately NOT merged with the provider ok/fail table: that table
            answers "did the call work", this answers "did many independent
            things answer". A query served entirely by one index succeeds
            (`ok`) and is still single-sourced, and only the second view shows
            that.
            """
            from app.agents.searchkit.diversity import diversity_report

            try:
                report = diversity_report(results, unresponsive)
            except Exception as exc:  # telemetry must never break retrieval
                logger.warning(
                    "[Search] diversity audit failed", exc_info=exc
                )
                return
            # Keep the log small and focused on a regression: the top engines
            # and domains, not every counter, or the line becomes unreadable.
            logger.info(
                "[Search] diversity engines=%s domains=%s concentrated=%s cause=%s "
                "upstream_ok=%s/%s dedup_overwritten=%s",
                report["engines"]["unique_engines"],
                report["publishers"]["unique_source_domains"],
                report["engines"]["concentrated"],
                report["concentration_cause"],
                report["searxng"]["responsive_count"],
                report["searxng"]["responsive_count"] + report["searxng"]["unresponsive_count"],
                report["dedup"]["overwritten_across_engines"],
            )
            existing = getattr(self, "diversity_stats", None)
            reports: List[Dict[str, Any]] = existing if isinstance(existing, list) else []
            if reports is not existing:
                self.diversity_stats = reports
            reports.append({
                "results": report["results"],
                "counts_by_engine": report["engines"]["counts_by_engine"],
                "unique_engines": report["engines"]["unique_engines"],
                "top_engine": report["engines"]["top_engine"],
                "top_engine_share": report["engines"]["top_engine_share"],
                "unique_source_domains": report["publishers"]["unique_source_domains"],
                "unique_publishers": report["publishers"]["unique_publishers"],
                "concentrated": report["engines"]["concentrated"],
                "concentration_cause": report["concentration_cause"],
                "unresponsive_engines": report["unresponsive_engines"],
                "searxng": report["searxng"],
                "dedup": report["dedup"],
            })
            if len(reports) > _DIVERSITY_LOG_LIMIT:
                del reports[: len(reports) - _DIVERSITY_LOG_LIMIT]

        def _count(self, provider: str, outcome: str) -> None:
            bucket = self.provider_stats.setdefault(provider, {"ok": 0, "fail": 0, "results": 0})
            if outcome in bucket:
                bucket[outcome] += 1
            self.health.record_provider_call(succeeded=(outcome == "ok"))
