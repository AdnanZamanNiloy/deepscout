"""Translate pipeline events into the frontend's NDJSON wire contract.

The API route and the React client share a fixed event vocabulary
(``progress``/``intent``/``route``/``direct_answer``/``plan``/
``search_progress``/``search_query``/``findings``/``critic``/``final_report``/
``error``). The pipeline produces a coarser set of node-stage signals, so this
module maps them onto that vocabulary.

Design notes
------------
* Only the events the pipeline can genuinely support are emitted. ``intent`` /
  ``route`` / ``direct_answer`` describe a router the current pipeline does not
  have, so it does not fabricate them. The frontend
  treats every event type as optional (its switch has a ``default``), so
  omitting them renders a clean pipeline rather than a wrong one.
* ``search_query`` reuse: the pipeline's node stages cannot report per-query
  results, so ``search_progress`` carries the snippet count and ``search_query``
  frames are emitted only when a stage genuinely has query text.
* Frame shape matches ``routes.event_line``: ``{"type", "ts", ...data}``.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

# Pipeline stage -> the display name the trace shows for that node.
_STAGE_LABELS = {
    "retrieval": "Searching the web",
    "architect": "Planning the outline",
    "review_gate": "Reviewing the plan",
    "narrative_writer": "Writing the report",
    "evidence_auditor": "Checking the facts",
    "diagram_designer": "Generating visuals",
    "report_assembler": "Assembling the report",
}


def frame(event_type: str, **data: Any) -> Dict[str, Any]:
    """One NDJSON frame dict, matching ``routes.event_line``'s shape."""
    return {"type": event_type, "ts": int(time.time() * 1000), **data}


class PipelineEventAdapter:
    """Stateful mapper: pipeline node events -> frontend frames.

    Stateful because the wire contract is edge-triggered: ``plan`` is emitted
    once, ``search_progress`` only when the snippet count grows, ``critic`` only
    when an iteration advances. Keeping the counters on the adapter (created per
    run) avoids module-level state (AGENTS.md 4.3).
    """

    def __init__(self, query: str):
        self.query = query
        self._emitted_plan = False
        self._emitted_stages: set = set()

    def to_frames(self, pipeline_event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Map one orchestrator event dict to zero or more wire frames."""
        node = str(pipeline_event.get("node") or "")
        stage = str(pipeline_event.get("stage") or node)
        frames: List[Dict[str, Any]] = []

        if stage == "architect" and pipeline_event.get("sections") and not self._emitted_plan:
            self._emitted_plan = True
            frames.append(frame("plan", items=list(pipeline_event["sections"]), orchestration={}, waves=[]))

        elif stage == "retrieval":
            # The pipeline does not report a per-query result list, so there is no
            # honest "N sources" number at this point. The values available are a
            # CHARACTER count (initial research text) and a SECTION count — both
            # meaningless as a source count. Emitting either as
            # `search_progress.snippets` made the panel read "24229 sources"
            # (the Pathfinder stage's character count) with no relation to the real
            # source total. Surface them as honest progress messages instead.
            if node == "pathfinder":
                chars = int(pipeline_event.get("initial_research_chars") or 0)
                if chars and "pathfinder" not in self._emitted_stages:
                    self._emitted_stages.add("pathfinder")
                    frames.append(frame(
                        "progress",
                        message=f"Gathered ~{chars:,} characters of initial research",
                    ))
            elif node == "scout_swarm":
                sections = int(pipeline_event.get("research_sections") or 0)
                if sections and "scout_swarm" not in self._emitted_stages:
                    self._emitted_stages.add("scout_swarm")
                    frames.append(frame(
                        "progress",
                        message=f"Researched {sections} section{'s' if sections != 1 else ''}",
                    ))

        elif stage == "evidence_auditor":
            notes = pipeline_event.get("fact_check_notes")
            if notes:
                # A non-empty fact-check note is the pipeline's revision signal.
                frames.append(frame(
                    "critic",
                    iteration=1,
                    reason=str(notes)[:400],
                    breakdown={},
                ))

        return frames

    def stage_frame(self, stage: str) -> Optional[Dict[str, Any]]:
        """A lightweight per-stage progress frame, emitted at most once each."""
        if stage in self._emitted_stages:
            return None
        self._emitted_stages.add(stage)
        label = _STAGE_LABELS.get(stage, stage)
        return frame("progress", message=label)


def final_report_frame(
    *,
    report: str,
    sources: Iterable[str],
    confidence: float = 0.0,
    audit: str = "",
    degraded: Optional[List[str]] = None,
    degraded_reasons: Optional[Dict[str, str]] = None,
    confidence_breakdown: Optional[Dict[str, Any]] = None,
    answer_support: Optional[Dict[str, Any]] = None,
    citation_health: Optional[Dict[str, Any]] = None,
    quality: Optional[Dict[str, Any]] = None,
    contradictions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the terminal ``final_report`` frame.

    Fields mirror what the frontend's ``final_report`` case reads. The audit
    fields (confidence breakdown, answer support, citation health, quality) are
    populated by the audit bridge from the reconstructed fact pool;
    when a run produced no usable evidence they are passed through as
    empty/defaults rather than invented — the panel renders them as "not
    available" rather than as authoritative zeros with meaning.
    """
    source_list = list(sources or [])
    support_rate: Optional[float] = None
    if isinstance(answer_support, dict):
        raw = answer_support.get("rate")
        if isinstance(raw, (int, float)):
            support_rate = float(raw)
    return frame(
        "final_report",
        report=report,
        confidence=confidence,
        degraded=degraded or [],
        audit=audit,
        degraded_reasons=degraded_reasons or {},
        provider_degraded=False,
        provider_kinds=[],
        answer_support=support_rate,
        wave_report=[],
        citation_health=citation_health or {},
        quality=quality or {},
        confidence_breakdown=confidence_breakdown or {},
        contradictions=contradictions or [],
        # The real number of source URLs the run gathered. The panel's
        # "Sources analyzed" reads this (via the run's snippet count) instead of
        # the initial-research character count it was mistakenly showing.
        source_count=len(source_list),
    )
