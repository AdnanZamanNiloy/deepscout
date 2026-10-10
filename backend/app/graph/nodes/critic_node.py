from __future__ import annotations

"""Node factory `make_critic_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from typing import Any, Dict, List
from app.agents.orchestrator import MODE_CONFIDENCE_TARGET
from app.core.confidence import compute_confidence
from app.core.contradictions import find_contradictions
from app.core.degradation import has_provider_degradation, take_fallbacks
from app.graph.evidence import _attach_evidence, _corroboration_queries, _counter_evidence_queries
from app.graph.nodes.helpers import _assess_focus
from app.graph.state import CriticUpdate, ResearchState

from app.core.logging import get_logger

logger = get_logger(__name__)


def make_critic_node(llm, critic_agent):
    async def _node(state: ResearchState) -> CriticUpdate:
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
            from app.core.contradictions import resolve_contradictions

            contradictions = resolve_contradictions(contradictions)
        except Exception as exc:  # resolution must never break a run
            logger.warning("contradiction_resolution_failed", error=str(exc), exc_info=exc)
        if contradictions:
            logger.info(
                "contradictions_found",
                count=len(contradictions),
                unresolved=sum(1 for c in contradictions if not c.get("resolved")),
            )


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

        # FUNDAMENTAL-GAP CONVERGENCE. The critic's own reason and gaps say
        # whether the required KIND of evidence is absent — "no source ranks
        # these", "only indirect evidence". When that same conclusion is reached
        # in consecutive rounds it is a property of the QUESTION, not a slow
        # search, and the loop must stop rather than reopen for an angle the
        # evidence cannot supply. Recorded here so the decision engine and the
        # writer can both read it.
        convergence_diagnosis: Dict[str, Any] = {}
        gap_history = list(state.get("gap_history") or [])
        try:
            from app.agents.convergence import assess_fundamental_gap

            reviews = [str(critique.get("reason", "") or "")]
            reviews.extend(str(g) for g in (critique.get("gaps") or ()))
            reviews.extend(str(g) for g in (critique.get("gate_failures") or ()))
            gap = assess_fundamental_gap(
                state["query"], reviews, history=gap_history
            )
            convergence_diagnosis = gap.to_dict()
            if gap.signature:
                gap_history.append(gap.signature)
                state["gap_history"] = gap_history
            if gap.identified:
                logger.info(
                    "fundamental_gap_converged",
                    rounds=gap.rounds, signature=gap.signature,
                )
        except Exception as exc:
            logger.warning("convergence_assessment_failed", error=str(exc), exc_info=exc)

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
            from app.agents.epistemic.asymmetry import assess_epistemics

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
        # Recorded separately from the number: the depth controller must be able
        # to tell "the evidence is weak" (search more) from "the evidence could
        # not be measured because extraction was degenerate" (searching cannot
        # help, and the cap keeps it under target forever).
        state["confidence_degraded_capped"] = bool(
            breakdown.get("degraded_capped", False)
        )

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

        # CRITICISM LEDGER (critic -> targeted task -> verify resolved). Each
        # gate failure and gap this critic raised becomes a tracked task; on the
        # next pass it is re-checked against the NEW pool and marked
        # resolved/attempted/exhausted. This is what makes the critic
        # ACTIONABLE rather than advisory: an unaddressed criticism keeps the
        # loop open while budget remains, and an unclosable one is disclosed as
        # a limitation instead of vanishing. Targets are appended to
        # improved_queries so the search layer aims directly at the criticism,
        # not only at generic gap-filling.
        criticism_ledger: Dict[str, Any] = {}
        try:
            from app.core.criticism_ledger import (
                update as _update_ledger,
                unresolved_targets as _unresolved_targets,
            )

            settings_for_ledger = getattr(llm, "settings", None)
            ledger_max = max(
                1, int(getattr(settings_for_ledger, "max_criticism_attempts", 2) or 2)
            )
            criticism_ledger = _update_ledger(
                state.get("criticism_ledger"),
                critique,
                state.get("facts", []),
                iteration=int(state.get("iteration", 0)),
                max_attempts=ledger_max,
            )
            state["criticism_ledger"] = criticism_ledger
            for target in _unresolved_targets(criticism_ledger):
                # A target is the axis/dimension name; make it a search-ready
                # query rather than a bare token so the search layer can use it.
                q = f"{target} {state['query']}"
                q = " ".join(q.split())
                if q and q not in improved:
                    improved.append(q)
        except Exception as exc:
            logger.warning("criticism_ledger_failed", error=str(exc), exc_info=exc)

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

        # FOCUS-DIVERGENCE HISTORY. Drift and concentration measured against the
        # ORIGINAL question, appended per pass. A drift that FAILS TO IMPROVE for
        # consecutive rounds is structural — the searchable sources simply do not
        # cover the question — and the corrective searches are adding off-topic
        # evidence (the run drifted 0.50 -> 0.61 while expanding). Recording the
        # series lets the stopping controller converge on that, instead of
        # re-searching toward a question the sources cannot answer. Run-scoped on
        # state; bounded to the last few rounds.
        focus_history = list(state.get("focus_history") or [])
        if focus_report:
            focus_history.append(
                {
                    "off_query_share": float(focus_report.get("off_query_share", 0.0) or 0.0),
                    "concentration": float(focus_report.get("concentration", 0.0) or 0.0),
                    "drifted": bool(focus_report.get("drifted")),
                    "concentrated": bool(focus_report.get("concentrated")),
                }
            )
            focus_history = focus_history[-6:]

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
            "facts": enriched_facts,
            "corroboration_queries": corroboration_queries,
            "corroboration_registry": corroboration_registry,
            "investigation_state": inv_state,
            "focus": {
                "report": focus_report or {},
                "queries": list((focus_state or {}).get("queries", ()) or ()),
            },
            "convergence": convergence_diagnosis,
            "gap_history": gap_history,
            "criticism_ledger": criticism_ledger,
            "focus_history": focus_history,
        }
    return _node
