"""Research skills: the discrete capabilities a researcher composes."""
from __future__ import annotations

from app.research.skills.browser import BrowserManager
from app.research.skills.context import ContextManager
from app.research.skills.curator import SourceCurator
from app.research.skills.researcher import ResearchConductor
from app.research.skills.writer import ReportGenerator

__all__ = [
    "BrowserManager",
    "ContextManager",
    "SourceCurator",
    "ResearchConductor",
    "ReportGenerator",
]
