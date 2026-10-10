"""Translate engine events into the frontend's NDJSON wire contract.

The API route and the React client share a fixed event vocabulary
(``progress``/``intent``/``route``/``direct_answer``/``plan``/
``search_progress``/``search_query``/``findings``/``critic``/``final_report``/
``error``). The engine produces a coarser set of node-stage signals, so this
module maps them onto that vocabulary.

Design notes
------------
* Only the events the engine can genuinely support are emitted. ``intent`` /
  ``route`` / ``direct_answer`` describe the *built-in* router's decisions; the
  engine team has no equivalent, so it does not fabricate them. The frontend
  treats every event type as optional (its switch has a ``default``), so
  omitting them renders a clean pipeline rather than a wrong one.
* ``search_query`` reuse: the engine's node stages cannot report per-query
  results, so ``search_progress`` carries the snippet count and ``search_query``
  frames are emitted only when a stage genuinely has query text.
* Frame shape matches ``routes.event_line``: ``{"type", "ts", ...data}``.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

# Engine stage -> the display name the trace shows for that node.
_STAGE_LABELS = {
    "search": "Search the web",
    "planner": "Planning the outline",
    "plan_review": "Reviewing the plan",
    "synthesizer": "Writing the report",
    "verifier": "Checking the facts",
    "visualizer": "Generating visuals",
    "finalize": "Assembling the report",
}


def frame(event_type: str, **data: Any) -> Dict[str, Any]:
    """One NDJSON frame dict, matching ``routes.event_line``'s shape."""
    return {"type": event_type, "ts": int(time.time() * 1000), **data}


class EngineEventAdapter:
    """Stateful mapper: engine node events -> frontend frames.

    Stateful because the wire contract is edge-triggered: ``plan`` is emitted
    once, ``search_progress`` only when the snippet count grows, ``critic`` only
    when an iteration advances. Keeping the counters on the adapter (created per
    run) avoids module-level state (AGENTS.md 4.3).
    """

    def __init__(self, query: str):
        self.query = query
        self._emitted_plan = False
        self._last_snippets = -1
        self._emitted_stages: set = set()

    def to_frames(self, engine_event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Map one orchestrator event dict to zero or more wire frames."""
        node = str(engine_event.get("engine_node") or "")
        stage = str(engine_event.get("stage") or node)
        frames: List[Dict[str, Any]] = []

        if stage == "planner" and engine_event.get("sections") and not self._emitted_plan:
            self._emitted_plan = True
            frames.append(frame("plan", items=list(engine_event["sections"]), orchestration={}, waves=[]))

        elif stage == "search":
            # The engine does not report a per-query result list; surface the
            # progress count so the pipeline trace shows a live Search stage.
            if node == "browser":
                count = int(engine_event.get("initial_research_chars") or 0)
            else:
                count = int(engine_event.get("research_sections") or 0)
            if count > self._last_snippets:
                self._last_snippets = count
                frames.append(frame("search_progress", snippets=count))

        elif stage == "verifier":
            notes = engine_event.get("fact_check_notes")
            if notes:
                # A non-empty fact-check note is the engine's revision signal.
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
) -> Dict[str, Any]:
    """Build the terminal ``final_report`` frame.

    Fields mirror what the frontend's ``final_report`` case reads. The engine
    has no confidence engine or citation auditor, so those are passed through
    as empty/defaults rather than invented — the audit panel renders them as
    "not available" rather than as authoritative zeros with meaning.
    """
    return frame(
        "final_report",
        report=report,
        confidence=confidence,
        degraded=degraded or [],
        audit=audit,
        degraded_reasons=degraded_reasons or {},
        provider_degraded=False,
        provider_kinds=[],
        answer_support=None,
        wave_report=[],
        citation_health={},
        quality={},
        outline={},
        section_wise=False,
    )
