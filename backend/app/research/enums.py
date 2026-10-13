"""Enumerations for the research pipeline.

Three independent axes travel with every request: what shape of report to
produce, where its evidence may come from, and the voice it is written in.
Keeping each as a closed enum means a value is spelled once here and nowhere
else, so a prompt and a branch check can never disagree about it.
"""
from __future__ import annotations

from enum import Enum


class ReportType(Enum):
    """The shape of report a run produces."""

    ResearchReport = "research_report"
    DetailedReport = "detailed_report"
    SubtopicReport = "subtopic_report"


class ReportSource(Enum):
    """The evidence pool a run may draw from."""

    Web = "web"
    Local = "local"
    Hybrid = "hybrid"


class Tone(Enum):
    """The writing voice applied to the finished report."""

    Objective = "Objective (impartial and unbiased presentation of facts and findings)"
