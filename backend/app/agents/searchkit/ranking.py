from __future__ import annotations

from typing import Dict, List

from app.agents.sources import canonical_url
from app.agents.sources import is_primary_source
from app.agents.sources import is_topically_irrelevant
from app.agents.sources import topical_engagement
from app.agents.sources import topicality_floor_applies
from app.core.logging import get_logger

from app.agents.searchkit.scoring import (
    _is_blocked,
    _score_result,
)
from app.agents.searchkit.text import (
    _domain,
    _is_semantic_duplicate,
)
from app.agents.searchkit.types import (
    SearchResult,
)

logger = get_logger(__name__)


ORIGIN_CAP = 2


def _apply_topical_floor(ranked, query, need) -> list:
    """Discard results that do not engage the question's subject at all.

    Ranking cannot do this job on its own. Even with authority discounted by
    topicality, a provider that returns eight off-topic pages still puts one in
    front of the fetch budget, and the summarizer then spends its context
    reading it. The recorded failure mode is concrete: a Bangladesh query
    surfaced a Malawi electrification paragraph, and because extractive fallback
    claims self-verify, the confidence engine scored the result "High".

    So relevance is enforced as a floor, not a preference. Results that engage
    nothing the question is about are dropped before ranking output.

    Two guards keep this from becoming a recall bug:
      * a query with no substantive subject is never filtered — there is
        nothing to be irrelevant to;
      * if EVERY result is below the floor, the single most-engaging one is
        kept. Returning nothing from a non-empty provider response is a
        retrieval decision, not a quality one, and an honest weak result beats
        a silently empty evidence base. It is logged, because "we only found
        something off-topic" is exactly what a report should not hide.
    """
    if not ranked:
        return ranked
    entity_tokens = need.entity_tokens if need is not None else ()
    if not topicality_floor_applies(query, entity_tokens):
        return ranked

    kept, below = [], []
    for r in ranked:
        text = f"{r.title or ''} {r.snippet or ''} {r.content or ''}"
        if is_topically_irrelevant(query, text, entity_tokens, r.title or "", r.url):
            below.append(r)
        else:
            kept.append(r)
    if below and not kept:
        best = below[-1]  # `ranked` is score-descending; the floor only reorders
        for r in below:
            if topical_engagement(
                query,
                f"{r.title or ''} {r.snippet or ''} {r.content or ''}",
                entity_tokens,
                r.title or "",
                r.url,
            ) > topical_engagement(
                query,
                f"{best.title or ''} {best.snippet or ''} {best.content or ''}",
                entity_tokens,
                best.title or "",
                best.url,
            ):
                best = r
        logger.info(
            "[Search] no result engaged the query's subject; keeping the closest of %d",
            len(below),
        )
        return [best]
    if below:
        logger.info(
            "[Search] dropped %d off-topic result(s) below the engagement floor",
            len(below),
        )
    return kept


def _deduplicate_and_rank(results, query, max_results=10, search_type: str = "", need=None,
                          preferred=()):
    """Canonical-URL dedup, scoring, near-duplicate removal, domain diversity.

    Also caps how many results may share one UNDERLYING source. Five outlets
    republishing the same study are one piece of evidence repeated, not five
    corroborating ones; without the cap they fill the fetch budget and crowd
    out the primary document they are all quoting.

    `preferred` is the set of publishers the queries STEERED toward without
    hard-filtering on them; matching hosts get a ranking bonus so the steering
    still buys something after the provider stopped filtering.
    """
    seen: set = set()
    filtered: List[SearchResult] = []

    for r in results:
        if not r.url:
            continue
        key = canonical_url(r.url)
        if not key or key in seen:
            continue
        if _is_blocked(r.url):
            continue
        seen.add(key)
        if search_type and (not r.search_type or r.search_type == "general"):
            r.search_type = search_type
        r.is_primary = is_primary_source(r.url)
        filtered.append(r)

    for r in filtered:
        r.reliability_score = _score_result(r, query, need, preferred)

    ranked = sorted(filtered, key=lambda r: r.reliability_score, reverse=True)
    ranked = _apply_topical_floor(ranked, query, need)

    # Near-duplicate snippets. Restricted to same-domain pairs plus very high
    # overlap across domains: two independent publishers describing the same
    # fact in similar words is CORROBORATION, and dropping the second copy is
    # how the pipeline used to destroy its own cross-source agreement signal
    # before it was ever measured.
    diverse: List[SearchResult] = []
    for r in ranked:
        duplicate = False
        for kept in diverse:
            same_host = _domain(r.url) == _domain(kept.url)
            threshold = 0.6 if same_host else 0.85
            if _is_semantic_duplicate(r.snippet, kept.snippet, threshold):
                duplicate = True
                break
        if not duplicate:
            diverse.append(r)

    selected: List[SearchResult] = []
    domain_count: Dict[str, int] = {}
    origin_count: Dict[str, int] = {}
    for r in diverse:
        d = _domain(r.url)
        cap = 1 if "wikipedia.org" in d else 2
        if domain_count.get(d, 0) >= cap:
            continue
        # Independence cap: at most ORIGIN_CAP pages may rest on one original.
        # The original itself is exempt so it is never the page dropped.
        from app.agents.sources import is_original_source, underlying_source_key

        origin = underlying_source_key(r.url, r.title or "", r.snippet or "", r.content or "")
        is_original = is_original_source(r.url, r.title or "", r.snippet or "", r.content or "")
        if not is_original and origin_count.get(origin, 0) >= ORIGIN_CAP:
            continue
        selected.append(r)
        domain_count[d] = domain_count.get(d, 0) + 1
        # The original does not consume the republication budget: it is the
        # source every other page is quoting, so counting it would halve the
        # allowance for the very repetition the cap exists to limit.
        if not is_original:
            origin_count[origin] = origin_count.get(origin, 0) + 1
        if len(selected) >= max_results:
            break

    return selected
