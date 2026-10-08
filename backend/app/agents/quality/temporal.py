from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Sequence

from app.agents.quality.primitives import (
    _now,
)


_DATE_PATTERNS: Sequence["re.Pattern[str]"] = (
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{4})/(\d{2})/(\d{2})"),
    re.compile(r"^(\d{4})-(\d{2})$"),
    re.compile(r"^(\d{4})$"),
)


_TIME_SENSITIVE_TYPES = {"status", "forecast", "timeline", "decision", "comparison"}


_STALE_DAYS_SENSITIVE = 270


_STALE_DAYS_GENERAL = 1095


def _parse_date(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    # ISO with time, the common case from most crawlers.
    try:
        cleaned = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = [int(g) for g in match.groups()]
        try:
            if len(groups) == 3:
                return datetime(groups[0], groups[1], groups[2])
            if len(groups) == 2:
                return datetime(groups[0], groups[1], 1)
            return datetime(groups[0], 1, 1)
        except ValueError:
            continue
    return None


def _fact_date(fact: Dict[str, Any]) -> Optional[datetime]:
    """Publication date preferred; retrieval date is a weak upper bound only.

    Retrieval tells you when the crawler ran, not when the claim was true, so
    it is never used to make evidence look fresh — only to date a source that
    published no date at all, and such facts are counted as undated.
    """
    for key in ("published_at", "published", "date", "article_date"):
        parsed = _parse_date(fact.get(key))
        if parsed:
            return parsed
    return None


@dataclass
class TemporalProfile:
    """When this report's evidence was actually published."""

    oldest: Optional[datetime] = None
    newest: Optional[datetime] = None
    median_age_days: Optional[int] = None
    dated: int = 0
    undated: int = 0
    time_sensitive: bool = False
    stale: bool = False

    @property
    def coverage(self) -> float:
        total = self.dated + self.undated
        return round(self.dated / total, 3) if total else 0.0

    def as_of_line(self) -> str:
        """The dating line every research report should carry and most don't."""
        if not self.newest:
            return (
                "- Evidence dating: no source carried a publication date, so the "
                "currency of these findings cannot be established"
            )
        span = (
            f"{self.oldest:%b %Y} to {self.newest:%b %Y}"
            if self.oldest and self.oldest != self.newest
            else f"{self.newest:%b %Y}"
        )
        line = f"- Evidence published: {span} (most recent source {self.newest:%d %b %Y})"
        if self.undated:
            line += f"; {self.undated} source(s) carried no date"
        return line

    def warning(self) -> str:
        if not self.stale or not self.newest:
            return ""
        age_days = (_now() - self.newest).days
        months = max(1, age_days // 30)
        if self.time_sensitive:
            return (
                f"The most recent source is about {months} month(s) old, and this "
                "question asks about a current or forward-looking state. Treat "
                f"every 'current' claim as true AS OF {self.newest:%B %Y}, not "
                "today, and re-run before acting on it."
            )
        return (
            f"The evidence base is dated — the most recent source is about "
            f"{months} month(s) old. Anything that has moved since then is not "
            "reflected here."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "oldest": self.oldest.isoformat() if self.oldest else None,
            "newest": self.newest.isoformat() if self.newest else None,
            "median_age_days": self.median_age_days,
            "dated": self.dated,
            "undated": self.undated,
            "date_coverage": self.coverage,
            "time_sensitive": self.time_sensitive,
            "stale": self.stale,
        }


def temporal_profile(
    facts: Sequence[Dict[str, Any]],
    *,
    query_type: str = "",
    now: Optional[datetime] = None,
) -> TemporalProfile:
    """Measure the evidence's age and decide whether it is too old to be silent.

    Staleness is relative to the QUESTION, not an absolute cutoff: an 18-month
    old paper is perfectly good evidence for "how does attention work" and
    disqualifying for "what is the current market leader".
    """
    now = now or _now()
    profile = TemporalProfile(time_sensitive=str(query_type or "").lower() in _TIME_SENSITIVE_TYPES)
    dates: List[datetime] = []
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        parsed = _fact_date(fact)
        if parsed and parsed <= now:
            dates.append(parsed)
        else:
            profile.undated += 1
    if not dates:
        return profile
    dates.sort()
    profile.dated = len(dates)
    profile.oldest = dates[0]
    profile.newest = dates[-1]
    ages = sorted((now - d).days for d in dates)
    profile.median_age_days = ages[len(ages) // 2]

    limit = _STALE_DAYS_SENSITIVE if profile.time_sensitive else _STALE_DAYS_GENERAL
    newest_age = (now - profile.newest).days
    profile.stale = newest_age > limit or (profile.median_age_days or 0) > limit * 2
    return profile
