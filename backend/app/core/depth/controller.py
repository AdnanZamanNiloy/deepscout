from __future__ import annotations

from typing import Any, Dict, List

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


from app.core.depth.checks import (
    _actionable_uncovered_axes,
    _budget_checks,
    _confidence_target,
    _convergence_checks,
    _criticism_checks,
    _focus_checks,
    _focus_diverging,
    _min_iterations,
    _novel_followups,
    _two_consecutive_stalls,
)
from app.core.depth.constants import (
    DECISION,
    DEFAULT_MINIMUM_SOURCES,
    MIN_AXES_COVERED,
)
from app.core.depth.signals import (
    _axes_below_threshold,
    _axes_covered,
    _axis_imbalance,
    _exhausted_claim_keys,
    _high_impact_uncorroborated,
    _needs_corroboration_count,
    _norm_claim_key,
    _severe_contradictions,
    _thin_dimensions,
    _uncovered_axes,
)


def evaluate(state: Dict[str, Any], settings: Settings | None = None) -> Dict[str, Any]:
    """Return the decision inputs and which rules fired — pure and inspectable."""
    settings = settings or get_settings()
    critique = state.get("critique", {})
    iteration = int(state.get("iteration", 0))
    max_iterations = int(state.get("max_iterations", 3))
    max_depth = int(settings.max_research_depth or 0) or max_iterations
    ceiling = max(max_iterations, max_depth)
    confidence = float(state.get("confidence", 0.0))
    history = list(state.get("confidence_history", []))
    improved = critique.get("improved_queries") or []

    minimum_sources = DEFAULT_MINIMUM_SOURCES
    for q in state.get("sub_questions", []):
        if isinstance(q, dict) and q.get("minimum_sources"):
            minimum_sources = max(1, int(q["minimum_sources"]))
            break

    axes_below = _axes_below_threshold(state, minimum_sources)
    axes_covered = _axes_covered(state, minimum_sources)
    uncovered = _uncovered_axes(state)
    target = _confidence_target(state, settings)
    budget = _budget_checks(state)
    focus = _focus_checks(state)
    novel = _novel_followups(state)
    min_iters = _min_iterations(state)
    needs_corroboration = _needs_corroboration_count(state)
    severe_contradictions = _severe_contradictions(state)
    high_impact = _high_impact_uncorroborated(state)
    thin = _thin_dimensions(state)
    criticism = _criticism_checks(state)

    # Exhausted claims are acknowledged limitations, not actionable gaps. Only
    # the ACTIVE (non-exhausted) high-impact gaps may drive expansion or block
    # a sufficiency stop; an exhausted-only gap set does neither. Claims the
    # investigation state has no entry for, or a non-exhausted entry for,
    # remain active — an un-attempted or attempted-with-budget gap is real.
    exhausted_keys = _exhausted_claim_keys(state)
    active_high_impact = [
        h for h in high_impact if _norm_claim_key(str(h.get("claim", ""))) not in exhausted_keys
    ]
    active_uncorroborated = len(active_high_impact)

    sufficiency_met = (
        confidence >= target
        and not axes_below
    )

    # Holistic evidence-sufficiency: confidence at target, axes covered, no thin
    # dimension, no uncorroborated important claim, no unresolved severe
    # contradiction. `sufficiency_met` keeps its narrower (confidence + per-axis
    # source floor) meaning; this is the stricter shape, reported for the trace
    # so a stop is never explained as "evidence sufficient" when part of the
    # evidence shape was in fact missing.
    evidence_sufficient = (
        sufficiency_met
        and not uncovered
        and not thin
        and active_uncorroborated == 0
        and severe_contradictions == 0
    )

    # A run whose evidence was produced by the deterministic fallback can never
    # reach `target`: DEGRADED_CAP (0.55) sits below every mode target (0.60 to
    # 0.85), so the confidence term of `sufficiency_met` is permanently false
    # and every remaining iteration was spent re-extracting the same sources
    # until the iteration or budget ceiling stopped it. That is a measurement
    # deficit, not an evidence deficit, and more searching cannot repair it.
    #
    # So the cap excuses the CONFIDENCE term only, and only once every other
    # evidence condition is satisfied on its own merits — uncovered axes, thin
    # dimensions, uncorroborated high-impact claims and severe contradictions
    # all still block. Confidence itself stays capped, so the run is still
    # delivered below the sufficiency threshold and still carries its
    # "confidence capped" disclosure; it just stops re-running a search that
    # cannot change the measurement.
    degraded_capped = bool(state.get("confidence_degraded_capped", False))
    confidence_unmeasurable = degraded_capped and confidence < target
    degraded_stop = (
        confidence_unmeasurable
        and not axes_below
        and not uncovered
        and not thin
        and active_uncorroborated == 0
        and severe_contradictions == 0
    )

    # Concrete, human-readable triggers — the WHY behind expand/finalize, so
    # the trace/UI can explain the decision instead of showing a bare verdict.
    # Deterministic ordering (sorted/ranked) so the reason string is stable.
    reasons: List[str] = []
    if severe_contradictions:
        reasons.append(
            f"{severe_contradictions} unresolved severe contradiction"
            + ("s" if severe_contradictions != 1 else "")
        )
    if thin:
        reasons.append(
            f"{len(thin)} thin dimension" + ("s" if len(thin) != 1 else "")
        )
    if active_high_impact:
        reasons.append(
            f"{active_uncorroborated} high-impact claim"
            + ("s" if active_uncorroborated != 1 else "")
            + " uncorroborated"
        )
    if uncovered:
        reasons.append(
            f"{len(uncovered)} uncovered planned axe" + ("s" if len(uncovered) != 1 else "")
        )
    if not reasons and not sufficiency_met:
        # Nothing structural is wrong, so the run is short of target purely on
        # confidence. Saying "evidence sufficient" here was the misleading case
        # `evidence_sufficient` was computed for and never read: the trace
        # claimed sufficiency on runs that were expanding, or stopping short,
        # for want of confidence alone.
        if confidence_unmeasurable:
            reasons.append(
                "confidence held below target by the degraded-extraction cap — "
                "searching further cannot change the measurement"
            )
        else:
            reasons.append(
                f"confidence {confidence:.2f} below target {target:.2f}"
            )


    checks = {
        "sufficiency_met": sufficiency_met,
        "evidence_sufficient": evidence_sufficient,
        "sufficiency_stop": bool(critique.get("is_sufficient", False)) or sufficiency_met,
        "critic_sufficient": bool(critique.get("is_sufficient", False)),
        # Degraded-extraction stop. Disjunct of the sufficiency stop, never a
        # replacement for it: a run that is short of evidence for any OTHER
        # reason keeps expanding, and `degraded_stop` is False whenever any
        # structural evidence condition fails.
        "degraded_capped": degraded_capped,
        "degraded_stop": degraded_stop,
        "marginal_gain_stop": _two_consecutive_stalls(history, settings.min_marginal_gain),
        "ceiling_reached": iteration >= ceiling,
        "min_iterations_not_reached": iteration < min_iters,
        # Coverage-gap detection (manual 2.8): compare COVERED axes (verified
        # facts via source attribution) against the axes the planner scoped.
        "coverage_gap": bool(improved) and (
            len(axes_covered) < MIN_AXES_COVERED
            or _axis_imbalance(state)
            or bool(axes_below)
        ),
        "axes_below_threshold": axes_below,
        "axes_covered": axes_covered,
        # Evidence-first stopping signals (research-loop fix):
        "uncovered_axes": uncovered,
        # `needs_corroboration_count` is the RAW count (all single-source
        # important claims) and stays for backwards compatibility. The ACTIVE
        # count excludes exhausted claims (acknowledged limitations); it is
        # what evidence_sufficient and the expansion gate read.
        "needs_corroboration_count": needs_corroboration,
        "exhausted_gap_count": len(exhausted_keys),
        "active_high_impact_uncorroborated_count": active_uncorroborated,
        "severe_contradictions": severe_contradictions,
        # Step 3 adaptive depth: impact-ranked corroboration gaps + thin
        # dimensions. `high_impact_uncorroborated` is the RAW ranked list for
        # trace/UI; `active_high_impact_uncorroborated` is what still drives
        # expansion.
        "high_impact_uncorroborated": high_impact,
        "high_impact_uncorroborated_count": len(high_impact),
        "active_high_impact_uncorroborated": active_high_impact,
        "thin_dimensions": thin,
        "counter_evidence_attempted": bool(state.get("counter_evidence_attempted", False)),
        # Feature-11 signals now live:
        "confidence_target": target,
        "novel_followups": novel,
        "no_novel_queries": bool(improved) and not novel,
        "budget": budget,
        "budget_stop": bool(budget["exhausted"] or not budget["can_afford_pass"]),
        # Query-anchored focus: drift and concentration against the ORIGINAL
        # question, which no evidence-size signal above can express.
        "focus": focus,
        # Critic-to-task ledger: unfinished criticisms (open/attempted) that
        # still have budget block a soft stop; exhausted ones are limitations.
        "criticism": criticism,
        "active_criticism_count": int(criticism.get("active_count", 0) or 0),
        # Explainability: the ordered trigger list behind the decision.
        "decision_reasons": reasons,
    }
    checks["decision_reason"] = "; ".join(reasons) if reasons else "evidence sufficient"
    return checks


def decide(state: Dict[str, Any], settings: Settings | None = None) -> DECISION:
    """Route decision only. For the checks behind it, use
    `decide_with_checks` — there is deliberately no module-level cache, so
    two concurrent runs can never read each other's decision."""
    decision, _ = decide_with_checks(state, settings)
    return decision


def decide_with_checks(
    state: Dict[str, Any], settings: Settings | None = None
) -> tuple[DECISION, Dict[str, Any]]:
    """Return (decision, checks) for a single evaluation.

    The checks live only for this call. Earlier versions stashed them in a
    module-level dict, which concurrent runs clobbered and which forced
    tests to call `decide()` then read global state out of band; callers
    now get an explicit value they own.
    """
    checks = evaluate(state, settings)

    def _with_reason(decision: DECISION, reason: str) -> tuple[DECISION, Dict[str, Any]]:
        """Stamp the concrete reason for this call (checks are per-call)."""
        checks["decision"] = decision
        checks["decision_reason"] = reason
        return decision, checks

    # ------------------------------------------------------------------
    # Hard walls are ABSOLUTE — nothing below can preempt them. Budget and the
    # iteration/depth ceiling are the anti-infinite-loop guarantee. The
    # ceiling is checked BEFORE every evidence-completeness block so an
    # uncovered axis, an uncorroborated claim or a severe contradiction can
    # only ever trigger expansion while iterations remain — never past the
    # ceiling. `hard_wall_reached` (used by route_after_critic) mirrors both.
    # ------------------------------------------------------------------
    if checks["budget_stop"]:
        # Budget is a hard wall: never expand into a pass we cannot pay for.
        return _with_reason(
            "finalize",
            "hard wall: research budget exhausted (dollars/tokens/calls/time)",
        )
    if checks["ceiling_reached"]:
        # The iteration/depth ceiling is a hard wall alongside budget: at the
        # limit the run finalizes even if gaps remain (they become limitations).
        reason = (
            "hard wall: iteration/depth ceiling reached; remaining gaps "
            "recorded as limitations"
        )
        if checks["decision_reasons"]:
            reason += f" ({checks['decision_reason']})"
        return _with_reason("finalize", reason)

    # Mode demands a minimum depth (audit re-scopes even a sufficient-looking
    # pass 1): only the hard walls above may preempt this.
    if checks["min_iterations_not_reached"]:
        return _with_reason(
            "expand",
            f"mode minimum depth not reached (iteration < {_min_iterations(state)})",
        )

    # ------------------------------------------------------------------
    # Evidence-completeness hard-blocks. These preempt every SOFT stop
    # (sufficiency, marginal gain, no-novel-queries): a run that still has an
    # unsourced planned angle, an uncorroborated important claim, a severe open
    # contradiction, or a thinly-evidenced planned dimension must not finalize
    # while a useful pass can still run.
    # ------------------------------------------------------------------
    # SEMANTIC GAP: STOP SEARCHING. A blocker about what the question MEANS is
    # not an evidence gap and cannot be closed by another pass. Checked BEFORE
    # every evidence-completeness block, because those blocks will otherwise
    # force a pass to "fix" a definition by finding more pages — which is exactly
    # how an ambiguous query accumulated research programmes for every reading
    # while the reviewer correctly kept saying none of them defined the ask.
    # The ambiguity policy already had its chance to ask (see
    # app/agents/ambiguity.py); once we are mid-loop, more searching is waste.
    if (state.get("critique") or {}).get("semantic_gap"):
        return _with_reason(
            "finalize",
            "the remaining blocker is a definition/interpretation problem, not "
            "missing evidence; further searching cannot resolve it, so it is "
            "recorded as a stated limitation",
        )

    # FUNDAMENTAL GAP: CONVERGE. The reviewer has concluded, in consecutive
    # rounds, that the KIND of evidence the question requires is absent ("no
    # source ranks these", "only indirect evidence"). That is a property of the
    # question, not a slow search: every block below can only add MORE searching,
    # and more searching cannot supply evidence that does not exist. Checked
    # before them so an unsourced planned angle cannot reopen the loop after the
    # fundamental gap is already established.
    convergence = _convergence_checks(state)
    if convergence["converge"]:
        return _with_reason("finalize", convergence["decision_reason"])

    # FOCUS GATE. Checked before the axis checks because it asks a different and
    # prior question: is this pass still about the user's question? Drift and
    # concentration are not "not enough evidence" — they are "the wrong
    # evidence", and every evidence-size check below will happily pass them.
    #
    # A narrow question is exempt on purpose. It is SUPPOSED to be answered from
    # one dimension, so steering it elsewhere is the drift this gate exists to
    # prevent.
    focus = _focus_checks(state)
    if focus["redirect"]:
        # DRIFT CONVERGENCE (structural), checked FIRST. A drift that has failed
        # to improve across consecutive rounds means the corrective searches are
        # adding off-topic evidence, not closing the gap — the sources reached do
        # not cover the question. This is a property of the question vs the
        # searchable web, not a slow run, so MORE searching cannot fix it.
        # Without this the redirect re-forced a pass every round while drift rose
        # (0.50 -> 0.61) and the run ballooned to 80 sources with gaps still open.
        diverging = _focus_diverging(state)
        if diverging["diverging"]:
            return _with_reason("finalize", diverging["reason"])
        # A redirect is only useful if there is somewhere NEW to redirect to. If
        # every uncovered/high-priority dimension has already been searched to
        # exhaustion, the drift cannot be corrected by more searching; finalize
        # with the gap recorded rather than spending another pass re-confirming
        # it. This is the convergence condition: drift forces a corrective pass,
        # and a correction that has been attempted and failed stops being forced.
        if focus["uncovered_priorities"] and not focus["actionable_left"]:
            return _with_reason(
                "finalize",
                "drift detected but every uncovered high-priority dimension has "
                "already been searched to exhaustion; recording as a limitation",
            )
        return _with_reason("expand", focus["decision_reason"])

    if checks["uncovered_axes"]:
        # A planned angle with zero verified facts is a hole, not a rounding
        # error. Expanding is the only way to fill it — UNLESS that angle has
        # already been searched to exhaustion, in which case the hole is a
        # finding (the question is not answerable from available sources) and
        # re-searching it buys nothing. Without this gate an unsupported axis
        # forced a pass every round forever.
        actionable = _actionable_uncovered_axes(state, checks["uncovered_axes"])
        if not actionable:
            return _with_reason(
                "finalize",
                f"{len(checks['uncovered_axes'])} uncovered planned axe"
                + ("s" if len(checks["uncovered_axes"]) != 1 else "")
                + " but all have been searched to exhaustion; recorded as "
                "limitations",
            )
        return _with_reason(
            "expand",
            f"{len(actionable)} uncovered planned axe"
            + ("s" if len(actionable) != 1 else "")
            + ": " + ", ".join(actionable[:3]),
        )

    # Only ACTIVE (non-exhausted) corroboration gaps block a stop or force a
    # pass. An exhausted claim is an acknowledged limitation — re-finding the
    # same dead end is spend, not research; the workflow already excludes it
    # from the next corroboration pass.
    gaps_present = (
        checks["active_high_impact_uncorroborated_count"] > 0
        or checks["severe_contradictions"] > 0
        or bool(checks["thin_dimensions"])
    )
    if gaps_present:
        if checks["novel_followups"]:
            return _with_reason("expand", checks["decision_reason"])
        # Nothing new left to search: record as limitations rather than burn a
        # pass re-finding the same pages (prevents an unbounded loop).
        reason = checks["decision_reason"] or "evidence gaps remain"
        return _with_reason(
            "finalize",
            f"evidence gaps remain but nothing novel is left to search ({reason}); "
            "recorded as limitations",
        )

    # A run whose confidence is pinned under target by the degraded-extraction
    # cap stops here, BEFORE the critic-driven expansion below. By this point
    # every structural evidence condition has already passed (that is what
    # `degraded_stop` requires), so the only thing left arguing for another
    # pass is a critic judging claims that are unrewritten extracted source
    # text. That judgement cannot improve with more of the same extraction, and
    # acting on it is what spent every remaining iteration before the ceiling
    # stopped the run. Confidence stays capped and the cap stays disclosed, so
    # the answer is still delivered as below-threshold.
    if checks["degraded_stop"]:
        return _with_reason(
            "finalize",
            "degraded extraction capped confidence below target and no evidence "
            "gap remains actionable; recorded as a capped-confidence result "
            "rather than re-searched",
        )

    # A critic that explicitly said "insufficient" and proposed actionable new
    # queries forces a pass — the model verdict is not waivable by a measured
    # sufficiency that ignores what the critic saw.
    if not checks["critic_sufficient"] and checks["novel_followups"]:
        return _with_reason(
            "expand",
            "critic reported insufficient evidence and proposed novel follow-up queries",
        )

    # CRITIC-LEDGER GATE. The critic raised specific, tracked criticisms
    # (app/core/criticism_ledger.py) that are still open/attempted — i.e. the
    # condition it named has NOT been resolved by any pass so far. While such a
    # criticism is actionable (budget remains) it blocks a soft stop, so a
    # criticism the model raised cannot be silently dropped just because
    # aggregate confidence drifted up. A criticism whose attempt budget is spent
    # is EXHAUSTED and deliberately does NOT block here — it is disclosed as a
    # limitation instead, which is what prevents an unclosable criticism from
    # looping forever.
    if checks["active_criticism_count"] > 0:
        if checks["novel_followups"]:
            targets = ", ".join(checks["criticism"].get("targets", [])[:3])
            return _with_reason(
                "expand",
                f"{checks['active_criticism_count']} unresolved critic-raised "
                f"evidence gap(s) still actionable"
                + (f": {targets}" if targets else ""),
            )
        # No novel query left to act on the criticism: disclose rather than loop.
        return _with_reason(
            "finalize",
            f"{checks['active_criticism_count']} critic-raised evidence gap(s) "
            "remain but no novel query is left to close them; recorded as "
            "limitations",
        )

    # ------------------------------------------------------------------
    # Soft stops. Reached only when the evidence base is complete.
    # ------------------------------------------------------------------
    if checks["sufficiency_stop"]:
        reason = "evidence sufficient: confidence at target and planned axes covered"
        if checks["exhausted_gap_count"] and not checks["active_high_impact_uncorroborated_count"]:
            reason += (
                f"; {checks['exhausted_gap_count']} exhausted gap"
                + ("s" if checks["exhausted_gap_count"] != 1 else "")
                + " recorded as acknowledged limitations"
            )
        return _with_reason("finalize", reason)
    if checks["marginal_gain_stop"]:
        return _with_reason(
            "finalize",
            "no meaningful marginal confidence gain for two consecutive iterations",
        )
    if checks["no_novel_queries"]:
        # Every proposed follow-up duplicates a search we already ran —
        # expanding would burn a pass to re-find the same pages.
        return _with_reason(
            "finalize",
            "no novel queries left: every follow-up duplicates an already-run search",
        )

    # Expansion trigger: critic sees a specific gap AND axis coverage is poor.
    if checks["coverage_gap"]:
        return _with_reason(
            "expand",
            "critic-reported coverage gap: axis spread/imbalance or a below-floor axis",
        )

    # Default: trust the critic's loop decision (it returned insufficient
    # with improved_queries even if axis data couldn't confirm a gap).
    if state.get("critique", {}).get("improved_queries"):
        return _with_reason(
            "expand",
            "critic returned insufficient with actionable follow-up queries",
        )

    return _with_reason("finalize", "no expansion trigger and no gaps detected")


def hard_wall_reached(state: Dict[str, Any], settings: Settings | None = None) -> bool:
    """True when only a hard wall (budget/time or iteration ceiling) can stop.

    Public so `route_after_critic` can tell an evidence gate it cannot act on
    (hard wall) from one it should honour — the ordering bug that let a soft
    depth-controller stop defeat the evidence gate.
    """
    checks = evaluate(state, settings)
    return bool(checks["budget_stop"] or checks["ceiling_reached"])


def stop_reason(state: Dict[str, Any], settings: Settings | None = None) -> str | None:
    """Deterministic explanation of why the pipeline stopped early — for the
    report's Limitations section. Returns None when nothing unusual fired."""
    checks = evaluate(state, settings)
    if checks["budget_stop"] and not checks["sufficiency_stop"]:
        budget = checks["budget"]
        util = float(budget.get("utilization") or 0.0)
        return (
            "Stopped early on the research budget (dollars/tokens/calls/time "
            f"utilization {util:.0%}); finalizing with the evidence gathered so far."
        )
    if checks["marginal_gain_stop"] and not checks["sufficiency_stop"]:
        return (
            "Stopped early on marginal information gain: confidence improved by "
            "less than the minimum gain threshold for two consecutive iterations."
        )
    if checks["no_novel_queries"] and not checks["sufficiency_stop"]:
        return (
            "Stopped early because every remaining question duplicated a search "
            "already run; the gaps are recorded below as limitations instead."
        )
    # Evidence-driven early stop: the run hit a hard wall while measurable
    # evidence gaps remained. Name them so the Limitations section reflects why
    # the report is not deeper, not just that it stopped.
    if (checks["ceiling_reached"] or checks["budget_stop"]) and checks["decision_reasons"]:
        return (
            "Stopped at the research depth limit with outstanding evidence gaps "
            f"({checks['decision_reason']}); they are recorded below as limitations."
        )
    return None
