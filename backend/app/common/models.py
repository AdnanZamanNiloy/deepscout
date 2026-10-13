"""Shared data shapes for the research pipeline.

The pipeline moves three kinds of structured data between stages:

* a :class:`Document` — a page of text with a title and source URL, used by the
  context filters and prompt formatting;
* the graph **state** dicts — ``ResearchState`` (top-level team state) and
  ``DraftState`` (per-section editing state);
* retrieved **site** records — the raw dicts the retriever layer returns.

``Document`` is a small stand-in for a heavier framework document object: the
pipeline only ever reads ``page_content`` and ``metadata``, so a dataclass keeps
the context layer free of an external dependency while matching that attribute
access surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict


@dataclass
class Document:
    """A single page of content plus its metadata.

    Mirrors the ``.page_content`` / ``.metadata`` access the context filters and
    prompt formatters rely on. ``metadata`` carries at least ``title`` and
    ``source`` (the URL); other keys are passed through untouched.
    """

    page_content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ResearchState(TypedDict, total=False):
    """Top-level team state flowing through the agent graph.

    Every key is optional because LangGraph merges partial node outputs into
    the running state; a node simply returns the keys it produced.
    """

    task: Dict[str, Any]
    initial_research: str
    title: Optional[str]
    date: Optional[str]
    sections: List[str]
    # Candidate outlines the planner produced, for interactive plan review.
    plan_alternatives: List[List[str]]
    # Free-text direction the user gave at plan review; flows into section research.
    plan_direction: str
    research_data: List[Any]
    introduction: str
    table_of_contents: str
    conclusion: str
    headers: Dict[str, Any]
    sources: List[Any]
    research_images: List[Any]
    diagrams: List[Any]
    report: str
    fact_check_notes: Optional[str]
    fact_check_revision_count: int
    human_feedback: Optional[str]
    plan_revision_count: int


class DraftState(TypedDict, total=False):
    """Per-section editing state for the parallel research sub-graph."""

    task: Dict[str, Any]
    topic: str
    title: Optional[str]
    headers: Dict[str, Any]
    draft: Any
    review: Optional[str]
    revision_notes: Optional[str]
    draft_revision_count: int
    sibling_sections: List[str]
