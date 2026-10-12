from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime



def parse_published_date(value: str) -> str:
    """Best-effort parse of provider/fetch date strings to YYYY-MM-DD."""
    text = (value or "").strip()
    if not text:
        return ""
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).date().isoformat()
        except (ValueError, TypeError):
            pass
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except (TypeError, ValueError):
            continue
    return ""
