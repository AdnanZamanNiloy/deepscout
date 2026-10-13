"""Enumerations for the research pipeline.

Three independent axes travel with every request: the shape of report to
produce, where its evidence may come from, and the voice it is written in.
Each is a closed enum so a value is spelled once, here, and nowhere else — a
prompt and a branch check can never disagree about it.
"""
from __future__ import annotations

from enum import Enum


class ReportKind(Enum):
    """The shape of report a run produces."""

    DeepDive = "deep_dive"
    Expanded = "expanded"
    Section = "section"


class EvidenceScope(Enum):
    """The evidence pool a run may draw from."""

    Web = "web"
    Local = "local"
    Mixed = "mixed"


class Tone(Enum):
    """The writing voice applied to the finished report."""

    Objective = "Objective (impartial and unbiased presentation of facts and findings)"
