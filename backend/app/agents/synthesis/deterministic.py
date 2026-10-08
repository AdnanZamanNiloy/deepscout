"""Deterministic extractive fallback: the report when the model call fails.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). An organized, honestly-labelled research digest — never imitation
prose. Also owns the gap/confidence helpers it needs.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set

from app.agents.evidence_utils import select_diverse

from app.agents.synthesis.citations import (
    MAX_LEGEND_SOURCES_HARD,
    _assign_numbers,
    _cite_token,
    _legend_block,
    _with_citation,
    audit_citations,
)
from app.agents.synthesis.postprocess import _normalize_query_concept
from app.agents.synthesis.profiles import PROFILE_BRIEF, ReportProfile
from app.agents.synthesis.primitives import _safe_int
from app.agents.synthesis.ranking import _section_title
from app.agents.synthesis.sections import _count_words, _reorder_sections
from app.agents.synthesis.types import SynthesisResult


def _gap_stats(ctx: Dict[str, Any], usable_facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts the Evidence & Confidence section needs.

    Prefers workflow context (contradictions, degraded stages, totals); derives
    the rest from the facts themselves.
    """
    total = _safe_int(ctx.get("total_facts", 0)) or len(usable_facts)
    if ctx.get("verified_count") is not None:
        verified = _safe_int(ctx.get("verified_count"))
    else:
        # Synthesis input passed verification upstream: anything explicitly
        # flagged False was already filtered, the rest counts as verified.
        verified = sum(1 for f in usable_facts if f.get("verified", True) is not False)
    contradictions = ctx.get("contradictions", []) or []
    if not isinstance(contradictions, list):
        contradictions = []
    degraded = ctx.get("degraded", []) or []
    if not isinstance(degraded, list):
        degraded = []
    return {
        "total": total,
        "verified": verified,
        "unverified_excluded": max(0, total - verified),
        "contradictions": contradictions[:5],
        "degraded": [str(d) for d in degraded if d],
    }


def _confidence_statement(ctx: Dict[str, Any], verified_count: int) -> str:
    """'High (0.76)' on the pipeline's 0-1 scale, or a volume-based level."""
    raw = ctx.get("confidence", None) if isinstance(ctx, dict) else None
    try:
        score = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "High" if verified_count >= 8 else ("Medium" if verified_count >= 3 else "Low")
    level = "High" if score >= 0.75 else ("Medium" if score >= 0.5 else "Low")
    # Thin evidence caps the stated level: fewer than three verified facts is
    # provisional by construction, so a high numeric score must not label the
    # extractive digest "High" while the same paragraph calls it provisional.
    if verified_count < 3 and level != "Low":
        level = "Low"
    return f"{level} ({score:.2f})"


def _gaps_section(stats: Dict[str, Any]) -> str:
    """Measured evidence accounting for the extractive path's AUDIT layer."""
    lines = [
        "## Evidence & Confidence",
        "",
        f"Well-supported: {stats['verified']} verified facts feed this report; "
        "every claim above traces to a cited source.",
    ]
    if stats.get("confidence_line"):
        lines.append(stats["confidence_line"])
    if stats["unverified_excluded"]:
        lines.append(
            f"Uncertain: {stats['unverified_excluded']} collected claims failed "
            "verification and were excluded from synthesis rather than repeated."
        )
    if stats["contradictions"]:
        lines.append(
            f"Conflicting evidence: {len(stats['contradictions'])} source conflict(s) "
            "flagged; conflicting numbers are reported as ranges, not picked."
        )
    if stats["degraded"]:
        lines.append(
            "Pipeline gaps: deterministic fallback covered "
            f"{', '.join(stats['degraded'])} — those sections are extractive, "
            "not model-written."
        )
    lines.extend([
        "",
        "## Limitations & Unknowns",
        "",
        "Could not verify: claims without a traceable source were discarded "
        "during synthesis and do not appear above.",
    ])
    return "\n".join(lines)


def _deterministic_report(
    query: str,
    usable_facts: Sequence[Dict[str, Any]],
    top_facts: Sequence[Dict[str, Any]],
    ctx: Dict[str, Any],
    angles: Sequence[str],
    profile: ReportProfile = PROFILE_BRIEF,
) -> SynthesisResult:
    """An organized, honestly-labeled research digest — not imitation prose.

    Gluing source sentences into paragraphs reads exactly like chunks cut from
    different sources, so this path leans into what it is: a structured
    briefing. It now LEADS WITH THE ANSWER — the highest-confidence claim,
    stated plainly — rather than opening on a count of verified facts, which is
    metadata, not an answer, and was the first thing a user saw on exactly the
    runs where the pipeline was already degraded.
    """
    diverse = select_diverse(list(top_facts), k=40, max_similarity=0.35)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in diverse:
        if not str(item.get("claim", "")).strip():
            continue
        # Sense first: an ambiguous query's fallback report must keep the
        # meanings in separate sections, exactly like the LLM path.
        key = (
            str(item.get("sense", "") or "").strip()
            or str(item.get("sub_question", "") or "").strip()
        )
        bucket = groups.setdefault(key, [])
        if len(bucket) < 8:
            bucket.append(item)
        if len(groups) >= 6 and all(len(v) >= 3 for v in groups.values()):
            break
    if not groups:
        if isinstance(ctx, dict):
            ctx["synthesis_machine_notes"] = []
        return SynthesisResult(
            answer=f"No reliable evidence was retrieved for {_normalize_query_concept(query)}.",
            used_fallback=True,
            profile=profile.name,
        )

    gap_stats = _gap_stats(ctx, list(usable_facts))
    n_sources = len(
        {str(i.get("source", "")) for items in groups.values() for i in items if i.get("source")}
    )

    used: List[Dict[str, Any]] = []
    seen_claims: Set[str] = set()

    def _take(item: Dict[str, Any]) -> None:
        claim = str(item.get("claim", ""))
        if claim not in seen_claims:
            seen_claims.add(claim)
            used.append(item)

    # Headline finding: the single highest-confidence claim, previewed in the
    # summary and excluded from its section (no duplicated claims anywhere).
    headline: Optional[Dict[str, Any]] = None
    for items in groups.values():
        if items:
            headline = items[0]
            break
    if headline is not None:
        _take(headline)

    sections: List[str] = []
    for key, items in list(groups.items())[:6]:
        bullets: List[str] = []
        for item in items:
            if str(item.get("claim", "")) in seen_claims:
                continue
            rendered = _with_citation(item).strip()
            if not rendered:
                continue
            _take(item)
            bullets.append(f"- {rendered}")
        if not bullets:
            continue
        title = _section_title(key) or "Findings"
        sections.append(f"## {title}\n\n" + "\n".join(bullets))

    # Lead with the answer. The extractive path states what the evidence shows
    # in prose; process provenance and counts are NOT put in the answer (they
    # live in the audit layer). Uncertainty is expressed naturally.
    headline_text = _with_citation(headline).strip() if headline is not None else ""
    opening_parts: List[str] = []
    if headline_text:
        # Synthesized lead: the best-supported finding stated as the answer,
        # followed by the other leading findings, rather than a self-referential
        # line about "the summary below". When the pool is thin this is labelled
        # provisional, but it is still an answer, not a process note. Claims
        # previewed here are marked used so no section repeats them.
        opening_parts.append(headline_text)
        follow: List[str] = []
        for item in diverse:
            if item is headline or item in used:
                continue
            if not str(item.get("claim", "")).strip():
                continue
            rendered = _with_citation(item).strip()
            if not rendered:
                continue
            _take(item)
            follow.append(rendered)
            if len(follow) >= 2:
                break
        if follow:
            opening_parts.append(" ".join(follow).rstrip())
        if gap_stats["verified"] < 3:
            opening_parts.append(
                "The evidence here is thin, so treat this as provisional — "
                "the best-supported reading, not a settled conclusion."
            )
    else:
        opening_parts.append(
            f"No reliable evidence was retrieved for "
            f"\u201c{_normalize_query_concept(query)}\u201d."
        )
    sections.insert(0, "\n\n".join(opening_parts))

    # A short natural uncertainty/caveat close, without pipeline mechanics.
    if gap_stats["contradictions"]:
        sections.append(
            "Sources disagree on some points; where figures conflict they are "
            "reported as a range rather than a single value."
        )

    # The extractive path knows exactly which source each claim came from, so it
    # cites perfectly — claims were rendered with an identity token; now that
    # the used set is final, tokens become numbers.
    numbered, pairs = _assign_numbers(used[:40], max_sources=MAX_LEGEND_SOURCES_HARD)
    answer = "\n\n".join(sections) + "\n\n" + _legend_block(numbered)
    for fact, index in pairs:
        answer = answer.replace(_cite_token(fact), f"[{index}]")
    answer = re.sub(r"\s*\[\[c\d+\]\]", "", answer)
    answer = _reorder_sections(answer)
    cited = [dict(fact, citation=index) for fact, index in pairs]
    gap_stats["confidence_line"] = (
        f"Confidence: {_confidence_statement(ctx, gap_stats['verified'])} — "
        f"{gap_stats['verified']} verified facts across {n_sources} sources."
    )
    notes = [_gaps_section(gap_stats).strip()]
    if isinstance(ctx, dict):
        ctx["synthesis_machine_notes"] = list(notes)
    return SynthesisResult(
        answer=answer,
        sources=numbered,
        audit=audit_citations(answer, numbered, cited),
        used_fallback=True,
        angles=list(angles),
        profile=profile.name,
        word_count=_count_words(answer),
        machine_notes=notes,
    )
