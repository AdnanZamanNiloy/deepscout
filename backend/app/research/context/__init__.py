"""Context filtering: turning pages into the model's evidence block."""
from __future__ import annotations

from app.research.context.documents import pages_to_documents, sections_to_documents
from app.research.context.lexical import (
    LexicalContextCompressor,
    bm25_scores,
    rank_written_sections,
    tokenize,
)
from app.research.context.select import resolve_context_filter, select_context

__all__ = [
    "pages_to_documents",
    "sections_to_documents",
    "LexicalContextCompressor",
    "bm25_scores",
    "rank_written_sections",
    "tokenize",
    "resolve_context_filter",
    "select_context",
]
