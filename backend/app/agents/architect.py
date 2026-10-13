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

from app.agents.scout import Scout, DraftReviewer, DraftReviser
from app.agents.routing import (
    DEFAULT_MAX_DRAFT_REVISIONS,
    MaxDraftRevisionsExceededError,
    route_draft_review,
)
from app.common.models import DraftState
from app.common.streaming import stream_output


class ResearchArchitect:
    """Plan the outline and run the per-section research sub-graph."""

    def __init__(self, llm, config, researcher_factory, sink=None, tone=None):
        self.llm = llm
        self.cfg = config
        self.researcher_factory = researcher_factory
        self.sink = sink
        self.tone = tone

    async def plan_research(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        """Plan the report outline from the initial research text.

        Returns the primary outline under ``sections`` (used by the automatic
        path) plus, when the run asks for alternatives, a list of candidate
        outlines under ``plan_alternatives`` for interactive review to choose
        from. Each candidate shares the plan's ``title``.
        """
        initial_research = research_state.get("initial_research")
        task = research_state.get("task") or {}
        human_feedback = research_state.get("human_feedback")
        max_sections = task.get("max_sections")
        alternatives = int(task.get("plan_alternatives") or 1)

        prompt = self._planning_messages(
            initial_research, human_feedback, max_sections, alternatives
        )
        plan = await self.llm.call_model_json(prompt)
        title = plan.get("title")

        # The mode's section budget is enforced HERE, by keeping the first N.
        # The prompt asks for "maximum N" and models routinely overshoot; the
        # outline is ordered by importance, so an overshoot is a formatting
        # slip, not a planning failure. Bouncing it back through the review gate
        # cost a whole extra planner round-trip and — because the re-plan prompt
        # says "fix exactly these issues" — reliably came back with sections
        # DROPPED. Truncating is free and loses nothing from the final list.
        cap = int(max_sections or 0)

        def _cap(secs: List[str]) -> List[str]:
            secs = [s for s in secs if s]
            return secs[:cap] if cap and len(secs) > cap else secs

        # Collect candidate outlines. The prompt asks for `alternatives` of
        # them under "alternatives"; tolerate a single-outline response too.
        candidates: List[List[str]] = []
        raw_alts = plan.get("alternatives")
        if isinstance(raw_alts, list):
            for alt in raw_alts:
                secs = _cap([str(s).strip() for s in (alt or [])])
                if secs:
                    candidates.append(secs)
        primary = _cap([str(s).strip() for s in (plan.get("sections") or [])])
        if not candidates and primary:
            candidates = [primary]
        if primary:
            # Ensure the model's primary outline is the first candidate.
            if not candidates or candidates[0] != primary:
                candidates.insert(0, primary)

        return {
            "title": title,
            # The run's actual date, not the model's guess: a model date is
            # often ambiguous (11/10/2026) or wrong, and the report should be
            # dated when it was produced.
            "date": datetime.now().strftime("%d %B %Y"),
            "sections": primary or (candidates[0] if candidates else []),
            "plan_alternatives": candidates,
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
            # A human-readable progress line. Interpolating the raw list printed
            # a Python repr ("['A', 'B']") into the trace. Emit markdown: the
            # trace renders it, so the sections land as a proper list instead of
            # one long paragraph that the two-line clamp then truncates.
            listed = "\n".join(f"- {q}" for q in queries)
            await stream_output(
                "logs",
                "parallel_research",
                f"Researching {len(queries)} section"
                f"{'s' if len(queries) != 1 else ''} in parallel:\n{listed}",
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
        human_feedback: Optional[str],
        max_sections: int,
        alternatives: int = 1,
    ) -> List[Dict[str, str]]:
        today = datetime.now().strftime("%d/%m/%Y")
        # `human_feedback` here is revision feedback on a prior outline (from
        # the interactive review or the automated gate). It is applied whenever
        # present; the `"no"` sentinel means approval and carries no instruction.
        #
        # The instruction stays NEUTRAL on purpose ("address the feedback"), not
        # "fix exactly these issues and change nothing else": the narrow phrasing
        # made the model treat the feedback as the whole brief and return a
        # SHORTER outline, which is how report sections went missing. The
        # feedback text already names what was wrong; the plan must still be a
        # full outline for the section count the mode allows.
        feedback_instruction = (
            f"Your previous outline was rejected by the plan review for this "
            f"reason: {human_feedback}. Plan the sections again so that reason no "
            f"longer applies, while still covering the subject as fully as before."
            if human_feedback and human_feedback != "no"
            else ""
        )
        if alternatives > 1:
            shape = (
                f"You must return nothing but a JSON with the fields 'title' (str) and "
                f"'sections' (the PRIMARY outline, maximum {max_sections} section headers) and "
                f"'alternatives' (a list of {alternatives} DISTINCT candidate outlines, each a list "
                f"of section headers). The alternatives must each take a genuinely different angle "
                f"on the subject (e.g. by depth, by stakeholder, by time horizon) — not rewordings "
                f"of one another. The first alternative should equal 'sections'.\n"
                f"Structure: '{{title: string, date: today's date, "
                f"sections: ['header 1', 'header 2', ...], "
                f"alternatives: [['header 1', 'header 2', ...], [...], [...]]}}'."
            )
        else:
            shape = (
                f"You must return nothing but a JSON with the fields 'title' (str) and "
                f"'sections' (maximum {max_sections} section headers) with the following structure:\n"
                f"'{{title: string research title, date: today's date, "
                f"sections: ['section header 1', 'section header 2', 'section header 3' ...]}}'."
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
                   The 'title' must be a plain, specific title that names the actual subject of the report. Do NOT use empty booster words like "A Comprehensive Analysis of", "An In-Depth Look at", or "A Structured Framework for" — they promise breadth the report may not deliver. Name the subject; do not advertise it.
                   The section headers must be distinct from one another: each must own a clearly different part of the subject, with no two headers covering the same ground.
                   {shape}""",
            },
        ]

    def _create_workflow(self) -> StateGraph:
        research = Scout(
            self.llm, self.cfg, self.researcher_factory, sink=self.sink, tone=self.tone
        )
        reviewer = DraftReviewer(self.llm, self.cfg, sink=self.sink)
        reviser = DraftReviser(self.llm, self.cfg, sink=self.sink)

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
            # Free-text direction the user gave at plan review ("focus on costs
            # and policy"), threaded to the section researcher/writer.
            "plan_direction": research_state.get("plan_direction") or "",
            # Every sibling section title, so a writer knows the report's shape.
            "sibling_sections": [s for s in (sections or []) if s != query],
            # Content of sections already completed this run (empty for the
            # first wave), so writers avoid restating earlier findings.
            "existing_headers": prior.get("existing_headers") or [],
            "relevant_written_contents": prior.get("relevant_written_contents") or [],
        }
