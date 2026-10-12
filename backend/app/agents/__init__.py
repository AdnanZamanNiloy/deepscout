"""The agent team: orchestration and the individual agents."""
from __future__ import annotations

from app.agents.architect import ResearchArchitect
from app.agents.team import ResearchTeam, build_research_team, Pathfinder
from app.agents.report import (
    EvidenceAuditor,
    ReviewGate,
    ReportAssembler,
    DiagramDesigner,
    NarrativeWriter,
)
from app.agents.scout import (
    DraftReviewer,
    DraftReviser,
    Scout,
)

__all__ = [
    "ResearchArchitect",
    "ResearchTeam",
    "build_research_team",
    "Pathfinder",
    "EvidenceAuditor",
    "ReviewGate",
    "ReportAssembler",
    "DiagramDesigner",
    "NarrativeWriter",
    "DraftReviewer",
    "DraftReviser",
    "Scout",
]
