from __future__ import annotations

from typing import Any, Dict

from app.core.logging import get_logger
from app.core.primitives import safe_float as _safe_float, safe_int as _safe_int  # noqa: F401

logger = get_logger(__name__)


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
