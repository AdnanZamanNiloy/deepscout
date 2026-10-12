"""Retriever factory.

Every retriever name resolves to the same :class:`SearchClientRetriever`. The
factory still accepts and echoes whatever names a request supplies (headers or
config) so the call surface is unchanged, but the returned class is always the
one wired to the run's search client — there is deliberately no second search
stack to keep in sync.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from app.retrieval.client import SearchClient
from app.research.retrieval.base import SearchClientRetriever


def build_retriever_class(
    client: SearchClient,
    name: str = "SearchClientRetriever",
    loop: Optional[asyncio.AbstractEventLoop] = None,
    source_sink=None,
) -> type:
    """Build a retriever class carrying ``client`` (and the app loop).

    A distinct subclass (rather than mutating the module-level class) keeps the
    binding per-run and lets tests construct an isolated retriever without
    touching process-wide state.
    """
    return type(
        name,
        (SearchClientRetriever,),
        {"_search_client": client, "_loop": loop, "_source_sink": source_sink},
    )


def get_retriever(retriever: str) -> type:
    """Return the retriever class for ``name``.

    All names map to the shared search-client retriever; the parameter is kept
    so callers that pass a configured name are unaffected.
    """
    return SearchClientRetriever


def get_default_retriever() -> type:
    """The retriever class used when nothing else is configured."""
    return SearchClientRetriever


def get_retrievers(headers: dict, cfg) -> List[type]:
    """Resolve the retriever class list for a run.

    Names from headers/config are accepted and ignored beyond validation: the
    product has one unified retriever. Always returns a single-element list.
    """
    if not headers.get("retrievers") and not headers.get("retriever"):
        pass  # config names are accepted but do not change the class
    return [SearchClientRetriever]
