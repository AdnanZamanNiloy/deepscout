"""Retrieval and evidence/quality helpers.

This package is shared infrastructure, not an agent layer: it holds the
multi-provider search client the research pipeline retrieves through
(``app.agents.search``) and the evidence/source/quality helpers the audit layer
and ``app/core`` consume (``app.agents.evidence``, ``app.agents.sources``,
``app.agents.quality``, ``app.agents.evidence_utils``). Import the submodule you
need directly, e.g. ``from app.agents.search import SearchClient``.
"""
from __future__ import annotations

__all__: list[str] = []
