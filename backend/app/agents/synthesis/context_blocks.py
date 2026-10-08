"""Prompt context blocks and measured evidence accounting.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Renders the decision-grade inputs the writer brief carries (allowed
material, ambiguity/interpretation contracts, analytical guidance, conflicts,
honesty baseline) and the measured evidence block the model cannot be trusted
to produce.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from app.agents.contradiction import summarize_contradictions
from app.agents.evidence_utils import extract_domain
from app.agents.research_quality import IndependenceReport, TemporalProfile
from app.agents.sources import canonical_url, primary_source_share

from app.agents.synthesis.findings import _ledger_warnings
from app.agents.synthesis.primitives import _corroboration, _safe_int


def _render_interpretations_block(intent: Dict[str, Any], ambiguity: Dict[str, Any] | None = None) -> str:
    """Answer-first guidance for an UNDER-SPECIFIED query.

    Distinct from the homonym disambiguation block: the term names ONE thing but
    the question does not say which useful reading is meant.

    The `ambiguity` policy (app/agents/ambiguity.py) decides the shape:
      * ASSUME   — one reading was chosen and researched. State the assumption
                   plainly, then answer that reading. Do NOT hedge across the
                   others; a confident scoped answer is the point.
      * SEPARATE — the readings are genuinely different and both were researched.
                   Each gets its OWN sections, and their evidence is never
                   blended: mixing two reading's claims is what produced a report
                   that answered none of them.
    """
    if not isinstance(intent, dict):
        return ""
    policy = ambiguity if isinstance(ambiguity, dict) else {}
    action = str(policy.get("action", "") or "")

    readings = [
        i for i in (intent.get("interpretations") or [])
        if isinstance(i, dict) and str(i.get("label", "")).strip()
    ]
    # The policy may carry reading labels when intent's are absent (the LLM sense
    # path populates `senses`, not `interpretations`).
    labels = [str(x) for x in (policy.get("interpretations") or []) if str(x).strip()]
    if len(readings) < 2 and len(labels) >= 2:
        readings = [{"label": x} for x in labels]
    if len(readings) < 2:
        return ""

    listed = "\n".join(
        f"  {i + 1}) **{str(r.get('label')).strip()}**"
        + (f" — {str(r.get('description', '')).strip()}" if str(r.get("description", "")).strip() else "")
        for i, r in enumerate(readings[:3])
    )

    if action == "assume":
        assumption = str(policy.get("assumption", "") or "").strip() or str(
            readings[0].get("label", "")
        ).strip()
        return (
            "UNDER-SPECIFIED QUERY — the question can be read in more than one "
            f"useful way. This report ANSWERS THE READING: **{assumption}**.\n"
            f"{listed}\n"
            "Open with ONE short sentence that (a) states which reading is being "
            "used and (b) defines the ambiguous term as it is being used here — "
            'e.g. "Here \'<term>\' is taken to mean <the reading above>." Then '
            "answer that reading directly. Do NOT hedge across the other "
            "readings and do NOT spend sections or citations on them — at most "
            "one clause acknowledging another reading exists is enough. A "
            "confident answer to a clearly stated reading beats a cautious "
            "non-answer to all of them."
        )

    if action == "separate":
        return (
            "MULTIPLE-INTERPRETATION QUERY — the question has genuinely distinct "
            "readings and each was researched separately:\n"
            f"{listed}\n"
            "Give EACH reading its OWN sections and answer it on its own terms. "
            "Keep their evidence apart: a claim gathered for one reading must "
            "never be cited as support for another. Do NOT blend them into a "
            "single averaged narrative — that is how every reading ends up "
            "half-answered."
        )

    return (
        "UNDER-SPECIFIED QUERY — the question can be read in more than one useful "
        "way. Do NOT spend the answer explaining that it is ambiguous. Instead:\n"
        f"{listed}\n"
        "Answer the reading(s) that are materially useful, giving each a short, "
        "direct answer of its own (a sentence to a short paragraph is enough for "
        "the secondary reading). Only if a reading would need substantially "
        "different research to answer should you name it without answering it."
    )


def _render_ambiguity_block(intent: Dict[str, Any]) -> str:
    """Mandatory disambiguation contract for an ambiguous query."""
    if not isinstance(intent, dict) or not intent.get("ambiguity"):
        return ""
    senses = [
        s for s in (intent.get("senses") or [])
        if isinstance(s, dict) and str(s.get("label", "")).strip()
    ]
    if not senses:
        return ""
    listed = "\n".join(
        f"  {i + 1}) **{str(s.get('label')).strip()}**"
        + (f" — {str(s.get('note', '')).strip()}" if str(s.get("note", "")).strip() else "")
        for i, s in enumerate(senses[:3])
    )
    action = str(intent.get("recommended_action", "") or "")
    focus = str((senses[0] or {}).get("label", "")).strip()
    if action == "research_both" and len(senses) > 1:
        structure = (
            "Structure the report so each researched meaning gets its OWN sections "
            "(evidence lines carry [sense: ...] tags — a section about one sense "
            "cites only that sense's facts)."
        )
    else:
        structure = (
            f"The detailed sections focus on meaning 1 ('{focus}'). Do NOT spend "
            "sections or citations on the other meaning(s) — their one "
            "disambiguation line above is enough."
        )
    return (
        "AMBIGUOUS QUERY — the term has distinct meanings:\n"
        f"{listed}\n\n"
        "The Executive Summary MUST open with a numbered disambiguation in EXACTLY "
        "the shape above (one line per sense: number with a closing parenthesis, "
        "bold sense name, em dash, one-clause explanation), then one sentence: "
        f"'Based on your question, this report focuses on meaning 1.' {structure}"
    )


def _render_analytical_guidance(ctx: Dict[str, Any]) -> str:
    """PRIMARY guidance: the AnalystBrief, then the SynthesisPlan, then the
    answer-priority directive.

    Placed at the TOP of the writer prompt so the analytical synthesis is the
    frame the writer writes from, not a footnote to an evidence dump. Renders
    nothing when none of the three are present, so callers can prepend
    unconditionally.
    """
    parts: List[str] = []
    brief = ctx.get("analytical_brief")
    if brief is not None and hasattr(brief, "render_for_writer"):
        try:
            rendered = str(brief.render_for_writer()).strip()
        except Exception:  # noqa: BLE001 - never let the brief break synthesis
            rendered = ""
        if rendered:
            parts.append(
                "WRITE THE ANSWER THE BRIEF IMPLIES — this is your primary "
                "guidance. Start from the central thesis, develop the insights, "
                "organise around the findings, integrate evidence into the "
                "narrative, and preserve the counter-evidence and uncertainty. "
                "Do not simply summarise the evidence below.\n\n"
                + rendered
            )
    plan = ctx.get("synthesis_plan")
    if plan is not None and hasattr(plan, "render_for_writer"):
        try:
            rendered_plan = str(plan.render_for_writer()).strip()
        except Exception:  # noqa: BLE001 - never let the plan break synthesis
            rendered_plan = ""
        if rendered_plan:
            parts.append(rendered_plan)
    # Answer priority (Phase 11): classify the material above into
    # CORE_ANSWER / SUPPORTING / CONTEXT / AUDIT_ONLY so the writer leads with
    # the answer and keeps audit bookkeeping out of the normal answer. Derived
    # from the SAME plan + brief, deterministically, with no extra LLM call.
    try:
        from app.core.answer_priority import render_for_writer as _render_priority

        priority = _render_priority(plan, brief)
    except Exception:  # noqa: BLE001 - priority guidance must never break
        priority = ""
    if priority:
        parts.append(priority.strip())
    return "\n\n".join(parts) + "\n\n" if parts else ""


def _render_context_block(ctx: Dict[str, Any]) -> str:
    """Decision-grade inputs for the LLM brief: conflicts to flag and the
    honesty baseline for the evidence section. Empty when no context passed."""
    if not ctx:
        return ""
    parts: List[str] = []
    if ctx.get("revision"):
        parts.append(
            "REVISION PASS — rewrite your previous draft as the final answer. "
            "Lead with the direct answer to the query in plain language; one idea "
            "per paragraph; cut filler, repetition and tangents; remove any section "
            "that does not serve the query; keep every [n] marker valid and attached "
            "to the sentence it supports; preserve the disambiguation block when the "
            "query was ambiguous."
        )
    contradictions = ctx.get("contradictions", []) or []
    if isinstance(contradictions, list) and contradictions:
        lines = []
        for c in contradictions[:5]:
            if not isinstance(c, dict):
                continue
            lines.append(
                f"- \"{str(c.get('claim_a', ''))[:140]}\" ({c.get('source_a', '')}) "
                f"CONFLICTS WITH \"{str(c.get('claim_b', ''))[:140]}\" ({c.get('source_b', '')})"
            )
        if lines:
            parts.append(
                "Flagged source conflicts (surface these, never silently pick one):\n"
                + "\n".join(lines)
            )
    try:
        conf = float(ctx.get("confidence", "nan"))
        parts.append(f"Pipeline confidence: {conf:.2f} (0.75+ = sufficient).")
    except (TypeError, ValueError):
        pass
    degraded = ctx.get("degraded", []) or []
    if isinstance(degraded, list) and degraded:
        parts.append(f"Stages on deterministic fallback: {', '.join(str(d) for d in degraded)}.")
    total = _safe_int(ctx.get("total_facts", 0))
    verified = _safe_int(ctx.get("verified_count", 0))
    if total:
        parts.append(
            f"Evidence pool: {verified}/{total} facts verified; unverified claims were excluded."
        )
    parts.append(
        "The figures above are INTERNAL METADATA for your judgement only. "
        "Never quote them verbatim in the report body — the appendix states "
        "them, and the body describes evidence strength in words. State any "
        "limitation ONCE: do not repeat formulations like 'the evidence does "
        "not establish', 'what the evidence does not contain', 'cannot be "
        "verified' or 'the research pool' across the answer."
    )

    distribution = ctx.get("evidence_distribution")
    if isinstance(distribution, dict) and any(
        _safe_int(distribution.get(g, 0)) for g in ("A", "B", "C", "D")
    ):
        parts.append(
            "Evidence grades for this pool "
            f"(A {distribution.get('A', 0)}, B {distribution.get('B', 0)}, "
            f"C {distribution.get('C', 0)}, D {distribution.get('D', 0)}).\n"
            "Write with these epistemic tiers and label them explicitly:\n"
            "- ESTABLISHED: A-grade, independently corroborated — state plainly.\n"
            "- STRONG: B-grade — state with a source.\n"
            "- DISPUTED: contradicted claims — present as a disagreement, never pick a side silently.\n"
            "- INFERRED: reasonable synthesis from multiple claims — mark as inference, not fact.\n"
            "- UNKNOWN: no evidence — say so instead of guessing.\n"
            "Never present a D-grade claim or an unsupported number as established fact; "
            "soften it ('one source reports…') or omit it."
        )

    # Evidence-grounded reasoning structure: the deterministic argument the
    # evidence actually supports, rendered from the ReasoningMap built in
    # workflow.synthesizer_node (a no-op when the map is absent or empty).
    reasoning = ctx.get("reasoning")
    if reasoning is not None and hasattr(reasoning, "render_for_writer"):
        try:
            rendered_reasoning = str(reasoning.render_for_writer()).strip()
        except Exception:  # noqa: BLE001 - never let the structure break synthesis
            rendered_reasoning = ""
        if rendered_reasoning:
            parts.append(rendered_reasoning)

    # The SynthesisPlan and AnalystBrief are NOT re-rendered here: they lead the
    # prompt via `_render_analytical_guidance` so the evidence dump cannot
    # overshadow them (Phase 8 context prioritization).

    # Surviving red-team objections belong in the brief, not only in the
    # appendix: a writer that knows the strongest counter-argument writes a
    # report that addresses it instead of one a reader can dismantle.
    findings = ctx.get("redteam_findings") or []
    if isinstance(findings, list) and findings:
        lines = []
        for item in findings[:4]:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement", "") or "").strip()
            if statement:
                lines.append(f"- {statement}")
        if lines:
            parts.append(
                "Standing objections to this evidence (acknowledge, do not ignore):\n"
                + "\n".join(lines)
            )
    feedback = ctx.get("quality_feedback") or []
    if isinstance(feedback, list) and feedback:
        parts.append(
            "QUALITY GATE — your previous draft failed the answer-quality review. "
            "Fix every point below in the corrected report:\n"
            + "\n".join(f"- {str(f)}" for f in feedback[:8])
        )
    if not parts:
        return ""
    return "Research honesty baseline:\n" + "\n".join(parts) + "\n\n"


def _measured_evidence_block(
    ctx: Dict[str, Any],
    usable_facts: Sequence[Dict[str, Any]],
    contradictions: Sequence[Dict[str, Any]],
    *,
    temporal: TemporalProfile | None = None,
    independence: IndependenceReport | None = None,
) -> str:
    """The accounting the model cannot be trusted to produce.

    The model writes an evidence section with numbers it estimates. The real
    counts, the confidence breakdown, the source mix and the composition
    warnings are assembled here from measured state and MERGED INTO the single
    evidence section, rather than appended as a competing one.
    """
    lines: List[str] = []


    distribution = ctx.get("evidence_distribution")
    if isinstance(distribution, dict) and any(
        _safe_int(distribution.get(g, 0)) for g in ("A", "B", "C", "D")
    ):
        lines.append(
            "- Evidence grades: "
            f"A={_safe_int(distribution.get('A', 0))}, "
            f"B={_safe_int(distribution.get('B', 0))}, "
            f"C={_safe_int(distribution.get('C', 0))}, "
            f"D={_safe_int(distribution.get('D', 0))} "
            "(A/B = verified and strongly or independently sourced)"
        )

    # Dating first: a reader deciding whether to act on this report needs to
    # know how old it is before anything else in the accounting.
    if temporal is not None:
        lines.append(temporal.as_of_line())

    urls = [str(f.get("source", "") or "") for f in usable_facts if f.get("source")]
    share = primary_source_share(urls)
    documents = len({canonical_url(u) or u for u in urls} - {""})
    domains = len({extract_domain(u) for u in urls} - {""})
    if independence is not None and independence.effective_sources and (
        independence.effective_sources < domains
    ):
        lines.append(
            f"- Documents read: {documents} across {domains} domain(s), but only "
            f"about {independence.effective_sources} are independent "
            "(the rest carry near-identical wording)"
        )
    else:
        lines.append(f"- Documents read: {documents} across {domains} independent domain(s)")
    lines.append(
        f"- Primary sources (official filings, papers, datasets, standards): {share:.0%}"
    )
    verified = sum(1 for f in usable_facts if f.get("verified") is True)
    lines.append(
        f"- Claims verified against their cited source: {verified}/{len(usable_facts)}"
    )
    corroborated = sum(1 for f in usable_facts if _corroboration(f) > 1)
    if corroborated:
        lines.append(f"- Claims independently corroborated by 2+ sources: {corroborated}")
    conflict = summarize_contradictions(contradictions)
    if conflict["total"]:
        lines.append(
            f"- Source conflicts detected: {conflict['cross_source']} across sources "
            f"({conflict['severe']} severe); reported as ranges above"
        )

    report = ctx.get("confidence_report")
    rendered = ""
    if report is not None and hasattr(report, "render"):
        try:
            rendered = str(report.render()).strip()
        except Exception:  # noqa: BLE001 - never let a panel break the report
            rendered = ""

    # What the adjudication concluded belongs in the report, not just in the
    # return value: a reader who sees "sources disagree" removed deserves to
    # know it was removed because the figures were measuring different years.
    epistemics = ctx.get("epistemics")
    if epistemics is not None and hasattr(epistemics, "notes"):
        for note in (epistemics.notes() or []):
            lines.append(f"- {note}")

    block = "\n".join(lines)
    warnings = _ledger_warnings(ctx)
    if warnings:
        block += "\n" + warnings
    if rendered:
        block += "\n\n" + rendered
    return block


def _objection_blocks(ctx: Dict[str, Any]) -> List[str]:
    """Standing objections and falsifiers — analytical and audit profiles only."""
    blocks: List[str] = []
    findings = ctx.get("redteam_findings") or []
    if isinstance(findings, list) and findings:
        lines = ["## Standing Objections", ""]
        for item in findings[:5]:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement", "") or "").strip()
            if not statement:
                continue
            test = str(item.get("test", "") or "").strip()
            lines.append(f"- {statement}" + (f" Resolve by: {test}" if test else ""))
        if len(lines) > 2:
            blocks.append("\n".join(lines))

    changers = ctx.get("what_would_change_our_mind") or []
    if isinstance(changers, list) and changers:
        lines = ["## What Would Change This Conclusion", ""]
        lines += [f"- {str(c).strip()}" for c in changers[:5] if str(c).strip()]
        if len(lines) > 2:
            blocks.append("\n".join(lines))
    return blocks
