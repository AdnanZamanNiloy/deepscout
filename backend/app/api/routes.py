import asyncio
import uuid
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field  # noqa: F401
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.usage import clear_run_usage, start_run_usage
from app.infra.budget import mode_timeout_seconds
from app.common.config import get_settings
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
    save_claims,
    mark_challenged_claims,
    claim_match_key,
    challenged_claim_texts,
    save_evidence,
    save_final_report,
    save_report,
    save_sources,
    start_research_run,
    touch_session,
)
from app.db.store.checkpoints import clear_checkpoint
from app.common.logging import bind_request_context, get_logger, unbind_request_context


router = APIRouter()

from app.api.research_support import (  # noqa: E402
    _persist_complete,
    _persist_report,
    _with_trace_capture,
    ResearchRequest,
    PlanDecisionRequest,
)
from app.runtime.plan_review import (  # noqa: E402
    PlanDecision,
    submit_plan_decision as _submit_plan_decision,
)

# Provider + provider-chain endpoints live in their own module; including the
# sub-router without a prefix keeps every path identical to the old module.
from app.api.providers_routes import router as _providers_router  # noqa: E402

router.include_router(_providers_router)

logger = get_logger(__name__)

# Applied to the expensive research stream only; other routes stay open.
limiter = Limiter(key_func=get_remote_address)


def _ts_to_iso(ts: Any) -> str:
    """Wire epoch-milliseconds to the ISO stamp ``agent_events`` stores.

    A stage frame carries the moment it was emitted; recording that verbatim
    (rather than "now", when the row happens to be written) is what makes the
    audit trail show a stage's real span instead of a zero-length one.
    """
    try:
        ms = float(ts)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def _finding_items(facts: Any, contradictions: Any = None) -> List[Dict[str, Any]]:
    """Project annotated facts to the ``findings`` frame's item shape.

    One item per verified claim, carrying only what the UI renders (the
    ``Finding`` contract: claim, source, verdict, provenance). Internal
    verification checks stay server-side; the persisted ``claims`` rows and
    this frame are built from the same fact list, so ``challenged`` is derived
    with the same normalized-text match the DB flagging uses.
    """
    challenged = challenged_claim_texts(contradictions)
    items: List[Dict[str, Any]] = []
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        items.append({
            "claim": str(fact.get("claim") or ""),
            "source": str(fact.get("source") or ""),
            "verified": bool(fact.get("verified")),
            "verification_score": float(fact.get("verification_score", 0.0) or 0.0),
            "verification_reason": str(fact.get("verification_reason") or ""),
            "agent": str(fact.get("agent") or "pipeline"),
            "confidence": float(fact.get("confidence", 0.0) or 0.0),
            "challenged": claim_match_key(fact.get("claim")) in challenged,
        })
    return items


def _final_frame_from_state(
    final_state: Dict[str, Any],
    *,
    report: str,
    sources: Any,
    confidence: float,
    degraded: Any,
    degraded_reasons: Any,
) -> Dict[str, Any]:
    """Build the terminal ``final_report`` frame from the runner's final state.

    The audit fields (confidence breakdown, answer support, citation health,
    quality, contradictions) are produced by the engine's audit bridge and
    carried on ``final_state``; this forwards them to the wire frame. Missing
    keys fall back to the adapter's empty defaults — never invented values.
    """
    from app.runtime.event_adapter import final_report_frame

    return final_report_frame(
        report=report,
        sources=sources,
        confidence=confidence,
        degraded=degraded,
        degraded_reasons=degraded_reasons,
        confidence_breakdown=final_state.get("confidence_breakdown") or {},
        confidence_notes=list(final_state.get("confidence_notes") or []),
        answer_support=final_state.get("answer_support"),
        quality=final_state.get("quality") or {},
        contradictions=final_state.get("contradictions") or [],
    )






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
    """Run a research request through the first-party research pipeline.

    The engine drives the run; this route owns everything the frontend contract
    depends on around it: the NDJSON framing, the run/session lifecycle records,
    incremental persistence, verbatim frame capture for replay, and the
    terminal report write. The engine is reached through the bridges installed
    at startup (app.runtime), so the Model Control Center still selects the
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
            from app.runtime.orchestrator import PipelineRunner
            from app.runtime.event_adapter import PipelineEventAdapter, final_report_frame

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
                # The run is terminal: its checkpoint has served its purpose and
                # is dropped, so a later resume of this id finds nothing to
                # rebuild from (a completed run is not resumable anyway, but the
                # row should not linger and be mistaken for one).
                if status in ("completed", "failed", "cancelled", "timeout"):
                    await _persist(clear_checkpoint(settings.database_url, request_id))

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

            runner = PipelineRunner(
                llm=llm_client,
                search_client=getattr(getattr(request.app.state, "workflow", None), "search_client", None),
                query=payload.query,
                mode=str(payload.mode or "standard"),
                headers={},
                run_id=request_id,
                hitl=payload.hitl,
            )
            adapter = PipelineEventAdapter(payload.query, str(payload.mode or "standard"))

            try:
                # Hard wall-clock cap for THIS mode (quick fails fast, deep is
                # allowed longer). One source of truth with the budget's
                # max_seconds: see mode_timeout_seconds().
                mode_timeout = mode_timeout_seconds(settings, str(payload.mode or "standard"))
                async with asyncio.timeout(mode_timeout):
                    async for pipeline_event in runner.run():
                        for stage_frame in (adapter.stage_frame(pipeline_event.get("node")),):
                            if stage_frame is not None:
                                yield frame_line(stage_frame)
                        for wire_frame in adapter.to_frames(pipeline_event):
                            yield frame_line(wire_frame)
                            # Stage lifecycle rows for the audit trail, persisted
                            # from the frames themselves so the recorded
                            # timestamps are the real ones and a restored run
                            # rebuilds the same ladder the live rail showed.
                            if wire_frame.get("type") == "stage":
                                running = wire_frame.get("status") == "running"
                                stamp = _ts_to_iso(wire_frame.get("ts"))
                                await _persist(record_event(
                                    settings.database_url, request_id,
                                    str(wire_frame.get("node") or "engine"),
                                    "start" if running else "end",
                                    payload=json.dumps({"stage": wire_frame.get("node")}),
                                    started_at="" if running else stamp,
                                    ended_at=stamp if running else "",
                                ))
                        # Node-level trail for the trace/audit (best effort).
                        await _persist(record_event(
                            settings.database_url, request_id,
                            str(pipeline_event.get("node") or "engine"), "end",
                            payload=json.dumps({
                                "stage": pipeline_event.get("stage"),
                                "sections": pipeline_event.get("sections"),
                            }),
                        ))
            except TimeoutError:
                await _finish_run("timeout", 0.0)
                yield event_line(
                    "error",
                    message=(
                        f"Research timed out after {int(mode_timeout)}s. "
                        "Try a narrower query, a faster mode, or raise RESEARCH_TIMEOUT_SEC."
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

            # Fetch real page text for cited URLs whose captured text is only
            # a snippet, so the audit verifier judges the page (bounded, and
            # outside the mode timeout — it can only enrich, never kill, the run).
            await runner.capture_cited_sources()
            final_state = runner.final_state()
            report = str(final_state.get("final_report") or "")
            sources = list(final_state.get("sources") or [])
            source_index = list(final_state.get("source_index") or [])
            facts = list(final_state.get("facts") or [])
            contradictions = list(final_state.get("contradictions") or [])
            confidence = float(final_state.get("confidence", 0.0) or 0.0)

            await _finish_run("completed", confidence)

            # Persist sources + claims + contradictions so the trace and the
            # evidence ledger are real (not just the final report text).
            if source_index:
                await _persist(save_sources(
                    settings.database_url, request_id,
                    [{"url": s.get("url"), "title": s.get("title"),
                      "publisher": s.get("publisher"),
                      "source_domain": s.get("source_domain"),
                      "retrieval_provider": s.get("retrieval_provider"),
                      "retrieval_engine": s.get("retrieval_engine")}
                     for s in source_index if s.get("url")],
                ))
                await _persist(save_evidence(
                    settings.database_url, request_id,
                    [{"url": s.get("url")} for s in source_index if s.get("url")],
                ))
            if facts:
                await _persist(save_claims(settings.database_url, request_id, facts))
            if contradictions:
                await _persist(mark_challenged_claims(
                    settings.database_url, request_id, contradictions,
                ))

            if facts:
                # One end-of-run findings frame so a LIVE run shows the same
                # claim/verified counts its own replay already rebuilds from
                # the persisted ``claims`` rows (replayPanelRun). Emitted once,
                # before the final report: the audit bridge annotates every
                # fact's verdict only at finalization.
                yield event_line(
                    "findings",
                    items=_finding_items(facts, contradictions),
                    verified_update=True,
                )

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
                frame = _final_frame_from_state(
                    final_state, report=report, sources=sources,
                    confidence=confidence, degraded=degraded,
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


@router.post("/research/{run_id}/plan")
async def submit_plan(run_id: str, request: Request, payload: PlanDecisionRequest) -> Dict[str, Any]:
    """Deliver a human-in-the-loop plan-review decision to a waiting run.

    When a run is started with interactive review (``hitl``), it pauses after
    planning and streams a ``plan_review`` frame carrying the proposed outlines,
    then blocks. This endpoint releases it: the user's chosen (or edited)
    outline and free-text direction are handed to the paused run, or the plan is
    sent back for revision.

    Returns 404 when no run with that id is currently waiting — either the run
    already proceeded (auto-approved on timeout) or the id is unknown.
    """
    action = str(payload.action or "approve").strip().lower()
    if action not in ("approve", "revise"):
        raise HTTPException(status_code=422, detail="action must be 'approve' or 'revise'")

    decision = PlanDecision(
        action=action,
        sections=[str(s).strip() for s in (payload.sections or []) if str(s).strip()],
        feedback=str(payload.feedback or "").strip(),
    )
    if not _submit_plan_decision(run_id, decision):
        raise HTTPException(
            status_code=404,
            detail=f"Run {run_id} is not waiting for a plan decision",
        )
    return {"status": "ok", "action": action}


@router.post("/research/{run_id}/resume")
@limiter.limit(get_settings().rate_limit)
async def resume_research(run_id: str, request: Request) -> StreamingResponse:
    """Resume a failed/timeout run.

    The research pipeline does not checkpoint intermediate state between
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
    # The resumed run keeps the mode it was started with, so its hard cap is
    # the same one the original run was held to (the stored complexity is the
    # mode string written at start).
    resume_mode = str(
        (state.get("orchestration") or {}).get("complexity_level") or "standard"
    )

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
        # Install the run's ledger under the SAME id the run had, so the runner
        # finds this run's checkpoint (keyed by run id) and the budget is
        # re-armed for the resumed attempt. Without this the checkpoint could
        # not be located and a resume would silently start from scratch.
        start_run_usage(request_id, settings, mode=resume_mode)

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

            from app.runtime.orchestrator import PipelineRunner
            from app.runtime.event_adapter import PipelineEventAdapter, final_report_frame

            runner = PipelineRunner(
                llm=llm_client,
                search_client=getattr(getattr(request.app.state, "workflow", None), "search_client", None),
                query=query,
                mode=resume_mode,
                headers={},
            )
            adapter = PipelineEventAdapter(query, resume_mode)
            try:
                async with asyncio.timeout(mode_timeout_seconds(settings, resume_mode)):
                    async for pipeline_event in runner.run():
                        stage_frame = adapter.stage_frame(pipeline_event.get("node"))
                        if stage_frame is not None:
                            yield frame_line(stage_frame)
                        for wire_frame in adapter.to_frames(pipeline_event):
                            yield frame_line(wire_frame)
                            if wire_frame.get("type") == "stage":
                                running = wire_frame.get("status") == "running"
                                stamp = _ts_to_iso(wire_frame.get("ts"))
                                await _persist(record_event(
                                    settings.database_url, request_id,
                                    str(wire_frame.get("node") or "engine"),
                                    "start" if running else "end",
                                    payload=json.dumps({"stage": wire_frame.get("node")}),
                                    started_at="" if running else stamp,
                                    ended_at=stamp if running else "",
                                ))
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

            await runner.capture_cited_sources()
            final_state = runner.final_state()
            report = str(final_state.get("final_report") or "")
            source_index = list(final_state.get("source_index") or [])
            sources = list(final_state.get("sources") or [])
            facts = list(final_state.get("facts") or [])
            contradictions = list(final_state.get("contradictions") or [])
            confidence = float(final_state.get("confidence", 0.0) or 0.0)
            await _finish("completed", confidence)

            if source_index:
                await _persist(save_sources(
                    settings.database_url, request_id,
                    [{"url": s.get("url"), "title": s.get("title"),
                      "publisher": s.get("publisher")} for s in source_index if s.get("url")],
                ))
            if facts:
                await _persist(save_claims(settings.database_url, request_id, facts))
            if contradictions:
                # Same challenge flagging a fresh run applies — a resumed run
                # must not persist claims the fresh path would have marked.
                await _persist(mark_challenged_claims(
                    settings.database_url, request_id, contradictions,
                ))
            if facts:
                # Same end-of-run findings frame as the fresh-run path: the
                # live panel must match what a replay of this id shows.
                yield event_line(
                    "findings",
                    items=_finding_items(facts, contradictions),
                    verified_update=True,
                )

            if report:
                await _persist_report(settings.database_url, request_id, query, report, confidence, audit="")
                await _persist(save_citations(settings.database_url, request_id, report))
                degraded = take_fallbacks()
                degradation = degradation_summary()
                frame = _final_frame_from_state(
                    final_state, report=report, sources=sources,
                    confidence=confidence, degraded=degraded,
                    degraded_reasons=degradation["reasons"],
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
