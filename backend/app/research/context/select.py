"""Turn pages of text into model context, consistently everywhere.

Both research sub-queries (scraped pages) and section writing (the finished
report) call :func:`select_context`. The ``context_filter`` config picks how
chunks are chosen:

  auto     keyword (the dependency-free default)
  keyword  BM25 keyword ranking: local, no API, no model, no embeddings
  none     no filtering: every page, in full

Anything unrecognised resolves to ``keyword``, so no setting can leave a run
without context. Content below the compression threshold is returned whole —
filtering is only worth its cost on large document sets.
"""
from __future__ import annotations

from typing import Callable, List

from app.research.config import ResearchConfig
from app.research.context.documents import pages_to_documents
from app.research.context.lexical import LexicalContextCompressor
from app.research.prompts import PromptKit

CONTEXT_FILTERS = ("auto", "keyword", "none")


def resolve_context_filter(setting: str | None) -> str:
    """The filter to use; ``auto`` resolves to the keyword (BM25) filter."""
    mode = (setting or "auto").strip().lower()
    if mode not in CONTEXT_FILTERS:
        mode = "auto"
    if mode == "auto":
        return "keyword"
    return mode


async def select_context(
    query: str,
    pages: List[dict],
    cfg: ResearchConfig,
    *,
    max_results: int = 10,
    prompt_family: type[PromptKit] | PromptKit = PromptKit,
    cost_callback: Callable[[float], None] | None = None,
) -> str:
    """Context for ``query`` from ``pages`` (dicts with raw_content/url/title)."""
    mode = resolve_context_filter(getattr(cfg, "context_filter", None))
    prompt_family = prompt_family or PromptKit
    documents = pages_to_documents(pages)

    if mode == "none":
        return prompt_family.render_documents(documents[:max_results], max_results)

    # Little content needs no filtering, whatever the mode.
    total_chars = sum(len(str(p.get("raw_content") or p.get("content") or "")) for p in pages)
    if total_chars < int(getattr(cfg, "compression_threshold", 8000)) and len(pages) <= max_results:
        return prompt_family.render_documents(documents[:max_results], max_results)

    return await LexicalContextCompressor(
        documents=documents,
        relative_threshold=getattr(cfg, "keyword_relative_threshold", 0.5),
        prompt_family=prompt_family,
    ).async_get_context(query, getattr(cfg, "keyword_max_results", 25))
