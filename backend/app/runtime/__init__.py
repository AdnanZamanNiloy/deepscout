"""Runtime wiring for the research pipeline.

Keeps the handles the pipeline needs (the LLM client and the search client) in
one place and hands them to a run when it starts. The first-party pipeline
(:mod:`app.research`) calls :class:`app.core.llm.LLMClient` and
:class:`app.retrieval.client.SearchClient` directly, so the Model Control Center
and the product's retrieval govern every call with no monkeypatching seam.

:func:`configure` is called once at app startup; :func:`runtime` returns the
installed handles (or ``None`` before configuration), which the resume/export
paths and tests read.
"""
from __future__ import annotations

from typing import Optional

from app.retrieval.client import SearchClient
from app.core.llm import LLMClient

_configured: Optional["RuntimeHandles"] = None


class RuntimeHandles:
    """The installed runtime handles, kept for diagnostics and tests."""

    def __init__(self, llm: LLMClient, search_client: SearchClient):
        self.llm = llm
        self.search_client = search_client


def configure(llm: LLMClient, search_client: SearchClient) -> RuntimeHandles:
    """Install the runtime handles. Idempotent: the last call wins."""
    global _configured
    _configured = RuntimeHandles(llm, search_client)
    return _configured


def runtime() -> Optional[RuntimeHandles]:
    """The installed runtime, or ``None`` when :func:`configure` has not run."""
    return _configured
