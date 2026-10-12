"""Enumerations for the research pipeline.

Report shape, source type and writing tone are the three knobs that flow from
a request into prompt construction and retrieval behaviour. They are plain
enums (not strings) so that the values used in prompts and branch checks have
one definition each and cannot drift between modules.
"""
from __future__ import annotations

from enum import Enum


class ReportType(Enum):
    """The kind of report a run produces."""

    ResearchReport = "research_report"
    DetailedReport = "detailed_report"
    SubtopicReport = "subtopic_report"


class ReportSource(Enum):
    """Where a run gathers evidence from."""

    Web = "web"
    Local = "local"
    Hybrid = "hybrid"


class Tone(Enum):
    """Writing tone applied to the generated report."""

    Objective = "Objective (impartial and unbiased presentation of facts and findings)"
