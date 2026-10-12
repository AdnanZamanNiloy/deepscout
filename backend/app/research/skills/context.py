"""Context management skill.

Finds the content relevant to a query from a set of gathered pages. Delegates
to the context-selection layer, which defaults to the dependency-free BM25
filter.
"""
from __future__ import annotations

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
