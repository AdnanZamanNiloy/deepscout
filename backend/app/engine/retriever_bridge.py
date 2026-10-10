"""Retriever bridge: back the engine's web search with DeepScout's SearchClient.

The engine calls a *retriever class* (not an instance) as
``cls(query, query_domains=...)`` and then a **synchronous**
``retriever.search(max_results=N)`` from inside ``asyncio.to_thread`` (see
``gptr/actions/query_processing.py`` and ``gptr/skills/researcher.py``). This
module exposes a class with exactly that shape whose ``search`` delegates to the
backend's multi-provider :class:`app.agents.search.SearchClient` (SearXNG with
Wikipedia/arXiv/Crossref fallbacks), so retrieval behavior — and the trace it
produces — is unchanged from the rest of the product.

Because DeepScout's client is async and already fetches page bodies, the bridge
declares ``requires_scraping = False`` and hands the engine the fetched content
under ``raw_content``. Cleaner results, and the engine skips its own scraper for
pages already read here.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from app.agents.search import SearchClient
from app.core.logging import get_logger

logger = get_logger(__name__)

# Distinguishes "no fetch requested" from "fetch returned nothing".
_DEFAULT_MAX_RESULTS = 10


class DeepScoutRetriever:
    """A retriever class the engine can instantiate and call synchronously.

    One instance per (query, domains) call, matching the engine's contract. The
    shared :class:`SearchClient` is injected at class-construction time so every
    instance reuses the same connection pooling, caches and health accounting.
    """

    #: The engine reads this to decide whether to scrape the returned URLs. Our
    #: client already fetched page bodies into ``content``, so we declare the
    #: results as already-retrieved and supply ``raw_content``.
    requires_scraping = False

    # Set by `make_retriever_class`; a plain class attribute keeps `__name__`
    # stable (the engine logs and MCP-detects by the retriever class name).
    _search_client: Optional[SearchClient] = None

    def __init__(self, query: str, query_domains: Optional[List[str]] = None):
        self.query = str(query or "").strip()
        # Domains are not hard-restricted here: the client ranks by topicality,
        # and the engine passes this for its own MCP path. Preserved on the
        # instance for observability.
        self.query_domains = list(query_domains or [])

    def search(self, max_results: int = _DEFAULT_MAX_RESULTS) -> List[Dict[str, Any]]:
        """Synchronous search used from a worker thread.

        ``asyncio.run`` is safe here: the engine invokes this via
        ``asyncio.to_thread``, so there is no running loop in this thread. A
        fresh client would re-create pools per call, so the class-level client
        is required — a missing client is a wiring error, surfaced loudly
        rather than returning a silent empty list.
        """
        client = type(self)._search_client
        if client is None:
            raise RuntimeError(
                "DeepScoutRetriever has no SearchClient; call "
                "install(search_client) at app startup before running the engine."
            )
        if not self.query:
            return []
        results = asyncio.run(client.run_search([self.query]))
        return self._to_engine_results(results, max_results)

    @staticmethod
    def _to_engine_results(
        results: List[Dict[str, Any]], max_results: int
    ) -> List[Dict[str, Any]]:
        """Shape backend search dicts into the engine's retriever contract.

        Keys the engine reads: ``href``/``url`` (either), and ``raw_content``
        when the retriever fetched the body itself. A result whose body was not
        fetched still contributes its URL and snippet — the engine will scrape
        it if the content proves necessary.
        """
        out: List[Dict[str, Any]] = []
        limit = max(1, int(max_results or _DEFAULT_MAX_RESULTS))
        for item in results or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            entry: Dict[str, Any] = {
                "url": url,
                "href": url,
                "title": str(item.get("title") or ""),
            }
            content = str(item.get("content") or "")
            if item.get("is_content_fetched") and content:
                entry["raw_content"] = content
            out.append(entry)
            if len(out) >= limit:
                break
        return out


def make_retriever_class(client: SearchClient, name: str = "DeepScoutRetriever"):
    """Build the retriever class carrying ``client``.

    A distinct subclass (rather than a module-level class mutated in place)
    keeps `install` idempotent and lets tests construct an isolated retriever
    without touching the process-wide wiring.
    """
    return type(name, (DeepScoutRetriever,), {"_search_client": client})


def install(client: SearchClient) -> type:
    """Patch the engine's retrieval seams to use ``client``.

    Two seams are patched:

    * ``gptr.actions.retriever.get_retrievers`` — what ``GPTResearcher`` calls
      to populate ``self.retrievers``; returns our single class.
    * ``gptr.actions.retriever.get_default_retriever`` — the fallback the
      factory reaches for when a name does not resolve.
    """
    retriever_cls = make_retriever_class(client)

    def _get_retrievers(headers, cfg):  # noqa: ANN001 - engine signature
        return [retriever_cls]

    # Import the vendored engine package first so its sys.path bootstrap runs
    # before any `gptr.*` import resolves.
    import engine  # noqa: F401
    import gptr.actions.retriever as retriever_mod

    retriever_mod.get_retrievers = _get_retrievers
    retriever_mod.get_default_retriever = lambda: retriever_cls

    # `gptr.agent` imported `get_retrievers` into its own namespace at module
    # load (`from .actions import get_retrievers`), so patching the factory
    # module alone would not reach `GPTResearcher.__init__`. Patch the bound
    # name where it is actually called.
    try:
        import gptr.agent as agent_mod

        agent_mod.get_retrievers = _get_retrievers
    except Exception as exc:  # pragma: no cover - defensive, engine must import
        logger.warning("[RetrieverBridge] could not patch gptr.agent: %s", exc)

    logger.info("[RetrieverBridge] engine retrieval routed through SearchClient")
    return retriever_cls
