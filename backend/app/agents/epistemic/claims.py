from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import re
from typing import Any, Dict, List, Sequence, Tuple

from app.agents.sources import classify_source
from app.agents.epistemic.primitives import (
    _guard,
    _safe_int,
    _text,
)


class ClaimType:
    """The kinds of assertion a research report makes.

    Not an Enum on purpose: these values are written into fact dicts that get
    serialized to JSON and compared against strings all over the host app, and
    a plain string constant survives that round trip without every consumer
    needing the import.
    """

    DEFINITIONAL = "definitional"
    DESCRIPTIVE = "descriptive"
    STATISTICAL = "statistical"
    CAUSAL = "causal"
    PREDICTIVE = "predictive"
    EVALUATIVE = "evaluative"
    ATTRIBUTIVE = "attributive"


_CLAIM_PATTERNS: Sequence[Tuple[str, "re.Pattern[str]"]] = (
    (ClaimType.PREDICTIVE, re.compile(
        r"\b(will|shall|expected to|projected to|forecast(?:ed)?|predicted|"
        r"anticipat(?:ed|es)|on track to|set to|by 20[3-9]\d|estimates? that .{0,40}"
        r"\b(?:will|by)\b)\b", re.I)),
    (ClaimType.CAUSAL, re.compile(
        r"\b(caused?|causes|causing|because of|due to|as a result of|leads? to|"
        r"led to|drives?|driven by|triggers?|triggered|responsible for|"
        r"attributable to|resulted? in|contributed? to|explains?)\b", re.I)),
    (ClaimType.EVALUATIVE, re.compile(
        r"\b(best|worst|better|worse|superior|inferior|should|ought|"
        r"outperform(?:s|ed)?|preferable|optimal|most effective|leading)\b", re.I)),
    (ClaimType.ATTRIBUTIVE, re.compile(
        r"\b(according to|said|stated|argues?|claims?|contends?|maintains?|"
        r"reported by|told|wrote|testified)\b", re.I)),
    (ClaimType.STATISTICAL, re.compile(
        r"\d+(?:[.,]\d+)?\s*(?:%|percent|per cent|bn|billion|million|"
        r"thousand|trillion|k\b)|\$\s*\d|\b\d{2,}\b", re.I)),
    (ClaimType.DEFINITIONAL, re.compile(
        r"\b(is defined as|refers to|is a type of|means that|is the term|"
        r"consists? of|is an? \w+ (?:that|which))\b", re.I)),
)


def classify_claim(claim: str) -> str:
    """Type a claim by the burden of proof it carries.

    Used to hold each claim to the standard its own form demands: a causal
    assertion resting on one blog is a defect even when a definitional one on
    the same source is fine.
    """
    text = str(claim or "").strip()
    if not text:
        return ClaimType.DESCRIPTIVE
    for claim_type, pattern in _CLAIM_PATTERNS:
        if pattern.search(text):
            return claim_type
    return ClaimType.DESCRIPTIVE


@dataclass(frozen=True)
class EvidenceStandard:
    """The bar a claim of this type must clear to be reported as established."""

    min_independent_sources: int
    requires_primary: bool
    verifiable: bool
    max_confidence_unmet: float
    rationale: str
    # When true, a primary source satisfies the corroboration requirement on
    # its own. This is the bar a careful analyst actually applies to a figure:
    # the regulator's own filing does not need a second outlet to repeat it,
    # but a number from trade press does. Demanding BOTH would mark almost
    # every real statistic unmet, and a check that fires on everything is a
    # check nobody reads.
    primary_substitutes_corroboration: bool = False


EVIDENCE_STANDARDS: Dict[str, EvidenceStandard] = {
    ClaimType.DEFINITIONAL: EvidenceStandard(
        1, False, True, 0.85,
        "A definition is checkable against one competent source.",
    ),
    ClaimType.DESCRIPTIVE: EvidenceStandard(
        1, False, True, 0.80,
        "A description of an observable state needs one reliable source.",
    ),
    ClaimType.ATTRIBUTIVE: EvidenceStandard(
        1, False, True, 0.80,
        "Who said what needs the source that carries the statement, no more.",
    ),
    ClaimType.STATISTICAL: EvidenceStandard(
        2, False, True, 0.65,
        "A figure must either come from the body that measured it or be "
        "carried by two independent sources; a lone secondary figure is one "
        "transcription error away from being wrong.",
        primary_substitutes_corroboration=True,
    ),
    ClaimType.CAUSAL: EvidenceStandard(
        2, False, True, 0.55,
        "A single source asserting causation is usually reporting a "
        "correlation; independent corroboration is the minimum.",
    ),
    ClaimType.EVALUATIVE: EvidenceStandard(
        2, False, True, 0.60,
        "A judgement ('best', 'should') is contestable by construction and "
        "needs more than one voice to be reported as a finding.",
    ),
    ClaimType.PREDICTIVE: EvidenceStandard(
        1, False, False, 0.50,
        "A projection cannot be verified against a source because the event "
        "has not occurred. It can only ever be attributed, never established.",
    ),
}


@dataclass
class ClaimStandard:
    """One claim measured against the bar its own type sets."""

    claim: str
    claim_type: str
    independent_sources: int
    is_primary: bool
    verified: bool
    meets_standard: bool
    shortfall: str = ""
    confidence_ceiling: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim": self.claim[:160],
            "claim_type": self.claim_type,
            "independent_sources": self.independent_sources,
            "is_primary": self.is_primary,
            "verified": self.verified,
            "meets_standard": self.meets_standard,
            "shortfall": self.shortfall,
            "confidence_ceiling": self.confidence_ceiling,
        }


@dataclass
class StandardsReport:
    """How much of the pool clears the bar its own claims set."""

    assessed: List[ClaimStandard] = field(default_factory=list)
    by_type: Dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.assessed)

    @property
    def met(self) -> int:
        return sum(1 for c in self.assessed if c.meets_standard)

    @property
    def unmet(self) -> List[ClaimStandard]:
        return [c for c in self.assessed if not c.meets_standard]

    @property
    def met_ratio(self) -> float:
        return round(self.met / self.total, 4) if self.total else 0.0

    @property
    def confidence_ceiling(self) -> float:
        """The highest confidence this pool's weakest important claims allow.

        Taken as the mean ceiling of the unmet claims rather than the minimum:
        one weak claim in a large pool should not cap the whole report, but a
        pool that is mostly unmet claims should be capped hard.
        """
        unmet = self.unmet
        if not unmet or not self.total:
            return 1.0
        share_unmet = len(unmet) / self.total
        if share_unmet < 0.15:
            return 1.0
        mean_ceiling = sum(c.confidence_ceiling for c in unmet) / len(unmet)
        # Blend toward 1.0 in proportion to how much of the pool is fine.
        return round(mean_ceiling + (1.0 - mean_ceiling) * (1.0 - share_unmet), 4)

    def note(self) -> str:
        if not self.unmet:
            return ""
        counts: Dict[str, int] = {}
        for item in self.unmet:
            counts[item.claim_type] = counts.get(item.claim_type, 0) + 1
        worst = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
        rendered = ", ".join(f"{n} {t}" for t, n in worst)
        return (
            f"{len(self.unmet)} of {self.total} claims do not meet the evidence "
            f"standard for their claim type ({rendered}); those are reported as "
            "provisional rather than established."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "met": self.met,
            "met_ratio": self.met_ratio,
            "confidence_ceiling": self.confidence_ceiling,
            "by_type": dict(self.by_type),
            "unmet": [c.to_dict() for c in self.unmet[:15]],
        }


def _independent_sources(fact: Dict[str, Any]) -> int:
    """Independent-source count, preferring the echo-adjusted figure."""
    if "independent_corroboration" in fact:
        return max(1, _safe_int(fact.get("independent_corroboration"), 1))
    return max(1, _safe_int(fact.get("corroboration_count", 1), 1))


def assess_standards(facts: Sequence[Dict[str, Any]]) -> StandardsReport:
    """Hold every claim to the standard its own type demands.

    One flat verification threshold treats "the EU adopted the rule in March"
    and "the rule caused a 12% drop in emissions" as equally easy to
    establish. They are not, and a research system that cannot tell the
    difference will report the second with the confidence of the first.
    """
    report = StandardsReport()
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        claim = _text(fact, "claim")
        if not claim:
            continue
        claim_type = str(fact.get("claim_type") or classify_claim(claim))
        standard = EVIDENCE_STANDARDS.get(claim_type, EVIDENCE_STANDARDS[ClaimType.DESCRIPTIVE])
        report.by_type[claim_type] = report.by_type.get(claim_type, 0) + 1

        sources = _independent_sources(fact)
        url = _text(fact, "source", "url")
        primary = bool(fact.get("is_primary"))
        if not primary and url:
            primary = _guard(lambda: bool(classify_source(url).is_primary), False)
        verified = fact.get("verified") is True

        shortfalls: List[str] = []
        corroboration_satisfied = sources >= standard.min_independent_sources or (
            standard.primary_substitutes_corroboration and primary
        )
        if not corroboration_satisfied:
            shortfalls.append(
                f"needs {standard.min_independent_sources} independent sources "
                f"(or a primary source), has {sources}"
                if standard.primary_substitutes_corroboration
                else f"needs {standard.min_independent_sources} independent sources, has {sources}"
            )
        if standard.requires_primary and not primary:
            shortfalls.append("no primary source for a quantitative claim")
        if standard.verifiable and not verified:
            shortfalls.append("not verified against its cited source")
        if not standard.verifiable:
            # A projection is never "unmet" for being unverified — it is
            # unverifiable by nature. It is unmet only if presented as fact.
            shortfalls = [s for s in shortfalls if "not verified" not in s]

        meets = not shortfalls
        report.assessed.append(
            ClaimStandard(
                claim=claim,
                claim_type=claim_type,
                independent_sources=sources,
                is_primary=primary,
                verified=verified,
                meets_standard=meets,
                shortfall="; ".join(shortfalls),
                confidence_ceiling=1.0 if meets else standard.max_confidence_unmet,
            )
        )
    return report
