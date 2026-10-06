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

import asyncio
import datetime
import re
from typing import Any, Dict, List, Mapping, Optional

from langgraph.graph import END, START, StateGraph

from app.agents.answer_conformance import check_answer_conformance
from app.agents.answer_quality import evaluate_answer
from app.agents.critic import critic_agent
from app.agents.direct_answer import direct_answer_agent
from app.agents.evidence_utils import (
    dedupe_semantic_facts,
    verify_answer_support,
)
from app.agents.intent import classify_intent, heuristic_intent
from app.agents.orchestrator import MODE_CONFIDENCE_TARGET, orchestrate
from app.agents.planner import normalize_text, planner_agent
from app.agents.redteam import redteam_agent
from app.agents.router import conversation_kind, deterministic_route, route_query
from app.agents.search import SearchClient
from app.agents.summarizer import summarizer_agent
from app.agents.synthesizer import synthesizer_agent
from app.agents.thesis_fidelity import check_thesis_fidelity
from app.agents.verifier import verify_facts
from app.core import depth_controller
from app.core.confidence import compute_confidence
from app.core.contradictions import find_contradictions
from app.core.degradation import has_provider_degradation, take_fallbacks
from app.core.investigation_state import (
    STATUS_CORROBORATED as STATUS_CORROBORATED,
    STATUS_EXHAUSTED as STATUS_EXHAUSTED,
)
from app.core.decision import build_decision_layer
from app.core.isolation import AgentContext, build_contexts
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


def _extract_question_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("question", "")).strip()
    return ""


def _merge_questions(existing: List[Any], new: List[Any]) -> List[Any]:
    """Append-only plan growth for expansion passes: keep every researched
    question, add genuinely new ones with continuing ids. Prevents the
    full-replan pattern where expansion discards the working plan."""
    seen = {normalize_text(_extract_question_text(q)) for q in existing or []} - {""}
    merged = list(existing or [])
    used_ids = [int(q.get("id", 0)) for q in merged if isinstance(q, dict)]
    next_id = max(used_ids) if used_ids else 0
    for item in new or []:
        text = normalize_text(_extract_question_text(item))
        if not text or text in seen:
            continue
        seen.add(text)
        next_id += 1
        merged.append({**item, "id": next_id} if isinstance(item, dict) else item)
    return merged


def _unanswered_questions(sub_questions: List[Any], search_results: List[Any]) -> List[str]:
    """Question texts with no results yet — expansion passes search only
    these instead of re-running the whole plan. NOTE: resume-rebuilt
    results lack sub_question keys, so a post-resume expansion re-searches
    once (safe fallback, not a loop — fresh results carry the key)."""
    answered = set()
    for r in search_results or []:
        if isinstance(r, dict):
            q = normalize_text(str(r.get("sub_question", "")))
            if q:
                answered.add(q)
    return [
        text for text in (_extract_question_text(i) for i in sub_questions or [])
        if text and normalize_text(text) not in answered
    ]


def _assess_focus(state: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Measure this pass against the ORIGINAL question, and aim the next one.

    Returns {"report", "summary", "queries"} or None when there is nothing to
    measure. Never raises: a failure here must not end a research run that has
    already gathered evidence, so it logs and returns None.

    The redirect is only issued when the run should actually change direction —
    `needs_redirect` returns False for a narrow question that is already covered,
    because widening a well-answered narrow question is the drift this is meant
    to prevent.
    """
    try:
        from app.agents.focus import (
            ResearchScope,
            assess_focus,
            needs_redirect,
            targeted_followups,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("[Focus] unavailable, skipping redirect: %s", exc, exc_info=exc)
        return None

    query = str(state.get("query", "") or "")
    plan = list(state.get("sub_questions", []) or [])
    facts = list(state.get("facts", []) or [])
    intent = state.get("intent") or {}
    iteration = int(state.get("iteration", 0) or 0)
    max_iterations = int(state.get("max_iterations", 3) or 3)

    try:
        scope = ResearchScope.from_plan(
            query, plan,
            query_type=str(intent.get("query_type", "") or ""),
            domain=str(intent.get("domain", "") or ""),
        )
        report = assess_focus(query, plan, facts, scope=scope)
        asked = state.get("executed_queries") or state.get("coverage_searched") or ()
        queries: List[str] = []
        if needs_redirect(report, iteration=iteration, max_iterations=max_iterations):
            # Bound inline rather than through Settings: this is a per-pass
            # steering cap, not operator-tunable configuration, and the
            # authoritative ceiling on follow-ups remains the depth controller's
            # budget check. Sizing it here would mean a new setting for a
            # constant the loop already bounds elsewhere.
            queries = targeted_followups(
                report, plan, limit=3, already_asked=asked,
            )
        return {
            "report": report.to_dict(),
            "summary": report.summary(),
            "queries": queries,
        }
    except Exception as exc:
        logger.warning(
            "[Focus] assessment failed, continuing without redirect: %s", exc, exc_info=exc
        )
        return None


# Nodes traversed per research pass after the first (planner→search→
# summarizer→verifier→critic) plus the intent/planner/search/summarizer/
# verifier/critic entry and the synthesizer→finalize tail. A pass budget is
# converted to a LangGraph superstep budget so a legitimate deep run cannot
# trip LangGraph's default recursion limit before route_after_critic's
# ceiling is ever reached.
_NODES_PER_PASS = 5
_GRAPH_ENTRY_AND_TAIL = 8


def graph_recursion_limit(state: ResearchState, extra: int = 4) -> int:
    """LangGraph superstep budget for a run.

    LangGraph's default recursion limit is 25, which a 5-pass deep run
    exceeds (intent + 5×(planner/search/summarizer/verifier/critic) +
    synthesizer/finalize ≈ 26+). Without this, the run aborts with
    GraphRecursionError before the routing-level iteration ceiling can
    finalize it. The budget is derived from the run's own ceiling plus a
    small margin, so a misconfigured `max_iterations` still cannot loop
    unbounded — route_after_critic is the actual stop, this is headroom.
    """
    max_iterations = max(1, int(state.get("max_iterations", 3) or 3))
    return _GRAPH_ENTRY_AND_TAIL + _NODES_PER_PASS * max_iterations + max(0, int(extra))


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
    """
    from app.agents.orchestrator import MODE_PRESETS, scaled_max_iterations

    preset = MODE_PRESETS.get(mode)
    if preset is not None:
        # A mode preset sets iterations explicitly — do NOT apply the
        # max(3, ...) floor (GAP-8) or quick mode would be no quicker.
        max_iterations = preset["max_iterations"]
        deep_research = preset["deep_research"]
        # The preset's agent cap REPLACES the setting default: deep and
        # executive are the only modes allowed to exceed MAX_PARALLEL_AGENTS
        # (vision §28: explicit opt-in via mode selection + governor check).
        max_parallel_agents = preset["max_agents"]
        effective_max_iterations = int(max_iterations)
    else:
        effective_max_iterations = max(3, int(max_iterations))
    plan = orchestrate(query, max_parallel_agents=max_parallel_agents,
                       deep_research=deep_research, mode=mode)
    # Fix B.1 — scale the deep/executive iteration budget to the map size now
    # that `orchestrate` has set target_agents. Bounded by scaled_max_iterations
    # (one pass per ~2 contracts, floored at 5); quick/standard unchanged.
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
        "mode": mode if preset is not None else "standard",
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

    async def intent_node(state: ResearchState) -> IntentUpdate:
        """Understand the question BEFORE shaping research (vision step 1).

        One grounding search on the raw query serves double duty: it gives the
        intent classifier real-world sense evidence, and the planner its
        terminology grounding (previously the planner ran this search itself
        on the raw query — the exact mechanism that pulled electrical-
        transformer statistics into an ML question's plan). Ambiguity is
        resolved here, never after the evidence is in.

        Latency: the grounding search runs ONLY on the research branch, and only
        AFTER the route decision. It used to run concurrently with the intent
        LLM call, which was correct while the two were independent — but its
        entire output is read by exactly one consumer, the planner
        (`planner_agent(context_snippets=...)`). Neither `conversation_node` nor
        `direct_answer_node` looks at it, and `classify_intent` is called
        without snippets. So on a greeting or a direct-answer turn the search
        was pure waste that `asyncio.gather` still made the node wait for.

        Measured: 19.7s of retrieval against 0.28s of LLM work, so a one-line
        "hi" took ~20s. Gating on the route drops the fast paths to the LLM
        cost and costs the research branch 0.28s against a run of 100s+.
        Behaviour-preserving: the only branch that reaches the planner is the
        one that runs the search, so the planner still gets its grounding.
        """
        # Latency: the grounding search and the intent LLM call are
        # independent — run them CONCURRENTLY. The classifier reads the
        # query's own phrasing (the primary signal; the forced-both policy
        # covers definitional ambiguity); the search results still ground
        # the planner, which was their original job.
        async def _context_search() -> List[str]:
            try:
                raw_results = await search_client.run_grounding_search(state["query"])
                return [
                    f"{str(r.get('title', '') or '').strip()}: {str(r.get('snippet', '') or '').strip()[:220]}"
                    for r in (raw_results or [])[:6]
                    if isinstance(r, dict) and (r.get("title") or r.get("snippet"))
                ]
            except Exception as exc:
                logger.warning("planner_context_search_failed", error=str(exc), exc_info=exc)
                return []

        # Latency: the route decision is CHEAP (two LLM calls, ~0.3s measured)
        # and it determines whether the grounding search is needed at all. The
        # search is the expensive part — a bare-string query is treated as a
        # contract, so `contract_queries` builds a primary-source variant and
        # the call fans out 2-3 site-scoped queries AND fetches page bodies
        # (~20s measured). Sequencing route-then-search spends 0.3s on the
        # research branch to save ~20s on both fast branches.
        intent_enabled = bool(getattr(llm.settings, "intent_enabled", True))

        # A conversational turn is settled by `conversation_kind`, a PURE
        # function with no model in it, and `route_query` already returns that
        # deterministic decision without spending an LLM call. But this node
        # used to run `classify_intent` FIRST, so "hi" paid a full LLM
        # round-trip to classify the intent of a greeting — then `route_query`
        # discarded the answer by deciding deterministically anyway.
        #
        # Nothing downstream needs the model's intent on this branch:
        # conversation_node reads only route["answer_sketch"], and
        # build_conversation_report reads only state["direct_answer"]. The
        # heuristic intent keeps state["intent"] populated for the trace at no
        # cost, which is the same deterministic fallback every other agent uses
        # (AGENTS.md 4.7). Removing this is the difference between a greeting
        # costing two serial LLM calls under Groq's rate limiter and costing
        # nothing.
        if conversation_kind(state["query"]):
            greeting_intent = heuristic_intent(state["query"]).to_dict()
            return {
                "intent": greeting_intent,
                "context_snippets": [],
                "route": deterministic_route(
                    state["query"], intent=greeting_intent
                ).to_dict(),
                # A greeting is never ambiguous, and the routing decision is made
                # by `conversation_kind` before ambiguity is consulted. Declared
                # explicitly so the update shape is complete.
                "ambiguity": {"action": "proceed", "interpretations": [],
                              "question": "", "reason": "conversational turn"},
            }

        async def _classify_and_route() -> tuple[Dict[str, Any], Dict[str, Any]]:
            # Router after classifier (it reads the ambiguity signal), and
            # both fail safe to "research" on any error.
            if intent_enabled:
                intent_dict = (await classify_intent(llm, state["query"])).to_dict()
            else:
                intent_dict = heuristic_intent(state["query"]).to_dict()
            route_decision = await route_query(
                llm, state["query"], intent=intent_dict
            )
            return intent_dict, route_decision.to_dict()

        intent_dict, route_dict = await _classify_and_route()

        # Only the research branch consumes grounding snippets (see docstring).
        # `route_after_intent` treats anything that is not an explicit
        # conversation/direct path as research, and so must this gate, or the
        # fail-safe direction would lose its grounding.
        route_path = str(route_dict.get("path", "") or "").lower()
        context_snippets: List[str] = []
        if route_path not in ("conversation", "direct"):
            context_snippets = await _context_search()

        # AMBIGUITY POLICY, decided here — after intent, before planning. The
        # intent layer says the question HAS several readings; this decides what to
        # do about it, which previously was nothing: the run acknowledged the
        # ambiguity and then researched every reading at once, so the reviewer
        # kept correctly noting that none of them defined the ask while searches
        # multiplied. More research cannot settle a definition.
        ambiguity = _decide_ambiguity(state["query"], intent_dict)

        return {
            "intent": intent_dict,
            "context_snippets": context_snippets,
            "route": route_dict,
            "ambiguity": ambiguity,
        }

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

    async def direct_answer_node(state: ResearchState) -> Dict[str, Any]:
        """Answer a stable-knowledge query without any research (R3).

        Reached only when route_after_intent chose the direct path. The
        agent may refuse (needs_research) — in that case this node records
        the refusal and route_after_direct sends the query on to the normal
        planner, so a wrong routing decision costs one call, not an answer.

        Confidence is the model's OWN self-assessment, capped strictly below
        the research sufficiency threshold: an ungrounded answer must never
        read as a sourced one. No facts, no verification, no citations —
        the report states plainly that this was a direct answer.
        """
        result = await direct_answer_agent(
            llm, state["query"], intent=state.get("intent") or {}
        )
        meta = result.to_dict()

        if not result.usable:
            # Escape hatch: the agent refused, was unsure, or failed. Record
            # the refusal and let routing fall through to the research path.
            logger.info(
                "direct_answer_refused",
                needs_research=result.needs_research,
                confidence=result.confidence,
            )
            return {"direct_answer": "", "direct_answer_meta": meta}

        # Cap: strictly below the research sufficiency threshold (0.75) even
        # at full self-confidence. An ungrounded answer must never read as a
        # sourced one, and the cap is configurable so the gap is explicit.
        cap = float(
            getattr(
                getattr(llm, "settings", None),
                "direct_answer_confidence_cap",
                0.55,
            )
            or 0.55
        )
        capped = min(float(result.confidence), cap)
        logger.info(
            "direct_answer_delivered",
            chars=len(result.answer),
            self_confidence=result.confidence,
            capped_confidence=round(capped, 3),
        )
        return {
            "direct_answer": result.answer,
            "direct_answer_meta": meta,
            "confidence": capped,
        }

    async def planner_node(state: ResearchState) -> PlannerUpdate:
        existing = state.get("sub_questions", []) or []
        feedback = state.get("critique_feedback", "")
        expanding = bool(existing) and int(state.get("iteration", 0)) > 0
        if expanding:
            already = "; ".join(
                _extract_question_text(q) for q in existing if _extract_question_text(q)
            )
            feedback = (
                feedback
                + "\nAlready researched (do not repeat — only add gap-closing questions): "
                + already
            ).strip()

        # Search-informed planning (gpt-researcher parity): the grounding
        # search on the raw query ran once in intent_node; expansion passes
        # already have critique feedback to aim at and skip the context.
        context_snippets: List[str] = []
        if not expanding:
            context_snippets = [
                str(s) for s in (state.get("context_snippets") or []) if str(s).strip()
            ]

        # Intent (understand-before-searching): senses, domain and explanation
        # level resolved before planning. The planner targets the user's
        # likely meaning instead of whatever the raw query string retrieves.
        intent = state.get("intent") or {}

        # v3 plan targets live on the orchestration dict (build_initial_state).
        orchestration = state.get("orchestration", {})
        if expanding:
            # FIXED: required_axes on expansion are the MISSING dimensions, not
            # the existing ones.
            #
            # The previous version seeded this with every axis ALREADY under
            # research, which told planner_agent to "guarantee a contract for
            # each of these" — for dimensions that already had one. Because
            # planner_agent only sees the new plan (not `existing`), it injected
            # a fresh contract per covered axis under a different question text,
            # which `_merge_questions` then appended as new. The plan grew to 8
            # contracts with every axis duplicated, and each pass spent its
            # search budget re-covering the dimensions that were already done —
            # the exact "still searching technical capability" behaviour.
            #
            # The gaps come from the focus report, which derives them from the
            # plan for THIS question (see app/agents/focus.py). Domain-agnostic.
            focus_report = (state.get("focus") or {}).get("report") or {}
            required_axes = []
            for dimension in (
                list(focus_report.get("missing") or ())
                + list(focus_report.get("thin") or ())
            ):
                name = str(dimension or "").strip()
                if name and name not in required_axes:
                    required_axes.append(name)
        else:
            required_axes = list(orchestration.get("required_axes", []) or [])
        sub_questions = await planner_agent(
            llm=llm,
            query=state["query"],
            critique_feedback=feedback,
            today=datetime.date.today().isoformat(),
            context_snippets=context_snippets or None,
            intent=intent or None,
            # v3 plan targets from orchestration: the plan is sized and
            # axis-shaped here; the hardware cap below stays as backstop.
            target_count=int(orchestration.get("target_sub_questions", 0) or 0) or None,
            required_axes=required_axes,
            minimum_sources=int(orchestration.get("min_sources_per_axis", 0) or 0) or 2,
        )
        # Hardware guardrail: cap the plan at the orchestrated target agents.
        target = int(orchestration.get("target_agents", 5) or 5)
        if expanding:
            # Per-axis expansion: keep researched history, cap only the NEW
            # additions at target so per-pass load stays bounded.
            merged = _merge_questions(existing, sub_questions)
            added = merged[len(existing):][: max(1, target)]
            sub_questions = [*existing, *added]
        else:
            sub_questions = sub_questions[: max(1, target)]

        # Axes the PLANNER just proposed fresh contracts for this pass. Gap
        # injection must not re-angle these: the planner's new question is
        # already a fresh angle, and superseding it would discard work proposed
        # in the same pass (it also made a fresh contract unreachable).
        def _axis_key(value: Any) -> str:
            """Canonical axis identity, so 'enterprise adoption' and
            'enterprise_adoption' compare equal — the same canonicalization the
            planner, the critic and coverage all use."""
            from app.agents.planner import dimension_to_axis

            return dimension_to_axis(
                str(value or "").strip().lower().replace(" ", "_")
            )

        # Contracts the PLANNER just proposed this pass. Gap injection must not
        # re-angle a dimension the planner has already produced a fresh contract
        # for: that contract IS the new angle, and superseding it discards work
        # proposed in the same call. Matched on the planner's OUTPUT questions,
        # which is exact, rather than on axes, which collide when a plan labels
        # several contracts with the same dimension.
        if expanding:
            prior_questions = {
                normalize_text(str(c.get("question", "") or ""))
                for c in (existing or [])
                if isinstance(c, dict)
            }
            freshly_planned = {
                normalize_text(str(c.get("question", "") or ""))
                for c in (sub_questions or [])
                if isinstance(c, dict)
            } - prior_questions
            freshly_planned_axes = {
                _axis_key(c.get("axis"))
                for c in (sub_questions or [])
                if isinstance(c, dict)
                and normalize_text(str(c.get("question", "") or "")) in freshly_planned
            }
        else:
            freshly_planned_axes = set()

        # GAP → TASK CONVERSION. The reviewer measures which planned dimensions
        # have no (or thin) evidence, but a measurement is not a research task.
        # Without this, the expansion plan is drawn from the axes ALREADY
        # researched (see required_axes above) plus whatever the model happens
        # to volunteer, so the same gaps are re-reported every round while
        # search keeps returning the same material. Emitting a contract per
        # missing dimension gives it an axis, which makes it a sub-question that
        # search_node executes, which is what moves coverage.
        #
        # Injected AFTER the cap and excluded from it: gap-closing contracts are
        # the reason this pass exists, so truncating them away would restore the
        # exact non-convergence being fixed.
        gap_report = (state.get("focus") or {}).get("report") or {}
        gap_missing = list(gap_report.get("missing") or ())
        gap_thin = list(gap_report.get("thin") or ())
        if gap_missing or gap_thin:
            try:
                from app.agents.planner import gap_contracts

                next_index = 1 + max(
                    (
                        int(q.get("id", 0))
                        for q in sub_questions
                        if isinstance(q, dict) and str(q.get("id", "")).strip().isdigit()
                    ),
                    default=0,
                )
                gap_missing = [
                    d for d in gap_missing
                    if _axis_key(d) not in freshly_planned_axes
                ]
                gap_thin = [
                    d for d in gap_thin
                    if _axis_key(d) not in freshly_planned_axes
                ]
                gaps = gap_contracts(
                    query=state["query"],
                    missing=gap_missing,
                    thin=gap_thin,
                    existing=sub_questions,
                    start_index=next_index,
                    domain=str((state.get("intent") or {}).get("domain", "") or "general"),
                    minimum_sources=int(orchestration.get("min_sources_per_axis", 0) or 0) or 2,
                    today=datetime.date.today().isoformat(),
                    # Bounded by the number of measured gaps, NOT by the agent
                    # target: gap-closing work is not subject to the plan cap,
                    # because truncating it away is the non-convergence this
                    # exists to fix. The per-pass search budget still bounds
                    # total spend.
                    limit=max(1, len(gap_missing) + len(gap_thin)),
                )
                if gaps:
                    # Replace, do not accumulate. A gap contract supersedes any
                    # earlier contract for the SAME dimension: that dimension was
                    # measured uncovered, so the older contract already failed to
                    # produce evidence and keeping it just spends budget twice on
                    # one dimension (the plan had reached 8 contracts with every
                    # axis duplicated). Contracts for other dimensions are kept.
                    gap_axes = {str(c.get("axis", "") or "") for c in gaps}
                    # Only supersede a contract that actually FAILED to produce
                    # results. A same-axis contract that WAS answered is kept:
                    # the new contract re-angles the ask, which is additive
                    # evidence, not a replacement for what already worked.
                    answered_questions = {
                        normalize_text(str(r.get("sub_question", "") or ""))
                        for r in (state.get("search_results") or [])
                        if isinstance(r, dict)
                    } - {""}
                    retained = [
                        q for q in sub_questions
                        if not (
                            isinstance(q, dict)
                            and str(q.get("axis", "") or "") in gap_axes
                            and normalize_text(str(q.get("question", "") or ""))
                            not in answered_questions
                        )
                    ]
                    replaced = len(sub_questions) - len(retained)
                    # Prepend: these are this pass's priority, and search_node
                    # issues queries in plan order within its per-pass budget.
                    sub_questions = [*gaps, *_merge_questions(retained, [])]
                    logger.info(
                        "planner_gap_contracts",
                        missing=len(gap_missing),
                        thin=len(gap_thin),
                        injected=[c["axis"] for c in gaps],
                        replaced=replaced,
                    )
            except Exception as exc:
                logger.warning(
                    "planner_gap_injection_failed", error=str(exc), exc_info=exc
                )
        # Wave structure (Feature 03): dependency-ordered groups the
        # summarizer executes sequentially, passing earlier-wave findings to
        # dependent contracts. Exposed on state so the UI can show the plan's
        # shape and the benchmark suite can verify wave execution.
        try:
            from app.agents.planner import execution_waves
            waves = execution_waves(sub_questions)
            wave_shape = [
                [q.get("question", "") if isinstance(q, dict) else str(q) for q in wave]
                for wave in waves
            ]
        except Exception:
            wave_shape = [[_extract_question_text(q) for q in sub_questions]]
        logger.info("planner_done", sub_questions=len(sub_questions), expanding=expanding,
                    waves=len(wave_shape))
        return {"sub_questions": sub_questions, "execution_waves": wave_shape}

    async def search_node(state: ResearchState) -> SearchUpdate:
        previous = [r for r in state.get("search_results", []) or [] if isinstance(r, dict)]
        # Per-axis expansion: search only questions with no results yet.
        # Each unanswered parent fans out to its alternate phrasings
        # (variants ride the parent contract, so nothing orphans).
        # Accumulation is explicitly capped (SEARCH_MAX_RESULTS_RETAINED) and
        # the verifier blanks raw content after each pass.
        settings = getattr(search_client, "settings", None)
        fresh: List[Any] = []
        answered = {
            normalize_text(str(r.get("sub_question", ""))) for r in previous
        } - {""}
        by_text = {}
        for item in state.get("sub_questions", []) or []:
            text = _extract_question_text(item)
            if text and text not in by_text:
                by_text[text] = item

        def _question_search_type(text: str) -> str:
            """The contract's search_type steers retrieval (news topic vs
            general). Variants inherit their parent's type — they are
            rephrasings, not new contracts."""
            item = by_text.get(text)
            if isinstance(item, dict):
                return str(item.get("search_type", "") or "")
            return ""

        for text in _unanswered_questions(state.get("sub_questions", []), previous):
            fresh.append((text, _question_search_type(text)))
            item = by_text.get(text)
            if isinstance(item, dict):
                parent_type = _question_search_type(text)
                for v in item.get("variants", []) or []:
                    vs = str(v or "").strip()
                    if vs and normalize_text(vs) not in answered:
                        fresh.append((vs, parent_type))

        # Fix A.3 — corroboration procurement must actually be EXECUTED, not
        # merely stored on the critique. These claim-specific, publisher-
        # excluding queries are injected straight into this pass's search set on
        # expansion passes, independent of whether the planner model chose to
        # turn critique feedback into a contract. Deduped against everything
        # already searched.
        cap = max(1, int(getattr(settings, "search_max_queries_per_pass", 8) or 8))
        corroboration_to_run: List[str] = []
        if int(state.get("iteration", 0)) > 0:
            # Central investigation allocator (app/core/investigation_planner.py):
            # instead of a fixed-order concatenation of the corroboration /
            # counter-evidence / primary-source channels, rank every candidate
            # investigation by expected value and take the top-K within the
            # per-pass budget. Deterministic fallback: when the allocator yields
            # nothing (empty/garbage state, all targets exhausted, every query
            # already executed), revert to the historical channel list exactly
            # as before — existing behavior is preserved, never weakened.
            allocation: Dict[str, Any] = {"selected": []}
            try:
                from app.core.investigation_planner import select_investigations

                allocation = select_investigations(state, budget_cap=cap)
            except Exception as exc:  # allocator failure must not break search
                logger.warning("investigation_allocator_failed", error=str(exc), exc_info=exc)
                allocation = {"selected": []}
            alloc_queries = [
                str(c.get("query", "") or "").strip()
                for c in (allocation.get("selected") or [])
                if isinstance(c, dict) and str(c.get("query", "") or "").strip()
            ]
            if alloc_queries:
                for text in alloc_queries:
                    key = normalize_text(text)
                    if not key or key in answered:
                        continue
                    corroboration_to_run.append(text)
                    answered.add(key)
            else:
                for q in state.get("corroboration_queries", []) or []:
                    text = str(q or "").strip()
                    key = normalize_text(text)
                    if not text or not key or key in answered:
                        continue
                    corroboration_to_run.append(text)
                    answered.add(key)

        # GAP-FIRST ALLOCATION of the per-pass query cap.
        #
        # The plan's own questions used to be truncated to `cap - len(
        # corroboration_to_run)` BEFORE corroboration was appended, so a pass
        # with many corroboration queries left almost no room for the plan.
        # Measured in a live run: 3 dimensions were reported missing, 3
        # gap contracts were planned, and only 2 reached search_node — the third
        # was crowded out by `(attempt 2)` / `site:arxiv.org` queries aimed at
        # the dimension that was ALREADY covered. That is the reported symptom
        # of searches continuing to concentrate on the covered direction.
        #
        # So contracts for dimensions the focus report named as missing get first
        # claim on the cap. Domain-agnostic: the protected set is read from the
        # focus report, which derives it from the plan.
        gap_axes = {
            str(a)
            for a in ((state.get("focus") or {}).get("report") or {}).get("missing", ())
        } | {
            str(a)
            for a in ((state.get("focus") or {}).get("report") or {}).get("thin", ())
        }

        def _axis_of(text: str) -> str:
            item = by_text.get(text)
            return str(item.get("axis", "") or "") if isinstance(item, dict) else ""

        if gap_axes:
            gap_fresh = [f for f in fresh if _axis_of(f[0]) in gap_axes]
            other_fresh = [f for f in fresh if _axis_of(f[0]) not in gap_axes]
        else:
            gap_fresh, other_fresh = [], list(fresh)

        # Gap-closing queries are never truncated by the cap: they are the
        # reason this pass exists, and dropping them re-creates the
        # non-convergence being fixed. Everything else competes for what is left,
        # corroboration included.
        gap_fresh = gap_fresh[:cap]
        remaining = max(0, cap - len(gap_fresh))
        room = max(0, remaining - len(corroboration_to_run))
        fresh = [*gap_fresh, *other_fresh[:room]]
        fresh.extend((q, "general") for q in corroboration_to_run)
        if not fresh:
            if previous:
                return {"search_results": previous}
            fallback = state.get("query", "").strip()
            if not fallback:
                return {"search_results": previous}
            fresh = [(fallback, "")]

        # Fix B.3 — hard per-run expansion wall. Count the expansion passes and
        # the extra searches they issue; once either cap is hit, stop issuing
        # NEW expansion searches (the evidence already gathered still flows on).
        prior_passes = int(state.get("expansion_passes", 0) or 0)
        is_expansion = int(state.get("iteration", 0)) > 0

        # Executed-query memory (perf): an identical query text returns the
        # same results, so re-issuing it across passes spends a full retrieval
        # round-trip for zero new evidence. The depth controller already builds
        # this memory (`coverage_searched` -> _searched_queries) but nothing
        # ever WROTE it, so primary-source/corroboration follow-ups regenerated
        # the same query every pass and were executed each time. Enforce the
        # memory here and record what actually runs. EXACT normalized-text
        # matching only: a differently-worded query is never dropped, so no
        # research path or source is lost.
        prior_executed = {
            normalize_text(str(q))
            for q in (state.get("executed_queries") or [])
            if str(q).strip()
        }
        prior_executed |= {
            normalize_text(str(q))
            for q in (state.get("coverage_searched") or [])
            if str(q).strip()
        }
        seen_executed = set(prior_executed)
        deduped: List[Any] = []
        for item in fresh:
            text = item[0] if isinstance(item, tuple) else str(item)
            key = normalize_text(str(text))
            if not key or key in seen_executed:
                continue
            seen_executed.add(key)
            deduped.append(item)
        dropped_repeats = len(fresh) - len(deduped)
        if dropped_repeats:
            logger.info("search_queries_deduped", dropped=dropped_repeats)
        fresh = deduped
        # Nothing novel left to search: keep the already-gathered evidence
        # flowing rather than re-running a query this run already issued.
        if not fresh and previous:
            return {"search_results": previous, "expansion_passes": prior_passes}

        max_passes = max(1, int(getattr(settings, "max_expansion_passes", 12) or 12))
        max_searches = max(1, int(getattr(settings, "max_expansion_searches", 48) or 48))
        expansion_passes = prior_passes + (1 if is_expansion else 0)
        budget_stop = is_expansion and (
            expansion_passes > max_passes
            or prior_passes * cap + len(fresh) > max_searches
        )
        if budget_stop:
            logger.info(
                "expansion_wall_reached",
                expansion_passes=expansion_passes,
                max_expansion_passes=max_passes,
                iteration=int(state.get("iteration", 0)),
            )
            return {
                "search_results": previous,
                "expansion_passes": prior_passes,
            }
        results = await search_client.run_search(fresh)
        # Track whether a disagreement-seeking/corroboration query was actually
        # issued — the stopping redesign requires counter-evidence to have been
        # attempted, and this is the only point that knows what really ran.
        counter_markers = (
            "conflicting evidence", "disagreement", "independent corroboration",
            "counter-evidence", "counter evidence", "verification",
            "independent source", "official report", "-site:",
        )
        issued_counter = any(
            any(marker in str(text).lower() for marker in counter_markers)
            for text, _ in fresh
        )
        seen_urls = {r.get("url") for r in previous if r.get("url")}
        merged = [*previous, *(r for r in results if r.get("url") not in seen_urls)]
        # Hard memory bound: expansion passes append results forever, so a
        # long deep run could otherwise accumulate hundreds of result dicts
        # in state (raw content is blanked after verification, but the list
        # and its metadata still grow). Keep the NEWEST results — the ones
        # the current pass's summarizer needs — and drop the oldest beyond
        # the cap. Verification already ran on older passes, so nothing
        # downstream loses content it still needs.
        cap_results = max(10, int(getattr(settings, "search_max_results_retained", 80) or 80))
        if len(merged) > cap_results:
            dropped = len(merged) - cap_results
            merged = merged[dropped:]
            logger.info("search_results_capped", dropped=dropped, retained=len(merged))
        logger.info("search_done", results=len(merged), fresh=len(fresh))
        # Corroboration LINKING: fresh expansion results are matched back to
        # pending needs_corroboration facts HERE, at the moment the new pages
        # exist. Previously the results were merely merged into state (and the
        # measurement primitives existed) but nothing ever matched a fresh
        # result to the claim that needed it, so corroborating_sources stayed
        # at one publisher and corroborated_ge2 was always 0. Annotate copies
        # of the facts so no fact is dropped and no same-publisher URL can
        # raise the count (find_corroborating_sources/apply_corroboration
        # enforce registrable-domain independence).
        updated_facts: List[Dict[str, Any]] = [
            f for f in (state.get("facts", []) or []) if isinstance(f, dict)
        ]
        matched_corroboration = 0
        try:
            from app.core.evidence_grade import (
                apply_corroboration,
                find_corroborating_sources,
                grade_claim,
                registrable_domain,
            )

            existing_domains = {
                registrable_domain(str(f.get("source", "") or ""))
                for f in updated_facts
            }
            new_publishers = sum(
                1
                for r in results
                if isinstance(r, dict)
                and registrable_domain(str(r.get("url", "") or ""))
                and registrable_domain(str(r.get("url", "") or ""))
                not in existing_domains
            )
            settings = getattr(search_client, "settings", None)
            threshold = float(getattr(settings, "corroboration_similarity", 0.55) or 0.55)
            annotated: List[Dict[str, Any]] = []
            for fact in updated_facts:
                claim = str(fact.get("claim", "") or "").strip()
                source = str(fact.get("source", "") or "").strip()
                if not claim or not source:
                    annotated.append(fact)
                    continue
                record = grade_claim(fact)
                if not (record.needs_corroboration or record.corroboration_count < 2):
                    annotated.append(fact)
                    continue
                existing_urls = [
                    str(u) for u in (fact.get("corroborating_sources") or [])
                    if str(u).strip()
                ]
                existing_urls.append(source)
                matches = find_corroborating_sources(
                    claim, results, existing_urls, threshold=threshold
                )
                gained = 0
                copy = dict(fact)
                for url in matches:
                    if apply_corroboration(copy, url):
                        gained += 1
                if gained:
                    matched_corroboration += 1
                    annotated.append(copy)
                else:
                    annotated.append(fact)
            updated_facts = annotated
            logger.info(
                "corroboration_funnel",
                queries_issued=len(fresh),
                results_returned=len(results),
                new_publishers=new_publishers,
                matched_corroboration=matched_corroboration,
            )
        except Exception as exc:
            logger.warning(
                "corroboration_linking_failed", error=str(exc), exc_info=exc
            )
            updated_facts = [
                f for f in (state.get("facts", []) or []) if isinstance(f, dict)
            ]

        update: SearchUpdate = {
            "search_results": merged,
            "counter_evidence_attempted": bool(
                state.get("counter_evidence_attempted") or issued_counter
            ),
            "expansion_passes": expansion_passes,
            # Persist the executed-query memory: everything prior plus the
            # queries this pass actually issued. Mirrored under
            # `coverage_searched`, the key the depth controller's
            # `_searched_queries()` reader already consumes (it was read but
            # never written before this).
            "executed_queries": sorted(seen_executed),
            "coverage_searched": sorted(seen_executed),
        }
        if matched_corroboration:
            update["facts"] = updated_facts
        return update

    async def summarizer_node(state: ResearchState) -> SummarizerUpdate:
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

    async def verifier_node(state: ResearchState) -> VerifierUpdate:
        verified = verify_facts(
            facts=state.get("facts", []),
            search_results=state.get("search_results", []),
        )
        # Temporal integrity gate (hard validation against the system clock):
        # future-dated ASSERTED events are dropped, future-dated projections are
        # kept but labelled. Runs after verification so it sees published_at
        # that verification surfaced.
        from app.core.temporal import apply_temporal_gate

        verified, temporal_stats = apply_temporal_gate(verified)
        stats = {
            "total": len(verified),
            "verified": sum(1 for f in verified if f.get("verified")),
            "temporal_dropped": temporal_stats.get("dropped", 0),
            "temporal_projections": temporal_stats.get("projections", 0),
        }

        # Raw content is discarded once verification completes — verification
        # is the last consumer of full page text (it checks claims against
        # content, not just snippets); nothing downstream (critic, synthesizer,
        # finalize, later iterations) needs the full page. Blank in place: these
        # dict objects are shared with the contexts built in summarizer_node.
        # Before blanking, retain a BOUNDED excerpt: the expansion-pass
        # corroboration linker runs after verification and otherwise has only
        # short snippets to match a pending claim against. The excerpt is a
        # few hundred chars (memory-release rule still holds — the full page is
        # released), falling back to the snippet when content was already empty.
        from app.core.evidence_grade import CORROBORATION_EXCERPT_CHARS

        for result in state.get("search_results", []):
            if not isinstance(result, dict) or "content" not in result:
                continue
            raw = str(result.get("content", "") or "")
            if not raw:
                raw = str(result.get("snippet", "") or "")
            if raw and not result.get("corroboration_excerpt"):
                result["corroboration_excerpt"] = re.sub(
                    r"\s+", " ", raw
                ).strip()[:CORROBORATION_EXCERPT_CHARS]
            result["content"] = ""

        logger.info("verifier_done", **stats)
        return {"facts": verified, "verification_stats": stats}

    async def critic_node(state: ResearchState) -> CriticUpdate:
        next_iteration = int(state.get("iteration", 0)) + 1

        # Contradiction Engine (3.2) computed HERE (not in verifier) so the
        # resume path — which re-enters at critic with persisted facts —
        # still feeds contradictions to the critic and the report.
        contradictions = find_contradictions(state.get("facts", []))
        # Fix C — resolution pass. A temporal or scope difference is an
        # EXPLAINED spread, not a disagreement; it is recorded for the report
        # but must not penalize confidence or drive further expansion. Only
        # genuinely conflicting (same unit/scope/period/metric, different
        # values) entries stay `resolved: false`.
        try:
            from app.core.contradiction_resolution import resolve_contradictions

            contradictions = resolve_contradictions(contradictions)
        except Exception as exc:  # resolution must never break a run
            logger.warning("contradiction_resolution_failed", error=str(exc), exc_info=exc)
        if contradictions:
            logger.info(
                "contradictions_found",
                count=len(contradictions),
                unresolved=sum(1 for c in contradictions if not c.get("resolved")),
            )

        # Red-team review (v3, heuristics only: deterministic, zero LLM
        # cost). Attacks the evidence base every pass; the survival score
        # feeds the critic gate and the findings render in the report.
        # Never fatal: heuristics must not break a run.
        try:
            redteam_state = (
                await redteam_agent(
                    None,
                    state["query"],
                    state.get("facts", []),
                    contradictions=contradictions,
                    use_llm=False,
                )
            ).to_dict()
        except Exception as exc:
            logger.warning("redteam_heuristics_failed", error=str(exc), exc_info=exc)
            redteam_state = {
                "findings": [], "survival_score": 0.6, "survives": True,
                "blocking": [], "targeted_queries": [], "summary": "",
            }

        # The critic's optional gate inputs, wired (they were built and tested
        # but never passed, so the coverage-gap gate could never fire, the
        # prompt never knew which searches already ran, and mode confidence
        # targets were honored only by the depth controller, not the gate).
        searched: List[str] = []
        for q in state.get("sub_questions", []) or []:
            if isinstance(q, dict):
                text = str(q.get("question", "")).strip()
                if text:
                    searched.append(text)
                for v in q.get("variants", []) or []:
                    vs = str(v or "").strip()
                    if vs:
                        searched.append(vs)
        mode_target = MODE_CONFIDENCE_TARGET.get(str(state.get("mode", "") or "standard"))

        # Focus assessment runs BEFORE the critic so the critic can gate on
        # drift and concentration, and the same report drives the redirect
        # queries below. Computed once per pass and reused.
        focus_state = _assess_focus(state)

        critique = await critic_agent(
            llm=llm,
            query=state["query"],
            facts=state.get("facts", []),
            iteration=next_iteration,
            max_iterations=int(state.get("max_iterations", 3)),
            contradictions=contradictions,
            query_type=str(state.get("orchestration", {}).get("query_type", "")),
            redteam_survival=float(redteam_state.get("survival_score", 0.6) or 0.6),
            plan=state.get("sub_questions", []),
            searched_queries=searched,
            confidence_target=mode_target,
            # Quick mode: the iteration ceiling makes the verdict
            # routing-neutral, so the critic runs gates-only (no LLM call)
            # and measured evidence stats stand in for the model verdict —
            # one fewer serial LLM call on the latency-sensitive mode.
            use_llm=str(state.get("mode", "standard")) != "quick",
            focus_report=(focus_state or {}).get("report") or None,
        )

        # Confidence Engine (Phase 2.4) replaces the inline weighted formula.
        # Degraded stages cap the score: a run whose evidence came from the
        # extractive fallback must not finalize as "High" confidence.
        source_dates = [
            r.get("published_at", "") for r in state.get("search_results", []) or []
            if isinstance(r, dict) and r.get("published_at")
        ]
        # Epistemics: adjudicated conflicts + claim standards measured once and
        # reused by the synthesizer (it reads ctx["epistemics"]). Without this
        # the confidence engine only sees a raw contradiction count, which
        # overcounts time-series/scope artifacts and leaves the conflict,
        # asymmetry and staleness caps dead on live runs.
        epistemics = state.get("epistemics")
        if epistemics is None:
            from app.agents.epistemics import assess_epistemics

            epistemics = assess_epistemics(
                state["query"], state.get("facts", []), contradictions
            )
            state["epistemics"] = epistemics

        breakdown = compute_confidence(
            facts=state.get("facts", []),
            critique=critique,
            iteration=next_iteration,
            max_iterations=int(state.get("max_iterations", 3)),
            source_dates=source_dates,
            degraded=take_fallbacks(),
            # v3 signal wiring: conflicts penalize, the previous synthesis's
            # support rate blends in, and unanswered plan axes cap the score.
            contradictions=contradictions,
            answer_support=state.get("answer_support"),
            sub_questions=state.get("sub_questions", []),
            epistemics=epistemics,
            query=state["query"],
            # A provider outage must not inflate confidence: the extractive
            # fallback self-verifies, so transport failures are capped like
            # degraded extraction (reliability #4).
            provider_degraded=has_provider_degradation(),
        )
        overall_conf = breakdown["overall"]

        improved = list(critique.get("improved_queries", []) or [])

        # FOCUS REDIRECT (drift + concentration). Everything above asks for more
        # evidence on what the run already found; nothing asked whether the run
        # is still working on the user's question. When research has piled onto
        # one dimension, or wandered off the query, these queries aim at the
        # dimensions that are MISSING, derived from the plan for THIS question.
        # They are placed BEFORE the evidence-first queries so a redirected
        # search is not crowded out by corroboration work on an already-covered
        # dimension.
        if focus_state is not None:
            for q in focus_state.get("queries", ()):
                if q and q not in improved:
                    improved.append(q)

        # Evidence-first: when the pool has uncorroborated or contradicted
        # claims, add disagreement-seeking queries so the expansion loop
        # researches the weakest evidence, not just more supporting pages.
        for q in _counter_evidence_queries(state):
            if q not in improved:
                improved.append(q)
        # Fix A — corroboration PROCUREMENT. Independent corroboration was
        # measured but never sought; these claim-specific, publisher-excluding
        # queries are executed directly by search_node (not left to the planner
        # model to rephrase). They also ride improved_queries so the stopping
        # policy can see them.
        corroboration_queries, corroboration_registry = _corroboration_queries(
            state, settings=getattr(llm, "settings", None)
        )
        # Per-claim INVESTIGATION state (closing the adaptive loop): record this
        # pass's targeted attempts against the claim they were issued for, then
        # reconcile each tracked claim's OUTCOME against the (re-graded) pool —
        # corroborated claims leave the open set, still-single-source claims
        # with their budget spent become exhausted and are surfaced as
        # limitations. The state is run-scoped (threaded through LangGraph) with
        # no module-level store; any failure logs and leaves the prior state.
        try:
            from app.core.investigation_state import record_attempts, reconcile_outcomes

            settings_for_inv = getattr(llm, "settings", None)
            inv_max_attempts = max(
                1, int(getattr(settings_for_inv, "max_corroboration_attempts", 2) or 2)
            )
            queries_by_claim = {
                str(k): list(v.get("issued") or [])
                for k, v in (corroboration_registry or {}).items()
                if isinstance(v, dict) and v.get("issued")
            }
            # The claims that actually received a targeted query this pass are
            # exactly the registry entries carrying `issued` text.
            attempted_targets = [
                {"claim": str(v.get("claim", "") or "")}
                for v in (corroboration_registry or {}).values()
                if isinstance(v, dict) and v.get("issued")
            ]
            inv_state = record_attempts(
                state.get("investigation_state"),
                attempted_targets,
                queries_by_claim=queries_by_claim,
                max_attempts=inv_max_attempts,
            )
            # DIMENSION attempts, in the same ledger and under a namespaced key.
            # Every dimension this pass actually searched is charged one attempt;
            # after its budget the dimension is exhausted and stops generating
            # candidates. Without this, a dimension that never yields evidence is
            # re-searched indefinitely — the run reached 80 sources with the same
            # gaps open, because the channel regenerated the question with a new
            # "(attempt N)" suffix that no query memory could match.
            from app.core.investigation_state import record_dimension_attempts

            searched_dimensions: List[str] = []
            for _query_text in (state.get("coverage_searched") or []) + (
                state.get("executed_queries") or []
            ):
                _text = str(_query_text or "").lower()
                for _dim in (
                    list((state.get("focus") or {}).get("report", {}).get("missing") or ())
                    + list((state.get("focus") or {}).get("report", {}).get("thin") or ())
                ):
                    _name = str(_dim or "").replace("_", " ")
                    if _name and _name in _text and _dim not in searched_dimensions:
                        searched_dimensions.append(_dim)
            # Fall back to the measured gap sets when query text matching finds
            # nothing: the report already says these are uncovered, and they were
            # the target of this pass's gap contracts.
            if not searched_dimensions:
                searched_dimensions = list(
                    (state.get("focus") or {}).get("report", {}).get("missing") or ()
                ) + list(
                    (state.get("focus") or {}).get("report", {}).get("thin") or ()
                )
            if searched_dimensions:
                inv_state = record_dimension_attempts(
                    inv_state,
                    searched_dimensions,
                    max_attempts=max(
                        1, int(getattr(settings_for_inv, "max_dimension_attempts", 2) or 2)
                    ),
                )
            inv_state = reconcile_outcomes(
                inv_state, state.get("facts", []), max_attempts=inv_max_attempts
            )
        except Exception as exc:
            logger.warning("investigation_state_failed", error=str(exc), exc_info=exc)
            inv_state = state.get("investigation_state") or {}
        # Primary-source completion (workstream A): a dimension whose evidence
        # is thin on primary/official publishers gets a targeted primary query
        # on the next pass. These reuse the same per-pass search channel as the
        # corroboration queries (never a new research loop) and are appended
        # after them so claim-specific procurement keeps priority.
        try:
            from app.core.evidence_completion import primary_source_followups

            attempt = max(0, int(state.get("iteration", 0)))
            for q in primary_source_followups(
                state.get("facts", []) or [],
                state.get("sub_questions", []) or [],
                limit=2,
                attempt=attempt,
            ):
                if q not in corroboration_queries:
                    corroboration_queries.append(q)
        except Exception as exc:
            logger.warning("primary_followup_generation_failed", error=str(exc), exc_info=exc)
        for q in corroboration_queries:
            if q not in improved:
                improved.append(q)
        # Freeze the augmented follow-ups on the critique so the depth
        # controller's novel-query check sees the counter-evidence queries too;
        # previously they existed only in critique_feedback and were invisible
        # to the stopping policy.
        critique["improved_queries"] = improved
        # Persist the focus assessment so the trace shows WHERE the run was
        # researching, not only how much it found. Kept out of the primary
        # answer: this is process metadata and belongs to the audit.
        focus_report = None
        if focus_state is not None:
            focus_report = focus_state.get("report") or {}
            if focus_report:
                logger.info("[Focus] %s", focus_state.get("summary", ""))
        critique_feedback = critique.get("reason", "")
        if improved:
            critique_feedback = f"{critique_feedback} Improved search focus: {'; '.join(improved)}"
        if focus_report and (focus_report.get("concentrated") or focus_report.get("drifted")):
            critique_feedback = (
                f"{critique_feedback} "
                f"Focus warning: {(focus_state or {}).get('summary', '')}"
            )

        # Claim-level evidence spine (requirement 6): attach the graded record
        # to the state facts so synthesis and the report can rely on
        # claim→source→verification→independence→corroboration→contradiction.
        enriched_facts = _attach_evidence(state.get("facts", []), contradictions)

        return {
            "critique": critique,
            "iteration": next_iteration,
            "confidence": overall_conf,
            "critique_feedback": critique_feedback,
            "confidence_breakdown": breakdown,
            "confidence_history": [*state.get("confidence_history", []), overall_conf],
            "contradictions": contradictions,
            "redteam": redteam_state,
            "facts": enriched_facts,
            "corroboration_queries": corroboration_queries,
            "corroboration_registry": corroboration_registry,
            "investigation_state": inv_state,
            "focus": {
                "report": focus_report or {},
                "queries": list((focus_state or {}).get("queries", ()) or ()),
            },
        }

    async def synthesizer_node(state: ResearchState) -> SynthesizerUpdate:
        # Only verification-passed facts are usable evidence (Phase 2.3).
        usable = _verified_facts(state.get("facts", []))
        all_facts = state.get("facts", [])
        intent = state.get("intent") or {}
        base_context = {
            "contradictions": state.get("contradictions", []),
            "confidence": state.get("confidence", None),
            "degraded": take_fallbacks(),
            "total_facts": len(all_facts),
            "verified_count": sum(1 for f in all_facts if f.get("verified")),
            "mode": state.get("mode", "standard"),
            "redteam_findings": (state.get("redteam", {}) or {}).get("findings", []),
            # Intent: the synthesis must answer the user's likely meaning
            # and disambiguate up front when the query was ambiguous.
            "intent": intent,
            # Ambiguity policy: whether the answer should state a chosen reading
            # (assume) or keep researched readings in separate, non-blended
            # strands (separate). `ask` never reaches synthesis — that run stops
            # at the clarification node.
            "ambiguity": state.get("ambiguity") or {},
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
        gate_enabled = bool(getattr(llm.settings, "quality_gate_enabled", True))
        revision_enabled = bool(getattr(llm.settings, "synthesis_revision_enabled", True))
        threshold = float(getattr(llm.settings, "quality_threshold", 70.0) or 70.0)

        # Answer-first outline + section-wise synthesis (GPT Researcher
        # adaptation). Section-wise is reserved for genuinely broad questions
        # in deeper modes: it costs one LLM call per section, and the
        # synthesizer itself falls back to a single pass when a section fails.
        outline_enabled = bool(getattr(llm.settings, "synthesis_outline_enabled", True))
        section_wise_enabled = bool(
            getattr(llm.settings, "synthesis_section_wise_enabled", True)
        ) and outline_enabled
        compress_context = bool(
            getattr(llm.settings, "synthesis_context_compression", True)
        )
        compress_threshold = float(
            getattr(llm.settings, "synthesis_compression_threshold", 0.72) or 0.72
        )
        answer_outline = None
        if outline_enabled:
            try:
                from app.agents.outline import build_outline

                answer_outline = build_outline(
                    state["query"],
                    usable,
                    state.get("sub_questions", []),
                    intent=intent,
                )
            except Exception as exc:
                logger.warning("outline_build_failed", error=str(exc), exc_info=exc)
                answer_outline = None
        section_wise = bool(
            section_wise_enabled
            and answer_outline is not None
            and answer_outline.broad
            and str(state.get("mode", "standard") or "standard") in ("deep", "executive", "standard", "audit")
        )

        # Evidence Synthesis Planning (Feature: synthesis plan): reason over the
        # evidence landscape BEFORE writing — which findings are dominant vs
        # incidental, which dimensions are thin relative to their centrality,
        # what may legitimately be synthesised across sources, and the intended
        # structure. Built from the same artifacts the rest of the node has
        # (outline, reasoning map, graded facts), deterministic and failure-safe.
        try:
            from app.agents.outline import build_blueprint
            from app.core.synthesis_planner import (
                build_synthesis_plan,
                required_dimensions_from_plan,
            )

            plan_blueprint = build_blueprint(
                state["query"],
                usable,
                state.get("sub_questions", []),
                intent=intent,
                outline=answer_outline,
                mode=str(state.get("mode", "standard") or "standard"),
                contradictions=state.get("contradictions") or [],
            )
            base_context["synthesis_plan"] = build_synthesis_plan(
                usable,
                state.get("contradictions") or [],
                query=state["query"],
                query_type=str(intent.get("query_type", "") or ""),
                outline=answer_outline,
                reasoning=base_context.get("reasoning"),
                strategy=plan_blueprint.strategy,
                sub_questions=state.get("sub_questions") or [],
                # Question-driven coverage: the plan's own dimensions are the
                # required set, so a required dimension retrieval never covered
                # is reported uncovered rather than silently dropped.
                required_dimensions=required_dimensions_from_plan(
                    state.get("sub_questions") or []
                ),
            )
        except Exception as exc:
            logger.warning("synthesis_plan_build_failed", error=str(exc), exc_info=exc)

        # LLM Analytical Synthesis (Phase 7): one small call that turns the
        # deterministic plan + verified evidence into an analyst's brief — a
        # central thesis, synthesised insights, relationships between findings,
        # counter-evidence and legitimate cross-source conclusions. It NEVER
        # replaces verification (the writer still cites [n] against verified
        # facts and verify_answer_support still runs) and degrades to the
        # deterministic plan when disabled, failed or empty (AGENTS.md 4.7).
        try:
            from app.agents.analyst import analytical_synthesis

            base_context["analytical_brief"] = await analytical_synthesis(
                llm,
                state["query"],
                usable,
                base_context.get("synthesis_plan"),
                query_type=str(intent.get("query_type", "") or ""),
                intent=intent,
                enabled=bool(getattr(llm.settings, "synthesis_analyst_enabled", True)),
            )
        except Exception as exc:
            logger.warning("analytical_synthesis_failed", error=str(exc), exc_info=exc)

        async def _synthesize_and_score(ctx: Dict[str, Any]):
            # Outline / section-wise / compression options ride in `context` so
            # the synthesizer entry point keeps its original signature (test
            # doubles patch it with that signature). The synthesizer writes the
            # machine-owned provenance it kept OUT of the answer back into this
            # dict, so we can route it to the audit layer.
            synthesis_ctx = {
                **ctx,
                "outline": answer_outline,
                "section_wise": section_wise,
                "compress_context": compress_context,
                "compress_threshold": compress_threshold,
            }
            answer = await synthesizer_agent(
                llm=llm,
                query=state["query"],
                facts=usable,
                context=synthesis_ctx,
            )
            machine_notes = [
                str(n) for n in (synthesis_ctx.get("synthesis_machine_notes") or []) if str(n).strip()
            ]
            # Report-contract verification: check the emitted answer's citations
            # against the evidence (never the reverse). Observational only —
            # it scores honesty, it does not rewrite.
            support = verify_answer_support(answer, state.get("facts", []))
            try:
                from app.agents.citation_check import check_citations

                health = await check_citations(
                    answer,
                    support,
                    enabled=bool(getattr(llm.settings, "citation_check_enabled", True)),
                    timeout=float(getattr(llm.settings, "citation_check_timeout_sec", 5.0) or 5.0),
                    max_sources=int(getattr(llm.settings, "citation_check_max", 10) or 10),
                )
            except Exception as exc:
                logger.warning("citation_health_check_failed", error=str(exc), exc_info=exc)
                health = {"checked": 0, "sources": [], "summary": {}, "enabled": False}
            quality = evaluate_answer(
                state["query"],
                intent=intent,
                answer=answer,
                facts=state.get("facts", []),
                answer_support=support,
                citation_health=health,
                contradictions=state.get("contradictions", []),
                redteam_findings=(state.get("redteam", {}) or {}).get("findings", []),
                mode=str(state.get("mode", "standard") or "standard"),
                threshold=threshold,
            )
            # Thesis fidelity (Phase 8): does the prose actually reflect the
            # analyst's brief? Observational, semantic (never string matching).
            # Its failures ride the SAME single revision pass as the quality
            # gate's — no new loop, no new LLM call.
            fidelity = check_thesis_fidelity(answer, ctx.get("analytical_brief"))
            # Answer conformance (Phase 12): does the finished prose actually do
            # the work the question's shape demands — mechanism for a "why",
            # criterion + verdict for a comparison, a marked projection for a
            # forecast, an explained conflict, calibrated inference, and depth
            # proportional to the question? Deterministic and observational; its
            # failures ride the SAME single revision pass (no new loop, no LLM).
            conformance = check_answer_conformance(
                answer,
                state["query"],
                intent,
                brief=ctx.get("analytical_brief"),
                plan=ctx.get("synthesis_plan"),
                contradictions=ctx.get("contradictions"),
                mode=str(state.get("mode", "standard") or "standard"),
            )
            return answer, support, health, quality, machine_notes, fidelity, conformance

        (answer, support, citation_health, quality, synthesis_notes, fidelity,
         conformance) = await _synthesize_and_score(base_context)

        # Answer revision pass (the quality optimizer's LLM half): the writer
        # rewrites its draft ONCE — fed the measured failures when the gate
        # failed, a polish mandate when it passed — and the better-scoring
        # draft ships. Never a loop (budget rule). Skipped when the gate is
        # disabled or the synthesizer itself is on deterministic fallback
        # (extraction cannot act on feedback).
        # Quick mode trades the unconditional polish pass for latency; the
        # gate still repairs a FAILING draft there.
        quick_mode = str(state.get("mode", "standard")) == "quick"
        # Thesis-fidelity and answer-conformance failures are actionable and
        # belong in the rewrite prompt alongside the gate's failures (bounded:
        # one revision).
        if fidelity.failures:
            logger.info("thesis_fidelity_failed", score=fidelity.score,
                        failures=len(fidelity.failures))
        if conformance.failures:
            logger.info("answer_conformance_failed", score=conformance.score,
                        query_type=conformance.query_type,
                        failures=len(conformance.failures))
        actionable = bool(fidelity.failures) or bool(conformance.failures)
        run_revision = (
            gate_enabled and revision_enabled
            and ("synthesizer" not in take_fallbacks())
            and (not quick_mode or not quality.passed or actionable)
        )
        if run_revision:
            feedback = [*quality.failures, *fidelity.failures, *conformance.failures]
            try:
                (answer2, support2, health2, quality2, notes2, fidelity2,
                 conformance2) = await _synthesize_and_score({
                    **base_context,
                    "quality_feedback": feedback,
                    "revision": True,
                })
            except Exception as exc:
                logger.warning("synthesis_revision_failed", error=str(exc), exc_info=exc)
            else:
                # Ship the revision when it is no worse on quality, fidelity AND
                # conformance — any of the three is why the pass may have run.
                if (
                    quality2.overall >= quality.overall
                    and fidelity2.score >= fidelity.score
                    and conformance2.score >= conformance.score
                ):
                    answer, support, citation_health, quality = (
                        answer2, support2, health2, quality2,
                    )
                    fidelity = fidelity2
                    conformance = conformance2
                    synthesis_notes = notes2

        logger.info("synthesizer_done", answer_chars=len(answer), usable_facts=len(usable),
                    support_rate=round(support_rate_val, 2) if (support_rate_val := support.get("rate")) is not None else None,
                    unsupported=len(support["unsupported"]),
                    citation_summary=citation_health.get("summary", {}),
                    quality=quality.overall, quality_passed=quality.passed)
        return {
            "synthesized_answer": answer,
            "answer_support": support,
            "citation_health": citation_health,
            "quality": quality.to_dict(),
            "evidence_distribution": evidence_distribution,
            "outline": answer_outline.to_dict() if answer_outline is not None else {},
            "section_wise": bool(section_wise),
            # Thesis fidelity: how faithfully the prose reflects the analyst's
            # brief. Audit-only; never rendered into the primary answer.
            "thesis_fidelity": fidelity.to_dict(),
            # Answer conformance (Phase 12): whether the prose fits the
            # question's shape, calibrates inference, synthesises conflicts and
            # has depth proportional to the question. Audit-only.
            "answer_conformance": conformance.to_dict(),
            # Machine-owned provenance the synthesizer kept out of the answer;
            # rendered by build_answer_audit.
            "synthesis_machine_notes": synthesis_notes,
        }

    async def finalize_node(state: ResearchState) -> FinalizeUpdate:
        # Direct-answer path (R3): the answer was produced with no facts,
        # sources or verification. A research-shaped report (supporting
        # evidence, contradictions, decision layer) would be fabricated
        # structure around an ungrounded answer, so the direct path gets its
        # own minimal, honest report instead.
        direct = str(state.get("direct_answer", "") or "").strip()
        if direct:
            meta = state.get("direct_answer_meta") or {}
            if meta.get("conversation_kind"):
                return {
                    "final_report": build_conversation_report(state),
                    "final_audit": "",
                    "decision_options": [],
                }
            return {
                "final_report": build_direct_answer_report(state),
                "final_audit": "",
                "decision_options": [],
            }
        # Decision options computed once and shared with the report builder.
        options = build_decision_layer(state)
        # Primary answer is the synthesizer's output verbatim; the machine-owned
        # disclosure moves to the separate audit document.
        return {
            "final_report": build_markdown_report(state, decision_options=options),
            "final_audit": build_answer_audit(state, decision_options=options),
            "decision_options": options,
        }

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
