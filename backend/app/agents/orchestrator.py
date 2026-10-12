"""The research team orchestrator.

Builds and drives the top-level LangGraph: an initial research pass, an outline
plan, an optional plan review, concurrent per-section research, the intro/
conclusion writer, a fact-check loop, a visualizer, and the publisher that
assembles the final document. The graph is the pipeline's control flow; the
:mod:`app.research.runner` module drives it and translates node completions into
the wire frames the frontend consumes.
"""
from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, StateGraph

from app.agents.editor import EditorAgent
from app.agents.report_agents import (
    FactCheckerAgent,
    HumanAgent,
    PublisherAgent,
    VisualizerAgent,
    WriterAgent,
)
from app.agents.routing import (
    DEFAULT_MAX_FACT_CHECK_REVISIONS,
    DEFAULT_MAX_PLAN_REVISIONS,
    MaxFactCheckRevisionsExceededError,
    MaxPlanRevisionsExceededError,
    route_fact_check,
    route_human_feedback,
)
from app.common.models import ResearchState


class ChiefCoordinatorAgent:
    """Coordinates the agent team for one research request."""

    def __init__(
        self,
        *,
        task: Dict[str, Any],
        llm,
        cfg,
        researcher_factory,
        output_dir: str = "./outputs",
        sink=None,
        tone=None,
    ):
        self.task = task
        self.llm = llm
        self.cfg = cfg
        self.researcher_factory = researcher_factory
        self.output_dir = output_dir
        self.sink = sink
        self.tone = tone

    def _initialize_agents(self) -> Dict[str, Any]:
        return {
            "editor": EditorAgent(
                self.llm, self.cfg, self.researcher_factory, sink=self.sink, tone=self.tone
            ),
            "writer": WriterAgent(self.llm, self.cfg, sink=self.sink),
            "fact_checker": FactCheckerAgent(self.llm, self.cfg, sink=self.sink),
            "visualizer": VisualizerAgent(self.llm, self.cfg, sink=self.sink),
            "publisher": PublisherAgent(self.llm, self.cfg, self.output_dir, sink=self.sink),
            "human": HumanAgent(self.llm, self.cfg, sink=self.sink),
            "researcher": _InitialResearcher(
                self.llm, self.cfg, self.researcher_factory, sink=self.sink, tone=self.tone
            ),
        }

    def build_graph(self) -> StateGraph:
        """Construct (uncompiled) the team graph."""
        agents = self._initialize_agents()
        workflow = StateGraph(ResearchState)

        workflow.add_node("browser", agents["researcher"].run_initial_research)
        workflow.add_node("planner", agents["editor"].plan_research)
        workflow.add_node("researcher", agents["editor"].run_parallel_research)
        workflow.add_node("writer", agents["writer"].run)
        workflow.add_node("fact_checker", agents["fact_checker"].run)
        workflow.add_node("visualizer", agents["visualizer"].run)
        workflow.add_node("publisher", agents["publisher"].run)
        workflow.add_node("human", agents["human"].review_plan)

        workflow.add_edge("browser", "planner")
        workflow.add_edge("planner", "human")
        workflow.add_edge("researcher", "writer")
        workflow.add_edge("writer", "fact_checker")
        workflow.add_edge("visualizer", "publisher")
        workflow.set_entry_point("browser")
        workflow.add_edge("publisher", END)

        workflow.add_conditional_edges(
            "human",
            self._route_human_feedback,
            {"accept": "researcher", "revise": "planner"},
        )
        workflow.add_conditional_edges(
            "fact_checker",
            self._route_fact_check,
            {"accept": "visualizer", "revise": "writer"},
        )
        return workflow

    def _route_human_feedback(self, review: Dict[str, Any]) -> str:
        max_revisions = self.task.get("max_plan_revisions", DEFAULT_MAX_PLAN_REVISIONS)
        try:
            return route_human_feedback(review, max_revisions)
        except MaxPlanRevisionsExceededError:
            return "accept"

    def _route_fact_check(self, state: Dict[str, Any]) -> str:
        max_revisions = self.task.get(
            "max_fact_check_revisions", DEFAULT_MAX_FACT_CHECK_REVISIONS
        )
        try:
            return route_fact_check(state, max_revisions)
        except MaxFactCheckRevisionsExceededError:
            return "accept"


class _InitialResearcher:
    """Runs the first research pass that seeds the outline plan."""

    def __init__(self, llm, cfg, researcher_factory, sink=None, tone=None):
        self.llm = llm
        self.cfg = cfg
        self.researcher_factory = researcher_factory
        self.sink = sink
        self.tone = tone

    async def run_initial_research(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        task = research_state.get("task") or {}
        query = task.get("query")
        source = task.get("source", "web")
        researcher = self.researcher_factory(
            query=query, report_source=source, tone=self.tone
        )
        initial = await researcher.conduct_research()
        report = await researcher.write_report(ext_context=initial)
        return {"task": task, "initial_research": report}


def build_chief_editor(
    *,
    task: Dict[str, Any],
    llm,
    cfg,
    researcher_factory,
    output_dir: str = "./outputs",
    sink=None,
    tone=None,
) -> ChiefCoordinatorAgent:
    """Convenience constructor."""
    return ChiefCoordinatorAgent(
        task=task,
        llm=llm,
        cfg=cfg,
        researcher_factory=researcher_factory,
        output_dir=output_dir,
        sink=sink,
        tone=tone,
    )
