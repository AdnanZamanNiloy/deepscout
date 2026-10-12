"""Drive the research team for one request and stream node progress.

:class:`ResearchRunner` is the single entry point the API route uses. It builds
the run's configuration and researcher factory, compiles the agent graph,
streams per-node updates, and assembles a final state dict shaped like the rest
of the backend expects (``final_report``, ``sources``, audit fields).

The runner owns the run-scoped plumbing that must not be module-level state:
the retriever's per-run source sink, the researcher factory, and the output
directory. Everything is created per run and garbage-collected with it.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.research.agents.orchestrator import build_chief_editor
from app.research.config import ResearchConfig, load_config
from app.research.enums import ReportType, Tone
from app.research.llm import LLMFunnel
from app.research.researcher import Researcher
from app.research.retrieval.factory import build_retriever_class
from app.research.streaming import EventSink

logger = get_logger(__name__)

# Node name -> the pipeline stage it corresponds to, for readable stage events.
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

# Mode presets: report sections and revision strictness.
_MODE_SECTIONS = {"quick": 3, "standard": 5, "deep": 8}


@dataclass
class RunnerEvent:
    """One normalized event from the team's progress."""

    kind: str
    action: str
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)


class _QueueSink(EventSink):
    """EventSink that pushes normalized events onto an asyncio queue."""

    def __init__(self, queue: "asyncio.Queue[RunnerEvent]"):
        self._queue = queue

    async def emit(self, kind: str, content: str, output: str, metadata: Any = None) -> None:
        await self._queue.put(
            RunnerEvent(kind=str(kind), action=str(content), message=str(output))
        )


class ResearchRunner:
    """Runs the agent team for one query and yields normalized events."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        search_client,
        query: str,
        mode: str = "standard",
        sink: Optional[EventSink] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.query = str(query or "").strip()
        self.mode = str(mode or "standard").strip().lower()
        self.headers = headers or {}
        self.llm = llm
        self.search_client = search_client
        self._queue: "asyncio.Queue[RunnerEvent]" = asyncio.Queue()
        self._external_sink = sink
        self._queue_sink = _QueueSink(self._queue)
        self.sink = sink or self._queue_sink

        # Raw backend search dicts collected during the run (deduped by URL);
        # the audit layer reconstructs facts from these.
        self.captured_sources: List[Dict[str, Any]] = []
        self._seen_urls: set = set()
        self._state: Dict[str, Any] = {}

    def _record_sources(self, results: List[Dict[str, Any]]) -> None:
        """Collect retrieved sources for the audit layer, deduped by URL."""
        for item in results or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            if url in self._seen_urls:
                if item.get("is_content_fetched"):
                    for i, existing in enumerate(self.captured_sources):
                        if str(existing.get("url")) == url:
                            self.captured_sources[i] = item
                            break
                continue
            self._seen_urls.add(url)
            self.captured_sources.append(item)

    def _build_task(self) -> Dict[str, Any]:
        sections = _MODE_SECTIONS.get(self.mode, _MODE_SECTIONS["standard"])
        return {
            "query": self.query,
            "max_sections": sections,
            "max_plan_revisions": 1 if self.mode == "quick" else 2,
            "max_fact_check_revisions": 1,
            "max_draft_revisions": 1 if self.mode == "quick" else 2,
            "include_human_feedback": False,
            "follow_guidelines": False,
            "guidelines": "",
            "source": "web",
            "verbose": False,
        }

    def _researcher_factory(self, cfg: ResearchConfig, retrievers: List[type], sink):
        """Return a callable building a Researcher bound to this run."""

        def make(
            *,
            query: str,
            report_type: str = ReportType.ResearchReport.value,
            parent_query: str = "",
            report_source: str = "web",
            tone=None,
        ) -> Researcher:
            return Researcher(
                query,
                llm=self.llm,
                cfg=cfg,
                retrievers=retrievers,
                sink=sink,
                report_type=report_type,
                parent_query=parent_query,
                report_source=report_source,
                tone=tone or Tone.Objective,
                verbose=cfg.verbose,
            )

        return make

    async def run(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield ``{stage, node, ...}`` events as nodes complete."""
        cfg = load_config()
        loop = asyncio.get_running_loop()
        if self.search_client is None:
            # A run with no retrieval client would return zero sources for every
            # section and silently produce an abstaining report. Fail loudly so
            # the misconfiguration is visible instead of looking like empty search.
            raise RuntimeError(
                "ResearchRunner has no search client — the runtime was not "
                "configured (app.engine.configure). Retrieval cannot run."
            )
        retriever_cls = build_retriever_class(
            self.search_client, loop=loop, source_sink=self._record_sources
        )
        retrievers = [retriever_cls]
        rescue_sink = self._bind_source_sink(retriever_cls)

        task = self._build_task()
        output_dir = f"./outputs/run_{uuid.uuid4().hex[:12]}"
        chief = build_chief_editor(
            task=task,
            llm=LLMFunnel(self.llm),
            cfg=cfg,
            researcher_factory=self._researcher_factory(cfg, retrievers, self.sink),
            output_dir=output_dir,
            sink=self.sink,
        )
        chain = chief.build_graph().compile()

        drainer = asyncio.create_task(self._drain())
        self._state = {"task": task}
        try:
            async for chunk in chain.astream(
                {"task": task},
                stream_mode="updates",
                config={"recursion_limit": 60},
            ):
                if not isinstance(chunk, dict):
                    continue
                for node, output in chunk.items():
                    if isinstance(output, dict):
                        self._state.update(output)
                    yield self._node_event(node, output)
        finally:
            rescue_sink()
            await asyncio.sleep(0)
            drainer.cancel()
            try:
                await drainer
            except asyncio.CancelledError:
                pass

    def _bind_source_sink(self, retriever_cls):
        """Bind the run's source collector; return a restorer.

        The retriever class here is a run-local subclass, so this is only needed
        to reset its sink when the run ends.
        """
        previous = retriever_cls.bind_source_sink(self._record_sources)

        def _restore() -> None:
            retriever_cls.bind_source_sink(previous)

        return _restore

    async def _drain(self) -> None:
        while True:
            await self._queue.get()

    def _node_event(self, node: str, output: Any) -> Dict[str, Any]:
        """Map a completed graph node to a pipeline-shaped event."""
        stage = _NODE_STAGE.get(node, node)
        event: Dict[str, Any] = {"node": node, "stage": stage}
        if isinstance(output, dict):
            if node == "planner" and output.get("sections"):
                event["sections"] = list(output.get("sections") or [])
            if node == "browser" and output.get("initial_research"):
                event["initial_research_chars"] = len(str(output.get("initial_research") or ""))
            if node == "researcher" and output.get("research_data") is not None:
                event["research_sections"] = len(output.get("research_data") or [])
            if node == "writer":
                event["has_introduction"] = bool(output.get("introduction"))
            if node == "fact_checker":
                event["fact_check_notes"] = output.get("fact_check_notes")
            if node == "publisher" and output.get("report"):
                event["report_chars"] = len(str(output.get("report") or ""))
        return event

    def final_state(self) -> Dict[str, Any]:
        """Assemble a backend-shaped final state from the team's state.

        Keys mirror what the route reads after a run: ``final_report`` (the
        primary answer), ``sources`` for persistence/trace, and the audit fields
        the panel renders. Audit is rebuilt deterministically from the captured
        sources + report by :func:`build_final_state`.
        """
        return build_final_state(self._state, self.captured_sources, self.query)


def _sanitize_markdown(markdown: str) -> str:
    """Normalise the assembled report for storage and rendering.

    Normalises ragged pipe tables into aligned GFM tables, drops template
    placeholders and repeated/adjacent duplicate headings (the publisher and the
    writer both emit "Introduction"/"Conclusion"), and collapses blank-line
    runs. Content is never dropped: a table row with too many cells keeps its
    overflow in the last column. Falls back to a plain whitespace tidy if the
    sanitizer is unavailable.
    """
    text = str(markdown or "")
    try:
        from app.engine.orchestrator import sanitize_report_markdown

        return sanitize_report_markdown(text)
    except Exception as exc:  # a sanitizer failure must never drop the report
        logger.warning("[Runner] report sanitize failed: %s", exc, exc_info=exc)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text = text.replace("&nbsp;", " ")
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_report_prose(markdown: str) -> str:
    """Strip presentation scaffolding the writer emits from the delivered report.

    Removes inline citation markers (``[1]``) and thematic-break rules, and
    converts em dashes to commas — the same cleanup the report has always gone
    through. Applied here, after the report is assembled and its tables are
    normalised, and only to the answer body (the audit/trace keeps its own
    numbering and the audit sections are never touched). Degrades to the input
    if the cleaner is unavailable, so it can never shorten a report to nothing.
    """
    try:
        from app.agents.sources import clean_writer_prose

        cleaned = clean_writer_prose(markdown)
        return cleaned or markdown
    except Exception as exc:  # a cleanup failure must never drop the report
        logger.warning("[Runner] report prose cleanup failed: %s", exc, exc_info=exc)
        return markdown


def _render_report(state: Dict[str, Any]) -> str:
    """Fallback markdown assembly if the publisher node did not run."""
    parts: List[str] = []
    if state.get("title"):
        parts.append(f"# {state['title']}\n")
    if state.get("introduction"):
        parts.extend(["## Introduction", str(state["introduction"]), ""])
    for section in state.get("research_data") or []:
        if isinstance(section, dict):
            for _key, value in section.items():
                parts.append(str(value))
        else:
            parts.append(str(section))
        parts.append("")
    if state.get("conclusion"):
        parts.extend(["## Conclusion", str(state["conclusion"]), ""])
    return "\n".join(parts).strip()


def build_final_state(
    state: Dict[str, Any],
    captured_sources: List[Dict[str, Any]],
    query: str,
) -> Dict[str, Any]:
    """Assemble the route-shaped final state, including the audit layer.

    Imported lazily so the runner has no hard dependency on the audit core at
    import time and tests can drive the graph without it.
    """
    from app.engine.audit_bridge import build_facts, build_source_index, compute_audit

    report = _sanitize_markdown(str(state.get("report") or _render_report(state) or ""))
    report = _clean_report_prose(report)
    source_index = build_source_index(captured_sources, state.get("sources") or [], report)
    sources = [s["url"] for s in source_index if s.get("url")]
    facts = build_facts(report, source_index, query)
    audit = compute_audit(query=query, report=report, sources=source_index, facts=facts)

    return {
        "final_report": report,
        "sources": sources,
        "source_index": source_index,
        "facts": facts,
        "title": state.get("title"),
        "sections": state.get("sections") or [],
        "fact_check_notes": state.get("fact_check_notes"),
        "pipeline_state": state,
        **{
            k: audit[k]
            for k in (
                "confidence",
                "confidence_breakdown",
                "contradictions",
                "answer_support",
                "evidence_distribution",
                "quality",
            )
            if k in audit
        },
    }
