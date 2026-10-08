from __future__ import annotations

"""Node factory `make_synthesizer_node`.

The state->context assembly (the `base_context` dict, including the
`"convergence": state.get("convergence")` seam) stays in `app/graph/workflow.py`
via the injected `build_context` callable; this module holds the orchestration
half (outline/plan/analyst build, synthesize-and-score, the single revision
pass). Named dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from typing import Any, Dict

from app.agents.answer_quality import evaluate_answer
from app.agents.answer_conformance import check_answer_conformance
from app.agents.thesis_fidelity import check_thesis_fidelity
from app.core.degradation import take_fallbacks
from app.core.logging import get_logger
from app.graph.state import ResearchState, SynthesizerUpdate

logger = get_logger(__name__)


def make_synthesizer_node(
    llm,
    synthesizer_agent,
    build_context,
    verify_answer_support,
):
    async def _node(state: ResearchState) -> SynthesizerUpdate:
        base_context, evidence_distribution, intent, usable = build_context(state, llm)
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
            # The synthesizer mirrors its machine-owned provenance into the dict
            # it was HANDED, and it is handed a copy (`{**ctx, ...}` above), so
            # the write-back is invisible to base_context — and therefore to the
            # caller. `synthesis_machine_notes` was copied back explicitly and
            # everything else was silently stranded in the local copy, which is
            # why the answer-construction decision read as empty on state. Copy
            # the whole set, not just the one key someone happened to need.
            for _key in (
                "definition_lock",
                "ranking_basis",
                "report_status",
                "answer_construction",
                "answer_construction_audit",
                "consistency_audit",
                "convergence_audit",
                "definition_audit",
                "ranking_audit",
            ):
                if _key in synthesis_ctx:
                    ctx[_key] = synthesis_ctx[_key]
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
            # Answer-construction decision + its audit, mirrored back onto
            # base_context by _mirror_machine_notes. Promoted here because the
            # audit is the only place the run records WHICH of the three answer
            # states it landed in and whether the prose obeyed it.
            "answer_construction": base_context.get("answer_construction") or {},
            "answer_construction_audit": base_context.get("answer_construction_audit") or {},
        }
    return _node
