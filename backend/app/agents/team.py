"""The research team orchestrator.

Builds and drives the top-level LangGraph: an initial research pass, an outline
plan, an optional plan review, concurrent per-section research, the intro/
conclusion writer, a fact-check loop, a visualizer, and the publisher that
assembles the final document. The graph is the pipeline's control flow; the
:mod:`app.research.runner` module drives it and translates node completions into
the wire frames the frontend consumes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, StateGraph

from app.agents.architect import ResearchArchitect
from app.agents.report import (
    EvidenceAuditor,
    ReviewGate,
    ReportAssembler,
    DiagramDesigner,
    NarrativeWriter,
)
from app.agents.routing import (
    DEFAULT_MAX_FACT_CHECK_REVISIONS,
    DEFAULT_MAX_PLAN_REVISIONS,
    MaxFactCheckRevisionsExceededError,
    MaxPlanRevisionsExceededError,
    route_evidence_audit,
    route_review_gate,
)
from app.common.logging import get_logger
from app.common.models import ResearchState

logger = get_logger(__name__)


class ResearchTeam:
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
        already_completed: Optional[set] = None,
    ):
        self.task = task
        self.llm = llm
        self.cfg = cfg
        self.researcher_factory = researcher_factory
        self.output_dir = output_dir
        self.sink = sink
        self.tone = tone
        # Nodes already folded into a resumed run's state. `_staged` short-
        # circuits them: their output is supplied by the checkpoint, so
        # re-running them would re-pay for work already done. Empty on a fresh
        # run, which makes the wrapper a plain pass-through.
        self.already_completed = set(already_completed or set())

    def _initialize_agents(self) -> Dict[str, Any]:
        return {
            "architect": ResearchArchitect(
                self.llm, self.cfg, self.researcher_factory, sink=self.sink, tone=self.tone
            ),
            "pathfinder": Pathfinder(
                self.llm, self.cfg, self.researcher_factory, sink=self.sink, tone=self.tone
            ),
            "narrative_writer": NarrativeWriter(self.llm, self.cfg, sink=self.sink),
            "evidence_auditor": EvidenceAuditor(self.llm, self.cfg, sink=self.sink),
            "diagram_designer": DiagramDesigner(self.llm, self.cfg, sink=self.sink),
            "report_assembler": ReportAssembler(self.llm, self.cfg, self.output_dir, sink=self.sink),
            "review_gate": ReviewGate(self.llm, self.cfg, sink=self.sink),
        }

    def _staged(self, node: str, fn):
        """Wrap a node so the wire sees it START, not only when it ends.

        LangGraph's ``stream_mode="updates"`` only yields once a node has
        finished, so before this existed the client learned about a stage only
        in hindsight: a run streaming for a minute could show every agent as
        "Queued" while the trace below it was visibly working. The stage
        lifecycle is what lets the live pipeline rail and the live trace agree.

        The end signal is emitted from ``finally`` so a node that raises cannot
        leave the rail spinning on a stage that will never finish.
        """
        async def staged_node(state: ResearchState) -> Dict[str, Any]:
            # Resumed run: this node's output is already in the seeded state, so
            # re-running it would re-pay for work already done. Report the stage
            # as complete (the rail must still settle) and return nothing new.
            if node in self.already_completed:
                await self._announce_stage(node, "start")
                await self._announce_stage(node, "end")
                logger.info("[team] %s skipped: restored from checkpoint", node)
                return {}
            await self._announce_stage(node, "start")
            try:
                result = await fn(state)
            finally:
                await self._announce_stage(node, "end")
            return result

        staged_node.__name__ = f"staged_{node}"
        return staged_node

    async def _announce_stage(self, node: str, phase: str) -> None:
        """Report a stage transition on the run's sink (best effort)."""
        if self.sink is None:
            return
        try:
            await self.sink.emit(
                "pipeline_stage",
                phase,
                "",
                {"node": node, "phase": phase},
            )
        except Exception as exc:  # a transport failure must not stop research
            logger.warning("[team] stage signal failed for %s/%s", node, phase, exc_info=exc)

    def build_graph(self) -> StateGraph:
        """Construct (uncompiled) the team graph."""
        agents = self._initialize_agents()
        workflow = StateGraph(ResearchState)

        workflow.add_node("pathfinder", self._staged("pathfinder", agents["pathfinder"].run_initial_research))
        workflow.add_node("architect", self._staged("architect", agents["architect"].plan_research))
        workflow.add_node("scout_swarm", self._staged("scout_swarm", agents["architect"].run_parallel_research))
        workflow.add_node("narrative_writer", self._staged("narrative_writer", agents["narrative_writer"].run))
        workflow.add_node("evidence_auditor", self._staged("evidence_auditor", agents["evidence_auditor"].run))
        workflow.add_node("diagram_designer", self._staged("diagram_designer", agents["diagram_designer"].run))
        workflow.add_node("report_assembler", self._staged("report_assembler", agents["report_assembler"].run))
        workflow.add_node("review_gate", self._staged("review_gate", agents["review_gate"].review_plan))

        workflow.add_edge("pathfinder", "architect")
        workflow.add_edge("architect", "review_gate")
        workflow.add_edge("scout_swarm", "narrative_writer")
        workflow.add_edge("narrative_writer", "evidence_auditor")
        workflow.add_edge("diagram_designer", "report_assembler")
        workflow.set_entry_point("pathfinder")
        workflow.add_edge("report_assembler", END)

        workflow.add_conditional_edges(
            "review_gate",
            self._route_review_gate,
            {"accept": "scout_swarm", "revise": "architect"},
        )
        workflow.add_conditional_edges(
            "evidence_auditor",
            self._route_evidence_audit,
            {"accept": "diagram_designer", "revise": "narrative_writer"},
        )
        return workflow

    def _route_review_gate(self, review: Dict[str, Any]) -> str:
        max_revisions = self.task.get("max_plan_revisions", DEFAULT_MAX_PLAN_REVISIONS)
        try:
            return route_review_gate(review, max_revisions)
        except MaxPlanRevisionsExceededError:
            return "accept"

    def _route_evidence_audit(self, state: Dict[str, Any]) -> str:
        max_revisions = self.task.get(
            "max_fact_check_revisions", DEFAULT_MAX_FACT_CHECK_REVISIONS
        )
        try:
            return route_evidence_audit(state, max_revisions)
        except MaxFactCheckRevisionsExceededError:
            return "accept"


class Pathfinder:
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
            query=query, evidence_scope=source, tone=self.tone
        )
        initial = await researcher.conduct_research()
        report = await researcher.write_report(ext_context=initial)
        return {"task": task, "initial_research": report}


def build_research_team(
    *,
    task: Dict[str, Any],
    llm,
    cfg,
    researcher_factory,
    output_dir: str = "./outputs",
    sink=None,
    tone=None,
    already_completed: Optional[set] = None,
) -> ResearchTeam:
    """Convenience constructor."""
    return ResearchTeam(
        task=task,
        llm=llm,
        cfg=cfg,
        researcher_factory=researcher_factory,
        output_dir=output_dir,
        sink=sink,
        tone=tone,
        already_completed=already_completed,
    )
