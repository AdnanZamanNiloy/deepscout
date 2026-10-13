"""Run the research pipeline for one request.

This is the API route's entry point. It constructs a
:class:`app.research.runner.ResearchRunner` for the request and exposes it under
the runner interface the route already calls (``run()`` streaming node events,
``final_state()`` returning the assembled state). The pipeline itself lives in
:mod:`app.research`; this module is the thin seam between the route and it.
"""
from __future__ import annotations

from typing import Any, Dict

from app.core.llm import LLMClient
from app.research.runner import ResearchRunner


class PipelineRunner:
    """Thin facade over :class:`ResearchRunner` for one research request.

    ``run()`` streams ``{stage, node, ...}`` events as the pipeline makes
    progress; ``final_state()`` returns the assembled report + audit state once
    the stream is exhausted.
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        search_client,
        query: str,
        mode: str = "standard",
        headers: Dict[str, str] | None = None,
        run_id: str | None = None,
        hitl: bool | None = None,
    ):
        self._runner = ResearchRunner(
            llm=llm,
            search_client=search_client,
            query=query,
            mode=mode,
            headers=headers or {},
            run_id=run_id,
            hitl=hitl,
        )

    async def run(self):
        async for event in self._runner.run():
            yield event

    def final_state(self) -> Dict[str, Any]:
        return self._runner.final_state()


__all__ = ["PipelineRunner"]
