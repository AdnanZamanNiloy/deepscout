from __future__ import annotations

import html
import re
from typing import Any, List
from urllib.parse import quote
from xml.etree import ElementTree

from app.core.logging import get_logger

from app.agents.searchkit.identity import source_identity
from app.agents.searchkit.types import (
    SearchResult,
)

logger = get_logger(__name__)


def _arxiv_to_results(xml_text: str, query: str) -> List[SearchResult]:
    """Parse an arXiv Atom feed. Pure, so it is testable without network."""
    out: List[SearchResult] = []
    try:
        root = ElementTree.fromstring(xml_text or "")
    except ElementTree.ParseError as exc:
        logger.warning("[Search] arXiv XML parse failed: %s", exc)
        return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        published = (entry.findtext("a:published", default="", namespaces=ns) or "").strip()
        link = ""
        for candidate in entry.findall("a:link", ns):
            if candidate.get("rel") in (None, "alternate"):
                link = candidate.get("href", "") or ""
                break
        if not link:
            link = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        if not link or not title:
            continue
        clean_summary = re.sub(r"\s+", " ", summary)
        # arXiv is both the retrieval provider and the publisher here: the PDF
        # is hosted by arxiv.org even when the preprint was later published
        # elsewhere, so the journal name is NOT invented from the entry.
        domain, _ = source_identity(link)
        out.append(SearchResult(
            title=re.sub(r"\s+", " ", title),
            url=link,
            snippet=clean_summary[:1200],
            content=clean_summary[:6000],
            provider="arxiv",
            search_type="academic",
            published_at=published,
            matched_query=query,
            source_domain=domain,
            publisher="arXiv",
            retrieval_provider="arxiv",
            retrieval_engine="arxiv",
            retrieval_engines=["arxiv"],
        ))
    return out


def _crossref_to_results(payload: Any, query: str) -> List[SearchResult]:
    """Map a Crossref /works response to SearchResults.

    Crossref indexes the DOI record itself: title, venue, date and abstract come
    from the publisher, not from a page that mentions the paper. That is the
    definition of a primary bibliographic source.
    """
    out: List[SearchResult] = []
    items = ((payload or {}).get("message") or {}).get("items") or []
    for row in items:
        if not isinstance(row, dict):
            continue
        doi = str(row.get("DOI", "") or "")
        url = str(row.get("URL", "") or (f"https://doi.org/{doi}" if doi else ""))
        titles = row.get("title") or []
        title = str(titles[0]) if titles else ""
        if not url or not title:
            continue
        abstract = re.sub(r"<[^>]+>", " ", str(row.get("abstract", "") or ""))
        abstract = re.sub(r"\s+", " ", html.unescape(abstract)).strip()
        container = row.get("container-title") or []
        venue = str(container[0]) if container else ""
        parts = ((row.get("issued") or {}).get("date-parts") or [[]])[0]
        published = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts[:3])) if parts else ""
        summary = abstract or f"{title}. {venue}".strip()
        # Crossref indexes the DOI record, so `publisher` is a real registered
        # value and the best publisher name available for a paper. The URL is
        # almost always doi.org, so the domain alone would say nothing useful.
        domain, publisher_name = source_identity(url, row.get("publisher"))
        out.append(SearchResult(
            title=re.sub(r"\s+", " ", title),
            url=url,
            snippet=summary[:1200],
            content=abstract[:6000],
            provider="crossref",
            search_type="academic",
            published_at=published,
            matched_query=query,
            source_domain=domain,
            publisher=publisher_name,
            retrieval_provider="crossref",
            retrieval_engine="crossref",
            retrieval_engines=["crossref"],
        ))
    return out


def _wiki_search_to_results(data: Any, query: str) -> List[SearchResult]:
    results: List[SearchResult] = []
    for item in ((data or {}).get("query") or {}).get("search") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "")
        if not title:
            continue
        url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        domain, _ = source_identity(url)
        results.append(SearchResult(
            title=title,
            url=url,
            snippet=html.unescape(re.sub(r"<.*?>", "", str(item.get("snippet", "") or ""))),
            provider="wikipedia",
            search_type="encyclopedia",
            published_at=str(item.get("timestamp", "") or ""),
            matched_query=query,
            source_domain=domain,
            publisher="Wikipedia",
            retrieval_provider="wikipedia",
            retrieval_engine="wikipedia",
            retrieval_engines=["wikipedia"],
        ))
    return results
