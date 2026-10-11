"""Context management skill.

Finds the content relevant to a query from a set of gathered pages, and ranks
previously written report sections against a query. Both delegate to the
context-selection layer, which defaults to the dependency-free BM25 filter.
"""
from __future__ import annotations

from typing import Dict, List, Set

from app.research.context.lexical import rank_written_sections
from app.research.context.select import select_context
from app.research.streaming import stream_output


class ContextManager:
    """Retrieve and filter context for the owning researcher."""

    def __init__(self, researcher):
        self.researcher = researcher

    async def get_similar_content_by_query(self, query: str, pages: list) -> str:
        """Return filtered context for ``query`` from ``pages``."""
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "fetching_query_content",
                f"Getting relevant content based on query: {query}...",
                self.researcher.sink,
            )
        return await select_context(
            query,
            pages,
            self.researcher.cfg,
            max_results=10,
            prompt_family=self.researcher.prompt_family,
            cost_callback=self.researcher.add_costs,
        )

    async def get_similar_written_contents_by_draft_section_titles(
        self,
        current_subtopic: str,
        draft_section_titles: List[str],
        written_contents: List[Dict],
        max_results: int = 10,
    ) -> List[str]:
        """Return written sections most related to a subtopic + its draft titles."""
        all_queries = [current_subtopic] + draft_section_titles
        results: Set[str] = set()
        for query in all_queries:
            results.update(
                rank_written_sections(query, written_contents, max_results=max_results)
            )
        return list(results)[:max_results]

    async def _rank_written(
        self, query: str, written_contents: List[Dict], max_results: int
    ) -> List[str]:
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "fetching_relevant_written_content",
                f"Getting relevant written content based on query: {query}...",
                self.researcher.sink,
            )
        # Written-content ranking is keyword-only in this build (no embedding
        # model is configured); the ranking helper degrades to [] on no overlap.
        return rank_written_sections(query, written_contents, max_results=max_results)
