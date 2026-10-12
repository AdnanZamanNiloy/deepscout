"""Source curation skill.

Optionally asks the model to rank the gathered sources by credibility and
relevance before they become report context. Off by default; when enabled it
degrades to the uncurated sources on any failure so a curation error never
empties the context.
"""
from __future__ import annotations

import logging
from typing import List

import json_repair

from app.common.streaming import stream_output

logger = logging.getLogger(__name__)


def _parse_json_list(text: str):
    """Parse a JSON list from a model response, tolerating fences/prose.

    Returns the list, or ``None`` when the payload is not a list.
    """
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
    try:
        parsed = json_repair.loads(cleaned)
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


class SourceCurator:
    """Rank and curate sources for the owning researcher."""

    def __init__(self, researcher):
        self.researcher = researcher

    async def curate_sources(self, source_data: List, max_results: int = 10) -> List:
        """Return sources ranked by credibility/relevance, or the input on failure."""
        logger.debug("Curating %s sources", len(source_data))
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "research_plan",
                "Evaluating and curating sources by credibility and relevance...",
                self.researcher.sink,
            )

        response = ""
        try:
            response = await self.researcher.llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": f"{self.researcher.role}"},
                    {
                        "role": "user",
                        "content": self.researcher.prompt_family.curate_sources(
                            self.researcher.query, source_data, max_results
                        ),
                    },
                ],
                temperature=0.2,
                max_tokens=8000,
                cost_callback=self.researcher.add_costs,
            )
            curated = _parse_json_list(response)
            if curated is None:
                raise ValueError("expected a JSON list of sources")
            logger.debug("Curated %s of %s sources", len(curated), len(source_data))
            if self.researcher.verbose:
                await stream_output(
                    "logs",
                    "research_plan",
                    f"Verified and ranked top {len(curated)} most reliable sources",
                    self.researcher.sink,
                )
            return curated
        except Exception as e:
            logger.error("Error in curate_sources: %s", e)
            if self.researcher.verbose:
                await stream_output(
                    "logs",
                    "research_plan",
                    f"Source verification failed: {e}",
                    self.researcher.sink,
                )
            return source_data
