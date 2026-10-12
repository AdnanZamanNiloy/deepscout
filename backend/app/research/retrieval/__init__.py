"""Retrieval layer.

The pipeline asks a *retriever class* for sites and then fetches the pages it
needs. In this codebase retrieval is unified: every configured retriever name
resolves to the same :class:`SearchClientRetriever`, which delegates to
:class:`app.retrieval.client.SearchClient` (the product's multi-provider search
with SearXNG/Wikipedia/arXiv/Crossref and their fallbacks). Keeping one
retriever means retrieval behaviour and its trace are identical no matter which
name a request selects — there is no second search stack to drift.

The retriever presents the synchronous ``search(max_results)`` shape the
pipeline calls from a worker thread, while internally marshalling onto the
app's event loop so the client's connection pool and semaphores stay valid.
"""
from __future__ import annotations

from app.research.retrieval.base import BaseRetriever, SearchClientRetriever
from app.research.retrieval.factory import get_retriever, get_retrievers, get_default_retriever

__all__ = [
    "BaseRetriever",
    "SearchClientRetriever",
    "get_retriever",
    "get_retrievers",
    "get_default_retriever",
]
