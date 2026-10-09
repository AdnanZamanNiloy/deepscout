from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from app.agents.sources import extract_domain as _host
from app.agents.sources import partition_site_targets
from app.agents.searchkit.identity import source_identity
from app.core.config import Settings
from app.agents.searchkit.types import (
    SearchResult,
)


_SITE_OPERATOR_RE = re.compile(r"site:(\S+)", re.IGNORECASE)


_SEARXNG_MAX_QUERY_CHARS = 400


def _searxng_enabled(settings: Settings) -> bool:
    """Is the SearXNG backend switched on and pointed somewhere?"""
    if not bool(getattr(settings, "searxng_enabled", True)):
        return False
    return bool(_searxng_endpoint(settings))


def _searxng_endpoint(settings: Settings) -> str:
    """Base URL of the SearXNG instance, without a trailing slash."""
    base = str(getattr(settings, "searxng_url", "") or "").strip()
    return base.rstrip("/")


def _searxng_search_type_params(search_type: str, settings: Settings) -> Dict[str, Any]:
    """Per-search-type category/language/time overrides for the aggregate.

    A news contract wants the `news` category and a recent window; an academic
    one wants `science`. Overriding per type is what keeps one instance usable
    for every contract shape, which a single global category list could not do.
    """
    stype = (search_type or "").strip().lower()
    configured = str(getattr(settings, "searxng_categories", "") or "").strip()
    categories = configured or "general,science"
    params: Dict[str, Any] = {"categories": categories}

    if stype == "news":
        # Keep `news` alongside the configured set rather than replacing it:
        # a current-events question still benefits from the open-web engines.
        if "news" not in categories:
            params["categories"] = f"{categories},news"
        params["time_range"] = "month"
    elif stype == "academic":
        # Scholarly indexes first; `news` and images only add noise here.
        params["categories"] = "science"
        # Papers do not decay in a week; do not filter them by recency.
        params.pop("time_range", None)
    elif stype == "statistical":
        # Official statistics live on the open web; recency is handled by our
        # own freshness scoring, not by discarding older documents.
        params["categories"] = "general,science"

    language = str(getattr(settings, "searxng_language", "") or "").strip()
    if language:
        params["language"] = language
    return params


def _prepare_searxng_query(
    query: Any, search_type: str = "", settings: Optional[Settings] = None
) -> tuple[str, Dict[str, Any]]:
    """Normalize a query and build the SearXNG request parameters.

    HARD `site:` targets are kept in the query text because SearXNG forwards
    them to its engines natively; SOFT targets are stripped, because a guessed
    publisher must never narrow the candidate set (that was the original bug
    this partition exists to prevent). Excluded domains have no SearXNG
    equivalent, so the caller applies them post-hoc -- see
    `_searxng_to_results`.
    """
    text = query if isinstance(query, str) else str(query or "")
    split = partition_site_targets(text)
    hard = list(split.hard)

    # SearXNG understands `site:` inside the query, so a hard target is simply
    # appended. Deduplicated and order-stable.
    kept_terms = [t for t in hard if t]
    stripped = _SITE_OPERATOR_RE.sub("", text)
    stripped = stripped.replace("(", " ").replace(")", " ")
    stripped = re.sub(r"\s+\bOR\b\s*$", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"(?:^|\s)-\s*$", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if kept_terms:
        stripped = f"{stripped} " + " OR ".join(f"site:{t}" for t in kept_terms)
        stripped = re.sub(r"\s+", " ", stripped).strip()

    params: Dict[str, Any] = {}
    if settings is not None:
        params = _searxng_search_type_params(search_type, settings)
    params.update({
        "q": stripped[:_SEARXNG_MAX_QUERY_CHARS],
        "format": "json",
    })
    params["pageno"] = 1
    safe = int(getattr(settings, "searxng_safesearch", 0) or 0) if settings else 0
    params["safesearch"] = max(0, min(2, safe))
    # NB: there is deliberately no result-count parameter here. SearXNG has no
    # per-request result limit -- per-engine counts live in the instance's
    # settings.yml -- so an invented parameter would just be ignored upstream.
    # `searxng_max_results` is applied client-side in `_searxng_search`.
    return stripped[:_SEARXNG_MAX_QUERY_CHARS], params


def _searxng_published(row: Dict[str, Any]) -> str:
    """Best available publish date from a SearXNG result.

    `MainResult.publishedDate` is a real datetime (serialized ISO by
    `webutils.JSONEncoder`); paper results carry `date_of_publication`; and
    `pubdate` is the older string form some engines still populate.
    """
    for key in ("publishedDate", "date_of_publication", "pubdate"):
        value = row.get(key)
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _searxng_to_results(
    payload: Any, query: str, exclude_domains: Sequence[str] = ()
) -> List["SearchResult"]:
    """Map a SearXNG /search?format=json response to SearchResults.

    Pure -- the network call stays in `_searxng_search` so this is testable
    offline. Fields follow `searx.result_types.MainResult`: `title`, `content`
    (the snippet), `url`, `publishedDate`.

    Two things the metasearch makes necessary that a single-provider API did
    not:
      * `engine` is recorded on the provider string, so the trace can show which
        upstream index actually produced a hit (DuckDuckGo, Mojeek, arXiv...).
      * `-site:` exclusions are applied here, because SearXNG has no equivalent
        request parameter for them.

    IDENTITY IS NOT PROVENANCE. `provider` keeps its historical
    "searxng:<engine>" value purely for backward compatibility, but the fields
    that mean anything are separate: `source_domain` is the page's own hostname
    and is what a reader must see, while `retrieval_engine` records which index
    answered. SearXNG's `engines` array is preferred over the singular `engine`
    because a metasearch routinely returns one page that several engines found —
    reading only `engine` silently credited one index and hid the overlap.
    """
    results: List[SearchResult] = []
    items = payload.get("results", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return results
    blocked = {d.strip().lower().lstrip(".") for d in (exclude_domains or ()) if d}
    for row in items:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        engine = str(row.get("engine") or "").strip()
        if engine.startswith("plugin:"):
            engine = engine.split(":", 1)[1]
        provider = f"searxng:{engine}" if engine else "searxng"
        if blocked and any(
            _host(url) == d or _host(url).endswith(f".{d}") for d in blocked
        ):
            continue
        content = str(row.get("content") or "").strip()
        # A paper result's abstract lives in `content` too, but the journal /
        # DOI metadata is worth carrying: `detect_primary_refs` reads a DOI out
        # of the snippet to recognise the ORIGINAL of a study.
        extras = []
        for key in ("journal", "doi", "publisher"):
            value = row.get(key)
            if isinstance(value, (list, tuple)):
                value = " ".join(str(v) for v in value if v)
            value = str(value or "").strip()
            if value:
                extras.append(value)
        snippet = content or str(row.get("title") or "")
        if extras:
            snippet = f"{snippet} [{'; '.join(extras)}]" if snippet else "; ".join(extras)

        # Every engine that returned this hit. Falls back to the singular
        # `engine`, and then to "" — never to the aggregator's own name, which
        # would make "searxng" look like a publisher.
        engines = [
            e.split(":", 1)[1] if str(e).startswith("plugin:") else str(e)
            for e in (row.get("engines") or [])
        ]
        engines = [e.strip() for e in engines if str(e).strip()]
        if not engines and engine:
            engines = [engine]
        source_domain, publisher_name = source_identity(url, row.get("publisher"))

        results.append(SearchResult(
            title=re.sub(r"<[^>]+>", "", str(row.get("title") or "")),
            url=url,
            snippet=snippet[:1500],
            content=content[:12000],
            provider=provider,
            published_at=_searxng_published(row),
            matched_query=query,
            source_domain=source_domain,
            publisher=publisher_name,
            retrieval_provider="searxng",
            retrieval_engine=engine,
            retrieval_engines=engines,
        ))
    return results
