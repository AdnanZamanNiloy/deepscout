from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.core.logging import get_logger
logger = get_logger(__name__)
from app.core.synthesis_intel.constants import (
    _BULLET_RE,
    _HEADING_RE,
    _SENTENCE_SPLIT_RE,
)
from app.core.synthesis_intel.moves import (
    _choose_move,
    refine_restatement,
)
from app.core.synthesis_intel.tokens import (
    analytical_dimensions,
    claim_key,
    claim_polarity,
)
from app.core.synthesis_intel.units import (
    _fuzzy_restatement,
    _is_refinable_sentence,
    _is_sentence_unit,
    _iter_units,
    _similar_to_any,
    split_report_sections,
)


@dataclass
class Refinement:
    """The outcome of classifying one writer sentence against the ledger."""

    text: str
    kept: bool = True
    transformed: bool = False
    dimension: str = ""
    first_section: str = ""
    # The adaptive reasoning move chosen for a transformed restatement
    # (implication / mechanism / causal / tradeoff / uncertainty / comparison /
    # strategic). Empty for kept-verbatim sentences.
    move: str = ""


@dataclass
class ClaimUse:
    """One claim, where it was first used, and the dimensions seen so far."""

    key: str
    text: str
    section: str
    dimensions: Set[str] = field(default_factory=set)
    occurrences: int = 1


@dataclass
class SynthesisIntelligenceReport:
    """Measured redundancy for one assembled report (the benchmark score)."""

    total_sentences: int = 0
    unique_claims: int = 0
    repeated_claims: int = 0
    removed_restatements: int = 0
    refined_transitions: int = 0
    allowed_expansions: int = 0
    sections: int = 0
    top_repeats: List[Dict[str, object]] = field(default_factory=list)

    @property
    def redundancy_ratio(self) -> float:
        """Repeated-claim count over unique claims; 0 is ideal."""
        if not self.unique_claims:
            return 0.0
        return round(self.repeated_claims / self.unique_claims, 4)

    def to_dict(self) -> Dict[str, object]:
        return {
            "total_sentences": self.total_sentences,
            "unique_claims": self.unique_claims,
            "repeated_claims": self.repeated_claims,
            "removed_restatements": self.removed_restatements,
            "refined_transitions": self.refined_transitions,
            "allowed_expansions": self.allowed_expansions,
            "sections": self.sections,
            "redundancy_ratio": self.redundancy_ratio,
            "top_repeats": self.top_repeats[:8],
        }


class ClaimLedger:
    """Tracks claim keys across sections and decides what may be re-used.

    Deterministic and order-sensitive: the FIRST section to state a claim
    keeps it (with its citation); later sections may only re-use it when they
    add an analytical dimension the earlier uses did not.
    """

    def __init__(self) -> None:
        self._uses: Dict[str, ClaimUse] = {}
        self._texts: List[str] = []
        self.removed_restatements = 0
        self.refined_restatements = 0
        self.allowed_expansions = 0
        self.total_sentences = 0

    @property
    def unique_claims(self) -> int:
        return len(self._uses)

    @property
    def repeated_claims(self) -> int:
        return sum(1 for use in self._uses.values() if use.occurrences > 1)

    def _find_prior(self, sentence: str) -> Optional[ClaimUse]:
        key = claim_key(sentence)
        if key:
            use = self._uses.get(key)
            if use is not None and claim_polarity(use.text) != claim_polarity(sentence):
                # Negation flip: same vocabulary, opposite assertion. Not a
                # repeat; fall through to the similarity check for a second
                # opinion rather than treating as same-key.
                use = None
            if use is not None:
                return use
        polarity = claim_polarity(sentence)
        for use in self._uses.values():
            if claim_polarity(use.text) != polarity:
                continue
            if _similar_to_any(sentence, [use.text]) or _fuzzy_restatement(sentence, use.text):
                return use
        return None

    def refine(
        self,
        section: str,
        sentence: str,
        *,
        signals: Optional[Dict[str, object]] = None,
        is_bullet: bool = False,
    ) -> Refinement:
        """Classify a sentence; TRANSFORM a bare restatement instead of dropping it.

        * A new claim is kept verbatim and registered.
        * A repeat that adds a genuinely new analytical dimension is kept
          verbatim (expansion) and the dimension is recorded.
        * A bare restatement is rewritten into a transition + analysis
          sentence that preserves the original claim, its number and its `[n]`
          marker. It is never removed, so a section can never lose its opening.
        * An enumerable BULLET restatement (`is_bullet`) is kept verbatim: a
          bullet is a data item, not a prose argument, and refining it spliced
          move clauses into Key Figures in live reports.

        `signals` carries evidence-derived hints for the analytical clause
        (contradicted / uncertain / corroborated / primary / axis); the
        transformation works deterministically with no signals and no LLM.
        """
        self.total_sentences += 1
        key = claim_key(sentence)
        prior = self._find_prior(sentence)
        if prior is None:
            if key:
                self._uses[key] = ClaimUse(key=key, text=sentence, section=section)
            self._texts.append(sentence)
            return Refinement(text=sentence, kept=True, transformed=False)

        prior.occurrences += 1
        dimensions = analytical_dimensions(sentence)
        novel = dimensions - prior.dimensions
        if novel:
            # Genuinely expanded: record the new dimension and allow it intact.
            prior.dimensions |= dimensions
            self.allowed_expansions += 1
            self._texts.append(sentence)
            return Refinement(text=sentence, kept=True, transformed=False)

        if not _is_refinable_sentence(sentence, is_bullet=is_bullet):
            # A bullet, label-style line or fragment: leave it intact rather
            # than splice an analytical clause onto a data item or non-sentence.
            self._texts.append(sentence)
            return Refinement(text=sentence, kept=True, transformed=False)

        move = _choose_move(sentence, signals)
        refined = refine_restatement(
            sentence,
            move=move,
            seed=key or sentence,
        )
        if refined == sentence.strip():
            # No topic-specific move clause applied, so the sentence is left
            # exactly as written. Treat it as kept-verbatim rather than a
            # refinement: nothing was transformed and no generic tail invented.
            self._texts.append(sentence)
            return Refinement(text=sentence, kept=True, transformed=False)
        # Record the dimension the move maps onto so a later true expansion of
        # the same claim is still detected as novel. `analytical_dimensions`
        # keeps its narrow vocabulary; trade-off/causal/strategic moves are
        # recorded as the implication dimension they express.
        prior.dimensions.add(
            move if move in ("mechanism", "implication", "comparison", "uncertainty") else "implication"
        )
        self.refined_restatements += 1
        self.removed_restatements += 1
        self._texts.append(refined)
        logger.debug(
            "[SynthesisIntel] refined restatement in section '%s' (first used in '%s', move=%s)",
            section, prior.section, move,
        )
        return Refinement(
            text=refined,
            kept=True,
            transformed=True,
            dimension=move,
            first_section=prior.section,
            move=move,
        )

    def register(self, section: str, sentence: str) -> bool:
        """Backward-compatible wrapper returning whether a sentence is kept.

        With the refinement layer every claim-bearing sentence is kept, so
        this always returns True for a non-empty unit; callers that need the
        transformed text must use `refine`.
        """
        return self.refine(section, sentence).kept

    def register_recap(self, section: str, sentence: str) -> None:
        """Record a claim from a recap section (Executive Summary / Key
        Findings) WITHOUT counting it as removable.

        Recaps are the report's deliberate preview; their text is kept whole,
        but their claims become prior uses so a deep-dive section cannot
        restate them as if newly discovered.
        """
        key = claim_key(sentence)
        self.total_sentences += 1
        prior = self._find_prior(sentence)
        if prior is None:
            if key:
                self._uses[key] = ClaimUse(key=key, text=sentence, section=section)
            self._texts.append(sentence)
            return
        prior.occurrences += 1
        prior.dimensions |= analytical_dimensions(sentence)
        self._texts.append(sentence)


def compress_section_text(
    text: str,
    ledger: ClaimLedger,
    section: str,
    *,
    signals: Optional[Dict[str, object]] = None,
) -> str:
    """Refine restating sentences in one section body, preserving structure.

    A restatement is never deleted: it is rewritten into a transition +
    analysis sentence (see `ClaimLedger.refine`), so a section's opening
    sentence survives as a coherent opener even when it repeated an earlier
    claim. Line-granular: headings are never touched and an empty section is
    never produced.
    """
    out_lines: List[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if not _is_sentence_unit(line):
            out_lines.append(line)
            continue
        is_bullet = bool(_BULLET_RE.match(line))
        stripped = line.strip()
        prefix = "- " if is_bullet else ""
        body = stripped.lstrip("-* ").strip()
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
        kept: List[str] = []
        for sentence in sentences:
            # `is_bullet` forbids transformation (an enumerable item is not a
            # prose restatement) but still registers the claim, so a later
            # prose section cannot restate a figure the list already stated.
            outcome = ledger.refine(section, sentence, signals=signals, is_bullet=is_bullet)
            if outcome.kept and outcome.text:
                kept.append(outcome.text)
        if not kept:
            continue
        if is_bullet:
            out_lines.extend(f"{prefix}{s}" for s in kept)
        else:
            out_lines.append(" ".join(kept))
    return "\n".join(out_lines)


def apply_synthesis_intelligence(
    answer: str,
    *,
    protect_headings: Sequence[str] = (),
    recap_headings: Sequence[str] = (),
    signals: Optional[Dict[str, object]] = None,
) -> Tuple[str, SynthesisIntelligenceReport]:
    """Deterministically REFINE cross-section restatements in a report.

    A restatement is rewritten into a contextual transition + analysis
    sentence rather than deleted (see `ClaimLedger.refine`), so a section
    whose opening repeated an earlier claim still opens with a complete,
    coherent sentence and the reader learns what the repetition means.

    `protect_headings` names sections that must keep their full body (the
    machine-appended appendices, which describe measured state and are not
    writer prose).

    `recap_headings` names the report's deliberate summary sections
    (Executive Summary, Key Findings). Their bodies are never refined —
    a brief is expected to preview its findings there — but every claim they
    state IS registered, so the deep-dive sections cannot restate them. This
    is the observed failure mode: the Executive Summary established "Rooppur
    … US$13 billion [1]" and seven later sections re-asserted it instead of
    adding analysis.

    `signals` carries report-level evidence hints (contradicted / uncertain /
    corroborated / primary / authoritative / query / query_type) that select
    the adaptive reasoning move of each refinement; the section axis is added
    per section from its heading. It is optional; the deterministic
    transformation runs with no signals and no LLM.
    """
    if not answer:
        return answer, SynthesisIntelligenceReport()

    protected = {h.strip().lower() for h in protect_headings}
    recap = {h.strip().lower() for h in recap_headings}
    sections = split_report_sections(answer)
    ledger = ClaimLedger()
    rebuilt: List[str] = []

    for heading, lines in sections:
        heading_key = heading.strip().lower()
        body_lines = list(lines)
        has_prose = any(_is_sentence_unit(l) for l in body_lines)
        if heading and has_prose and heading_key not in protected:
            if heading_key in recap:
                # Register the recap's claims so later sections cannot restate
                # them, but keep the recap text itself intact.
                for _, sentence in _iter_units(body_lines):
                    ledger.register_recap(heading, sentence)
            else:
                section_signals = dict(signals or {})
                section_signals.setdefault("axis", heading)
                body_lines = compress_section_text(
                    "\n".join(body_lines),
                    ledger,
                    heading or "Preamble",
                    signals=section_signals,
                ).split("\n")
        block = [heading] if heading else []
        block.extend(body_lines)
        rebuilt.append("\n".join(block))

    refined = "\n".join(rebuilt).strip()

    # Guard: never return an empty body because of this layer. An over-eager
    # transformation is worse than the redundancy it addresses, so fall back
    # to the original text when nothing meaningful survived.
    if not _has_writer_prose(refined):
        logger.warning("[SynthesisIntel] refinement emptied the report; keeping original")
        return answer, SynthesisIntelligenceReport()

    report = SynthesisIntelligenceReport(
        total_sentences=ledger.total_sentences,
        unique_claims=ledger.unique_claims,
        repeated_claims=ledger.repeated_claims,
        removed_restatements=ledger.removed_restatements,
        refined_transitions=ledger.refined_restatements,
        allowed_expansions=ledger.allowed_expansions,
        sections=sum(1 for h, l in sections if h and any(_is_sentence_unit(x) for x in l)),
        top_repeats=_top_repeats(ledger),
    )
    if report.refined_transitions:
        logger.info(
            "[SynthesisIntel] refined %d cross-section restatement(s) into transitions; %d expansion(s) kept",
            report.refined_transitions, report.allowed_expansions,
        )
    return refined, report


def _has_writer_prose(text: str) -> bool:
    """True when the report still carries more than headings alone."""
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if not stripped or _HEADING_RE.match(stripped):
            continue
        if re.search(r"[A-Za-z]", stripped) and len(stripped.split()) >= 4:
            return True
    return False


def _top_repeats(ledger: ClaimLedger, limit: int = 8) -> List[Dict[str, object]]:
    repeated = sorted(
        (u for u in ledger._uses.values() if u.occurrences > 1),
        key=lambda u: (-u.occurrences, u.section),
    )
    return [
        {
            "occurrences": u.occurrences,
            "first_section": u.section,
            "dimensions": sorted(u.dimensions),
            "text": u.text[:160],
        }
        for u in repeated[:limit]
    ]


logger = get_logger(__name__)
