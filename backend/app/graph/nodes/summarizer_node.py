from __future__ import annotations

"""Node factory `make_summarizer_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from typing import Any, Dict, List, Optional
import asyncio
from app.agents.evidence_utils import dedupe_semantic_facts
from app.core.isolation import AgentContext, build_contexts
from app.graph.evidence import _acquire_corroboration
from app.graph.state import ResearchState, SummarizerUpdate
from app.core.logging import get_logger

logger = get_logger(__name__)


def make_summarizer_node(llm, summarizer_agent):
    async def _node(state: ResearchState) -> SummarizerUpdate:
        # Agent Context Isolation (2.9): each sub-question worker sees only
        # its own AgentContext — own contract + own results, never the full
        # ResearchState or another sub-question's raw content.
        contexts = build_contexts(
            sub_questions=state.get("sub_questions", []),
            search_results=state.get("search_results", []),
        )

        # Wave execution (Feature 03): the planner already computes
        # dependency waves — run them in order so dependent contracts
        # ("compare X vs Y" depending on "what is X") extract with the
        # earlier wave's findings as grounding context. Independent members
        # of one wave still run concurrently under the LLM semaphore.
        by_wave: Dict[int, List[AgentContext]] = {}
        for ctx in contexts:
            wave = int(ctx.contract.get("wave", 0) or 0) if isinstance(ctx.contract, dict) else 0
            by_wave.setdefault(max(0, wave), []).append(ctx)
        wave_numbers = sorted(by_wave)

        async def _summarize_context(ctx: AgentContext, prior: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
            if not ctx.own_results:
                return []
            # Specialist routing (3.1): role comes from THIS context's
            # delegation contract — each specialist sees only its own
            # scoped context, never the shared ResearchState (2.9).
            # `prior_findings` is passed ONLY when a dependent wave has
            # prerequisite context — wave-0 calls keep the historical
            # signature shape so duck-typed fakes keep working. Same for
            # `sense` (intent disambiguation): passed only when the plan
            # actually carries one.
            sense = ctx.sense()
            if prior and sense:
                return await summarizer_agent(
                    llm=llm,
                    query=state["query"],
                    search_results=ctx.own_results,
                    specialist_role=ctx.specialist_role(),
                    prior_findings=prior,
                    sense=sense,
                )
            if prior:
                return await summarizer_agent(
                    llm=llm,
                    query=state["query"],
                    search_results=ctx.own_results,
                    specialist_role=ctx.specialist_role(),
                    prior_findings=prior,
                )
            if sense:
                return await summarizer_agent(
                    llm=llm,
                    query=state["query"],
                    search_results=ctx.own_results,
                    specialist_role=ctx.specialist_role(),
                    sense=sense,
                )
            return await summarizer_agent(
                llm=llm,
                query=state["query"],
                search_results=ctx.own_results,
                specialist_role=ctx.specialist_role(),
            )

        wave_report: List[Dict[str, Any]] = []
        accumulated: List[Dict[str, Any]] = []
        for wave in wave_numbers:
            wave_contexts = [c for c in by_wave[wave] if c.own_results]
            results = await asyncio.gather(*(
                _summarize_context(ctx, accumulated if wave > 0 else None)
                for ctx in wave_contexts
            ))
            wave_facts = [fact for facts in results for fact in facts]
            accumulated = [*accumulated, *wave_facts]
            wave_report.append({
                "wave": wave,
                "contracts": len(wave_contexts),
                "facts_extracted": len(wave_facts),
                "with_prerequisites": bool(wave > 0 and accumulated),
            })
            logger.info("wave_done", wave=wave, contracts=len(wave_contexts),
                        facts=len(wave_facts))

        fresh_facts = accumulated
        merged = dedupe_semantic_facts([*state.get("facts", []), *fresh_facts])
        # Corroboration ACQUISITION: attach a NEW publisher's supporting page
        # text to pending single-source claims, deterministically. Measurement
        # (`independent_corroboration`) already existed; without this pass the
        # corroboration searches ran and their results were never matched back
        # to the claims that needed them.
        merged = _acquire_corroboration(merged, state.get("search_results", []))
        logger.info("summarizer_done", fresh_facts=len(fresh_facts), total_facts=len(merged),
                    waves=len(wave_report))
        return {"facts": merged, "wave_report": wave_report}
    return _node
