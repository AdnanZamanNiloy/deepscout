"""Retriever base and the search-client-backed implementation.

A retriever is a class instantiated once per ``(query, query_domains)`` and
called via a synchronous ``search(max_results)`` method (the pipeline invokes it
from a worker thread). :class:`SearchClientRetriever` fulfils that contract by
delegating to :class:`app.agents.search.SearchClient`.

Because the product's client already fetches page bodies, the retriever declares
``requires_scraping = False`` and returns the fetched text under ``raw_content``
— so the pipeline does not re-fetch pages the client already read, and
retrieval behaviour and trace stay identical to the rest of the product.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from app.agents.search import SearchClient
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_RESULTS = 10


class BaseRetriever:
    """Contract every retriever class satisfies.

    ``requires_scraping`` tells the pipeline whether returned records are links
    that must be fetched (``True``) or already carry their body in
    ``raw_content`` (``False``). ``None`` means "not declared" and the pipeline
    falls back to a length heuristic.
    """

    requires_scraping: Optional[bool] = None

    def __init__(self, query: str, query_domains: Optional[List[str]] = None):
        self.query = str(query or "").strip()
        self.query_domains = list(query_domains or [])

    def search(self, max_results: int = _DEFAULT_MAX_RESULTS) -> List[Dict[str, Any]]:
        raise NotImplementedError


class SearchClientRetriever(BaseRetriever):
    """Retriever that delegates to the product's :class:`SearchClient`.

    On construction the shared client and the app's running loop are injected
    class-side (see :func:`app.research.retrieval.factory.build_retriever_class`)
    so every instance reuses one connection pool, cache and health accounting.
    """

    requires_scraping = False

    _search_client: Optional[SearchClient] = None
    _loop: Optional[asyncio.AbstractEventLoop] = None
    #: Per-run collector for retrieved sources (raw dicts), or None. Class-scoped
    #: so it survives the worker-thread hop where ``search`` runs.
    _source_sink: Optional[Callable[[List[Dict[str, Any]]], None]] = None

    def search(self, max_results: int = _DEFAULT_MAX_RESULTS) -> List[Dict[str, Any]]:
        """Synchronous search the pipeline calls from a worker thread.

        The async client must run on the loop that created its semaphores (the
        app's main loop). ``asyncio.run`` here would spin up a new loop in this
        thread and every rate-limit/connection semaphore would raise "bound to a
        different event loop". Instead the coroutine is marshalled onto the
        captured main loop and awaited from this thread. ``asyncio.run`` remains
        the fallback only when no loop was captured (a unit test driving the
        retriever outside the app).
        """
        client = type(self)._search_client
        if client is None:
            raise RuntimeError(
                "SearchClientRetriever has no SearchClient; call the retrieval "
                "factory with a client before running the pipeline."
            )
        if not self.query:
            return []
        loop = type(self)._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                client.run_search([self.query]), loop
            )
            results = future.result()
        else:
            results = asyncio.run(client.run_search([self.query]))
        self._record_sources(results)
        return self._to_engine_results(results, max_results)

    @classmethod
    def bind_source_sink(
        cls, sink: Optional[Callable[[List[Dict[str, Any]]], None]]
    ) -> Optional[Callable[[List[Dict[str, Any]]], None]]:
        """Bind a per-run source collector; return the previous sink to restore."""
        previous = cls._source_sink
        cls._source_sink = sink
        return previous

    @classmethod
    def _record_sources(cls, results: List[Dict[str, Any]]) -> None:
        """Hand raw backend search dicts to the run's collector, if any.

        Best-effort observability for the audit layer: a failing collector must
        never break retrieval, so errors are logged and swallowed with context.
        """
        sink = cls._source_sink
        if sink is None or not results:
            return
        try:
            sink([r for r in results if isinstance(r, dict)])
        except Exception as exc:  # pragma: no cover - observer must not break search
            logger.warning("[Retriever] source sink failed: %s", exc, exc_info=exc)

    @staticmethod
    def _to_engine_results(
        results: List[Dict[str, Any]], max_results: int
    ) -> List[Dict[str, Any]]:
        """Shape backend search dicts into the retriever contract.

        Keys the pipeline reads: ``href``/``url`` (either) and ``raw_content``
        when the body was already fetched. A result whose body was not fetched
        still contributes its URL and snippet, and the pipeline will fetch it if
        the content proves necessary.
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
