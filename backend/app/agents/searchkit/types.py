from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import time
from typing import Any



@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str = ""
    sub_question: str = ""
    provider: str = "unknown"
    search_type: str = "general"
    reliability_score: float = 0.0
    content_length: int = 0
    fetched_at: float = field(default_factory=time.time)
    is_content_fetched: bool = False
    # Publish date when the provider supplies one (DDG news `date`, Wikipedia
    # revision `timestamp`, arXiv `published`, Crossref `issued`) or a fetch
    # Last-Modified header. "" means unknown — never synthesized.
    published_at: str = ""
    # Which planned query actually produced this hit: the base question, a
    # variant, or the primary-source-scoped variant. Kept so the trace can show
    # which phrasings are earning their cost.
    matched_query: str = ""
    is_primary: bool = False

    # --- Source identity vs retrieval provenance ------------------------
    # These are different questions and were previously conflated into the
    # single `provider` string, which for SearXNG read "searxng:google cse".
    # That made a RETRIEVAL ENGINE masquerade as the PUBLISHER, so a paper from
    # mdpi.com was labelled "searxng:google cse" everywhere downstream.
    #
    # `provider` is kept unchanged for backward compatibility (cache entries and
    # existing readers depend on its exact value), but nothing that means
    # "who published this" may read it again.
    #
    # source_domain: canonical hostname of the page itself (lowercased, `www.`
    #   and any port/userinfo stripped). This is the primary source label.
    # publisher: the publication/organisation name when the provider states one
    #   reliably (Crossref's publisher field, a journal name). NEVER invented
    #   from the engine, and never a prettified guess at the domain.
    # retrieval_provider: the service we queried — "searxng", "wikipedia",
    #   "arxiv", "crossref".
    # retrieval_engine: the upstream index that actually returned this hit —
    #   "google cse", "duckduckgo", "arxiv", ... For non-metasearch providers
    #   this equals retrieval_provider.
    # retrieval_engines: every engine that returned this hit. A metasearch can
    #   surface one page from several engines; keeping only the primary would
    #   understate engine diversity and misattribute the page.
    source_domain: str = ""
    publisher: str = ""
    retrieval_provider: str = ""
    retrieval_engine: str = ""
    retrieval_engines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.content or self.snippet,
            "sub_question": self.sub_question,
            "provider": self.provider,
            "search_type": self.search_type,
            "reliability_score": round(self.reliability_score, 3),
            "content_length": self.content_length,
            # Whether this page's content was actually fetched and read, as
            # opposed to only appearing in the result list. The trace needs the
            # distinction to show which sources were genuinely opened; without
            # it here the flag lived only on the dataclass and never reached
            # graph state, so "View web page" could never be grounded in fact.
            # Additive key — existing readers ignore it.
            "is_content_fetched": self.is_content_fetched,
            "published_at": self.published_at,
            "matched_query": self.matched_query,
            "is_primary": self.is_primary,
            # Source identity and retrieval provenance, kept as separate keys so
            # no reader can collapse them back into one label. Additive.
            "source_domain": self.source_domain,
            "publisher": self.publisher,
            "retrieval_provider": self.retrieval_provider,
            "retrieval_engine": self.retrieval_engine,
            "retrieval_engines": list(self.retrieval_engines),
        }
