"""Mandatory report sections: enforced post-assembly, per profile.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Decides whether a conditional section would carry real content and
renders a missing required section from measured state only — never inventing
facts.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Set, Tuple

from app.agents.synthesis.findings import (
    _missing_skeptical_angles,
    _render_finding_line,
)
from app.agents.synthesis.profiles import PROFILE_BRIEF, ReportProfile
from app.agents.synthesis.primitives import _corroboration, _safe_int
from app.agents.synthesis.ranking import _has_numeric_facts, _stratified_top_facts
from app.agents.synthesis.sections import (
    _CANONICAL_ALIASES,
    _canonical_key,
    _normalized_aliases,
    _present_section_keys,
    _REQUIRED_KEY_BY_HEADING,
    _normalize_heading,
)


def _section_has_substance(
    key: str,
    *,
    ctx: Dict[str, Any],
    usable_facts: Sequence[Dict[str, Any]],
    contradictions: Sequence[Dict[str, Any]],
    cited_facts: Sequence[Dict[str, Any]],
) -> bool:
    """Would this conditional section say anything? If not, it is not added.

    This is the difference between a report and a form. A "Key Figures" section
    reading "no quantitative figures were extracted" and a Counterarguments
    section reading "no counter-evidence search was completed" are both worse
    than their own absence on a question that never needed them. Anything
    ADVERSE that was actually measured always passes this test.
    """
    if key == "key figures":
        return _has_numeric_facts(cited_facts or usable_facts, minimum=3)
    if key == "counterarguments":
        return bool(contradictions) or bool(ctx.get("redteam_findings")) or bool(
            _missing_skeptical_angles(ctx)
        )
    if key == "limitations":
        if ctx.get("coverage_gaps") or ctx.get("degraded"):
            return True
        weak = sum(
            1 for f in usable_facts
            if f.get("verified") is not True or _corroboration(f) <= 1
        )
        return weak > 0
    if key == "open questions":
        gaps = ctx.get("coverage_gaps")
        return bool(isinstance(gaps, (list, tuple)) and any(str(g).strip() for g in gaps))
    if key == "key findings":
        return bool(cited_facts or usable_facts)
    return True


def _render_required_section(
    canonical: str,
    *,
    ctx: Dict[str, Any],
    usable_facts: Sequence[Dict[str, Any]],
    contradictions: Sequence[Dict[str, Any]],
    cited_facts: Sequence[Dict[str, Any]] = (),
    profile: ReportProfile = PROFILE_BRIEF,
) -> str:
    """Deterministic body for a missing required section, from measured state.

    Never invents facts: each section is assembled from signals the pipeline
    already measured (grade distribution, verified/corroborated counts,
    contradictions, red-team findings, source mix). A genuinely empty signal
    produces an honest "none detected" statement, not a fabricated one.
    """
    total = len(usable_facts)
    verified = sum(1 for f in usable_facts if f.get("verified") is True)
    corroborated = sum(1 for f in usable_facts if _corroboration(f) > 1)
    distribution = ctx.get("evidence_distribution")
    dist = distribution if isinstance(distribution, dict) else {}
    a = _safe_int(dist.get("A", 0))
    b = _safe_int(dist.get("B", 0))
    c = _safe_int(dist.get("C", 0))
    d = _safe_int(dist.get("D", 0))

    if canonical == "Key Findings":
        lines = ["## Key Findings", ""]
        source_facts = list(cited_facts) if cited_facts else list(usable_facts)
        top = _stratified_top_facts(source_facts, per_angle=2, cap=profile.max_findings)
        for fact in top:
            rendered = _render_finding_line(fact, verbose=profile.verbose_findings)
            if rendered:
                lines.append(rendered)
        if len(lines) == 2:
            lines.append("- No verified findings were extracted from the available evidence.")
        return "\n".join(lines)

    if canonical == "Evidence & Confidence":
        if total == 0:
            body = "No verified evidence was available to grade."
        else:
            body = (
                f"{verified} of {total} claims were verified against their cited "
                f"source, and {corroborated} are corroborated by two or more "
                "INDEPENDENT sources (syndicated copies of one story count once)."
            )
            # Only state the grade distribution when grading actually ran.
            # Printing "A=0, B=0, C=0, D=0" announces that nothing was measured
            # while looking like a measurement.
            if a or b or c or d:
                body += (
                    f" Evidence grades: A={a}, B={b}, C={c}, D={d} "
                    "(A/B = verified and strongly or independently sourced)."
                )
        return "## Evidence & Confidence\n\n" + body

    if canonical == "Limitations & Unknowns":
        lines = ["## Limitations & Unknowns", ""]
        if total:
            weak = c + d
            if weak:
                lines.append(
                    f"- {weak} claim(s) are single-source or unverified and should "
                    "be treated as provisional."
                )
            if corroborated < max(1, total // 2):
                lines.append(
                    "- Fewer than half the claims are independently corroborated; "
                    "some findings rest on a single publisher."
                )
        unknown = ctx.get("coverage_gaps")
        if isinstance(unknown, (list, tuple)):
            lines.extend(f"- {str(u).strip()}" for u in unknown[:5] if str(u).strip())
        degraded = ctx.get("degraded") or []
        if isinstance(degraded, list) and degraded:
            lines.append(
                "- Pipeline stages on deterministic fallback: "
                f"{', '.join(str(x) for x in degraded)} — those sections are "
                "extractive, not model-written."
            )
        if len(lines) == 2:
            lines.append("- No specific limitations were measured for this evidence set.")
        return "\n".join(lines)

    if canonical == "Counterarguments & Disputed Points":
        lines = ["## Counterarguments & Disputed Points", ""]
        for item in (contradictions or [])[:5]:
            if not isinstance(item, dict):
                continue
            a_text = str(item.get("claim_a", "") or "")[:140]
            b_text = str(item.get("claim_b", "") or "")[:140]
            if a_text and b_text:
                suffix = (
                    f" RESOLVED: {item.get('resolution', '')}" if item.get("resolved") else ""
                )
                lines.append(f"- \"{a_text}\" conflicts with \"{b_text}\".{suffix}")
        findings = ctx.get("redteam_findings") or []
        if isinstance(findings, list):
            for item in findings[:5]:
                if isinstance(item, dict):
                    statement = str(item.get("statement", "") or "").strip()
                    if statement:
                        lines.append(f"- {statement}")
        if len(lines) == 2:
            # Guard: never claim the absence of counterarguments unless a
            # dedicated counter-evidence search actually ran AND returned
            # nothing AND no missing skeptical angle remains anywhere in the
            # report. An empty section with no failed search is an UNKNOWN, not
            # a clean bill of health.
            counter_searched = bool(ctx.get("counter_evidence_attempted"))
            missing_skeptical = _missing_skeptical_angles(ctx)
            if missing_skeptical:
                lines.append(
                    "- Counter-evidence is INCOMPLETE: this report itself lists "
                    f"{len(missing_skeptical)} unresolved skeptical angle(s) "
                    f"({'; '.join(missing_skeptical[:3])}). No conclusion about the "
                    "absence of credible opposing claims can be drawn."
                )
            elif counter_searched:
                lines.append(
                    "- A dedicated counter-evidence search was run and returned no "
                    "credible opposing claims or source conflicts, and no missing "
                    "skeptical angle remains in this report. This is an absence of "
                    "found counter-evidence, not proof none exists."
                )
            else:
                lines.append(
                    "- No counter-evidence search was completed for this run, so "
                    "the absence of counterarguments here is UNKNOWN, not "
                    "established. Treat the dominant narrative as unopposed by "
                    "default and re-run with the counter-evidence track before "
                    "relying on it."
                )
        return "\n".join(lines)

    if canonical == "Open Questions & Missing Angles":
        lines = ["## Open Questions & Missing Angles", ""]
        gaps = ctx.get("coverage_gaps")
        listed = (
            [str(g).strip() for g in gaps if str(g).strip()]
            if isinstance(gaps, (list, tuple)) else []
        )
        for gap in listed[:8]:
            lines.append(f"- {gap}")
        if not listed:
            lines.append(
                "- No planned angle was left unsourced for this evidence set; "
                "residual uncertainty is captured in the confidence band above."
            )
        return "\n".join(lines)

    if canonical == "Key Figures":
        lines = ["## Key Figures", ""]
        figures = [
            f for f in (cited_facts or usable_facts)
            if re.search(r"\d", str(f.get("claim", "") or ""))
        ]
        for fact in figures[:10]:
            rendered = _render_finding_line(fact, verbose=profile.verbose_findings)
            if rendered:
                lines.append(rendered)
        if len(lines) == 2:
            lines.append("- No quantitative figures were extracted from the evidence.")
        return "\n".join(lines)

    if canonical == "Auditable Source Ledger":
        lines = ["## Auditable Source Ledger", ""]
        seen: Set[str] = set()
        for fact in usable_facts:
            source = str(fact.get("source", "") or "").strip()
            if not source or source in seen:
                continue
            seen.add(source)
            fetched = str(fact.get("fetched_at", "") or "")
            published = str(fact.get("published_at", "") or "")
            pulled = str(fact.get("retrieved_at", "") or fetched or "unrecorded")
            flags: List[str] = []
            if _corroboration(fact) <= 1:
                flags.append("single-source / provisional")
            if fact.get("temporal_projection"):
                flags.append("projection (not observed)")
            if fact.get("verified") is not True:
                flags.append("unverified")
            dates = ([f"published {published}"] if published else []) + [f"retrieved {pulled}"]
            suffix = f" [{'; '.join(flags)}]" if flags else ""
            lines.append(f"- {source} ({'; '.join(dates)}){suffix}")
        if len(lines) == 2:
            lines.append("- No sources were retained for this run.")
        return "\n".join(lines)

    # Executive Summary fallback (only when the writer omitted it entirely).
    # Leads with the strongest claim so the reader gets an ANSWER, not an
    # inventory of the pipeline's activity.
    headline = ""
    ranked = _stratified_top_facts(list(cited_facts) or list(usable_facts), per_angle=1, cap=1)
    if ranked:
        claim = re.sub(r"\s+", " ", str(ranked[0].get("claim", "") or "")).strip()
        index = _safe_int(ranked[0].get("citation"), 0)
        if claim:
            headline = f"Short answer: {claim.rstrip('.')}" + (f" [{index}]." if index else ".")
    confidence_note = ""
    score = ctx.get("confidence")
    if isinstance(score, (int, float)):
        band = "High" if score >= 0.75 else "Medium" if score >= 0.5 else "Low"
        confidence_note = f" Overall confidence: {band} ({float(score):.2f})."
    return (
        "## Executive Summary\n\n"
        + (headline + "\n\n" if headline else "")
        + f"This report draws on {verified} verified claim(s) out of {total} "
        f"extracted.{confidence_note}"
    )


def _add_required_sections(
    answer: str,
    *,
    ctx: Dict[str, Any],
    usable_facts: Sequence[Dict[str, Any]],
    contradictions: Sequence[Dict[str, Any]],
    cited_facts: Sequence[Dict[str, Any]] = (),
    profile: ReportProfile = PROFILE_BRIEF,
) -> Tuple[str, Set[str]]:
    """Add missing sections for this profile; report which ones were machine-made.

    The returned key set is what makes the citation audit honest: the audit used
    to grade these deterministic sections as if the writer had produced them,
    so their counts became "ungrounded numbers" and their bullets deflated
    citation density.
    """
    if not answer:
        return answer, set()
    present = _present_section_keys(answer)
    added_keys: Set[str] = set()
    blocks: List[str] = []

    for canonical in profile.required + profile.conditional:
        key = _REQUIRED_KEY_BY_HEADING.get(canonical, _canonical_key(canonical))
        if any(alias in present for alias in _normalized_aliases(canonical)):
            continue
        if canonical in profile.conditional and not _section_has_substance(
            key,
            ctx=ctx,
            usable_facts=usable_facts,
            contradictions=contradictions,
            cited_facts=cited_facts,
        ):
            continue
        blocks.append(
            _render_required_section(
                canonical,
                ctx=ctx,
                usable_facts=usable_facts,
                contradictions=contradictions,
                cited_facts=cited_facts,
                profile=profile,
            )
        )
        if key:
            added_keys.add(key)

    if not blocks:
        return answer, added_keys
    return answer.rstrip() + "\n\n" + "\n\n".join(blocks), added_keys


def _has_reasoning_section(answer: str) -> bool:
    present = _present_section_keys(answer)
    return any(
        _normalize_heading(alias) in present
        for alias in _CANONICAL_ALIASES["reasoning"]
    )


def _add_reasoning_structure(answer: str, *, ctx: Dict[str, Any]) -> Tuple[str, Set[str]]:
    """Append the deterministic argument structure when the writer omitted it.

    The ReasoningMap is the argument layer, so its loss must never depend on the
    writer choosing to surface it. When the report already carries a
    Reasoning/Argument/Conclusions section this is a no-op; otherwise the
    measured structure is appended verbatim — no invented content. Total and
    fail-safe: a missing, empty or unrenderable map leaves the answer untouched.
    """
    if not answer:
        return answer, set()
    reasoning = (ctx or {}).get("reasoning")
    if reasoning is None or not hasattr(reasoning, "render_for_writer"):
        return answer, set()
    if _has_reasoning_section(answer):
        return answer, set()
    render = getattr(reasoning, "render_for_report", None) or getattr(
        reasoning, "render_for_writer"
    )
    try:
        rendered = str(render()).strip()
    except Exception:  # noqa: BLE001
        return answer, set()
    if not rendered:
        return answer, set()
    return answer.rstrip() + "\n\n## Reasoning\n\n" + rendered, {"reasoning"}
