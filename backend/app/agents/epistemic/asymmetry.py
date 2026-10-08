from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import re
from typing import Any, Dict, List, Sequence, Set

from app.agents.epistemic.claims import (
    StandardsReport,
    assess_standards,
)
from app.agents.epistemic.conflicts import (
    Resolution,
    adjudicate_all,
)
from app.agents.epistemic.primitives import (
    _guard,
)


_COMPARISON_SPLIT_RE = re.compile(
    r"\s+(?:vs\.?|versus|compared (?:to|with)|against|or)\s+|,\s*", re.I
)


_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "to", "and", "is", "are", "was",
    "which", "what", "how", "why", "better", "best", "worse", "should", "we",
    "i", "do", "does", "between", "difference", "compare", "comparison", "vs",
}


@dataclass
class AsymmetryReport:
    """Evidence balance across the entities the question actually names."""

    entities: Dict[str, int] = field(default_factory=dict)
    balanced: bool = True
    starved: List[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        """Largest-to-smallest evidence ratio across named entities."""
        if len(self.entities) < 2:
            return 1.0
        counts = sorted(self.entities.values())
        return round(counts[-1] / max(1, counts[0]), 2)

    def note(self) -> str:
        if self.balanced or not self.entities:
            return ""
        rendered = ", ".join(f"{name} ({count})" for name, count in sorted(
            self.entities.items(), key=lambda kv: -kv[1]
        ))
        starved = ", ".join(self.starved)
        return (
            f"Evidence is unevenly distributed across the entities compared: "
            f"{rendered}. Findings about {starved} rest on materially less "
            "evidence than the rest, so any comparison involving them is "
            "provisional rather than settled."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": dict(self.entities),
            "balanced": self.balanced,
            "starved": list(self.starved),
            "ratio": self.ratio,
        }


def _candidate_entities(query: str) -> List[str]:
    """The things a comparison question is comparing.

    Split on comparison connectives, then keep the content words. Crude by
    design: a false entity simply finds no evidence and is dropped, whereas
    missing a real one means the imbalance goes unreported.
    """
    text = str(query or "").strip().rstrip("?")
    if not text:
        return []
    parts = [p.strip() for p in _COMPARISON_SPLIT_RE.split(text) if p.strip()]
    if len(parts) < 2:
        return []
    entities: List[str] = []
    for part in parts:
        words = [w for w in re.findall(r"[A-Za-z][\w.+-]{1,}", part)
                 if w.lower() not in _STOPWORDS]
        if not words:
            continue
        # Prefer a capitalised run (a proper name); else the longest word.
        caps = [w for w in words if w[:1].isupper()]
        entity = " ".join(caps[-2:]) if caps else max(words, key=len)
        if len(entity) >= 3 and entity.lower() not in _STOPWORDS:
            entities.append(entity)
    # Dedupe, preserving order.
    seen: Set[str] = set()
    out: List[str] = []
    for entity in entities:
        key = entity.lower()
        if key not in seen:
            seen.add(key)
            out.append(entity)
    return out[:5]


def coverage_asymmetry(
    query: str,
    facts: Sequence[Dict[str, Any]],
    *,
    min_ratio: float = 3.0,
) -> AsymmetryReport:
    """Detect a comparison answered mostly from one side.

    `coverage_gaps` asks whether each planned angle produced evidence. It
    passes a report with nine sources on option A and one on option B, because
    both angles are non-empty — and that report will confidently recommend A,
    having barely looked at B. This measures the split across the entities the
    QUESTION names, which is the axis bias actually travels along.
    """
    report = AsymmetryReport()
    entities = _guard(lambda: _candidate_entities(query), [])
    if len(entities) < 2:
        return report

    counts: Dict[str, int] = {}
    for entity in entities:
        needle = entity.lower()
        hits = 0
        for fact in facts or []:
            if not isinstance(fact, dict):
                continue
            haystack = f"{fact.get('claim', '')} {fact.get('sub_question', '')}".lower()
            if needle in haystack:
                hits += 1
        counts[entity] = hits

    # Entities with zero evidence are dropped: they are usually a parsing
    # artifact rather than a real side of the comparison. Only entities that
    # actually appear in the pool can be meaningfully compared for balance.
    present = {name: count for name, count in counts.items() if count > 0}
    if len(present) < 2:
        return report

    report.entities = present
    smallest = min(present.values())
    largest = max(present.values())
    if smallest > 0 and largest / smallest >= min_ratio:
        report.balanced = False
        cutoff = largest / min_ratio
        report.starved = sorted(
            name for name, count in present.items() if count <= cutoff
        )
    return report


@dataclass
class EpistemicReport:
    """Everything this layer concluded about what the evidence supports."""

    resolutions: List[Resolution] = field(default_factory=list)
    standards: StandardsReport = field(default_factory=StandardsReport)
    asymmetry: AsymmetryReport = field(default_factory=AsymmetryReport)

    @property
    def genuine_conflicts(self) -> List[Resolution]:
        return [r for r in self.resolutions if r.is_real_conflict]

    @property
    def suppressed(self) -> List[Resolution]:
        """Detected 'conflicts' that were not disagreements at all."""
        return [r for r in self.resolutions if not r.is_real_conflict]

    @property
    def unresolved(self) -> List[Resolution]:
        return [r for r in self.genuine_conflicts if not r.resolved]

    @property
    def confidence_ceiling(self) -> float:
        """The cap this layer puts on overall confidence.

        Unresolved genuine conflicts are the binding constraint: a report that
        cannot say which of two contradictory figures is right does not get to
        call itself high-confidence, however many sources it read.
        """
        ceiling = self.standards.confidence_ceiling
        unresolved = len(self.unresolved)
        if unresolved >= 3:
            ceiling = min(ceiling, 0.55)
        elif unresolved == 2:
            ceiling = min(ceiling, 0.65)
        elif unresolved == 1:
            ceiling = min(ceiling, 0.72)
        if not self.asymmetry.balanced:
            ceiling = min(ceiling, 0.70)
        return round(ceiling, 4)

    def notes(self) -> List[str]:
        out: List[str] = []
        suppressed = self.suppressed
        if suppressed:
            out.append(
                f"{len(suppressed)} detected conflict(s) were not disagreements "
                "(different time periods, scopes or units) and are reported as "
                "such rather than as a range."
            )
        resolved = [r for r in self.genuine_conflicts if r.resolved]
        if resolved:
            out.append(
                f"{len(resolved)} source conflict(s) were adjudicated on stated "
                f"rules; {len(self.unresolved)} remain genuinely open."
            )
        standards_note = self.standards.note()
        if standards_note:
            out.append(standards_note)
        asymmetry_note = self.asymmetry.note()
        if asymmetry_note:
            out.append(asymmetry_note)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolutions": [r.to_dict() for r in self.resolutions[:25]],
            "genuine_conflicts": len(self.genuine_conflicts),
            "suppressed_conflicts": len(self.suppressed),
            "unresolved_conflicts": len(self.unresolved),
            "standards": self.standards.to_dict(),
            "asymmetry": self.asymmetry.to_dict(),
            "confidence_ceiling": self.confidence_ceiling,
            "notes": self.notes(),
        }


def assess_epistemics(
    query: str,
    facts: Sequence[Dict[str, Any]],
    contradictions: Sequence[Dict[str, Any]] = (),
) -> EpistemicReport:
    """Run the whole layer. Total and fail-safe.

    Each stage is isolated, so one failing costs its own finding and nothing
    else: an epistemic check must never be the reason a report does not ship.
    """
    report = EpistemicReport()
    report.resolutions = _guard(lambda: adjudicate_all(contradictions, facts), [])
    report.standards = _guard(lambda: assess_standards(facts), StandardsReport())
    report.asymmetry = _guard(lambda: coverage_asymmetry(query, facts), AsymmetryReport())
    return report
