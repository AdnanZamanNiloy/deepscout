from __future__ import annotations

from typing import Any, Dict

from app.core.logging import get_logger

logger = get_logger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _text(fact: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(fact.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _guard(fn, default):
    """Run a check; on any failure return the default. Never break a report."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - an epistemic check is never fatal
        logger.debug("[Epistemics] check failed; returning neutral result", exc_info=True)
        return default
