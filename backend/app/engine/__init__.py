"""Runtime wiring for the research pipeline.

Keeps the handles the pipeline needs (the LLM client and the search client) in
one place and hands them to a run when it starts. Unlike the previous
adapter-based design there is nothing to patch: the first-party pipeline
(:mod:`app.research`) calls :class:`app.core.llm.LLMClient` and
:class:`app.agents.search.SearchClient` directly, so the Model Control Center
and the product's retrieval govern every call with no monkeypatching seam.

:func:`configure` is called once at app startup; :func:`runtime` returns the
installed handles (or ``None`` before configuration), which the resume/export
paths and tests read.
"""
from __future__ import annotations

from typing import Optional

from app.agents.search import SearchClient
from app.core.llm import LLMClient

_configured: Optional["EngineRuntime"] = None


class EngineRuntime:
    """The installed runtime handles, kept for diagnostics and tests."""

    def __init__(self, llm: LLMClient, search_client: SearchClient):
        self.llm = llm
        self.search_client = search_client
        #: Retained for callers that referenced a retriever class on the old
        #: runtime; the pipeline builds its retriever per run instead.
        self.retriever_cls = None


def configure(llm: LLMClient, search_client: SearchClient) -> EngineRuntime:
    """Install the runtime handles. Idempotent: the last call wins."""
    global _configured
    _configured = EngineRuntime(llm, search_client)
    return _configured


def runtime() -> Optional[EngineRuntime]:
    """The installed runtime, or ``None`` when :func:`configure` has not run."""
    return _configured
