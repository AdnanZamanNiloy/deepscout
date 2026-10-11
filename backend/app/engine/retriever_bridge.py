"""Retriever bridge: back the engine's web search with DeepScout's SearchClient.

The engine calls a *retriever class* (not an instance) as
``cls(query, query_domains=...)`` and then a **synchronous**
``retriever.search(max_results=N)`` from inside ``asyncio.to_thread`` (see
``refeng/actions/query_processing.py`` and ``refeng/skills/researcher.py``). This
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
import contextvars
from typing import Any, Callable, Dict, List, Optional

from app.agents.search import SearchClient
from app.core.logging import get_logger

logger = get_logger(__name__)

# Distinguishes "no fetch requested" from "fetch returned nothing".
_DEFAULT_MAX_RESULTS = 10

# Per-run source collector. The engine instantiates the retriever class itself,
# so there is no constructor seam to hand it a run-scoped object. A ContextVar
# carries the active collector on the async task that owns the run — the search
# runs on the app's main loop via run_coroutine_threadsafe, but we set the
# ContextVar from within the same coroutine that calls ``search`` would break:
# the sync ``search`` runs in a worker thread, so the collector is read here
# through the bound instance instead. See ``make_retriever_class``.
_SOURCE_SINK: contextvars.ContextVar[Optional[Callable[[List[Dict[str, Any]]], None]]] = (
    contextvars.ContextVar("engine_source_sink", default=None)
)


def set_source_sink(
    sink: Optional[Callable[[List[Dict[str, Any]]], None]],
) -> contextvars.Token:
    """Install the per-run collector for retrieved sources.

    Returns the ContextVar token so the caller can ``reset`` it in a finally
    block (AGENTS.md 4.3: run-scoped, explicitly cleaned up). ``sink`` receives
    the raw backend search dicts (title/url/content/published_at/...) before
    they are shaped for the engine.
    """
    return _SOURCE_SINK.set(sink)


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
    #: The app's main event loop, captured at install time. Async work is
    #: marshalled onto this loop so the client's loop-bound semaphores stay
    #: valid (see ``search``).
    _loop: Optional[asyncio.AbstractEventLoop] = None
    #: Per-run collector for retrieved sources, or None. Read in ``search`` as
    #: the raw backend dicts come back, so the run's audit layer sees the real
    #: sources (title/url/content/metadata) rather than URLs scraped out of the
    #: final report text. Class-scoped so it survives the worker-thread hop.
    _source_sink: Optional[Callable[[List[Dict[str, Any]]], None]] = None

    def __init__(self, query: str, query_domains: Optional[List[str]] = None):
        self.query = str(query or "").strip()
        # Domains are not hard-restricted here: the client ranks by topicality,
        # and the engine passes this for its own MCP path. Preserved on the
        # instance for observability.
        self.query_domains = list(query_domains or [])

    def search(self, max_results: int = _DEFAULT_MAX_RESULTS) -> List[Dict[str, Any]]:
        """Synchronous search the engine calls from a worker thread.

        Critically, the async ``SearchClient`` must run on the SAME event loop
        that created its internal semaphores (the app's main loop). Calling
        ``asyncio.run`` here would spin up a NEW loop in this thread and every
        rate-limit/connection semaphore would raise "bound to a different event
        loop" — observed live as each search contract failing while the run
        limped on with partial evidence. Instead the coroutine is marshalled
        onto the captured main loop via ``run_coroutine_threadsafe`` and awaited
        from this thread.

        ``asyncio.run`` remains the fallback only when no loop was captured
        (e.g. a unit test driving the retriever outside the app), where there is
        no shared state to conflict with.
        """
        client = type(self)._search_client
        if client is None:
            raise RuntimeError(
                "DeepScoutRetriever has no SearchClient; call "
                "install(search_client) at app startup before running the engine."
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
        """Bind a per-run source collector to this retriever class.

        Called by the orchestrator at run start and cleared at run end. Returns
        the PREVIOUS sink so the caller can restore it in a finally block (a
        nested/overlapping run must not permanently steal the outer run's sink).
        Class-scoped so the benchmark/test path can bind an isolated collector
        without touching the process-wide wiring.
        """
        previous = cls._source_sink
        cls._source_sink = sink
        return previous

    @classmethod
    def _record_sources(cls, results: List[Dict[str, Any]]) -> None:
        """Hand the raw backend search dicts to the run's collector, if any.

        The sink is best-effort observability for the audit layer: a failing
        collector must never break retrieval, so errors are logged and swallowed
        with context (AGENTS.md 4.4).
        """
        sink = cls._source_sink
        if sink is None or not results:
            return
        try:
            sink([r for r in results if isinstance(r, dict)])
        except Exception as exc:  # pragma: no cover - observer must not break search
            logger.warning("[RetrieverBridge] source sink failed: %s", exc, exc_info=exc)

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


def make_retriever_class(
    client: SearchClient,
    name: str = "DeepScoutRetriever",
    loop: Optional[asyncio.AbstractEventLoop] = None,
    source_sink: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
):
    """Build the retriever class carrying ``client`` (and the main loop).

    A distinct subclass (rather than a module-level class mutated in place)
    keeps `install` idempotent and lets tests construct an isolated retriever
    without touching the process-wide wiring. ``source_sink`` (optional) is
    stored class-side so it survives the worker-thread hop where the engine
    calls the synchronous ``search``.
    """
    return type(
        name,
        (DeepScoutRetriever,),
        {"_search_client": client, "_loop": loop, "_source_sink": source_sink},
    )


def install(
    client: SearchClient,
    loop: Optional[asyncio.AbstractEventLoop] = None,
    source_sink: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> type:
    """Patch the engine's retrieval seams to use ``client``.

    Two seams are patched:

    * ``refeng.actions.retriever.get_retrievers`` — what ``ReferenceEngine`` calls
      to populate ``self.retrievers``; returns our single class.
    * ``refeng.actions.retriever.get_default_retriever`` — the fallback the
      factory reaches for when a name does not resolve.

    ``loop`` is the app's running main loop; it is captured so the retriever's
    async search runs on the loop that owns the client's semaphores. When
    omitted, the currently-running loop is used if there is one.
    """
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
    retriever_cls = make_retriever_class(client, loop=loop, source_sink=source_sink)

    def _get_retrievers(headers, cfg):  # noqa: ANN001 - engine signature
        return [retriever_cls]

    # Import the vendored engine package first so its sys.path bootstrap runs
    # before any `refeng.*` import resolves.
    import engine  # noqa: F401
    import refeng.actions.retriever as retriever_mod

    retriever_mod.get_retrievers = _get_retrievers
    retriever_mod.get_default_retriever = lambda: retriever_cls

    # `refeng.agent` imported `get_retrievers` into its own namespace at module
    # load (`from .actions import get_retrievers`), so patching the factory
    # module alone would not reach `ReferenceEngine.__init__`. Patch the bound
    # name where it is actually called.
    try:
        import refeng.agent as agent_mod

        agent_mod.get_retrievers = _get_retrievers
    except Exception as exc:  # pragma: no cover - defensive, engine must import
        logger.warning("[RetrieverBridge] could not patch refeng.agent: %s", exc)

    logger.info("[RetrieverBridge] engine retrieval routed through SearchClient")
    return retriever_cls
