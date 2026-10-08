from __future__ import annotations

"""SearchClient content-fetch methods, split into a mixin (refactor).

Moved verbatim from `app/agents/search.py`; `SearchClient` inherits this so
behaviour and the class surface are unchanged."""

import asyncio
from typing import List

import httpx

from app.agents.reliability import gather_bounded
from app.agents.retrieval_health import failure_cools_host
from app.agents.sources import build_substitution_query, canonical_url, classify_source, is_primary_source
from app.core.logging import get_logger
from app.agents.searchkit.types import SearchResult
from app.agents.searchkit.fetch import _BROWSER_USER_AGENT, _domain_of
from app.agents.searchkit.scoring import _score_result

logger = get_logger(__name__)


def _fetch_content_outcome(*args, **kwargs):
    """Resolve the fetch primitive from the facade at call time.

    `_fetch_content_outcome` stays in `app.agents.search` because tests patch
    `search._fetch_content` there (a documented monkeypatch seam). A module-level
    import would create a cycle, so it is resolved lazily.
    """
    from app.agents import search as _search

    return _search._fetch_content_outcome(*args, **kwargs)

class ContentMixin:
        async def _attach_content(self, ranked: List[SearchResult]) -> None:
            """Download the top N pages concurrently under a fetch bulkhead.

            Access-hardening rules, all additive to the existing fetch bulkhead:
              * a domain that is cooling down (403/429/timeout history this run)
                is SKIPPED, not retried — later passes stop re-paying for a wall;
              * a canonical URL that already failed this run is never re-fetched;
              * TRANSIENT failures get bounded retries with jitter/Retry-After;
                403 fails fast and cools the host;
              * every outcome is counted in `self.health`, so a run that found
                nothing because publishers blocked it is distinguishable from a
                run where the evidence genuinely was not there.
            """
            fetch_n = max(1, int(getattr(self.settings, "search_fetch_top_n", 3) or 3))
            targets = ranked[:fetch_n]
            if not targets:
                return

            fetch_retries = max(1, int(getattr(self.settings, "search_fetch_retry_attempts", 2) or 2))
            # URL -> was the host already cooling when we skipped? The fallback
            # query below only fires when an AUTHORITATIVE host was unavailable,
            # so we remember which domains caused a skip.
            unavailable_domains: List[str] = []

            async with httpx.AsyncClient(
                timeout=12,
                follow_redirects=True,
                headers={"User-Agent": _BROWSER_USER_AGENT},
                limits=httpx.Limits(max_connections=self._fetch_limit),
            ) as client:

                async def _attach(r: SearchResult) -> None:
                    if r.content:
                        # Providers that already returned text (SearXNG, arXiv,
                        # Crossref) must never be re-fetched.
                        r.content_length = len(r.content)
                        r.is_content_fetched = True
                        # Count the ATTEMPT too. This branch used to record only a
                        # success, so `successful_fetch_rate` divided by zero
                        # attempts and reported 0.0 for a run where every result
                        # arrived fully readable. That was survivable when the
                        # primary provider rarely shipped inline text; SearXNG
                        # ships it for most results, so the metric would have read
                        # "0% of pages were readable" on a perfectly healthy run
                        # and bench/eval_retrieval gates on exactly that number.
                        self.health.record_attempt()
                        self.health.record_success(
                            domain=_domain_of(r.url),
                            primary=r.is_primary or is_primary_source(r.url),
                            url=canonical_url(r.url),
                            authoritative=classify_source(r.url).is_primary,
                        )
                        return

                    domain = _domain_of(r.url)
                    key = canonical_url(r.url)

                    # Already-known-dead document: do not spend a request again.
                    if self.failed_fetches.seen(key):
                        self.health.record_skip("duplicate_failure")
                        logger.debug("[Search] skipping known-failed URL: %s", r.url[:80])
                        return

                    # Host on cooldown: skip without a request.
                    if domain and self.domain_registry.is_cooling(domain):
                        self.health.record_skip("cooldown")
                        unavailable_domains.append(domain)
                        logger.debug(
                            "[Search] skipping cooled-down domain %s (%.0fs left)",
                            domain, self.domain_registry.remaining(domain),
                        )
                        return

                    self.health.record_attempt()

                    def _note_retry(attempt: int, reason: str) -> None:
                        # Each transient retry is itself a failed attempt against
                        # the host, so it counts toward the cooldown streak: an
                        # exhausted retry budget IS "repeated failure".
                        self.health.record_retry(succeeded=False)
                        if domain and failure_cools_host(reason):
                            before = self.domain_registry.is_cooling(domain)
                            self.domain_registry.record_failure(domain, reason)
                            if not before and self.domain_registry.is_cooling(domain):
                                self.health.record_cooldown_opened()
                                unavailable_domains.append(domain)

                    outcome = await _fetch_content_outcome(
                        client, r.url, max_attempts=fetch_retries,
                        timeout=self._timeout, on_retry=_note_retry,
                    )
                    if outcome.ok:
                        if outcome.attempts > 1:
                            # A retry that did not need the observer's fail note.
                            self.health.retries_succeeded += 1
                        r.content = outcome.text
                        r.content_length = len(outcome.text)
                        r.is_content_fetched = True
                        if not r.published_at:
                            r.published_at = outcome.last_modified
                        self.health.record_success(
                            domain=domain,
                            primary=r.is_primary or is_primary_source(r.url),
                            url=key,
                            authoritative=classify_source(r.url).is_primary,
                        )
                        if domain:
                            self.domain_registry.record_success(domain)
                        return

                    self.health.record_failure(outcome.reason)
                    if failure_cools_host(outcome.reason):
                        if domain and not self.domain_registry.is_cooling(domain):
                            # The retry callback above already counted each
                            # transient attempt; only the FIRST failure of this
                            # fetch reaches here uncooled (403 has no retries).
                            self.domain_registry.record_failure(domain, outcome.reason)
                            self.health.record_cooldown_opened()
                        if outcome.reason == "forbidden" and domain not in unavailable_domains:
                            unavailable_domains.append(domain)
                    self.failed_fetches.mark(key, outcome.reason)
                    logger.info(
                        "[Search] fetch failed for %s (%s, %d attempt(s))",
                        r.url[:80], outcome.reason, outcome.attempts,
                    )

                await gather_bounded(
                    [(lambda r=r: _attach(r)) for r in targets], self._fetch_limit
                )

                if unavailable_domains:
                    await self._primary_fallback(
                        ranked, unavailable_domains, client=client,
                        max_attempts=fetch_retries,
                    )

        async def _primary_fallback(
            self,
            ranked: List[SearchResult],
            unavailable_domains: List[str],
            *,
            client: httpx.AsyncClient | None = None,
            max_attempts: int = 2,
        ) -> None:
            """When an authoritative host is unavailable, acquire EQUIVALENT
            evidence from a DIFFERENT authoritative/independent publisher.

            This reuses the existing primary-source machinery
            (`build_substitution_query`): it builds a `site:`-scoped query aimed at an
            authoritative publisher in the SAME jurisdiction as the one that failed,
            issues it through the SAME search providers, and appends any new results
            to `ranked` so the caller's content-attach and downstream ranking see
            them. It is a targeted substitution, not a new search system and not a
            retry of the blocked host.

            Bounded: at most `search_primary_fallback_max` queries per contract,
            only when the setting is enabled, and only for results whose host is
            actually unavailable.
            """
            if not bool(getattr(self.settings, "search_primary_fallback_enabled", True)):
                return
            blocked = {d for d in unavailable_domains if d}
            if not blocked:
                return
            max_fallbacks = max(0, int(getattr(self.settings, "search_primary_fallback_max", 2) or 0))
            if max_fallbacks <= 0:
                return

            # Phase 1 — plan (sync): select up to max_fallbacks blocked results
            # and build their substitution queries. No I/O, same selection rules
            # as the old interleaved loop (the ranked slice was always taken up
            # front, so appending during processing never fed back into it).
            planned: List[tuple] = []
            acquired_urls = {canonical_url(r.url) for r in ranked}
            for result in ranked[: max(1, int(getattr(self.settings, "search_fetch_top_n", 3) or 3))]:
                if len(planned) >= max_fallbacks:
                    break
                host = _domain_of(result.url)
                if host not in blocked:
                    continue
                question = result.sub_question or result.matched_query or result.title
                fallback_query = build_substitution_query(
                    question, result.search_type or "general", host, max_sites=2
                )
                fallback_query = (fallback_query or "").strip()
                if not fallback_query:
                    continue
                planned.append((result, fallback_query))
            if not planned:
                return

            # Phase 2 — fetch concurrently: each substitution query is an
            # independent provider round; running them serially doubled the
            # wall time of an already-degraded retrieval path (live baseline).
            batches_list = await asyncio.gather(
                *(
                    self._providers_for(fallback_query, result.search_type or "general")
                    for result, fallback_query in planned
                ),
                return_exceptions=True,
            )

            # Phase 3 — merge in plan order (deterministic dedup/scoring order).
            for (result, fallback_query), batches in zip(planned, batches_list):
                if isinstance(batches, BaseException):
                    logger.warning(
                        "[Search] primary fallback query failed (%s): %s",
                        type(batches).__name__, batches,
                    )
                    self.health.record_fallback_query(0)
                    continue
                new_hits = 0
                for candidate in batches or []:
                    candidate_domain = _domain_of(candidate.url)
                    key = canonical_url(candidate.url)
                    if not candidate.url or key in acquired_urls:
                        continue
                    # Never substitute with another host that is also unavailable.
                    if candidate_domain and (
                        candidate_domain in blocked
                        or self.domain_registry.is_cooling(candidate_domain)
                    ):
                        continue
                    acquired_urls.add(key)
                    candidate.is_primary = is_primary_source(candidate.url)
                    candidate.reliability_score = _score_result(
                        candidate, result.sub_question or result.matched_query
                    )
                    ranked.append(candidate)
                    new_hits += 1
                    if new_hits >= max_fallbacks:
                        break
                self.health.record_fallback_query(new_hits)
                logger.info(
                    "[Search] primary fallback for %s: query=%s new_hits=%d",
                    ",".join(sorted(blocked))[:80], fallback_query[:80], new_hits,
                )
            # Re-rank so substituted primary hits compete on the existing score,
            # not on insertion position. Pure function of the same scorer.
            ranked.sort(key=lambda r: r.reliability_score, reverse=True)
            await self._attach_fallback_content(
                ranked, acquired_urls, client=client, max_attempts=max_attempts
            )

        async def _attach_fallback_content(
            self,
            ranked: List[SearchResult],
            acquired_urls: set,
            *,
            client: httpx.AsyncClient | None = None,
            max_attempts: int = 2,
        ) -> None:
            """Fetch content for the substituted primary hits so a fallback
            acquisition is equivalent evidence, not a bare snippet. Bounded to the
            substituted results only; failures are counted like any other fetch."""
            if client is None:
                return
            targets = [r for r in ranked if canonical_url(r.url) in acquired_urls and not r.content]
            if not targets:
                return

            async def _attach(r: SearchResult) -> None:
                domain = _domain_of(r.url)
                key = canonical_url(r.url)
                if self.failed_fetches.seen(key) or (
                    domain and self.domain_registry.is_cooling(domain)
                ):
                    self.health.record_skip(
                        "cooldown" if domain and self.domain_registry.is_cooling(domain)
                        else "duplicate_failure"
                    )
                    return
                self.health.record_attempt()
                outcome = await _fetch_content_outcome(
                    client, r.url, max_attempts=max_attempts, timeout=self._timeout,
                )
                if outcome.ok:
                    r.content = outcome.text
                    r.content_length = len(outcome.text)
                    r.is_content_fetched = True
                    if not r.published_at:
                        r.published_at = outcome.last_modified
                    self.health.record_success(
                        domain=domain, primary=r.is_primary,
                        url=key, authoritative=classify_source(r.url).is_primary,
                    )
                    if domain:
                        self.domain_registry.record_success(domain)
                    return
                self.health.record_failure(outcome.reason)
                if failure_cools_host(outcome.reason) and domain:
                    self.domain_registry.record_failure(domain, outcome.reason)
                self.failed_fetches.mark(key, outcome.reason)

            await gather_bounded(
                [(lambda r=r: _attach(r)) for r in targets], self._fetch_limit
            )
