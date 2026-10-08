"""Plan assembly: fallback, selection, dependency sanitisation and waves.

Extracted verbatim from `app/agents/planner.py` (refactor; no behaviour change).
The deterministic fallback plan, priority/diversity-aware truncation, and the
dependency sanitisation that turns `depends_on` into execution waves.

`planner.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.agents.planning.contracts import (
    _contract,
    _intent_research_senses,
    _query_concept,
    _sense_concept,
    _trends_scope_wanted,
    _year_from,
)
from app.agents.planning.normalize import axis_search_type, dimension_to_axis
from app.agents.planning.types import (
    COUNTER_EVIDENCE_AXIS,
    FRONTIER_AXES,
    FRONTIER_AXIS_QUESTIONS,
)


def sanitize_dependencies(plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop dangling, self- and cyclic dependencies, then assign waves.

    A cycle or a self-reference silently disabled dependency-aware execution
    before (the field was never read, so nothing broke visibly — it just never
    worked). Waves are the payoff: wave 0 contracts run in parallel
    immediately, wave 1 contracts run after their prerequisites and can be
    given the earlier findings as context.
    """
    ids = {int(item["id"]) for item in plan if isinstance(item.get("id"), int)}
    for item in plan:
        raw = item.get("depends_on") or []
        if not isinstance(raw, (list, tuple)):
            raw = []
        deps: List[int] = []
        for value in raw:
            try:
                dep = int(value)
            except (TypeError, ValueError):
                continue
            if dep in ids and dep != int(item["id"]) and dep not in deps:
                deps.append(dep)
        item["depends_on"] = deps[:3]

    by_id = {int(item["id"]): item for item in plan}
    waves: Dict[int, int] = {}

    def _wave(node_id: int, seen: Set[int]) -> int:
        if node_id in waves:
            return waves[node_id]
        if node_id in seen:          # cycle: break it by treating as root
            by_id[node_id]["depends_on"] = []
            waves[node_id] = 0
            return 0
        seen = seen | {node_id}
        deps = by_id[node_id].get("depends_on") or []
        depth = 0 if not deps else 1 + max(_wave(d, seen) for d in deps)
        depth = min(depth, 2)        # never more than 3 waves: latency floor
        waves[node_id] = depth
        return depth

    for node_id in list(by_id):
        by_id[node_id]["wave"] = _wave(node_id, set())

    # A cycle-broken node had its depends_on cleared mid-recursion, but its
    # outer _wave frame still computed a depth from the OLD deps and
    # overwrote the memo — stranding the node in a later wave even though it
    # is now a root. Roots must run in wave 0.
    for item in by_id.values():
        if not item.get("depends_on") and int(item.get("wave", 0) or 0) > 0:
            item["wave"] = 0
    return plan


def select_plan(
    plan: List[Dict[str, Any]], target_count: int, required_axes: Sequence[str] = ()
) -> List[Dict[str, Any]]:
    """Trim to `target_count` while protecting axis and search_type diversity.

    Pure priority sorting was the bug: a model that marks everything priority 1
    made truncation arbitrary, and a plan could end up as three encyclopedia
    questions with no evidence angle. Selection order is:
      1. every required axis (one contract each, best priority)
      2. one contract per remaining unseen search_type
      3. the rest by (priority, id)
    """
    if target_count <= 0:
        return []
    ranked = sorted(
        plan, key=lambda item: (int(item.get("priority", 2)), int(item.get("id", 0)))
    )
    # Required dimensions arrive as model labels ("cost and financing"): map
    # each to the canonical axis its contract carries so the protection below
    # actually matches (enforce_axis_coverage applies the same mapping).
    required = [dimension_to_axis(a) for a in (required_axes or ())]
    picked: List[Dict[str, Any]] = []
    picked_ids: Set[int] = set()

    def _take(item: Dict[str, Any]) -> None:
        picked.append(item)
        picked_ids.add(int(item.get("id", -1)))

    for axis in required:
        # Required axes are a CONTRACT, not a preference: they must survive
        # truncation. The budget cap below is honoured for optional angles, but
        # a required axis is only skipped when no contract serves it at all.
        # Previously `len(picked) >= target_count` broke this loop, so with
        # target=3 and axes [definition, evidence, criticism, mechanism,
        # outlook] the injected mechanism/outlook angles were selected and then
        # immediately discarded — the same 3-axis plan every pass, which is the
        # decomposition gap the WHY angle exists to close.
        for item in ranked:
            if int(item.get("id", -1)) in picked_ids:
                continue
            if str(item.get("axis", "")) == axis:
                _take(item)
                break

    seen_types = {str(item.get("search_type", "")) for item in picked}
    for item in ranked:
        if len(picked) >= target_count:
            break
        if int(item.get("id", -1)) in picked_ids:
            continue
        stype = str(item.get("search_type", ""))
        if stype not in seen_types:
            _take(item)
            seen_types.add(stype)

    for item in ranked:
        if len(picked) >= target_count:
            break
        if int(item.get("id", -1)) not in picked_ids:
            _take(item)

    picked.sort(key=lambda item: (int(item.get("priority", 2)), int(item.get("id", 0))))
    return picked


def fallback_plan(
    query: str,
    target_count: int = 4,
    required_axes: Sequence[str] = ("definition", "evidence", "criticism"),
    today: str = "",
    intent: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Deterministic plan for when the LLM call fails.

    Now shaped by the same axis contract as a real plan (an evidence angle and a
    criticism angle, not four definition variants) and sized to the
    orchestrator's target, so a degraded run is a smaller research plan rather
    than a different, weaker kind of plan. When the intent stage flagged the
    query ambiguous, contracts are sense-scoped and split across the researched
    senses — a degraded plan still targets the user's likely meaning.
    """
    concept = _query_concept(query)
    year = _year_from(today)
    blueprint: List[Tuple[str, str, str, int]] = [
        (f"{concept} definition explanation overview", "definition", "encyclopedia", 1),
        (f"{concept} statistics official data figures{year}", "evidence", "statistical", 1),
        (f"{concept} limitations criticism counter-evidence risks", "criticism", "academic", 2),
        (f"{concept} mechanism how it works components", "mechanism", "academic", 2),
        (f"{concept} real world applications examples compared", "application", "comparison", 2),
        (f"{concept} recent developments outlook{year}", "outlook", "news", 3),
    ]
    # A trends-style question gets the frontier spread even on the deterministic
    # path, so a degraded run answers "current state of X" with the same breadth
    # a model plan would. Same conditional as the model path: the SPREAD is
    # earned by the question's shape, never applied to a narrow one.
    if _trends_scope_wanted(query):
        for axis in (*FRONTIER_AXES, COUNTER_EVIDENCE_AXIS):
            scaffold = FRONTIER_AXIS_QUESTIONS.get(axis, "")
            if not scaffold:
                continue
            blueprint.append(
                (scaffold.format(subject=concept), axis, axis_search_type(axis), 3)
            )
    ordered = [b for b in blueprint if b[1] in set(required_axes or ())] + [
        b for b in blueprint if b[1] not in set(required_axes or ())
    ]

    research = _intent_research_senses(intent)
    specs: List[Tuple[str, str, str, int, str, str]] = []
    if research:
        # Interleave senses so each researched meaning gets the top axes.
        for (question, axis, search_type, priority) in ordered:
            for (label, domain) in research:
                if len(specs) >= max(1, target_count):
                    break
                sense_question = f"{_sense_concept(label)}{question[len(concept):]}"
                specs.append((sense_question, axis, search_type, priority, label, domain))
            if len(specs) >= max(1, target_count):
                break
    else:
        specs = [
            (question, axis, search_type, priority, "", "general")
            for (question, axis, search_type, priority) in ordered[: max(1, target_count)]
        ]

    plan = [
        _contract(
            index=i + 1,
            question=question,
            axis=axis,
            search_type=search_type,
            priority=priority,
            domain=domain,
            coverage_goal=f"fallback {axis} coverage",
            sense=sense,
        )
        for i, (question, axis, search_type, priority, sense, domain) in enumerate(specs)
    ]
    return sanitize_dependencies(plan)


def execution_waves(plan: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group contracts into dependency-ordered waves for parallel dispatch.

    Wave members are independent and may run concurrently; a later wave starts
    only after the previous one finishes, and its contracts can be handed the
    earlier findings. This is the mechanism that makes "adaptive orchestration"
    more than a synonym for "fan out everything at once".
    """
    waves: Dict[int, List[Dict[str, Any]]] = {}
    for contract in plan or ():
        waves.setdefault(int(contract.get("wave", 0) or 0), []).append(contract)
    return [waves[key] for key in sorted(waves)]


