"""Research skills: the discrete capabilities a researcher composes."""
from __future__ import annotations

from app.research.skills.browser import PageScraper
from app.research.skills.context import ContextManager
from app.research.skills.curator import SourceRanker
from app.research.skills.researcher import EvidenceGatherer
from app.research.skills.writer import ReportWriter

__all__ = [
    "PageScraper",
    "ContextManager",
    "SourceRanker",
    "EvidenceGatherer",
    "ReportWriter",
]
