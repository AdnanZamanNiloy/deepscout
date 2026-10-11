"""First-party research pipeline.

This package owns the full research run end to end: planning the outline,
gathering and filtering evidence, writing the report, checking it, and
assembling the final document. It is driven by :mod:`app.research.runner`
(one coroutine per request) and talks to the outside world only through
:class:`app.core.llm.LLMClient` (generation) and
:class:`app.agents.search.SearchClient` (retrieval), so every provider and
retry policy is governed by the Model Control Center.

Importing this package is side-effect free; nothing is wired until
:class:`app.research.runner.ResearchRunner` is instantiated for a run.
"""
from __future__ import annotations

__all__ = ["ResearchRunner", "__version__"]

__version__ = "1.0.0"


def __getattr__(name: str):
    """Lazily expose the runner so importing the package stays cheap."""
    if name == "ResearchRunner":
        from app.research.runner import ResearchRunner

        return ResearchRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
