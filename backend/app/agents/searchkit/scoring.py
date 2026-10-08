from __future__ import annotations

from typing import Sequence

from app.agents.sources import TOPICALITY_AUTHORITY_FLOOR
from app.agents.sources import classify_source
from app.agents.sources import documentary_authority
from app.agents.sources import extract_domain as _host
from app.agents.sources import freshness_score
from app.agents.sources import topical_engagement
from app.agents.searchkit.types import (
    SearchResult,
)


BLOCKED_DOMAINS = {
    "pinterest.com", "instagram.com", "facebook.com",
    "twitter.com", "x.com", "tiktok.com", "youtube.com",
    "amazon.com", "ebay.com", "quora.com",
}


def _is_blocked(url: str) -> bool:
    """Host-based blocklist check.

    The previous substring test (`"x.com" in url`) blocked any URL whose path or
    host merely contained a blocked string — "matrix.com", "netflix.com/x.com/",
    a query parameter mentioning youtube.com — and was therefore both too
    aggressive and unpredictable.
    """
    host = _host(url)
    if not host:
        return True
    return any(host == b or host.endswith(f".{b}") for b in BLOCKED_DOMAINS)


PREFERRED_HOST_BONUS = 0.16


PREFERRED_FAMILY_BONUS = 0.08


def _preferred_domain_bonus(url: str, targets: Sequence[str]) -> float:
    """Bonus for a host matching one of the query's soft `site:` targets.

    Matches subdomains, so a `site:gov.bd` target is satisfied by `bbs.gov.bd`
    and a `site:worldbank.org` target by `data.worldbank.org`.
    """
    if not targets:
        return 0.0
    domain = _host(url)
    if not domain:
        return 0.0
    best = 0.0
    for target in targets:
        term = (target or "").strip().lower().lstrip(".")
        if not term:
            continue
        if domain == term:
            best = max(best, PREFERRED_HOST_BONUS)
        elif domain.endswith(f".{term}"):
            best = max(best, PREFERRED_FAMILY_BONUS)
    return best


def _score_result(result: SearchResult, query: str, need=None, preferred=()) -> float:
    """Rank a result before any content is fetched.

    Authority alone answers "is this publisher worth listening to", which is not
    the same question as "does this document answer what was asked". A live run
    showed why that distinction is load-bearing: asked for the latest revenue
    guidance from a company's most recent earnings filing, eight encyclopedia
    pages defining "forward guidance" outranked the issuer's own investor
    relations pages. Those publishers are authoritative and the document was
    still the wrong type.

    So scoring now folds in, per result:
      * evidence-type fit  — does this tier match the KIND of document required
      * definition misfit — a glossary page returned to a non-definition question
      * entity engagement  — does it actually mention what the question is about
      * originality         — is it the source, or a page quoting one

    Recency is still scored by decay against the result's own search_type, so a
    2019 news hit sinks while a 2019 paper does not. The flat Wikipedia penalty
    remains, but does not apply when Wikipedia is the right answer.
    """
    profile = classify_source(result.url)
    base = documentary_authority(
        result.url, result.title or "", result.snippet or "", result.content or ""
    )

    snippet = result.snippet or ""
    content = result.content or ""
    haystack = f"{result.title or ''} {snippet} {content}"

    richness = min(0.10, len(snippet) / 1500)
    entity_tokens = need.entity_tokens if need is not None else ()
    engagement = topical_engagement(
        query, haystack, entity_tokens, result.title or "", result.url
    )
    content_bonus = 0.06 if result.is_content_fetched else 0.0
    primary_bonus = 0.10 if profile.is_primary else 0.0
    recency = freshness_score(result.published_at, result.search_type or "default")
    recency_weight = 0.12 if (result.search_type or "").lower() == "news" else 0.06
    # The Wikipedia penalty is a tie-breaker for questions that do not want an
    # encyclopedia. It must not fire when one was asked for, or the penalised
    # source is the correct answer. `want_encyclopedic` is computed before use.
    want_encyclopedic = False
    if need is not None:
        from app.agents.evidence_type import EV_ENCYCLOPEDIC, required_types

        wanted = required_types(need) or frozenset((need.primary,))
        want_encyclopedic = EV_ENCYCLOPEDIC in wanted
    wiki_penalty = 0.15 if "wikipedia.org" in (result.url or "") and not want_encyclopedic else 0.0

    total = (
        # Authority is discounted by how much of the question's subject this
        # document engages. Additive relevance could never do this: authority
        # spans 0.95 while a relevance bonus spanned 0.25, so an authoritative
        # page about a different subject beat the on-topic answer by ~0.37 and
        # nothing dropped it. Multiplying means irrelevance can outrank a tier
        # gap, while on-topic ordering is otherwise unchanged.
        (base * (
            TOPICALITY_AUTHORITY_FLOOR
            + (1.0 - TOPICALITY_AUTHORITY_FLOOR) * engagement
        ))
        + richness
        + (engagement * 0.25)
        + content_bonus
        + primary_bonus
        + (recency * recency_weight)
        - wiki_penalty
        + _preferred_domain_bonus(result.url, preferred)
    )

    if need is not None:
        from app.agents.sources import (
            definition_misfit,
            entity_miss,
            evidence_fit,
            first_party_bonus,
            is_original_source,
        )

        fit, _why = evidence_fit(profile, wanted)
        total += fit
        total += definition_misfit(result.title or "", snippet, need.asks_definition)
        total += entity_miss(query, need.entity_tokens, haystack, result.url)
        total += first_party_bonus(result.url, need.entity_tokens)
        # Prefer the original document over a page quoting it — but only when
        # the document is the KIND asked for. A study is the original source of
        # itself, and that earns it nothing on a "what is X" question.
        if fit > 0 and is_original_source(result.url, result.title or "", snippet, content):
            total += 0.12
    return total
