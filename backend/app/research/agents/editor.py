"""Planning agent and parallel section research.

The editor plans the report outline from the initial research, then runs one
research unit per section concurrently. Each section unit is itself a small
graph (research -> review -> revise) so a section can be polished before it
becomes part of the report. There is an optional human-feedback step, off by
default in an API context.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from langgraph.graph import END, StateGraph

from app.research.agents.research_unit import SectionResearchAgent, ReviewerAgent, ReviserAgent
from app.research.agents.routing import (
    DEFAULT_MAX_DRAFT_REVISIONS,
    MaxDraftRevisionsExceededError,
    route_draft_review,
)
from app.research.models import DraftState
from app.research.streaming import stream_output


class EditorAgent:
    """Plan the outline and run the per-section research sub-graph."""

    def __init__(self, llm, config, researcher_factory, sink=None, tone=None):
        self.llm = llm
        self.cfg = config
        self.researcher_factory = researcher_factory
        self.sink = sink
        self.tone = tone

    async def plan_research(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        """Plan the report outline from the initial research text."""
        initial_research = research_state.get("initial_research")
        task = research_state.get("task") or {}
        include_human_feedback = task.get("include_human_feedback")
        human_feedback = research_state.get("human_feedback")
        max_sections = task.get("max_sections")

        prompt = self._planning_messages(
            initial_research, include_human_feedback, human_feedback, max_sections
        )
        plan = await self.llm.call_model_json(prompt)
        return {
            "title": plan.get("title"),
            "date": plan.get("date"),
            "sections": plan.get("sections"),
        }

    async def run_parallel_research(
        self, research_state: Dict[str, Any]
    ) -> Dict[str, List[str]]:
        """Research every planned section and collect the drafts.

        Sections run in bounded waves rather than all at once: each wave's
        writers receive the titles and written excerpts of the sections already
        completed, so later sections can avoid restating earlier findings — the
        duplication and overlap that independent parallel sections produced.
        The first wave has nothing to reference, so it is the only one that
        cannot dedupe against prior work.
        """
        workflow = self._create_workflow()
        chain = workflow.compile()

        queries = list(research_state.get("sections") or [])
        title = research_state.get("title")
        if not queries:
            return {"research_data": []}

        if self.sink:
            await stream_output(
                "logs",
                "parallel_research",
                f"Running research for the following queries: {queries}",
                self.sink,
            )

        # Drafts completed so far, original planning order: {section: draft}.
        written: List[Dict[str, Any]] = []
        by_section: Dict[str, Any] = {}
        wave_size = max(1, int(getattr(self.cfg, "max_parallel_agents", 2) or 2))

        for start in range(0, len(queries), wave_size):
            wave = queries[start : start + wave_size]
            prior = self._prior_context(written, queries)
            tasks = [
                chain.ainvoke(
                    self._section_input(research_state, query, title, queries, prior)
                )
                for query in wave
            ]
            for query, result in zip(wave, await asyncio.gather(*tasks)):
                draft = result.get("draft")
                by_section[query] = draft
                written.append({"section_title": query, "draft": draft})

        # Preserve the planner's section order in the returned data.
        research_results = [by_section.get(q) for q in queries]
        return {"research_data": research_results}

    @staticmethod
    def _prior_context(
        written: List[Dict[str, Any]], all_sections: List[str]
    ) -> Dict[str, Any]:
        """Titles + written excerpts of sections already completed.

        Returns ``existing_headers`` (a compact coverage map so a writer knows
        the ground its siblings own) and ``relevant_written_contents`` (the
        actual drafted text, so a writer can avoid restating a statistic or
        argument already made). Both are empty for the first wave.
        """
        existing_headers = [
            {"subtopic task": entry["section_title"], "note": "covered by another section of this report"}
            for entry in written
        ]
        relevant: List[Dict[str, str]] = []
        for entry in written:
            draft = entry.get("draft")
            text = ""
            if isinstance(draft, dict):
                text = "\n\n".join(
                    str(v) for v in draft.values() if isinstance(v, str)
                )
            elif isinstance(draft, str):
                text = draft
            if text.strip():
                relevant.append(
                    {"section_title": entry["section_title"], "written_content": text}
                )
        return {
            "existing_headers": existing_headers,
            "relevant_written_contents": relevant,
        }

    def _planning_messages(
        self,
        initial_research: str,
        include_human_feedback: bool,
        human_feedback: Optional[str],
        max_sections: int,
    ) -> List[Dict[str, str]]:
        today = datetime.now().strftime("%d/%m/%Y")
        feedback_instruction = (
            f"Human feedback: {human_feedback}. You must plan the sections based on the human feedback."
            if include_human_feedback and human_feedback and human_feedback != "no"
            else ""
        )
        return [
            {
                "role": "system",
                "content": "You are a research editor. Your goal is to oversee the research project "
                "from inception to completion. Your main task is to plan the article section "
                "layout based on an initial research summary.\n ",
            },
            {
                "role": "user",
                "content": f"""Today's date is {today}
                   Research summary report: '{initial_research}'
                   {feedback_instruction}
                   \nYour task is to generate an outline of sections headers for the research project
                   based on the research summary report above.
                   You must generate a maximum of {max_sections} section headers.
                   You must focus ONLY on related research topics for subheaders and do NOT include introduction, conclusion and references.
                   You must return nothing but a JSON with the fields 'title' (str) and 
                   'sections' (maximum {max_sections} section headers) with the following structure:
                   '{{title: string research title, date: today's date, 
                   sections: ['section header 1', 'section header 2', 'section header 3' ...]}}'.""",
            },
        ]

    def _create_workflow(self) -> StateGraph:
        research = SectionResearchAgent(
            self.llm, self.cfg, self.researcher_factory, sink=self.sink, tone=self.tone
        )
        reviewer = ReviewerAgent(self.llm, self.cfg, sink=self.sink)
        reviser = ReviserAgent(self.llm, self.cfg, sink=self.sink)

        workflow = StateGraph(DraftState)
        workflow.add_node("researcher", research.run_depth_research)
        workflow.add_node("reviewer", reviewer.run)
        workflow.add_node("reviser", reviser.run)
        workflow.set_entry_point("researcher")
        workflow.add_edge("researcher", "reviewer")
        workflow.add_edge("reviser", "reviewer")
        workflow.add_conditional_edges(
            "reviewer",
            self._route_draft_review,
            {"accept": END, "revise": "reviser"},
        )
        return workflow

    def _route_draft_review(self, draft: Dict[str, Any]) -> str:
        task = draft.get("task") or {}
        max_draft_revisions = task.get("max_draft_revisions", DEFAULT_MAX_DRAFT_REVISIONS)
        try:
            return route_draft_review(draft, max_draft_revisions)
        except MaxDraftRevisionsExceededError:
            return "accept"

    @staticmethod
    def _section_input(
        research_state: Dict[str, Any],
        query: str,
        title: str,
        sections: Optional[List[str]] = None,
        prior: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        prior = prior or {}
        return {
            "task": research_state.get("task"),
            "topic": query,
            "title": title,
            # Every sibling section title, so a writer knows the report's shape.
            "sibling_sections": [s for s in (sections or []) if s != query],
            # Content of sections already completed this run (empty for the
            # first wave), so writers avoid restating earlier findings.
            "existing_headers": prior.get("existing_headers") or [],
            "relevant_written_contents": prior.get("relevant_written_contents") or [],
        }
