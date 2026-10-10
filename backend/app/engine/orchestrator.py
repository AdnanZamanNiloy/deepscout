"""Run the engine's multi-agent team for one DeepScout research request.

Responsibilities
----------------
* Build the engine's ``task`` dict from a DeepScout request (query, mode,
  section count, model).
* Compile and drive the ``ChiefEditorAgent`` LangGraph team, streaming node
  updates so the API route can translate them into NDJSON frames.
* Capture the engine's own log/web-socket events through a small sink object
  shaped like the engine's ``websocket`` (``send_json``), which is the
  documented streaming seam the engine already uses.
* Assemble a final state dict shaped like the backend's ``ResearchState``
  (``final_report``, ``sources``, ``facts`` ...) so existing persistence and
  the audit layer keep working unchanged.

The engine talks to the outside world through two injected seams only — the
``websocket`` object and (via app.engine bridges) the LLM/search clients — so
this module needs no modification to the vendored code.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Engine node name -> the DeepScout pipeline stage it corresponds to. Used to
# emit node-completion events with readable stage names.
_NODE_STAGE = {
    "browser": "search",
    "planner": "planner",
    "human": "plan_review",
    "researcher": "search",
    "writer": "synthesizer",
    "fact_checker": "verifier",
    "visualizer": "visualizer",
    "publisher": "finalize",
}

# Mode presets: how many report sections and how strictly the team iterates.
_MODE_SECTIONS = {"quick": 3, "standard": 5, "deep": 8}


def _slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "")).strip("-").lower()
    return cleaned[:limit] or "run"


@dataclass
class EngineEvent:
    """One captured event from the engine's streaming seams.

    ``kind`` is the engine's coarse type (currently always ``"logs"``);
    ``action`` is the finer content key (``"initial_research"`` etc.);
    ``payload`` carries structured data when available.
    """

    kind: str
    action: str
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)


class _EventSink:
    """Object shaped like the engine's websocket, capturing events in memory.

    The engine calls ``await websocket.send_json({...})``. This sink normalizes
    those dicts into :class:`EngineEvent` and pushes them onto an asyncio queue
    the runner drains. Bounded by the run's lifetime: created per run and
    garbage-collected with it (no module-level state).
    """

    def __init__(self, queue: "asyncio.Queue[EngineEvent]"):
        self._queue = queue

    async def send_json(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        kind = str(data.get("type") or "logs")
        action = str(data.get("content") or "")
        message = str(data.get("output") or "")
        await self._queue.put(
            EngineEvent(kind=kind, action=action, message=message, payload=data)
        )


class MultiAgentRunner:
    """Drives the engine team for one query and yields normalized events."""

    def __init__(
        self,
        *,
        query: str,
        mode: str = "standard",
        model: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.query = str(query or "").strip()
        self.mode = str(mode or "standard").strip().lower()
        self.model = model or "deepscout"
        self.headers = headers or {}
        self._queue: "asyncio.Queue[EngineEvent]" = asyncio.Queue()
        self.sink = _EventSink(self._queue)
        self.sources: List[str] = []

    def _build_task(self) -> Dict[str, Any]:
        sections = _MODE_SECTIONS.get(self.mode, _MODE_SECTIONS["standard"])
        return {
            "query": self.query,
            "model": self.model,
            "max_sections": sections,
            "max_plan_revisions": 1 if self.mode == "quick" else 2,
            "max_fact_check_revisions": 1,
            "publish_formats": {"markdown": False, "pdf": False, "docx": False},
            # No interactive human step in an API context: the plan is accepted
            # immediately so the team proceeds to research.
            "include_human_feedback": False,
            "follow_guidelines": False,
            "guidelines": "",
            "source": "web",
            "verbose": False,
        }

    async def run(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield ``{type, ...}`` event dicts as the team makes progress.

        On exhaustion the caller reads :meth:`final_state` for the assembled
        report. Uses ``stream_mode="updates"`` so each yielded item is the set
        of channels a node wrote — the basis for per-stage events.
        """
        from multi_agents.agents.orchestrator import ChiefEditorAgent

        task = self._build_task()
        agent = ChiefEditorAgent(
            task=task,
            websocket=self.sink,
            stream_output=self._engine_stream_output,
            headers=self.headers,
        )
        graph = agent.init_research_team()
        chain = graph.compile()

        # Drain engine log events concurrently while the graph runs.
        drainer = asyncio.create_task(self._drain_sink())

        self._state: Dict[str, Any] = {"task": task}
        try:
            async for chunk in chain.astream(
                {"task": task},
                stream_mode="updates",
                config={"recursion_limit": 60},
            ):
                # `updates` yields {node_name: node_output} per superstep.
                if not isinstance(chunk, dict):
                    continue
                for node, output in chunk.items():
                    if isinstance(output, dict):
                        self._state.update(output)
                    yield self._node_event(node, output)
        finally:
            # Give the drainer a chance to flush, then stop it.
            await asyncio.sleep(0)
            drainer.cancel()
            try:
                await drainer
            except asyncio.CancelledError:
                pass

    async def _engine_stream_output(self, *args: Any, **kwargs: Any) -> None:
        """Engine ``stream_output`` replacement.

        The engine's real ``stream_output`` signature is
        ``(type, content, output, websocket, ...)``. We accept it positionally
        and route the message through the same sink so all engine signals share
        one ordering.
        """
        kind = args[0] if len(args) > 0 else kwargs.get("type", "logs")
        content = args[1] if len(args) > 1 else kwargs.get("content", "")
        output = args[2] if len(args) > 2 else kwargs.get("output", "")
        await self._queue.put(
            EngineEvent(kind=str(kind), action=str(content), message=str(output))
        )

    async def _drain_sink(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                self._last_engine_event = event
            except Exception:  # pragma: no cover - diagnostics only
                pass

    def _node_event(self, node: str, output: Any) -> Dict[str, Any]:
        """Map a completed graph node to a DeepScout-shaped event."""
        stage = _NODE_STAGE.get(node, node)
        event: Dict[str, Any] = {"engine_node": node, "stage": stage}
        if isinstance(output, dict):
            if node == "planner" and output.get("sections"):
                event["sections"] = list(output.get("sections") or [])
            if node == "browser" and output.get("initial_research"):
                event["initial_research_chars"] = len(
                    str(output.get("initial_research") or "")
                )
            if node in ("researcher",) and output.get("research_data") is not None:
                event["research_sections"] = len(output.get("research_data") or [])
            if node == "writer":
                event["has_introduction"] = bool(output.get("introduction"))
            if node == "fact_checker":
                event["fact_check_notes"] = output.get("fact_check_notes")
            if node == "publisher" and output.get("report"):
                event["report_chars"] = len(str(output.get("report") or ""))
        return event

    def final_state(self) -> Dict[str, Any]:
        """Assemble a backend-shaped final state from the engine's state.

        Keys mirror what ``app/api/routes.py`` reads after a run:
        ``final_report`` (primary answer), ``sources`` (for persistence and the
        trace), and ``facts``/``confidence`` where the engine provides them.
        The audit layer's richer fields are produced separately by the adapter.
        """
        state = getattr(self, "_state", {}) or {}
        report = str(state.get("report") or self._render_report(state) or "")
        sources = self._collect_sources(state)
        return {
            "final_report": report,
            "sources": sources,
            "title": state.get("title"),
            "sections": state.get("sections") or [],
            "fact_check_notes": state.get("fact_check_notes"),
            "engine_state": state,
        }

    def _render_report(self, state: Dict[str, Any]) -> str:
        """Fallback markdown assembly if the publisher node did not run.

        The publisher node normally writes ``report``. If a run ends early
        (fact-check ceiling, cancellation), assemble what exists so the user
        still receives the draft rather than nothing.
        """
        parts: List[str] = []
        if state.get("title"):
            parts.append(f"# {state['title']}\n")
        if state.get("introduction"):
            parts.extend(["## Introduction", str(state["introduction"]), ""])
        for section in state.get("research_data") or []:
            if isinstance(section, dict):
                for _, value in section.items():
                    parts.append(str(value))
            else:
                parts.append(str(section))
            parts.append("")
        if state.get("conclusion"):
            parts.extend(["## Conclusion", str(state["conclusion"]), ""])
        return "\n".join(parts).strip()

    def _collect_sources(self, state: Dict[str, Any]) -> List[str]:
        """Extract cited URLs from the assembled report and research data.

        The default engine graph does not populate ``sources``; this recovers
        the URLs actually cited so persistence, the citation legend and the
        export path have real data instead of an empty list.
        """
        text = str(state.get("report") or self._render_report(state) or "")
        found: List[str] = []
        seen: set = set()
        for match in re.findall(r"https?://[^\s)\]>\"']+", text):
            url = match.rstrip(".,;")
            if url not in seen:
                seen.add(url)
                found.append(url)
        # Engine-provided sources (subtopic reports append a Sources section).
        for source in state.get("sources") or []:
            text_source = str(source)
            if text_source.startswith("http") and text_source not in seen:
                seen.add(text_source)
                found.append(text_source)
        self.sources = found
        return found
