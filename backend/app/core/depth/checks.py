from __future__ import annotations

from typing import Any, Dict, List, Sequence

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


from app.core.depth.constants import (
    DEFAULT_MINIMUM_SOURCES,
    MODE_MIN_ITERATIONS,
)
from app.core.depth.signals import (
    _axes_below_threshold,
)


def _marginal_gain(history: List[float]) -> List[float]:
    return [history[i] - history[i - 1] for i in range(1, len(history))]


def _two_consecutive_stalls(history: List[float], min_gain: float) -> bool:
    deltas = _marginal_gain(history)
    if len(deltas) < 2:
        return False
    return deltas[-1] < min_gain and deltas[-2] < min_gain


def _query_key(query: str) -> str:
    import re

    tokens = sorted(set(re.findall(r"[a-z0-9]{3,}", (query or "").lower())))
    return " ".join(tokens)


def _similar_query(a: str, b: str, threshold: float = 0.8) -> bool:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def _searched_queries(state: Dict[str, Any]) -> set:
    """Every query this run has already issued: sub-question texts that
    produced results, their variants, and prior critic follow-ups."""
    searched: set = set()
    for r in state.get("search_results", []) or []:
        if isinstance(r, dict):
            key = _query_key(str(r.get("sub_question", "")))
            if key:
                searched.add(key)
    for q in state.get("sub_questions", []) or []:
        if isinstance(q, dict):
            for text in [q.get("question", ""), *(q.get("variants", []) or [])]:
                key = _query_key(str(text or ""))
                if key:
                    searched.add(key)
    for c in state.get("coverage_searched", []) or []:
        key = _query_key(str(c or ""))
        if key:
            searched.add(key)
    return searched


def _novel_followups(state: Dict[str, Any]) -> List[str]:
    """Critic follow-ups that are NOT near-duplicates of already-run searches."""
    from app.agents.planner import normalize_text

    searched = _searched_queries(state)
    if not searched:
        return [str(q) for q in state.get("critique", {}).get("improved_queries", []) or []]
    out: List[str] = []
    for q in state.get("critique", {}).get("improved_queries", []) or []:
        text = str(q or "").strip()
        key = _query_key(text)
        if not text or not key:
            continue
        if any(_similar_query(key, seen) for seen in searched):
            continue
        if any(_similar_query(key, _query_key(other)) for other in out):
            continue
        out.append(normalize_text(text))
    return out


def _budget_checks(state: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from app.core.usage import get_run_usage

        usage = get_run_usage()
    except Exception:
        usage = None
    if usage is None:
        return {"active": False, "exhausted": False, "can_afford_pass": True, "utilization": 0.0}
    pending = max(1, len(_axes_below_threshold(state, DEFAULT_MINIMUM_SOURCES)))
    return {
        "active": True,
        "exhausted": usage.exhausted,
        "can_afford_pass": usage.can_afford_pass(pending),
        "utilization": usage.budget.utilization(),
        "snapshot": usage.budget.snapshot(),
    }


def _convergence_checks(state: Dict[str, Any]) -> Dict[str, Any]:
    """Has the reviewer concluded, repeatedly, that the evidence cannot answer?

    Reads the diagnosis critic_node already recorded (app/agents/convergence.py),
    so the decision and the diagnosis cannot disagree. A run that has not reached
    the critic has no diagnosis and never converges on absent information.
    """
    convergence = state.get("convergence") or {}
    if not isinstance(convergence, dict) or not convergence:
        return {"converge": False, "decision_reason": "", "rounds": 0}
    identified = bool(convergence.get("identified"))
    return {
        "converge": identified,
        "rounds": int(convergence.get("rounds", 0) or 0),
        "decision_reason": str(convergence.get("reason", "") or "") or (
            "the reviewer reported the same fundamental evidence gap across "
            "rounds; the evidence cannot answer the question as asked, so it is "
            "recorded as a stated conclusion rather than searched again"
        ),
    }


def _focus_checks(state: Dict[str, Any]) -> Dict[str, Any]:
    """Read the loop's focus assessment (app/agents/focus.py) for a redirect.

    `focus` is written by critic_node, which computes the report once per pass
    via `workflow._assess_focus`. A run that has not reached the critic yet has
    no assessment and must not be redirected on absent information.

    Reads only what is already on state, so this stays cheap and pure like every
    other check here. Returns the redirect decision rather than acting on it, so
    `evaluate` can report which rule fired.
    """
    focus = state.get("focus") or {}
    report = focus.get("report") if isinstance(focus, dict) else None
    if not isinstance(report, dict) or not report:
        return {
            "redirect": False,
            "decision_reason": "",
            "drifted": False,
            "concentrated": False,
            "coverage": 0.0,
        }

    drifted = bool(report.get("drifted"))
    concentrated = bool(report.get("concentrated")) and not report.get("is_narrow")
    redirect = bool(drifted or concentrated)

    reasons: List[str] = []
    if drifted:
        reasons.append(
            f"research drifted from the question (off-query share "
            f"{float(report.get('off_query_share', 0.0)):.2f})"
        )
    if concentrated:
        reasons.append(
            f"evidence concentrated on '{report.get('dominant_dimension', '?')}' "
            f"({float(report.get('concentration', 0.0)):.2f}) while other planned "
            f"dimensions are uncovered"
        )
    return {
        "redirect": redirect,
        "decision_reason": "; ".join(reasons),
        "drifted": drifted,
        "concentrated": concentrated,
        "coverage": float(report.get("coverage", 0.0) or 0.0),
        # Dimensions the plan requires but has no evidence for. A redirect can
        # only act on these; if none are left actionable the redirect has run out
        # of things to correct.
        "uncovered_priorities": list(report.get("missing") or ())
        + list(report.get("thin") or ()),
        "actionable_left": any(
            _dimension_attempts_left(state, d)
            for d in list(report.get("missing") or ())
            + list(report.get("thin") or ())
        ),
    }


def _dimension_attempts_left(state: Dict[str, Any], dimension: str) -> bool:
    """Has this dimension any search budget left?

    Reads the same ledger the candidate generator consults, so the stopping
    decision and the selection decision can never disagree about whether a gap is
    still worth a pass. Unknown dimension (no entry) counts as budget left.
    """
    try:
        from app.core.investigation_state import dimension_key

        investigation = state.get("investigation_state") or {}
        if not isinstance(investigation, dict):
            return True
        entry = investigation.get(dimension_key(str(dimension or "")))
        if not isinstance(entry, dict):
            return True
        if str(entry.get("status", "")) == "exhausted":
            return False
        max_attempts = max(1, int(entry.get("max_attempts", 1) or 1))
        return int(entry.get("attempts", 0) or 0) < max_attempts
    except Exception as exc:  # a ledger bug must not stop a run
        logger.warning("dimension_attempts_check_failed", error=str(exc), exc_info=exc)
        return True


def _actionable_uncovered_axes(
    state: Dict[str, Any], uncovered_axes: Sequence[str]
) -> List[str]:
    """Uncovered planned axes that still have search budget.

    An axis searched to exhaustion is not actionable: the evidence does not exist
    in the reachable sources, so another pass cannot fill it.
    """
    return [a for a in uncovered_axes if _dimension_attempts_left(state, a)]


def _criticism_checks(state: Dict[str, Any]) -> Dict[str, Any]:
    """Critic-raised tasks that are still actionable (app/core/criticism_ledger).

    The critic ledger tracks each gate-failure/gap as a task and marks it
    resolved when the specific condition no longer holds. A task still
    open/attempted is an UNFINISHED criticism: it must block a soft stop while
    budget remains, exactly as an uncovered axis does. A task marked exhausted
    (its attempt budget spent without closure) is an acknowledged limitation and
    must NOT block — otherwise an unclosable criticism loops forever.

    Reads only what critic_node already wrote to state; empty/absent ledger =>
    no criticism constraint (behaviour identical to before this existed).
    """
    try:
        from app.core.criticism_ledger import active_tasks, summary

        ledger = state.get("criticism_ledger") or {}
        active = active_tasks(ledger)
        return {
            "active_count": len(active),
            "targets": [str(t.get("target", "") or "") for t in active if t.get("target")],
            "summary": summary(ledger),
        }
    except Exception as exc:  # a ledger bug must not stop a run
        logger.warning("criticism_checks_failed", error=str(exc), exc_info=exc)
        return {"active_count": 0, "targets": [], "summary": {}}


def _confidence_target(state: Dict[str, Any], settings: Settings) -> float:
    """Mode-aware target: audit demands more proof than quick by design."""
    try:
        from app.agents.orchestrator import MODE_CONFIDENCE_TARGET

        mode = str(state.get("mode", "") or "standard").lower()
        if mode in MODE_CONFIDENCE_TARGET:
            return float(MODE_CONFIDENCE_TARGET[mode])
    except Exception:
        pass
    return float(settings.sufficiency_threshold)


def _min_iterations(state: Dict[str, Any]) -> int:
    mode = str(state.get("mode", "") or "standard").lower()
    return int(MODE_MIN_ITERATIONS.get(mode, 1))
