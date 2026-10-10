import asyncio
import uuid
import json
import time
from typing import Any, AsyncGenerator, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field  # noqa: F401
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.usage import clear_run_usage, start_run_usage
from app.core.config import get_settings
from app.core.providers import ProviderSecretUnavailableError
from app.core.degradation import (
    clear_fallbacks,
    degradation_summary,
    reset_fallbacks,
    take_fallbacks,
)
from app.db.sqlite import (
    save_trace_frames,  # noqa: F401
    complete_research_run,
    ensure_session,
    get_run_trace,
    get_session,
    list_sessions,
    load_state_for_resume,
    mark_run_resumable_reset,
    record_event,
    save_citations,
    save_evidence,
    save_final_report,
    save_report,
    save_sources,
    start_research_run,
    touch_session,
)
from app.core.logging import bind_request_context, get_logger, unbind_request_context


router = APIRouter()

from app.api.research_support import (  # noqa: E402
    _persist_complete,
    _persist_report,
    _with_trace_capture,
    ResearchRequest,
)

# Provider + provider-chain endpoints live in their own module; including the
# sub-router without a prefix keeps every path identical to the old module.
from app.api.providers_routes import router as _providers_router  # noqa: E402

router.include_router(_providers_router)

logger = get_logger(__name__)

# Applied to the expensive research stream only; other routes stay open.
limiter = Limiter(key_func=get_remote_address)






@router.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    """Liveness, plus whether this process is the one running SearXNG.

    `searxng` distinguishes the three states that look identical from outside:
    managed (this backend started it and will stop it), external (something
    else already served the endpoint, so we never touch it), and down (search
    silently degraded to Wikipedia/arXiv/Crossref).
    """
    payload: Dict[str, Any] = {"status": "ok"}
    supervisor = getattr(request.app.state, "searxng", None)
    if supervisor is not None:
        payload["searxng"] = {
            "url": supervisor.base_url,
            "managed": bool(getattr(supervisor, "managed", False)),
        }
    return payload




@router.get("/research/{run_id}/trace")
async def research_trace(run_id: str, request: Request) -> Dict[str, Any]:
    """Research Replay (3.4): ordered, joinable reconstruction of one run."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workflow is not initialized")

    trace = await get_run_trace(settings.database_url, run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return trace




@router.get("/research/{run_id}/export/{fmt}")
async def export_research_report(run_id: str, fmt: str, request: Request) -> StreamingResponse:
    """Download a completed report as Markdown, DOCX or PDF.

    Renders from the SAME canonical data the trace endpoint returns (persisted
    final_reports.report_markdown + stored citations/sources), never from
    rendered UI text. A run without a completed report returns 409 so the UI
    can explain the missing report rather than serving an empty file.
    """
    from app.core import export as report_export

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workflow is not initialized")
    fmt = (fmt or "").lower()
    if not report_export.is_supported_format(fmt):
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported export format '{fmt}'. Use one of: md, docx, pdf.",
        )
    trace = await get_run_trace(settings.database_url, run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    try:
        payload, media_type, filename = report_export.render_report(trace, fmt)
    except report_export.ExportError as exc:
        # Missing/incomplete report is a client-explainable state, not a 500.
        raise HTTPException(status_code=409, detail=str(exc))
    return StreamingResponse(
        iter([payload]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions")
async def sessions_list(request: Request) -> Dict[str, Any]:
    """Sidebar data: one row per chat session (newest activity first).

    Previously the UI derived one entry per run, which is why three
    questions in one chat produced three separate sidebar chats.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workflow is not initialized")
    return {"sessions": await list_sessions(settings.database_url)}


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str, request: Request) -> Dict[str, Any]:
    """Full ordered chat history for one session, so reload restores it."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workflow is not initialized")
    session = await get_session(settings.database_url, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session_id: {session_id}")
    return session


@router.post("/research/stream")
@limiter.limit(get_settings().rate_limit)
async def stream_research(request: Request, payload: ResearchRequest) -> StreamingResponse:
    """Run a research request through the multi-agent engine team.

    The engine drives the run; this route owns everything the frontend contract
    depends on around it: the NDJSON framing, the run/session lifecycle records,
    incremental persistence, verbatim frame capture for replay, and the
    terminal report write. The engine is reached through the bridges installed
    at startup (app.engine), so the Model Control Center still selects the
    provider and SearchClient still performs retrieval.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workflow is not initialized")

    request_id = str(uuid.uuid4())
    # Reuse the chat's session id when the client supplies one; otherwise the
    # backend owns a fresh session so old clients still get a stable grouping.
    session_id = str(payload.session_id).strip() if payload.session_id else str(uuid.uuid4())

    def event_line(event_type: str, **data: Any) -> str:
        # Every frame carries an emission timestamp that survives persistence,
        # so a replayed run still shows its length even though the client's
        # arrival stamp (__ts) is not stored.
        payload_data = {"type": event_type, "ts": int(time.time() * 1000), **data}
        return json.dumps(payload_data, ensure_ascii=True) + "\n"

    def frame_line(wire_frame: Dict[str, Any]) -> str:
        """Serialize an adapter-built frame (which already carries ``type``).

        The adapters produce the frontend's frame shape directly; this re-emits
        it with a fresh timestamp via :func:`event_line`, keeping one serialization
        path so every frame is stamped identically.
        """
        data = dict(wire_frame)
        event_type = str(data.pop("type", "progress"))
        data.pop("ts", None)
        return event_line(event_type, **data)

    async def event_stream() -> AsyncGenerator[str, None]:
        bind_request_context(request_id=request_id)
        reset_fallbacks()
        start_run_usage(request_id, settings, mode=str(payload.mode or "standard"))
        try:
            from app.engine.orchestrator import MultiAgentRunner
            from app.engine.event_adapter import EngineEventAdapter, final_report_frame

            async def _persist(coro):
                """Persistence must never kill a research run — log and continue."""
                try:
                    await coro
                except Exception as exc:
                    logger.warning("persistence_failed", error=str(exc), exc_info=exc)

            async def _finish_run(status: str, confidence: float) -> None:
                await _persist(complete_research_run(
                    settings.database_url, request_id, status, confidence=confidence,
                ))
                await _persist(touch_session(settings.database_url, session_id))

            await _persist(ensure_session(settings.database_url, session_id, title=payload.query))
            await _persist(start_research_run(
                settings.database_url,
                request_id,
                payload.query,
                complexity=str(payload.mode or "standard"),
                agent_count=0,
                max_iterations=1,
                session_id=session_id,
            ))

            yield event_line("progress", request_id=request_id, session_id=session_id,
                             message="Query received")

            # Pre-flight provider probe: a run with zero reachable LLM providers
            # would fail minutes in. Fail in seconds with per-provider reasons.
            llm_client = getattr(request.app.state, "llm", None)
            if llm_client is not None:
                try:
                    probe_ok, probe_detail = await llm_client.probe_all()
                except ProviderSecretUnavailableError as exc:
                    await _finish_run("failed", 0.0)
                    yield event_line("error", message=str(exc))
                    return
                if not probe_ok:
                    await _finish_run("failed", 0.0)
                    yield event_line(
                        "error",
                        message=(
                            f"No LLM provider is reachable right now — {probe_detail}. "
                            "Add or switch providers in the Providers tab, or wait "
                            "for provider quotas to reset."
                        ),
                    )
                    return

            runner = MultiAgentRunner(
                query=payload.query,
                mode=str(payload.mode or "standard"),
                headers={},
            )
            adapter = EngineEventAdapter(payload.query)

            try:
                async with asyncio.timeout(settings.research_timeout_sec):
                    async for engine_event in runner.run():
                        for stage_frame in (adapter.stage_frame(engine_event.get("stage")),):
                            if stage_frame is not None:
                                yield frame_line(stage_frame)
                        for wire_frame in adapter.to_frames(engine_event):
                            yield frame_line(wire_frame)
                        # Node-level trail for the trace/audit (best effort).
                        await _persist(record_event(
                            settings.database_url, request_id,
                            str(engine_event.get("engine_node") or "engine"), "end",
                            payload=json.dumps({
                                "stage": engine_event.get("stage"),
                                "sections": engine_event.get("sections"),
                            }),
                        ))
            except TimeoutError:
                await _finish_run("timeout", 0.0)
                yield event_line(
                    "error",
                    message=(
                        f"Research timed out after {int(settings.research_timeout_sec)}s. "
                        "Try a narrower query or raise RESEARCH_TIMEOUT_SEC."
                    ),
                )
                return
            except asyncio.CancelledError:
                # Client disconnected / pressed Stop: a cancelled scope cannot
                # await, so mark via a detached task or the row sits 'running'.
                asyncio.get_running_loop().create_task(
                    complete_research_run(settings.database_url, request_id, "cancelled", confidence=0.0)
                )
                asyncio.get_running_loop().create_task(
                    touch_session(settings.database_url, session_id)
                )
                raise
            except ProviderSecretUnavailableError as exc:
                await _finish_run("failed", 0.0)
                yield event_line("error", message=str(exc))
                return
            except Exception as exc:
                await _finish_run("failed", 0.0)
                message = str(exc)
                if "No LLM provider configured" in message:
                    yield event_line(
                        "error",
                        message=(
                            "No LLM provider is configured. Add one in the "
                            "Providers tab (UI: /#/model-controls) — it takes "
                            "effect immediately, no restart needed."
                        ),
                    )
                else:
                    yield event_line("error", message=f"Research workflow failed: {message}")
                return

            final_state = runner.final_state()
            report = str(final_state.get("final_report") or "")
            sources = list(final_state.get("sources") or [])
            confidence = float(final_state.get("confidence", 0.0) or 0.0)

            await _finish_run("completed", confidence)

            # Persist sources so the trace, citation legend and export have real
            # data. Engine source entries are URLs; saved as minimal rows.
            if sources:
                await _persist(save_sources(
                    settings.database_url, request_id,
                    [{"url": url} for url in sources],
                ))
                await _persist(save_evidence(
                    settings.database_url, request_id,
                    [{"url": url} for url in sources],
                ))

            degraded = take_fallbacks()
            degradation = degradation_summary()
            for agent in degraded:
                await _persist(record_event(
                    settings.database_url, request_id, agent, "fallback",
                    payload=json.dumps({"agent": agent, "reason": degradation["reasons"].get(agent, "")}),
                ))

            if report:
                await _persist(save_report(
                    database_path=settings.database_url,
                    query=payload.query,
                    report=report,
                    confidence=confidence,
                ))
                await _persist(save_final_report(
                    settings.database_url, request_id, report, confidence,
                    audit_markdown="",
                ))
                await _persist(save_citations(settings.database_url, request_id, report))
                frame = final_report_frame(
                    report=report,
                    sources=sources,
                    confidence=confidence,
                    degraded=degraded,
                    degraded_reasons=degradation["reasons"],
                )
                yield frame_line(frame)
            else:
                frame = final_report_frame(
                    report="No final report generated.",
                    sources=[],
                    confidence=confidence,
                    degraded=degradation["agents"],
                    degraded_reasons=degradation["reasons"],
                )
                yield frame_line(frame)
        finally:
            clear_fallbacks()
            clear_run_usage()
            unbind_request_context()

    return StreamingResponse(
        _with_trace_capture(event_stream(), settings.database_url, request_id),
        media_type="application/x-ndjson",
    )


@router.post("/research/{run_id}/resume")
@limiter.limit(get_settings().rate_limit)
async def resume_research(run_id: str, request: Request) -> StreamingResponse:
    """Resume a failed/timeout run.

    The multi-agent engine team does not checkpoint intermediate state between
    nodes, so a resume restarts the run from scratch rather than rebuilding
    evidence from persisted rows. The run identity is preserved and the same
    NDJSON contract is honored; only non-terminal runs are resumed.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="Workflow is not initialized")

    state = await load_state_for_resume(settings.database_url, run_id)
    if state is None:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is not resumable (unknown run_id or status is not failed/timeout)",
        )
    if state.get("final_report"):
        raise HTTPException(status_code=409, detail=f"Run {run_id} already has a final report")

    await mark_run_resumable_reset(settings.database_url, run_id)

    request_id = run_id  # resume continues the SAME run identity
    resume_session_id = state.get("session_id") or ""
    query = str(state.get("query") or "")

    def event_line(event_type: str, **data: Any) -> str:
        return json.dumps(
            {"type": event_type, "ts": int(time.time() * 1000), **data},
            ensure_ascii=True,
        ) + "\n"

    def frame_line(wire_frame: Dict[str, Any]) -> str:
        """Serialize an adapter-built frame (which already carries ``type``)."""
        data = dict(wire_frame)
        event_type = str(data.pop("type", "progress"))
        data.pop("ts", None)
        return event_line(event_type, **data)

    async def resume_stream() -> AsyncGenerator[str, None]:
        bind_request_context(request_id=request_id, resumed=True)
        reset_fallbacks()

        async def _persist(coro):
            try:
                await coro
            except Exception as exc:
                logger.warning("persistence_failed", error=str(exc), exc_info=exc)

        async def _finish(status: str, confidence: float) -> None:
            await _persist_complete(settings.database_url, request_id, status, confidence)
            if resume_session_id:
                await _persist(touch_session(settings.database_url, resume_session_id))

        try:
            yield event_line("progress", request_id=request_id, session_id=resume_session_id,
                             message=f"Resuming run {request_id[:8]}")

            llm_client = getattr(request.app.state, "llm", None)
            if llm_client is not None:
                probe_ok, probe_detail = await llm_client.probe_all()
                if not probe_ok:
                    await _finish("failed", 0.0)
                    yield event_line(
                        "error",
                        message=(
                            f"No LLM provider is reachable right now — {probe_detail}. "
                            "Add or switch providers in the Providers tab, or wait "
                            "for provider quotas to reset."
                        ),
                    )
                    return

            from app.engine.orchestrator import MultiAgentRunner
            from app.engine.event_adapter import EngineEventAdapter, final_report_frame

            runner = MultiAgentRunner(query=query, mode="standard", headers={})
            adapter = EngineEventAdapter(query)
            try:
                async with asyncio.timeout(settings.research_timeout_sec):
                    async for engine_event in runner.run():
                        stage_frame = adapter.stage_frame(engine_event.get("stage"))
                        if stage_frame is not None:
                            yield frame_line(stage_frame)
                        for wire_frame in adapter.to_frames(engine_event):
                            yield frame_line(wire_frame)
            except TimeoutError:
                await _finish("timeout", 0.0)
                yield event_line("error", message="Resumed run timed out. Try again or raise RESEARCH_TIMEOUT_SEC.")
                return
            except asyncio.CancelledError:
                asyncio.get_running_loop().create_task(
                    _persist_complete(settings.database_url, request_id, "cancelled", 0.0)
                )
                if resume_session_id:
                    asyncio.get_running_loop().create_task(
                        touch_session(settings.database_url, resume_session_id)
                    )
                raise
            except Exception as exc:
                await _finish("failed", 0.0)
                yield event_line("error", message=f"Resumed run failed: {exc}")
                return

            final_state = runner.final_state()
            report = str(final_state.get("final_report") or "")
            sources = list(final_state.get("sources") or [])
            confidence = float(final_state.get("confidence", 0.0) or 0.0)
            await _finish("completed", confidence)

            if sources:
                await _persist(save_sources(
                    settings.database_url, request_id, [{"url": url} for url in sources],
                ))

            if report:
                await _persist_report(settings.database_url, request_id, query, report, confidence, audit="")
                await _persist(save_citations(settings.database_url, request_id, report))
                degraded = take_fallbacks()
                degradation = degradation_summary()
                frame = final_report_frame(
                    report=report, sources=sources, confidence=confidence,
                    degraded=degraded, degraded_reasons=degradation["reasons"],
                )
                yield frame_line(frame)
            else:
                degradation = degradation_summary()
                frame = final_report_frame(
                    report="No final report generated.", sources=[], confidence=confidence,
                    degraded=degradation["agents"], degraded_reasons=degradation["reasons"],
                )
                yield frame_line(frame)
        finally:
            clear_fallbacks()
            unbind_request_context()

    return StreamingResponse(
        _with_trace_capture(resume_stream(), settings.database_url, run_id),
        media_type="application/x-ndjson",
    )
