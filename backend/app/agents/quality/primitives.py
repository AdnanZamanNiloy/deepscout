from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import List, Set, Tuple

from app.agents.evidence_utils import extract_numbers
from app.core.primitives import safe_float as _safe_float, safe_int as _safe_int  # noqa: F401


_TRIVIAL_NUMBERS: Set[float] = {float(n) for n in range(0, 11)}


def _significant_values(text: str, limit: int = 16) -> Set[Tuple[float, str]]:
    """(value, unit) pairs worth checking, with bare small integers dropped."""
    out: Set[Tuple[float, str]] = set()
    try:
        quantities = extract_numbers(text or "", limit=limit)
    except Exception:  # noqa: BLE001 - never let extraction break the audit
        return out
    for q in quantities:
        unit = str(getattr(q, "unit", "") or "").lower()
        value = round(_safe_float(getattr(q, "value", 0.0)), 4)
        if value in _TRIVIAL_NUMBERS and not unit:
            continue
        out.add((value, unit))
    return out


def _values_match(a: Tuple[float, str], b: Tuple[float, str], tolerance: float = 0.02) -> bool:
    """Same quantity, allowing paraphrase rounding but not a different year.

    Years and small magnitudes compare exactly: a relative tolerance would make
    2024 and 2025 the same number, which is the single most plausible
    fabrication in a research report.
    """
    value_a, unit_a = a
    value_b, unit_b = b
    if unit_a and unit_b and unit_a != unit_b:
        return False
    if value_a == value_b:
        return True
    if _is_year(value_a) or _is_year(value_b) or abs(value_a) < 20 or abs(value_b) < 20:
        return False
    scale = max(abs(value_a), abs(value_b), 1e-9)
    return abs(value_a - value_b) / scale <= tolerance


from app.core.primitives import is_year as _is_year  # noqa: F401


def _now() -> datetime:
    """Naive UTC now. `_now()` is deprecated from Python 3.12."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _markers(sentence: str) -> List[int]:
    return [int(m) for m in re.findall(r"\[(\d+)\]", sentence or "")]


def _strip_markers(sentence: str) -> str:
    return re.sub(r"\[\d+\]", "", sentence or "").strip()
