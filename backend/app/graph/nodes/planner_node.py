from __future__ import annotations

"""Node factory `make_planner_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from typing import Any, List
import datetime
from app.agents.planner import normalize_text
from app.graph.nodes.helpers import _extract_question_text, _merge_questions
from app.graph.state import PlannerUpdate, ResearchState

from app.core.logging import get_logger

logger = get_logger(__name__)


def make_planner_node(llm, planner_agent):
    async def _node(state: ResearchState) -> PlannerUpdate:
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
    return _node
