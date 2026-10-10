from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from app.agents.planner import normalize_text
from app.core.logging import get_logger
from app.graph.state import ResearchState

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
