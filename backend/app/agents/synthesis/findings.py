"""Finding rendering and the composition warnings the evidence section carries.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Per-claim confidence/grade/justification and the one-line finding
renderer, plus the deterministic ledger warnings and the missing-skeptical-angle
scan.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.agents.synthesis.primitives import _corroboration, _safe_float, _safe_int


def _finding_confidence(fact: Dict[str, Any]) -> float:
    """Per-claim confidence in [0, 1], from measured signals only.

    Base is the claim's own confidence; verification and independent
    corroboration each lift it, and a single-source or unverified claim is
    capped in the provisional band. Never invented: a fact with no confidence
    field reads 0.0, not a flattering default.
    """
    base = max(0.0, min(1.0, _safe_float(fact.get("confidence", 0.0))))
    if fact.get("verified") is True:
        base += 0.05
    if _corroboration(fact) >= 2:
        base += 0.05
    if _corroboration(fact) <= 1 or fact.get("verified") is not True:
        base = min(base, 0.60)
    return round(max(0.0, min(1.0, base)), 2)


def _finding_grade(fact: Dict[str, Any]) -> str:
    """A/B/C/D evidence grade for one fact (empty when ungraded)."""
    grade = str(fact.get("evidence_grade", "") or "").strip().upper()
    if grade in ("A", "B", "C", "D"):
        return grade
    evidence = fact.get("evidence")
    if isinstance(evidence, dict):
        grade = str(evidence.get("grade", "") or "").strip().upper()
        if grade in ("A", "B", "C", "D"):
            return grade
    return ""


def _finding_justification(fact: Dict[str, Any]) -> str:
    """Short, measured justification for a finding's confidence."""
    parts: List[str] = ["verified against source" if fact.get("verified") is True else "unverified"]
    corroboration = _corroboration(fact)
    parts.append(
        f"{corroboration} independent sources" if corroboration >= 2 else "single source"
    )
    if fact.get("temporal_projection"):
        parts.append("projection, not observed")
    return "; ".join(parts)


def _render_finding_line(fact: Dict[str, Any], *, verbose: bool = False) -> str:
    """One Key-Findings bullet.

    In `audit` profiles every bullet carries its confidence, grade and
    justification. Everywhere else that annotation is noise on the most-read
    part of the report — and it contradicts the prompt's own rule against
    printing pipeline metrics in prose — so a normal bullet is the claim and
    its citation, with a short provisional flag ONLY when the claim is weak.
    Nothing adverse is hidden: the flag appears whenever it applies.
    """
    claim = re.sub(r"\s+", " ", str(fact.get("claim", "") or "")).strip()
    if not claim:
        return ""
    index = _safe_int(fact.get("citation"), 0)
    marker = f" [{index}]" if index else ""
    if verbose:
        confidence = _finding_confidence(fact)
        grade = _finding_grade(fact)
        grade_tag = f", grade {grade}" if grade else ""
        return (
            f"- {claim}{marker} — confidence {confidence:.2f}{grade_tag} "
            f"({_finding_justification(fact)})"
        )
    flags: List[str] = []
    if fact.get("verified") is not True:
        flags.append("unverified")
    if _corroboration(fact) <= 1:
        flags.append("single source")
    if fact.get("temporal_projection"):
        flags.append("projection")
    suffix = f" *({', '.join(flags)})*" if flags else ""
    return f"- {claim}{marker}{suffix}"


def _missing_skeptical_angles(ctx: Dict[str, Any]) -> List[str]:
    """Skeptical/counter-evidence angles the report itself admits are missing.

    Scans the coverage gaps for vocabulary indicating an
    absent counter-evidence angle. When any exists, the report cannot also claim
    it found no credible opposing views: that would be a self-contradiction.
    """
    cues = (
        "counter", "skeptic", "sceptic", "oppos", "disagree", "dissent",
        "critic", "hype", "plateau", "roi", "bubble", "risk", "limitation",
        "alternative", "contradict", "over-hype", "overhype",
    )
    sources: List[str] = []
    gaps = ctx.get("coverage_gaps")
    if isinstance(gaps, (list, tuple)):
        sources.extend(str(g).strip() for g in gaps if str(g).strip())
    return [text for text in sources if any(cue in text.lower() for cue in cues)]


def _ledger_warnings(ctx: Dict[str, Any]) -> str:
    """Deterministic composition warnings for the evidence section.

    Three composition failures are surfaced, never hidden: regulation dominance
    (>30% of evidence), non-Western under-representation, and a thin primary
    share. Each is a warning, not a silent omission.
    """
    warnings: List[str] = []
    regulation = _safe_float(ctx.get("regulation_share", 0.0))
    if regulation > 0.30:
        warnings.append(
            f"Regulation-sourced evidence is {regulation:.0%} of the pool (>30%). "
            "Regulatory material is reactive context, not the lead story; read "
            "capability, economics and adoption findings with that skew in mind."
        )
    non_western = _safe_float(ctx.get("non_western_share", 0.0))
    if non_western < 0.10:
        warnings.append(
            f"Non-Western sources are {non_western:.0%} of the pool. The picture "
            "may be US/EU-centric; treat global claims as provisional."
        )
    primary = _safe_float(ctx.get("primary_share", 0.0))
    if primary < 0.30:
        warnings.append(
            f"Only {primary:.0%} of sources are primary (papers, official reports, "
            "filings); secondary summaries dominate."
        )
    if not warnings:
        return ""
    return "\n".join(f"- {w}" for w in warnings)
