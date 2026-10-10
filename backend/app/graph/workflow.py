"""The research graph: state transitions, not rendering or evidence policy.

This module used to be 2496 lines holding everything -- the TypedDicts, nine
helper clusters, four report builders and the graph assembly -- which made the
part that actually matters (the ten nodes and their routing) unreadable inside
its own file. It now holds the state plumbing and the graph, with three
siblings:

  state.py     the TypedDicts every node is typed against (pure declarations)
  evidence.py  corroboration procurement, coverage gaps, claim scoping
  reports.py   the delivered answer document and the separate audit/trace

Those three are re-exported below under their original names, so every existing
`from app.graph.workflow import _corroboration_queries` and every
`monkeypatch.setattr(wf, "planner_agent", ...)` keeps working unchanged. That
matters beyond convenience: the tests patch agent functions ON THIS MODULE, so
the node closures must keep resolving them here rather than in a module of
their own. The `X as X` form is the PEP 484 explicit re-export marker, so these
are not "unused imports".

The nodes stay nested inside `create_workflow` because they close over `llm`
and `search_client`. Lifting them out is a real refactor with real regression
risk, and it is not what made this file hard to read.
"""
from __future__ import annotations

import asyncio  # noqa: F401
import datetime  # noqa: F401
import re  # noqa: F401
from typing import Any, Dict, List, Mapping, Optional  # noqa: F401

from langgraph.graph import END, START, StateGraph

from app.agents.answer_conformance import check_answer_conformance  # noqa: F401
from app.agents.answer_quality import evaluate_answer  # noqa: F401
from app.agents.critic import critic_agent
from app.agents.direct_answer import direct_answer_agent
from app.agents.evidence_utils import (
    dedupe_semantic_facts,  # noqa: F401
    verify_answer_support,
)
from app.agents.intent import classify_intent, heuristic_intent  # noqa: F401
from app.agents.orchestrator import MODE_CONFIDENCE_TARGET, orchestrate  # noqa: F401
from app.agents.planner import normalize_text, planner_agent  # noqa: F401
from app.agents.router import conversation_kind, deterministic_route, route_query  # noqa: F401
from app.agents.search import SearchClient
from app.agents.summarizer import summarizer_agent
from app.agents.synthesizer import synthesizer_agent
from app.agents.thesis_fidelity import check_thesis_fidelity  # noqa: F401
from app.agents.verifier import verify_facts
from app.core.depth import controller as depth_controller
from app.core.confidence import compute_confidence  # noqa: F401
from app.core.contradictions import find_contradictions  # noqa: F401
from app.core.degradation import has_provider_degradation, take_fallbacks  # noqa: F401
from app.core.investigation_state import (
    STATUS_CORROBORATED as STATUS_CORROBORATED,
    STATUS_EXHAUSTED as STATUS_EXHAUSTED,
)
from app.core.decision import build_decision_layer  # noqa: F401
from app.core.isolation import AgentContext, build_contexts  # noqa: F401
from app.core.llm import LLMClient
from app.core.logging import get_logger

from app.graph.evidence import (
    IN_SCOPE_SIMILARITY as IN_SCOPE_SIMILARITY,
    _acquire_corroboration as _acquire_corroboration,
    _attach_evidence as _attach_evidence,
    _claim_terms as _claim_terms,
    _corroboration_queries as _corroboration_queries,
    _counter_evidence_queries as _counter_evidence_queries,
    _evidence_gaps_remain as _evidence_gaps_remain,
    _measured_coverage_gaps as _measured_coverage_gaps,
    _prepare_supporting_evidence as _prepare_supporting_evidence,
    _query_inscope_facts as _query_inscope_facts,
    _safe_float as _safe_float,
    _summary_claim_texts as _summary_claim_texts,
    _verified_facts as _verified_facts,
    get_settings_safe as get_settings_safe,
)
from app.graph.reports import (
    build_answer_audit as build_answer_audit,
    build_conversation_report as build_conversation_report,
    build_direct_answer_report as build_direct_answer_report,
    build_markdown_report as build_markdown_report,
)
from app.graph.state import (
    CriticUpdate as CriticUpdate,
    FinalizeUpdate as FinalizeUpdate,
    IntentUpdate as IntentUpdate,
    PlannerUpdate as PlannerUpdate,
    ResearchState as ResearchState,
    SearchUpdate as SearchUpdate,
    SummarizerUpdate as SummarizerUpdate,
    SynthesizerUpdate as SynthesizerUpdate,
    VerifierUpdate as VerifierUpdate,
)

logger = get_logger(__name__)

from app.graph.nodes.intent_node import make_intent_node
from app.graph.nodes.direct_answer_node import make_direct_answer_node
from app.graph.nodes.summarizer_node import make_summarizer_node
from app.graph.nodes.verifier_node import make_verifier_node
from app.graph.nodes.finalize_node import make_finalize_node
from app.graph.nodes.planner_node import make_planner_node  # noqa: F401
from app.graph.nodes.synthesizer_node import make_synthesizer_node  # noqa: F401
from app.graph.nodes.search_node import make_search_node  # noqa: F401
from app.graph.nodes.critic_node import make_critic_node  # noqa: F401
from app.graph.nodes.helpers import (  # noqa: F401
    _extract_question_text,
    _merge_questions,
    _unanswered_questions,
    _assess_focus,
    _NODES_PER_PASS,
    _GRAPH_ENTRY_AND_TAIL,
    graph_recursion_limit,
)


def build_initial_state(
    query: str,
    max_iterations: int,
    deep_research: bool = False,
    max_parallel_agents: int = 3,
    mode: str = "standard",
) -> ResearchState:
    """Build the run's initial state.

    When `mode` is a valid preset (3.7), it overrides the raw parameters
    with its (max_agents, max_iterations, deep_research) tuple.

    `mode` is resolved through `resolve_mode` first: a legacy name an old client
    or a stored run still carries ("executive", "audit") is mapped onto the mode
    that inherited its behaviour instead of falling through to the default, so a
    saved executive run still gets deep research rather than silently becoming
    shallow.
    """
    from app.agents.orchestrator import MODE_PRESETS, scaled_max_iterations
    from app.agents.orchestrator import resolve_mode

    mode = resolve_mode(mode)

    preset = MODE_PRESETS.get(mode)
    if preset is not None:
        # A mode preset sets iterations explicitly — do NOT apply the
        # max(3, ...) floor (GAP-8) or quick mode would be no quicker.
        max_iterations = preset["max_iterations"]
        deep_research = preset["deep_research"]
        # The preset's agent cap REPLACES the setting default: only `deep`
        # is allowed to exceed MAX_PARALLEL_AGENTS (vision §28: explicit opt-in
        # via mode selection + governor check).
        max_parallel_agents = preset["max_agents"]
        effective_max_iterations = int(max_iterations)
    else:
        effective_max_iterations = max(3, int(max_iterations))
    plan = orchestrate(query, max_parallel_agents=max_parallel_agents,
                       deep_research=deep_research, mode=mode)
    # Scale the deep iteration budget to the map size now that `orchestrate`
    # has set target_agents. Bounded by scaled_max_iterations (one pass per
    # ~2 contracts, floored); quick/standard unchanged.
    if preset is not None:
        effective_max_iterations = scaled_max_iterations(
            mode, plan.target_agents
        )
    targets = plan.targets.to_dict() if plan.targets is not None else {}
    return {
        "query": query,
        "sub_questions": [],
        "search_results": [],
        "facts": [],
        "critique": {},
        "critique_feedback": "",
        "iteration": 0,
        "max_iterations": effective_max_iterations,
        "final_report": "",
        "final_audit": "",
        "synthesized_answer": "",
        "synthesis_machine_notes": [],
        "confidence": 0.0,
        "orchestration": {
            "complexity_score": plan.complexity.score,
            "complexity_level": plan.complexity.level,
            "query_type": plan.complexity.query_type,
            "target_agents": plan.target_agents,
            "max_parallel_agents": plan.max_parallel_agents,
            "clamped": plan.clamped,
            "deep_research": plan.deep_research,
            "notes": plan.notes,
            # v3 plan targets: hard requirements the planner must honour.
            "required_axes": list(targets.get("required_axes", []) or []),
            "target_sub_questions": int(targets.get("sub_questions", 0) or 0),
            "min_sources_per_axis": int(targets.get("min_sources_per_axis", 0) or 0),
        },
        "deep_research": plan.deep_research,
        "confidence_history": [],
        "mode": mode,
        "intent": {},
        "context_snippets": [],
    }


def create_workflow(llm: LLMClient, search_client: SearchClient, entry_node: str | None = None):
    """Compile the research graph.

    entry_node=None (default): START → intent → planner (full pipeline).
    entry_node="critic": START → critic — used by the resume endpoint (3.3)
    so a failed/timeout run continues from persisted evidence instead of
    re-running intent/planner/search.
    """
    if entry_node is not None and entry_node != "critic":
        raise ValueError(f"unsupported entry_node={entry_node!r} (only 'critic' is supported)")
    graph = StateGraph(ResearchState)

    def _decide_ambiguity(query: str, intent_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Compute the ambiguity policy, total and cheap.

        Runs on every research turn, so it must never raise: a failure falls back
        to PROCEED, which is the pre-existing behaviour and therefore cannot make
        a run worse than before this existed (AGENTS.md 4.7).

        `broad_question` marks a question that asks for several things at once —
        the multi-part shape the focus layer also recognises. A broad question
        that CAN be answered by covering its parts is SEPARATE, not ASK, so
        breadth is not mistaken for ambiguity.
        """
        try:
            from app.agents.ambiguity import decide_ambiguity

            return decide_ambiguity(
                query, intent_dict, broad_question=_looks_broad(query)
            ).to_dict()
        except Exception as exc:
            logger.warning("ambiguity_decision_failed", error=str(exc), exc_info=exc)
            return {"action": "proceed", "interpretations": [], "question": ""}

    def _looks_broad(query: str) -> bool:
        """Does the question ask about more than one thing?

        Reuses the focus layer's conjunction count so breadth means the same
        thing in both places, rather than introducing a second definition.
        """
        try:
            from app.agents.focus import _conjunction_parts

            return _conjunction_parts(query) >= 2
        except Exception:
            return False


    intent_node = make_intent_node(llm, search_client, classify_intent, route_query, _decide_ambiguity, _looks_broad)




    direct_answer_node = make_direct_answer_node(llm, direct_answer_agent)


    planner_node = make_planner_node(llm, planner_agent)


    search_node = make_search_node(search_client)


    summarizer_node = make_summarizer_node(llm, summarizer_agent)


    verifier_node = make_verifier_node(verify_facts)


    critic_node = make_critic_node(llm, critic_agent)


    def _build_synthesizer_context(state: ResearchState, _llm):
        # State -> synthesis context. This is the graph<->synthesis seam: it
        # maps ResearchState onto the dict the writer is handed. It lives here
        # (not in the node factory) because it owns the convergence/ambiguity/
        # coverage mapping the graph produces.
        # Only verification-passed facts are usable evidence (Phase 2.3).
        usable = _verified_facts(state.get("facts", []))
        all_facts = state.get("facts", [])
        intent = state.get("intent") or {}
        base_context: Dict[str, Any] = {
            "contradictions": state.get("contradictions", []),
            "confidence": state.get("confidence", None),
            "degraded": take_fallbacks(),
            "total_facts": len(all_facts),
            "verified_count": sum(1 for f in all_facts if f.get("verified")),
            "mode": state.get("mode", "standard"),
            # Intent: the synthesis must answer the user's likely meaning
            # and disambiguate up front when the query was ambiguous.
            "intent": intent,
            # Ambiguity policy: whether the answer should state a chosen reading
            # (assume) or keep researched readings in separate, non-blended
            # strands (separate). `ask` never reaches synthesis — that run stops
            # at the clarification node.
            "ambiguity": state.get("ambiguity") or {},
            # Convergence diagnosis: whether the loop concluded the evidence
            # cannot answer the question as asked. The writer must say so rather
            # than manufacture a winner or force the question into a shape the
            # evidence supports. Also carries the coverage/ranking inputs.
            "convergence": state.get("convergence") or {},
            "focus": state.get("focus") or {},
            "critique": state.get("critique") or {},
            # Answer-first outline inputs: the plan's axes are the query's
            # dimensions; the synthesizer turns them into the report's shape.
            "sub_questions": state.get("sub_questions", []),
            # Mandatory-section inputs: measured coverage gaps so the
            # limitations section is populated from real deficiencies.
            "coverage_gaps": _measured_coverage_gaps(state),
            # Counter-argument guard: whether a dedicated counter-evidence
            # search actually ran, so the report never claims "no credible
            # counterarguments" from an unsearched pool.
            "counter_evidence_attempted": bool(state.get("counter_evidence_attempted")),
        }
        # Raw source excerpts for the writer: the distilled claims read thin on
        # their own. The verifier retains a bounded excerpt per source before it
        # releases the full page; surface those so the writer can quote, connect
        # and qualify from primary material instead of a claim list.
        try:
            from app.core.config import get_settings

            max_sources = int(
                getattr(get_settings(), "synthesis_source_excerpt_sources", 8) or 8
            )
            excerpts: Dict[str, str] = {}
            for result in state.get("search_results", []) or []:
                if not isinstance(result, dict):
                    continue
                url = str(result.get("url", "") or "").strip()
                excerpt = str(result.get("source_excerpt", "") or "").strip()
                if url and excerpt and url not in excerpts:
                    excerpts[url] = excerpt
                    if len(excerpts) >= max_sources:
                        break
            if excerpts:
                base_context["source_excerpts"] = excerpts
        except Exception as exc:
            logger.warning("source_excerpt_collection_failed", error=str(exc), exc_info=exc)
        # Source-ledger composition: regulation dominance and non-Western
        # under-representation are surfaced on the report so a legal-summary
        # drift is visible, and so the writer can flag a provisional band.
        try:
            from app.agents.evidence_utils import evidence_stats

            ledger_stats = evidence_stats(all_facts)
            base_context["source_ledger"] = ledger_stats
            base_context["regulation_share"] = ledger_stats.get("regulation_share", 0.0)
            base_context["primary_share"] = ledger_stats.get("primary_share", 0.0)
            base_context["non_western_share"] = ledger_stats.get("non_western_share", 0.0)
            base_context["temporal_dropped"] = int(
                (state.get("verification_stats", {}) or {}).get("temporal_dropped", 0) or 0
            )
        except Exception as exc:  # ledger stats must never break synthesis
            logger.warning("source_ledger_failed", error=str(exc), exc_info=exc)
        # Evidence grades (Step 4): the measured quality distribution drives
        # the writer's epistemic labeling. Computed once, failure-safe.
        evidence_distribution: Dict[str, int] = {}
        try:
            from app.core.evidence_grade import grade_facts

            graded = grade_facts(usable, contradictions=state.get("contradictions") or [])
            dist = {"A": 0, "B": 0, "C": 0, "D": 0}
            for g in graded:
                grade = str((g.get("evidence") or {}).get("grade", "D"))
                dist[grade] = dist.get(grade, 0) + 1
            base_context["evidence_distribution"] = dist
            evidence_distribution = dist
        except Exception as exc:
            logger.warning("evidence_distribution_failed", error=str(exc), exc_info=exc)
        # Evidence-grounded reasoning structure (Feature: argument synthesis):
        # the deterministic argument layer the writer must state a conclusion
        # from, present competing explanations for, and separate established /
        # inferred / unknown. Built from the same graded facts and state as the
        # rest of the pipeline; failure-safe (the writer simply runs without it).
        try:
            from app.core.reasoning_engine import build_reasoning

            base_context["reasoning"] = build_reasoning(
                usable,
                state.get("contradictions") or [],
                query=state["query"],
                query_type=str(intent.get("query_type", "") or ""),
                investigation_state=state.get("investigation_state"),
                sub_questions=state.get("sub_questions") or [],
                depth_state=state,
            )
        except Exception as exc:
            logger.warning("reasoning_build_failed", error=str(exc), exc_info=exc)
        return base_context, evidence_distribution, intent, usable

    synthesizer_node = make_synthesizer_node(
        llm, synthesizer_agent, _build_synthesizer_context, verify_answer_support)


    finalize_node = make_finalize_node()


    def route_after_critic(state: ResearchState) -> str:
        critique = state.get("critique", {})
        is_sufficient = bool(critique.get("is_sufficient", False))

        # Hard walls (budget/time, iteration ceiling) are absolute — an
        # evidence gap cannot be acted on if no pass can run. Checked here,
        # before ANY expand branch, so no evidence-gap override or
        # critic-insufficient expansion can route past the ceiling and run
        # LangGraph into its recursion limit.
        if depth_controller.hard_wall_reached(state):
            logger.info(
                "route_hard_wall_finalize",
                iteration=int(state.get("iteration", 0)),
                max_iterations=int(state.get("max_iterations", 0)),
            )
            return "synthesizer"

        # Evidence-first sufficiency (Step 3): a critic saying "enough" is an
        # OPINION, not proof. Before trusting it, check the measured evidence
        # base — uncovered axis gaps, uncorroborated quantitative claims, and
        # unresolved contradictions are grounds to keep researching even when
        # the model is satisfied. When the evidence gate is clean, the critic
        # wins exactly as before (no extra iteration, no score change).
        if is_sufficient and _evidence_gaps_remain(state):
            logger.info("evidence_gate_overrides_critic", iteration=int(state.get("iteration", 0)))
            is_sufficient = False

        if is_sufficient:
            return "synthesizer"

        # Dynamic Research Depth (2.8) decides expand vs finalize from axis
        # coverage, corroboration, contradictions, marginal gain and the
        # hard walls. The evidence gate is now baked into `decide`'s priority
        # order, so a soft stop (marginal gain / no-novel-queries) can no
        # longer defeat an outstanding coverage or corroboration gap.
        decision = depth_controller.decide(state)
        logger.info("depth_decision", decision=decision, iteration=int(state.get("iteration", 0)))
        if decision == "expand":
            return "planner"
        return "synthesizer"

    def route_after_intent(state: ResearchState) -> str:
        """R3/R5 branch: honour the router's decision, else research.

        `conversation` (R5) handles greetings/meta deterministically;
        `direct` (R3) takes the answer node; anything else — including a
        missing/garbage route — researches, the fail-safe direction
        (AGENTS.md 4.7).
        """
        route = state.get("route") or {}
        path = str(route.get("path", "") or "").lower()
        if path == "conversation":
            logger.info("route_conversation_branch")
            return "conversation"
        if path == "direct":
            logger.info("route_direct_branch", confidence=route.get("confidence"))
            return "direct_answer"
        # An unresolvable ambiguity stops the run with a clarifying question
        # rather than planning research under every reading. Checked after the
        # conversation/direct branches so a greeting or a stable-knowledge
        # question is never interrogated.
        ambiguity = state.get("ambiguity") or {}
        if str(ambiguity.get("action", "") or "") == "ask":
            logger.info("route_clarification_branch")
            return "clarification"
        return "planner"

    async def conversation_node(state: ResearchState) -> Dict[str, Any]:
        """Reply to a social/meta turn with no LLM and no research (R5).

        The reply was fixed deterministically by the router
        (`answer_sketch`); this node just surfaces it on the direct-answer
        channel so the finalize/report/stream paths are shared.
        """
        route = state.get("route") or {}
        reply = str(route.get("answer_sketch", "") or "").strip()
        return {
            "direct_answer": reply,
            "direct_answer_meta": {
                "conversation_kind": (route.get("signals") or {}).get("conversation_kind", ""),
                "reason": route.get("reason", ""),
                "confidence": 1.0,
            },
            "confidence": 1.0,
        }

    def route_after_direct(state: ResearchState) -> str:
        """R3 escape hatch: a delivered direct answer finalizes; a refusal or
        any missing answer falls through to the research planner."""
        if str(state.get("direct_answer", "") or "").strip():
            return "finalize"
        logger.info("direct_answer_fallthrough")
        return "planner"

    async def clarification_node(state: ResearchState) -> Dict[str, Any]:
        """Return a clarifying question INSTEAD of researching every reading.

        Reached only when the ambiguity policy says the readings would produce
        substantially different answers and nothing in the question chooses
        between them. The alternative — researching all of them — is what the
        reviewer kept flagging: six research programmes for a question whose
        terms were never pinned down, with no amount of extra evidence able to
        decide which one the user meant.

        The question is delivered on the direct-answer channel so the existing
        finalize / report / stream paths are shared, exactly like
        `conversation_node`. Confidence is 1.0: this is a complete, correct
        response to an underdetermined question, not a degraded answer.
        Nothing is fabricated — no reading is chosen, so no claim is made.
        """
        ambiguity = state.get("ambiguity") or {}
        question = str(ambiguity.get("question", "") or "").strip()
        readings = [str(x) for x in (ambiguity.get("interpretations") or []) if str(x).strip()]
        logger.info(
            "ambiguity_clarification", readings=len(readings), chars=len(question)
        )
        return {
            "direct_answer": question,
            "direct_answer_meta": {
                "kind": "clarification",
                "interpretations": readings,
                "reason": ambiguity.get("reason", ""),
                "confidence": 1.0,
            },
            "confidence": 1.0,
        }

    graph.add_node("intent", intent_node)
    graph.add_node("conversation", conversation_node)
    graph.add_node("clarification", clarification_node)
    graph.add_node("direct_answer", direct_answer_node)
    graph.add_node("planner", planner_node)
    graph.add_node("search", search_node)
    graph.add_node("summarizer", summarizer_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("critic", critic_node)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, entry_node if entry_node else "intent")
    if entry_node is None:
        # Understand-before-searching: intent runs before any plan is shaped.
        # R3: the router decides direct vs research; the direct branch goes
        # to the answer node (which may refuse and fall through to planner).
        graph.add_conditional_edges(
            "intent",
            route_after_intent,
            {
                "conversation": "conversation",
                "direct_answer": "direct_answer",
                "clarification": "clarification",
                "planner": "planner",
            },
        )
        graph.add_conditional_edges(
            "direct_answer",
            route_after_direct,
            {"finalize": "finalize", "planner": "planner"},
        )
        # R5: a conversation reply is already complete — straight to finalize.
        graph.add_edge("conversation", "finalize")
        # An unresolvable ambiguity is answered by the question itself — the run
        # stops here. No research pass is planned, because no amount of evidence
        # can decide which reading the user meant.
        graph.add_edge("clarification", "finalize")
    graph.add_edge("planner", "search")
    graph.add_edge("search", "summarizer")
    graph.add_edge("summarizer", "verifier")
    graph.add_edge("verifier", "critic")
    graph.add_conditional_edges("critic", route_after_critic, {"planner": "planner", "synthesizer": "synthesizer"})
    graph.add_edge("synthesizer", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
