from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timezone
from typing import Dict, Optional, Sequence

from app.core.logging import get_logger

logger = get_logger(__name__)


FRESHNESS_HALF_LIFE: Dict[str, float] = {
    "news": 120.0,
    "statistical": 550.0,
    "comparison": 730.0,
    "academic": 1460.0,
    "encyclopedia": 2200.0,
    "default": 730.0,
}


def _as_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).date()
        except (TypeError, ValueError):
            continue
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except (TypeError, ValueError):
            continue
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
        return None


def freshness_score(
    published_at: str,
    search_type: str = "default",
    today: Optional[date] = None,
    unknown_score: float = 0.45,
) -> float:
    """Exponential-decay recency weight in [0, 1].

    `unknown_score` is deliberately mid-scale, not 0: an undated page is
    unknown, not stale, and penalizing it as stale would systematically
    demote primary PDFs (which rarely expose dates) in favour of blogs
    (which always do).
    """
    parsed = _as_date(published_at)
    if parsed is None:
        return unknown_score
    ref = today or datetime.now(timezone.utc).date()
    age_days = max(0.0, (ref - parsed).days)
    half_life = FRESHNESS_HALF_LIFE.get(
        (search_type or "default").strip().lower(), FRESHNESS_HALF_LIFE["default"]
    )
    return round(0.5 ** (age_days / half_life), 4)


def evidence_freshness(
    items: Sequence[Dict[str, object]],
    search_type_key: str = "search_type",
    date_key: str = "published_at",
    today: Optional[date] = None,
) -> float:
    """Mean freshness across items; 0.45 (unknown) when nothing is dated."""
    scores = [
        freshness_score(
            str(item.get(date_key, "") or ""),
            str(item.get(search_type_key, "default") or "default"),
            today=today,
        )
        for item in items or []
    ]
    return round(sum(scores) / len(scores), 4) if scores else 0.45
