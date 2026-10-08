from __future__ import annotations

"""Node factory `make_intent_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from typing import Any, Dict, List
from app.agents.intent import heuristic_intent
from app.agents.router import conversation_kind, deterministic_route
from app.graph.state import IntentUpdate, ResearchState
from app.core.logging import get_logger

logger = get_logger(__name__)


def make_intent_node(llm, search_client, classify_intent, route_query, _decide_ambiguity, _looks_broad):
    async def _node(state: ResearchState) -> IntentUpdate:
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
        # The planner reads the chosen reading from the intent dict so contracts
        # for the ANSWERED reading are the ones that get sense-tagged. Kept as a
        # nested key because IntentReport is a frozen shape the planner already
        # consumes, and adding a top-level field would change that contract.
        intent_dict["ambiguity_policy"] = ambiguity

        return {
            "intent": intent_dict,
            "context_snippets": context_snippets,
            "route": route_dict,
            "ambiguity": ambiguity,
        }
    return _node
