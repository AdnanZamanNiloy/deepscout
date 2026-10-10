"""Dynamic planning: dimension selection, validation and the directive call.

Extracted verbatim from `app/agents/planner.py` (refactor; no behaviour change).
`plan_dimensions` asks a model which research dimensions THIS query needs and
falls back to a deterministic, query-type-aware heuristic (AGENTS.md 4.7);
`validate_plan_dimensions` appends any missing required angle deterministically.

`planner.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.core.schemas import PlanningDirectiveModel
from app.core.usage import set_stage_hint

from app.core.degradation import record_fallback

from app.agents.planning.normalize import (
    dimension_to_axis,
    normalize_text,
)
from app.agents.planning.prompts import PLANNING_DIRECTIVE_PROMPT
from app.agents.planning.types import (
    VALID_SEARCH_TYPES,
    _HEURISTIC_DIMENSION_DEFAULTS,
    _REQUIRED_ANGLE_AXIS,
    _REQUIRED_ANGLE_TOKENS,
)

logger = get_logger(__name__)


def _heuristic_dimensions(query: str, intent: Optional[Dict[str, Any]] = None) -> List[str]:
    """Deterministic, query-type-aware dimension list for the fallback path.

    Mirrors the LLM directive's rules with lexical signals only (AGENTS.md
    4.7): a decision query gets cost/risk dimensions, a comparative query gets
    a head-to-head dimension, a causal query gets post-mortem dimensions —
    genuinely different sets per query type, never the old fixed template.
    """
    intent = intent or {}
    text = f" {(query or '').lower().strip()} "
    qtype = str(intent.get("query_type", "") or "").strip().lower()
    if qtype not in ("factual", "comparative", "analytical", "exploratory"):
        # Local import: orchestrator is a leaf module that must not import the
        # planner (planner -> orchestrator would close a cycle at import time).
        from app.agents.orchestrator import classify_query_type

        qtype = classify_query_type(query)
    dims: List[str] = []

    def _add(name: str) -> None:
        if name not in dims:
            dims.append(name)

    is_decision = any(
        m in text
        for m in ("should we", "should i", "should our", "should the",
                  "should ", "invest", "worth it", "recommend", "decide")
    )
    # A RECOMMENDATION request ("suggest me some X", "give me ideas for Y",
    # "what should I study") is not a factual lookup: it needs candidate options,
    # the criteria that make one suitable, and authoritative sources that
    # recommend/rank. Planned as `else` -> "definition" (what ARE research
    # areas), so the run searched for papers ABOUT the subject and returned a
    # survey of open problems instead of a set of suggestions. Checked before
    # `is_decision` so the guidance shape wins over the trade-off shape.
    is_recommendation = any(
        m in text
        for m in ("suggest", "recommend", "advise", "give me some", "give me a few",
                  "give me ideas", "some ideas", "ideas for", "topic suggestions",
                  "what should i study", "what should i research", "what should we study",
                  "which topics", "what topics", "best topics", "good topics")
    )
    is_comparative = qtype == "comparative" or any(
        m in text for m in (" vs ", " versus ", "compare", "compared", "comparison")
    )
    is_causal = any(
        m in text
        for m in ("what caused", "why did", "why does", "why is", "cause of",
                  "cause ", "reasons for", "root cause", "how did", "led to")
    )
    is_mechanism = qtype == "analytical" or any(
        m in text
        for m in ("how does", "how do", "how is", "how it works", "mechanism",
                  "work and", "works")
    )
    is_quant = any(
        m in text
        for m in ("how much", "how many", "cost", "price", "market size",
                  "growth", "rate", "percentage", "percent", "share",
                  "forecast", "projection", "statistics", "by 20")
    )
    is_contested = any(
        m in text
        for m in ("harmful", "controversial", "debate", "criticism", "myth",
                  "safe", "danger", "ethical", "controversial", "bias")
    )

    if is_recommendation:
        # Candidate options FIRST (the thing the user asked for), then the
        # criteria that make one suitable, then the authoritative bodies that
        # rank or recommend them, then pitfalls. This is what makes the planner
        # search for guidance sources rather than subject-matter papers.
        _add("candidate options")
        _add("selection criteria")
        _add("authoritative recommendations")
        _add("risks and pitfalls")
    elif is_decision:
        _add("policy options")
        _add("cost and financing")
        _add("risk and feasibility")
        _add("evidence and projections")
    elif is_causal:
        _add("causal mechanism")
        _add("alternative explanations")
        _add("institutional and regulatory failures")
    elif is_comparative:
        _add("head-to-head comparison")
        _add("cost per unit")
        _add("operational trade-offs")
        _add("evidence and data")
    else:
        _add("definition")
        if is_mechanism:
            _add("mechanism of action")
        _add("evidence and data")

    if is_quant and not any("evidence" in d for d in dims):
        _add("evidence and projections")
    if is_contested:
        _add("counter-evidence and criticism")
    if not any("criticism" in d or "counter" in d for d in dims):
        _add("criticism and limitations")

    # Preserve the historical WHY dimension for analytical/trend queries even
    # when the lexical shape above produced only broad dimensions.
    if qtype in ("analytical", "exploratory") and not any(
        "mechanism" in d or "cause" in d for d in dims
    ):
        _add("underlying drivers")

    return dims[:6] or list(_HEURISTIC_DIMENSION_DEFAULTS)


def _coarse_query_type(query: str) -> str:
    """query_type classification for the directive stage (leaf import)."""
    from app.agents.orchestrator import classify_query_type

    return classify_query_type(query)


def _angle_token_covered(label: str, angle: str) -> bool:
    """Lexical coverage test or()ed with the angle's canonical-axis mapping.

    "cost per unit" is a quantitative angle by vocabulary; "head-to-head
    comparison" is served by the canonical `comparison` axis even though it
    carries no literal "compar"/"versus" marker in every wording.
    """
    tokens = _REQUIRED_ANGLE_TOKENS[angle]
    text = normalize_text(label)
    if any(tok in text for tok in tokens):
        return True
    return dimension_to_axis(label) == _REQUIRED_ANGLE_AXIS[angle]


def _dedupe_exact_dimensions(dims: Sequence[str]) -> Tuple[List[str], bool]:
    """Collapse exactly-normalized duplicate dimensions, preserving order.

    Only exact duplicates (whitespace/case-insensitive) are dropped: near
    duplicates are left to `select_plan`/`deduplicate_semantic`, which own
    semantic overlap and must not be second-guessed here. Order and the first
    occurrence of each label are always preserved.
    """
    result: List[str] = []
    seen: Set[str] = set()
    duplicate_flag = False
    for raw in dims or ():
        name = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not name:
            continue
        key = normalize_text(name)
        if key in seen:
            duplicate_flag = True
            continue
        seen.add(key)
        result.append(name)
    return result, duplicate_flag


def validate_plan_dimensions(
    query: str,
    dimensions: Sequence[str],
    must_cover: Sequence[str],
    *,
    complexity: Any = None,
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Validate planned dimensions against what the query actually requires.

    Fully deterministic, no LLM calls (AGENTS.md 4.7): reuses the orchestrator's
    `score_complexity` classifiers and the planner's own `dimension_to_axis`.
    Missing required angles are APPENDED (never replace/reorder the model's
    plan) and added to `must_cover` so `enforce_axis_coverage` turns each into a
    real delegation contract. Non-required angles are never dropped, so a valid
    plan comes back byte-identical. Any classifier failure skips validation
    rather than raising — an empty plan is returned unchanged.
    """
    dims, duplicate_flag = _dedupe_exact_dimensions(dimensions)
    cover, _ = _dedupe_exact_dimensions(must_cover)
    # must_cover entries that do not name a planned dimension are dropped: every
    # must_cover entry must correspond to a dimension that becomes a contract.
    dim_keys = {normalize_text(d) for d in dims}
    cover = [c for c in cover if normalize_text(c) in dim_keys]
    if not dims:
        return dims, cover, {"applied": False, "reason": "no_dimensions"}

    try:
        if complexity is None:
            from app.agents.orchestrator import score_complexity

            complexity = score_complexity(query)
        required_angles: List[str] = []
        if complexity.needs_quantitative:
            required_angles.append("quantitative")
        if complexity.is_decision:
            required_angles.append("decision")
        if complexity.is_contested:
            required_angles.append("contested")
        if complexity.query_type == "comparative":
            required_angles.append("comparison")
    except Exception as exc:
        # Fail-safe: a classifier failure must never break planning.
        logger.warning("[Planner] plan validation skipped (classifier failed)", exc_info=exc)
        return dims, cover, {"applied": False, "reason": "classifier_error"}

    pool = dims + cover
    added: List[str] = []
    for angle in required_angles:
        if not any(_angle_token_covered(label, angle) for label in pool):
            injected = f"{angle} evidence" if angle == "quantitative" else f"{angle} angle"
            dims.append(injected)
            cover.append(injected)
            pool = dims + cover
            added.append(injected)

    meta = {
        "applied": True,
        "query_type": complexity.query_type,
        "required_angles": required_angles,
        "added": added,
        "duplicate_dimensions": duplicate_flag,
    }
    if added:
        logger.info("[Planner] plan validation added required dimensions: %s", ", ".join(added))
    if duplicate_flag:
        logger.warning("[Planner] plan contains overlapping dimensions (flagged, not merged)")
    return dims, cover, meta


async def plan_dimensions(
    llm: LLMClient,
    query: str,
    *,
    today: str = "",
    intent: Optional[Dict[str, Any]] = None,
    context_snippets: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Ask the model which research dimensions THIS query needs.

    Returns (dimensions, must_cover, meta). On any LLM failure the deterministic
    query-type-aware fallback is returned so a degraded run still gets genuinely
    different dimensions per query type rather than a generic template
    (AGENTS.md 4.7). `meta` carries the model's query_type/domain plus a
    `planned_by` marker ("llm" | "heuristic") for the trace.
    """
    intent = intent or {}
    date_block = f"\nToday is {today.strip()}." if today.strip() else ""
    intent_note = ""
    if intent.get("ambiguity") and intent.get("senses"):
        labels = [
            str(s.get("label", "")).strip()
            for s in intent["senses"]
            if isinstance(s, dict) and str(s.get("label", "")).strip()
        ]
        if labels:
            intent_note = (
                "\nThis query is AMBIGUOUS between: " + "; ".join(labels[:2])
                + ". Include a disambiguation dimension for each meaning."
            )
    snippet_block = ""
    snippets = [s for s in (context_snippets or []) if str(s).strip()]
    if snippets:
        snippet_block = (
            "\nTop web results for the raw query (ground your dimensions in "
            "this real terminology; do not answer the query):\n"
            + "\n".join(f"- {s}" for s in snippets[:5])
        )
    user_prompt = (
        f"Query: {query}{date_block}{intent_note}{snippet_block}\n\n"
        "Which research dimensions does THIS query need? Return JSON only."
    )

    try:
        set_stage_hint("planner")
        payload = await llm.generate_json(
            system_prompt=PLANNING_DIRECTIVE_PROMPT,
            user_prompt=user_prompt,
            response_model=PlanningDirectiveModel,
        )
    except Exception as exc:
        logger.warning("[Planner] dimension directive LLM failed, using heuristic", exc_info=exc)
        record_fallback("planner_dimensions")
        return _validated_heuristic_dimensions(query, intent)

    if not isinstance(payload, dict):
        logger.warning("[Planner] dimension directive returned non-dict, using heuristic")
        record_fallback("planner_dimensions")
        return _validated_heuristic_dimensions(query, intent)

    dims: List[str] = []
    for raw in payload.get("dimensions") or []:
        name = re.sub(r"\s+", " ", str(raw or "")).strip()
        if name and len(name) <= 60 and name.lower() not in {d.lower() for d in dims}:
            dims.append(name)
    if not dims:
        logger.warning("[Planner] dimension directive returned no dimensions, using heuristic")
        record_fallback("planner_dimensions")
        return _validated_heuristic_dimensions(query, intent)

    must_cover: List[str] = []
    for raw in payload.get("must_cover") or []:
        name = re.sub(r"\s+", " ", str(raw or "")).strip()
        if name and name not in must_cover:
            must_cover.append(name)
    if not must_cover:
        must_cover = dims[:2]

    # Validate the model's dimensions against what the query actually requires
    # (quantitative / decision / contested / comparative). Missing angles are
    # APPENDED and added to must_cover, so the prompt's R3/R4 requirements are
    # enforced deterministically instead of trusted. Reordering never happens,
    # so an already-valid plan is returned unchanged.
    dims, must_cover, validation_meta = validate_plan_dimensions(query, dims, must_cover)

    meta = {
        "planned_by": "llm",
        "query_type": str(payload.get("query_type", "") or _coarse_query_type(query)),
        "dominant_domain": str(payload.get("dominant_domain", "general") or "general"),
        "reasoning": str(payload.get("reasoning", "") or ""),
        "coverage_note": str(payload.get("coverage_note", "") or ""),
        "preferred_search_types": [
            str(s) for s in (payload.get("preferred_search_types") or [])
            if str(s) in VALID_SEARCH_TYPES
        ],
        "plan_validation": validation_meta,
    }
    logger.info("[Planner] dynamic dimensions: %s (must_cover=%s)", dims, must_cover)
    return dims, must_cover, meta


def _validated_heuristic_dimensions(
    query: str, intent: Optional[Dict[str, Any]] = None
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Heuristic dimensions, also run through the deterministic validator.

    The second return path of `plan_dimensions`: when the directive yields no
    dimensions the heuristic list is only a coarse starting point, so it is
    validated (and missing required angles appended) exactly like the LLM
    path. `must_cover` defaults to the first two dimensions and gains every
    appended required angle, so each maps to a real contract downstream.
    """
    dims = _heuristic_dimensions(query, intent)
    dims, must_cover, validation_meta = validate_plan_dimensions(query, dims, dims[:2])
    return dims, must_cover, {
        "planned_by": "heuristic",
        "query_type": _coarse_query_type(query),
        "plan_validation": validation_meta,
    }


