"""Result dataclasses for the synthesis package.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). `CitationAudit` is the measured traceability of a finished draft;
`SynthesisResult` is the report plus everything needed to defend it.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class CitationAudit:
    """What the finished draft actually supports, measured not assumed."""

    total_sentences: int = 0
    cited_sentences: int = 0
    uncited_factual: List[str] = field(default_factory=list)
    invalid_markers: List[int] = field(default_factory=list)
    ungrounded_numbers: List[str] = field(default_factory=list)
    weakly_supported: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def citation_density(self) -> float:
        if not self.total_sentences:
            return 0.0
        return round(self.cited_sentences / self.total_sentences, 4)

    @property
    def is_clean(self) -> bool:
        return not (self.uncited_factual or self.invalid_markers or self.ungrounded_numbers)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_sentences": self.total_sentences,
            "cited_sentences": self.cited_sentences,
            "citation_density": self.citation_density,
            "uncited_factual": self.uncited_factual[:10],
            "invalid_markers": self.invalid_markers[:10],
            "ungrounded_numbers": self.ungrounded_numbers[:10],
            "weakly_supported": self.weakly_supported[:10],
            "is_clean": self.is_clean,
        }


@dataclass
class SynthesisResult:
    """The report plus everything needed to defend it."""

    answer: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    audit: CitationAudit = field(default_factory=CitationAudit)
    used_fallback: bool = False
    angles: List[str] = field(default_factory=list)
    synthesis_intelligence: Dict[str, Any] = field(default_factory=dict)
    profile: str = ""
    word_count: int = 0
    quality: Dict[str, Any] = field(default_factory=dict)
    # Machine-owned provenance that is deliberately NOT part of the primary
    # answer for adaptive profiles (evidence accounting, integrity notes,
    # objection blocks). It is emitted here so an audit consumer can render it
    # without it ever touching the answer body.
    machine_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "audit": self.audit.to_dict(),
            "used_fallback": self.used_fallback,
            "angles": list(self.angles),
            "synthesis_intelligence": dict(self.synthesis_intelligence),
            "profile": self.profile,
            "word_count": self.word_count,
            "quality": dict(self.quality),
            "machine_notes": list(self.machine_notes),
        }
