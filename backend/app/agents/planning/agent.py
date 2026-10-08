"""Planner agent: produce a validated, axis-complete research plan.

Extracted verbatim from `app/agents/planner.py` (refactor; no behaviour change).
`planner_agent` runs the dimension directive (first pass), builds and repairs
the contracts, enforces required axes and the conditional frontier tracks, and
falls back deterministically when the model fails (AGENTS.md 4.7).

`planner.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from app.core.degradation import record_fallback
from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.core.schemas import PlannerOutputModel
from app.core.usage import set_stage_hint

from app.agents.planning.contracts import (
    _contract,
    _assign_intent_senses,
    enforce_axis_coverage,
    enforce_frontier_axes,
    _trends_scope_wanted,
)
from app.agents.planning.dimensions import plan_dimensions
from app.agents.planning.normalize import (
    _clean_str_list,
    deduplicate_semantic,
    diversity_coverage,
    is_valid_question,
    normalize_domain,
    normalize_text,
)
from app.agents.planning.plan import fallback_plan, sanitize_dependencies, select_plan
from app.agents.planning.prompts import PLANNER_SYSTEM_PROMPT
from app.agents.planning.types import (
    DEFAULT_MINIMUM_SOURCES,
    VALID_TOOLS,
    PlannerOutput,
)

logger = get_logger(__name__)


async def planner_agent(
    llm: LLMClient,
    query: str,
    critique_feedback: str = "",
    today: str = "",
    context_snippets: List[str] | None = None,
    target_count: Optional[int] = None,
    required_axes: Sequence[str] = (),
    minimum_sources: int = DEFAULT_MINIMUM_SOURCES,
    intent: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Produce a validated, axis-complete, dependency-ordered research plan.

    New optional arguments come from `OrchestrationPlan.targets`; omitting them
    reproduces the previous behaviour (five sub-questions, no axis enforcement),
    so existing call sites keep working while the workflow migrates. `intent`
    (when provided) steers the plan at the user's likely meaning and tags
    contracts with sense labels for ambiguous queries.
    """
    target = int(target_count) if target_count else 5
    target = max(1, min(8, target))

    feedback_block = (
        f"\nCritique feedback: {critique_feedback}" if critique_feedback else ""
    )
    date_block = (
        f"\nToday is {today.strip()} — use this year in time-sensitive questions."
        if today.strip() else ""
    )

    intent = intent or {}

    # -------- Dynamic planning directive (Phase 2) --------
    # The plan must cover the dimensions THIS query needs. On the first pass we
    # ask the directive stage which dimensions those are and use its answer as
    # the required set, replacing the generic axis templates. On expansion
    # passes the workflow has already folded in the axes under research, so we
    # do NOT re-plan dimensions (that would undo per-axis expansion) — the
    # caller's required_axes is authoritative and any missing one is filled by
    # the dimension synthesizer below.
    planned_dimensions: List[str] = []
    required_questions: Dict[str, str] = {}
    directive_meta: Dict[str, Any] = {}
    # `critique_feedback` is non-empty only on expansion passes (the workflow
    # passes the critic's gap list). First-pass planning is the only time the
    # dimension directive runs; expansion must preserve the axes already under
    # research instead of re-deriving a fresh dimension set.
    if not critique_feedback:
        planned_dimensions, _must_cover, directive_meta = await plan_dimensions(
            llm, query, today=today, intent=intent, context_snippets=context_snippets,
        )
        # The directive's dimensions become the plan's hard requirements.
        # Caller-supplied required_axes (orchestration budget axes) are folded
        # in ONLY when the directive actually planned (planned_by=llm): on the
        # deterministic fallback the caller's explicit axes are the contract
        # (a caller that passed required_axes gets exactly those, not the
        # heuristic set added on top — otherwise required axes balloon past the
        # plan budget).
        if directive_meta.get("planned_by") == "llm":
            # The directive's dimensions ARE the contract: they are specific to
            # this query and already encode whatever evidence/criticism/mechanism
            # angles it needs. Re-merging the orchestration's generic axes here
            # would re-introduce exactly the boilerplate this stage replaces
            # (measured: every plan then carried definition/evidence/criticism/
            # mechanism alongside its real dimensions). The orchestration axes
            # remain the fallback contract when the directive cannot plan.
            required_axes = tuple(
                str(d or "").strip() for d in planned_dimensions if str(d or "").strip()
            )
        # On the deterministic fallback (directive LLM failed) the caller's
        # required_axes, if any, stay authoritative; if the caller passed none,
        # nothing is force-injected here — a plan-LLM failure already routes to
        # fallback_plan(), which guarantees the canonical safety-net axes.
        # must_cover dimensions are non-negotiable: if the plan model omits one,
        # the coverage enforcement below synthesizes a contract from the
        # dimension label itself. (No literal question is supplied — the
        # synthesizer derives one from the label + query concept.)
        if directive_meta.get("coverage_note"):
            logger.info("[Planner] directive coverage note: %s", directive_meta["coverage_note"])

    # Intent block: the resolved understanding of the question. It overrides
    # the model's own reading — that is the whole point of resolving intent
    # BEFORE research is shaped.
    intent_senses = [
        s for s in (intent.get("senses") or [])
        if isinstance(s, dict) and str(s.get("label", "")).strip()
    ]
    intent_parts: List[str] = []
    if intent.get("ambiguity") and intent_senses:
        listed = "\n".join(
            f"  {i + 1}. {str(s.get('label')).strip()} "
            f"({s.get('domain', 'general')}, p={float(s.get('probability', 0) or 0):.2f})"
            for i, s in enumerate(intent_senses[:3])
        )
        if intent.get("recommended_action") == "research_both":
            stance = (
                "Research BOTH leading senses — split the plan's budget across them, "
                "and never mix the two meanings inside one sub-question."
            )
        else:
            stance = (
                f"Research ONLY the most likely sense ('{intent_senses[0].get('label')}'); "
                "the report will cover the other meaning(s) in a brief disambiguation "
                "paragraph, so do not spend sub-questions on them."
            )
        intent_parts.append(
            "AMBIGUOUS QUERY — the user's term has distinct meanings:\n"
            f"{listed}\n{stance}\n"
            "Every sub-question MUST carry a \"sense\" field set to the exact label "
            "of the meaning it researches."
        )
    elif intent_senses:
        intent_parts.append(
            f"Likely meaning: {intent_senses[0].get('label')} — target the plan at this sense."
        )
    # Under-specified (not homonymous) query: the term has multiple useful
    # readings. Plan a sub-question per materially useful reading so the answer
    # can address both instead of explaining the ambiguity.
    interpretations = [
        i for i in (intent.get("interpretations") or [])
        if isinstance(i, dict) and str(i.get("label", "")).strip()
    ]
    if len(interpretations) >= 2:
        listed = "; ".join(str(i.get("label")).strip() for i in interpretations[:3])
        intent_parts.append(
            "UNDER-SPECIFIED QUERY — it can be read in more than one useful way: "
            f"{listed}. Plan a sub-question for each materially useful reading so "
            "the answer addresses both; do not spend research on the fact that the "
            "term is ambiguous."
        )
    if intent.get("domain"):
        intent_parts.append(
            f"Research domain: {intent.get('domain')} (overrides your own classification)."
        )
    if intent.get("explanation_level") == "basic":
        intent_parts.append(
            "Explanation level: basic — prefer one clear definitional sub-question "
            "over many technical angles."
        )
    intent_block = (
        "\nUser intent (resolved before research — obey it):\n" + "\n".join(intent_parts) + "\n"
        if intent_parts
        else ""
    )
    axis_block = ""
    if required_axes:
        axis_block = (
            "\nRequired research dimensions — the plan MUST contain one "
            "sub-question for each of: "
            f"{', '.join(str(a).replace('_', ' ') for a in required_axes)}. "
            "These were derived for THIS query. A plan missing any of them will "
            "be repaired automatically, and the repaired question will be cruder "
            "than one you write yourself. Use each dimension's label as the "
            "sub-question's axis."
        )
    budget_block = (
        f"\nProduce at most {target} sub-questions — this is a hard budget, so "
        "spend it on the highest-value angles rather than listing everything."
    )
    context_block = ""
    snippets = [s for s in (context_snippets or []) if str(s).strip()]
    if snippets:
        context_block = (
            "\nWeb context — top search results for the raw query. Use it to "
            "ground and disambiguate the sub-questions (real terminology, "
            "entities, and numbers the plan should target), never to answer "
            "the query itself:\n"
            + "\n".join(f"- {s}" for s in snippets[:6])
        )

    user_prompt = f"""
Query: {query}
{feedback_block}
{date_block}
{intent_block}
{axis_block}
{budget_block}
{context_block}

Generate a structured research plan.
Return JSON only.
"""

    try:
        set_stage_hint("planner")
        payload: PlannerOutput = await llm.generate_json(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=PlannerOutputModel,
        )
    except Exception as e:
        logger.error(f"[Planner] LLM failed: {e}", exc_info=e)
        record_fallback("planner")
        return fallback_plan(query, target, required_axes or ("definition", "evidence", "criticism"), today, intent=intent)

    sub_questions = payload.get("sub_questions", []) if isinstance(payload, dict) else []
    if not sub_questions:
        logger.warning("[Planner] Empty LLM output, using fallback")
        record_fallback("planner")
        return fallback_plan(query, target, required_axes or ("definition", "evidence", "criticism"), today, intent=intent)

    dominant_domain = normalize_domain(
        str(payload.get("dominant_domain", "general")) if isinstance(payload, dict) else "general"
    )
    # Intent overrides the model's own domain classification when confident.
    if intent.get("domain"):
        dominant_domain = normalize_domain(str(intent["domain"]))

    # ---------------- post-processing ----------------

    cleaned: List[Dict[str, Any]] = []
    for i, item in enumerate(sub_questions):
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "") or "").strip()
        if not q or not is_valid_question(q):
            continue

        raw_variants = item.get("variants", [])
        variants: List[str] = []
        if isinstance(raw_variants, list):
            for v in raw_variants[:2]:
                vs = str(v or "").strip()
                if vs and is_valid_question(vs) and normalize_text(vs) != normalize_text(q):
                    variants.append(vs)

        try:
            declared_id = int(item.get("id", i + 1))
        except (TypeError, ValueError):
            declared_id = i + 1

        cleaned.append(
            _contract(
                index=declared_id,
                question=q,
                axis=str(item.get("axis", "general") or "general"),
                search_type=str(item.get("search_type", "") or ""),
                priority=int(item.get("priority", 2) or 2)
                if str(item.get("priority", "2")).strip().isdigit() else 2,
                domain=str(item.get("domain", dominant_domain) or dominant_domain),
                coverage_goal=str(item.get("coverage_goal", "") or ""),
                minimum_sources=max(
                    minimum_sources,
                    int(item.get("minimum_sources", minimum_sources) or minimum_sources)
                    if str(item.get("minimum_sources", "")).strip().isdigit()
                    else minimum_sources,
                ),
                stop_condition=str(item.get("stop_condition", "") or "").strip(),
                variants=variants,
                depends_on=item.get("depends_on", []),
                agent=str(item.get("agent", "") or "").strip(),
                sense=str(item.get("sense", "") or "").strip(),
                tools=[
                    t for t in _clean_str_list(item.get("tools", ["web_search"]))
                    if t in VALID_TOOLS
                ] or ["web_search"],
                scope=_clean_str_list(item.get("scope", [])),
            )
        )

    # Deduplicate ids so dependency resolution and selection stay coherent.
    seen_ids: Set[int] = set()
    for item in cleaned:
        if item["id"] in seen_ids or item["id"] <= 0:
            item["id"] = max(seen_ids, default=0) + 1
        seen_ids.add(item["id"])

    cleaned = deduplicate_semantic(cleaned)
    cleaned, injected = enforce_axis_coverage(
        cleaned, query, required_axes,
        domain=dominant_domain, minimum_sources=minimum_sources, today=today,
        required_questions=required_questions,
    )

    final = select_plan(cleaned, target, required_axes)

    # Frontier tracks and counter-evidence are CONDITIONAL on the question's
    # own shape, decided in _trends_scope_wanted. They are injected after
    # select_plan because when they apply they are not subject to the target
    # budget; when they do not apply the plan is exactly what was planned.
    trends_scope = _trends_scope_wanted(query)
    if trends_scope:
        final, frontier_injected = enforce_frontier_axes(
            final, query,
            domain=dominant_domain,
            minimum_sources=minimum_sources,
            today=today,
            include_counter_evidence=True,
        )
    else:
        frontier_injected = []
    injected = [*injected, *frontier_injected]
    final = sanitize_dependencies(final)
    final = _assign_intent_senses(final, intent)


    if not final:
        logger.warning("[Planner] All filtered out, fallback used")
        record_fallback("planner")
        return fallback_plan(query, target, required_axes or ("definition", "evidence", "criticism"), today, intent=intent)

    logger.info(
        "[Planner] %d contract(s), axes=%s, search_types=%s, injected=%s, planned_by=%s",
        len(final),
        sorted({item["axis"] for item in final}),
        sorted(diversity_coverage(final)),
        injected or "none",
        directive_meta.get("planned_by", "caller" if critique_feedback else "heuristic"),
    )
    return final
