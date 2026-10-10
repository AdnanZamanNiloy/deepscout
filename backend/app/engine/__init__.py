"""Adapters that wire the vendored engine to DeepScout's provider stack.

Importing this package does nothing side-effecting beyond making the two
bridges available. Call :func:`configure` once at app startup (from the
FastAPI lifespan) to install them; the engine then:

* generates text through ``app.core.llm.LLMClient`` (Model Control Center), and
* retrieves web content through ``app.agents.search.SearchClient``.

Keeping the installation explicit (rather than at import time) means tests and
benchmarks can import the engine without a live provider configuration.
"""
from __future__ import annotations

from typing import Optional

from app.core.llm import LLMClient
from app.agents.search import SearchClient

from app.engine.llm_bridge import EngineLLMBridge, install as _install_llm
from app.engine.retriever_bridge import install as _install_retriever

_configured: Optional["EngineRuntime"] = None


class EngineRuntime:
    """The installed bridge handles, kept for diagnostics and tests."""

    def __init__(self, llm_bridge: EngineLLMBridge, retriever_cls: type):
        self.llm_bridge = llm_bridge
        self.retriever_cls = retriever_cls


def configure(llm: LLMClient, search_client: SearchClient) -> EngineRuntime:
    """Install both bridges. Idempotent: the last call wins.

    Re-invocation is safe and is the intended path after a hot-reload or in a
    test that swaps clients — each call re-binds the engine seams to the
    handles it is given.
    """
    global _configured
    llm_bridge = _install_llm(llm)
    retriever_cls = _install_retriever(search_client)
    _configured = EngineRuntime(llm_bridge, retriever_cls)
    return _configured


def runtime() -> Optional[EngineRuntime]:
    """The installed runtime, or None when :func:`configure` has not run."""
    return _configured
