"""Shared pure primitives: safe coercion used across agents and core.

Single home for the small, total helpers that were previously copy-pasted into
several modules (`quality/primitives`, `epistemic/primitives`,
`synthesis/primitives`, `graph/evidence`). Behaviour is identical to each copy:
an unparseable value yields the supplied default instead of raising, because
external data (model output, fetched pages) is never trusted to hold clean
types.
"""
from __future__ import annotations

from typing import Any


# A RECOMMENDATION / GUIDANCE request: the user wants suggestions they can act
# on, not an analysis of the query's subject or one of its modifiers. The ask
# ("suggest some topics") is itself unambiguous and non-definitional. This is
# shared by every layer that must treat such a request differently: the
# ambiguity policy (its modifier adjectives are not a real ambiguity), the
# query-type classifier (it is exploratory, not factual), and the critic's
# definition gate (it does not owe a definitional "X is Y" claim).
#
# Structural verbs only — no subject knowledge, so this stays domain-agnostic.
_GUIDANCE_MARKERS: tuple = (
    "suggest", "recommend", "advise", "give me some", "give me a few",
    "give me ideas", "give me examples", "some ideas", "topic suggestions",
    "ideas for", "why should i", "which should i", "which should we",
    "what should i study", "what should i research", "what should i learn",
    "what should i choose", "what should i pick", "what should we study",
    "what should we research", "what should we do", "what topics",
    "which topics", "good topics", "best topics",
)


def is_guidance_query(query: Any) -> bool:
    """True when the query asks for suggestions/guidance rather than analysis."""
    low = f" {str(query or '').lower().strip()} "
    return any(marker in low for marker in _GUIDANCE_MARKERS)


def safe_int(value: Any, default: int = 0) -> int:
    """int() that never raises. Dirty data must not crash a pipeline stage."""
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_year(value: float) -> bool:
    """True for an integer in the year range (used for exact number grounding)."""
    return float(value).is_integer() and 1000.0 <= value <= 2999.0


def normalize_key(text: Any) -> str:
    """Whitespace-collapsed lowercase comparison/ordering key.

    Byte-identical to the `_norm`/`_normalize` helpers previously copied into
    answer_conformance, evidence_completion, reasoning_engine and
    synthesis_planner — a deterministic key for comparing claims and labels.
    """
    return " ".join(str(text or "").lower().split())


def jaccard(a: Any, b: Any) -> float:
    """Jaccard similarity of two token sets; 0.0 when either is empty.

    Byte-identical to the `_jaccard` copies previously in focus, evidence/text
    and core/semantic. Accepts any sized iterables (set, frozenset, list).
    """
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
