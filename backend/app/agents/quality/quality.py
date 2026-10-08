from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.agents.evidence_utils import semantic_similarity
from app.agents.evidence_utils import split_into_sentences
from app.agents.quality.factual import (
    is_factual_sentence,
)
from app.agents.quality.independence import (
    IndependenceReport,
    assess_independence,
    independent_corroboration,
)
from app.agents.quality.primitives import (
    _markers,
    _safe_float,
    _safe_int,
    _significant_values,
    _strip_markers,
    _values_match,
)
from app.agents.quality.temporal import (
    TemporalProfile,
    temporal_profile,
)


@dataclass
class QualityFinding:
    """One defect, with enough context for a reader to check it themselves."""

    kind: str
    detail: str
    sentence: str = ""
    severity: str = "warn"  # "warn" | "serious"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "sentence": self.sentence[:200],
            "severity": self.severity,
        }


_HEDGE_RE = re.compile(
    r"\b(may|might|could|appears?|seems?|suggests?|indicates?|reportedly|"
    r"allegedly|estimated|approximately|roughly|around|about|likely|unlikely|"
    r"probabl[ey]|possibl[ey]|one source|a single source|according to|claims?|"
    r"unverified|provisional|preliminary|so far|as of|not confirmed|"
    r"if accurate|on one account)\b",
    re.I,
)


_ASSERTIVE_RE = re.compile(
    r"\b(is|are|was|were|will|has|have|does|do|proves?|demonstrates?|shows?|"
    r"confirms?|establishes?|means?)\b",
    re.I,
)


_STRONG_CLAIM_RE = re.compile(
    r"\b(proves?|proven|confirms?|confirmed|establishes?|demonstrates?|"
    r"guarantees?|always|never|all |every |no one|definitive(?:ly)?)\b", re.I
)


def check_citation_grounding(
    answer: str,
    cited_facts: Sequence[Dict[str, Any]],
    *,
    sentences: Optional[Sequence[str]] = None,
) -> List[QualityFinding]:
    """Bind every figure to the source cited next to it, not to the pool.

    The pool-wide check answers "does this number exist in the evidence at
    all", which passes a sentence that cites [3] while quoting a figure that
    only [7] supports. To a reader following the citation that is a
    fabrication: they open [3] and the number is not there. This check is the
    difference between citations that decorate and citations that verify.
    """
    findings: List[QualityFinding] = []
    by_number: Dict[int, List[str]] = {}
    for fact in cited_facts or []:
        index = _safe_int(fact.get("citation"), 0)
        if not index:
            continue
        text = f"{fact.get('claim', '')} {fact.get('direct_quote', '')}"
        by_number.setdefault(index, []).append(text)

    pool_values: Set[Tuple[float, str]] = set()
    for texts in by_number.values():
        for text in texts:
            pool_values |= _significant_values(text, limit=60)

    units = sentences if sentences is not None else _sentence_units(answer)
    for sentence in units:
        markers = _markers(sentence)
        if not markers:
            continue
        stated = _significant_values(_strip_markers(sentence))
        if not stated:
            continue
        cited_values: Set[Tuple[float, str]] = set()
        for marker in markers:
            for text in by_number.get(marker, []):
                cited_values |= _significant_values(text, limit=60)
        if not cited_values:
            continue
        for value in stated:
            if any(_values_match(value, known) for known in cited_values):
                continue
            elsewhere = any(_values_match(value, known) for known in pool_values)
            rendered = f"{value[0]:g}{(' ' + value[1]) if value[1] else ''}"
            findings.append(
                QualityFinding(
                    kind="misattributed_figure" if elsewhere else "unsupported_figure",
                    detail=(
                        f"{rendered} is cited to "
                        + ", ".join(f"[{m}]" for m in markers)
                        + (
                            ", but that figure appears in a different source"
                            if elsewhere
                            else ", which does not contain it"
                        )
                    ),
                    sentence=sentence,
                    severity="serious",
                )
            )
    return findings


def _sentence_units(answer: str, limit: int = 400) -> List[str]:
    """Line-aware sentence split, so an uncited bullet cannot hide in a list."""
    units: List[str] = []
    for raw_line in (answer or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("-* ").strip()
        if not line:
            continue
        try:
            pieces = split_into_sentences(line, max_sentences=20)
        except Exception:  # noqa: BLE001
            pieces = [line]
        for piece in pieces:
            text = piece.strip()
            if text:
                units.append(text)
        if len(units) >= limit:
            break
    return units[:limit]


def detect_overclaims(
    answer: str,
    cited_facts: Sequence[Dict[str, Any]],
    *,
    sentences: Optional[Sequence[str]] = None,
) -> List[QualityFinding]:
    """Find sentences asserting more than their cited evidence can carry.

    An unverified, single-source, C/D-grade claim written as "X is Y" is
    indistinguishable to the reader from an A-grade corroborated one. The whole
    value of grading evidence is lost if the prose flattens it. A hedge, an
    attribution, or an estimative qualifier discharges the requirement — this
    is not a demand for vagueness, it is a demand that certainty be earned.
    """
    findings: List[QualityFinding] = []
    by_number: Dict[int, List[Dict[str, Any]]] = {}
    for fact in cited_facts or []:
        index = _safe_int(fact.get("citation"), 0)
        if index:
            by_number.setdefault(index, []).append(fact)

    units = sentences if sentences is not None else _sentence_units(answer)
    for sentence in units:
        markers = _markers(sentence)
        if not markers:
            continue
        supporting = [f for m in markers for f in by_number.get(m, [])]
        if not supporting:
            continue

        best_confidence = max(_safe_float(f.get("confidence", 0.0)) for f in supporting)
        any_verified = any(f.get("verified") is True for f in supporting)
        best_independence = max(independent_corroboration(f) for f in supporting)
        grades = {str(f.get("evidence_grade", "") or "").upper() for f in supporting}
        weak_grade = bool(grades & {"C", "D"}) and not (grades & {"A", "B"})

        weak = (not any_verified) or best_independence <= 1 or weak_grade or best_confidence < 0.45
        if not weak:
            continue

        body = _strip_markers(sentence)
        if _HEDGE_RE.search(body):
            continue
        if not _ASSERTIVE_RE.search(body):
            continue

        reasons: List[str] = []
        if not any_verified:
            reasons.append("unverified")
        if best_independence <= 1:
            reasons.append("single independent source")
        if weak_grade:
            reasons.append("C/D grade")
        findings.append(
            QualityFinding(
                kind="overclaim",
                detail=(
                    "stated as established fact, but its cited evidence is "
                    + " and ".join(reasons or ["weak"])
                ),
                sentence=sentence,
                severity="warn",
            )
        )

    # An absolute claim ("proves", "always", "never") is flagged regardless of
    # grade: research evidence essentially never supports that register.
    for sentence in units:
        if _STRONG_CLAIM_RE.search(_strip_markers(sentence)):
            findings.append(
                QualityFinding(
                    kind="absolute_claim",
                    detail=(
                        "uses absolute or proof language, which research evidence "
                        "rarely supports; state the strength of support instead"
                    ),
                    sentence=sentence,
                    severity="warn",
                )
            )
    return findings


_TOPIC_OVERLAP = 0.42


def detect_internal_conflicts(sections: Dict[str, str]) -> List[QualityFinding]:
    """Catch the same quantity stated two different ways in one report.

    Section-wise synthesis writes each section blind to its siblings, so the
    Executive Summary can say 12% while a deep-dive says 18% for the same
    thing. Deliberately conservative: two sentences must be about the same
    subject (token overlap) AND carry the same unit with different values
    before anything is flagged, so ordinary different-but-related figures are
    left alone.
    """
    findings: List[QualityFinding] = []
    indexed: List[Tuple[str, str, Set[Tuple[float, str]]]] = []
    for title, body in (sections or {}).items():
        for sentence in _sentence_units(body):
            values = _significant_values(_strip_markers(sentence))
            if values:
                indexed.append((title, sentence, values))

    seen: Set[Tuple[str, str]] = set()
    for i in range(len(indexed)):
        title_a, sentence_a, values_a = indexed[i]
        for j in range(i + 1, len(indexed)):
            title_b, sentence_b, values_b = indexed[j]
            if title_a == title_b:
                continue
            try:
                overlap = semantic_similarity(
                    _strip_markers(sentence_a), _strip_markers(sentence_b)
                )
            except Exception:  # noqa: BLE001
                continue
            if overlap < _TOPIC_OVERLAP:
                continue
            for value_a in values_a:
                for value_b in values_b:
                    if not value_a[1] or value_a[1] != value_b[1]:
                        continue
                    if _values_match(value_a, value_b):
                        continue
                    key = (f"{value_a[0]:g}{value_a[1]}", f"{value_b[0]:g}{value_b[1]}")
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(
                        QualityFinding(
                            kind="internal_conflict",
                            detail=(
                                f"'{title_a}' gives {value_a[0]:g} {value_a[1]} where "
                                f"'{title_b}' gives {value_b[0]:g} {value_b[1]} for what "
                                "appears to be the same quantity"
                            ),
                            sentence=sentence_a,
                            severity="serious",
                        )
                    )
    return findings


def section_coverage(sections: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Citation density per section, so one clean section cannot mask a bare one.

    A global density of 70% looks acceptable and can hide a section at 0%.
    Readers trust or distrust a report section by section, so it is measured
    that way.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for title, body in (sections or {}).items():
        factual = 0
        cited = 0
        for sentence in _sentence_units(body):
            if not is_factual_sentence(_strip_markers(sentence)):
                continue
            factual += 1
            if _markers(sentence):
                cited += 1
        if factual:
            out[title] = {
                "factual_sentences": factual,
                "cited": cited,
                "density": round(cited / factual, 3),
            }
    return out


_ESTIMATIVE_BANDS: Sequence[Tuple[float, str]] = (
    (0.90, "almost certainly"),
    (0.75, "very likely"),
    (0.55, "likely"),
    (0.45, "roughly even odds"),
    (0.25, "unlikely"),
    (0.00, "very unlikely"),
)


def estimative_band(confidence: float) -> str:
    """The estimative phrase a given confidence licenses."""
    score = max(0.0, min(1.0, _safe_float(confidence)))
    for threshold, phrase in _ESTIMATIVE_BANDS:
        if score >= threshold:
            return phrase
    return "very unlikely"


_CALIBRATION_CONTRACT = (
    "CALIBRATED LANGUAGE — the reader must be able to tell how much to trust "
    "each statement from the wording alone:\n"
    "- State an A-grade, independently corroborated finding plainly, with no "
    "hedge. Hedging established facts is its own failure.\n"
    "- A single-source, unverified or C/D-grade finding MUST carry an "
    "attribution or qualifier ('one source reports', 'estimated at', "
    "'reportedly'). Never write it in the same flat voice as a verified one.\n"
    "- Where you express a likelihood, use these exact phrases and nothing "
    "vaguer: almost certainly / very likely / likely / roughly even odds / "
    "unlikely / very unlikely.\n"
    "- Never write 'proves', 'confirms', 'always', 'never' or 'definitively'. "
    "Research evidence supports degrees of confidence, not proof.\n"
    "- Do not stack hedges. One qualifier per claim; 'may possibly somewhat "
    "suggest' communicates nothing."
)


_PREMISE_CONTRACT = (
    "CHECK THE QUESTION'S PREMISE FIRST. If the evidence contradicts something "
    "the question assumes (it asks why X happened and the evidence says X did "
    "not happen, or names an entity the evidence shows does not exist or is "
    "being confused with another), say so in the FIRST sentence and answer the "
    "question the reader should have asked. Answering a false premise fluently "
    "is the most damaging thing this report can do."
)


def render_quality_contract(
    *,
    profile: Optional[TemporalProfile] = None,
    confidence: Optional[float] = None,
    query_type: str = "",
) -> str:
    """The writing contract that prevents the defects this module measures.

    Injected into the writer prompt so the standard is enforced from both ends:
    the model is told what calibrated, dated, premise-checked writing looks
    like, and the audit afterwards measures whether it delivered.
    """
    parts: List[str] = [_PREMISE_CONTRACT, _CALIBRATION_CONTRACT]

    temporal_lines: List[str] = []
    if profile and profile.newest:
        temporal_lines.append(
            "DATING — the evidence for this report was published between "
            f"{profile.oldest:%B %Y} and {profile.newest:%B %Y}."
            if profile.oldest and profile.oldest != profile.newest
            else f"DATING — the evidence for this report dates to {profile.newest:%B %Y}."
        )
        temporal_lines.append(
            "Attach an explicit as-of date to every claim about a current "
            "state, price, ranking, headcount or status. Write 'as of "
            f"{profile.newest:%B %Y}', never a bare 'currently'."
        )
        if profile.stale:
            temporal_lines.append(
                "This evidence is OLD relative to the question. Say so plainly "
                "in the Executive Summary rather than presenting dated findings "
                "as the present state."
            )
    elif profile:
        temporal_lines.append(
            "DATING — no source carried a publication date. Do not write "
            "'currently', 'as of today', 'the latest' or any other claim of "
            "currency you cannot support; say the evidence is undated."
        )
    if temporal_lines:
        parts.append("\n".join(temporal_lines))

    if confidence is not None:
        phrase = estimative_band(_safe_float(confidence))
        parts.append(
            "OVERALL CALIBRATION — the evidence base supports a conclusion that "
            f"is {phrase} correct. Do not write the report in a register more "
            "confident than that."
        )
    return "\n\n".join(parts)


@dataclass
class ResearchQualityReport:
    """Everything this layer measured, for the report and for the caller."""

    findings: List[QualityFinding] = field(default_factory=list)
    temporal: TemporalProfile = field(default_factory=TemporalProfile)
    independence: IndependenceReport = field(default_factory=IndependenceReport)
    coverage: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def serious(self) -> List[QualityFinding]:
        return [f for f in self.findings if f.severity == "serious"]

    @property
    def is_clean(self) -> bool:
        return not self.findings

    def by_kind(self, kind: str) -> List[QualityFinding]:
        return [f for f in self.findings if f.kind == kind]

    def weakest_sections(self, threshold: float = 0.5) -> List[Tuple[str, float]]:
        return sorted(
            (
                (title, float(stats["density"]))
                for title, stats in self.coverage.items()
                if float(stats["density"]) < threshold and int(stats["factual_sentences"]) >= 3
            ),
            key=lambda item: item[1],
        )

    def render_note(self) -> str:
        """Report the defects in the report itself, or return "" when clean."""
        lines: List[str] = []

        misattributed = self.by_kind("misattributed_figure")
        if misattributed:
            lines.append(
                f"{len(misattributed)} figure(s) are attributed to a source that "
                "does not contain them — the number exists elsewhere in the "
                f"evidence. First: {misattributed[0].detail}."
            )
        unsupported = self.by_kind("unsupported_figure")
        if unsupported:
            lines.append(
                f"{len(unsupported)} figure(s) appear in no cited source. "
                f"First: {unsupported[0].detail}."
            )
        conflicts = self.by_kind("internal_conflict")
        if conflicts:
            lines.append(
                f"{len(conflicts)} internal inconsistency(ies): {conflicts[0].detail}."
            )
        overclaims = self.by_kind("overclaim")
        if overclaims:
            lines.append(
                f"{len(overclaims)} statement(s) are written as settled fact on "
                "evidence that is unverified or single-source; read them as "
                "provisional."
            )
        absolutes = self.by_kind("absolute_claim")
        if absolutes:
            lines.append(
                f"{len(absolutes)} statement(s) use proof or absolute language "
                "that the evidence does not support."
            )
        weak = self.weakest_sections()
        if weak:
            named = ", ".join(f"{title} ({density:.0%})" for title, density in weak[:3])
            lines.append(f"Thinly cited section(s): {named}.")

        temporal_warning = self.temporal.warning()
        if temporal_warning:
            lines.append(temporal_warning)
        independence_warning = self.independence.warning()
        if independence_warning:
            lines.append(independence_warning)

        if not lines:
            return ""
        return "\n\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings[:20]],
            "serious_count": len(self.serious),
            "temporal": self.temporal.to_dict(),
            "independence": self.independence.to_dict(),
            "section_coverage": self.coverage,
            "is_clean": self.is_clean,
        }


def assess_report_quality(
    answer: str,
    *,
    cited_facts: Sequence[Dict[str, Any]] = (),
    usable_facts: Sequence[Dict[str, Any]] = (),
    sections: Optional[Dict[str, str]] = None,
    query_type: str = "",
    temporal: Optional[TemporalProfile] = None,
    independence: Optional[IndependenceReport] = None,
) -> ResearchQualityReport:
    """Run every check on a finished draft. Total and fail-safe.

    Each check is isolated: one raising does not lose the others, and the worst
    case is fewer findings, never a broken report.
    """
    report = ResearchQualityReport()
    report.temporal = temporal or _guarded(
        lambda: temporal_profile(usable_facts, query_type=query_type), TemporalProfile()
    )
    report.independence = independence or _guarded(
        lambda: assess_independence(usable_facts), IndependenceReport()
    )

    units = _guarded(lambda: _sentence_units(answer), [])
    report.findings.extend(
        _guarded(lambda: check_citation_grounding(answer, cited_facts, sentences=units), [])
    )
    report.findings.extend(
        _guarded(lambda: detect_overclaims(answer, cited_facts, sentences=units), [])
    )
    if sections:
        report.findings.extend(_guarded(lambda: detect_internal_conflicts(sections), []))
        report.coverage = _guarded(lambda: section_coverage(sections), {})
    return report


def _guarded(fn, default):
    """Run a check; on any failure return the default rather than propagating."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a quality check must never break synthesis
        return default
