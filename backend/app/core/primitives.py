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
