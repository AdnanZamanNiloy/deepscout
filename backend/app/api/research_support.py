"""Research request models, stream capture and persistence helpers.

Extracted verbatim from `app/api/routes.py` (refactor; no behaviour change).
These are the router-independent pieces the research endpoints use; being free
of `router`/`limiter` they can live in their own module with no import cycle.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.db.sqlite import (
    complete_research_run,
    record_event,
    save_critic_review,
    save_final_report,
    save_report,
    save_trace_frames,
)

logger = get_logger(__name__)


class ResearchRequest(BaseModel):
    # min_length=1: the router (R5) handles short social turns ("hey", "hi")
    # as conversation; an empty query is still rejected.
    query: str = Field(..., min_length=1, max_length=500)
    deep_research: bool = False
    # The three research modes. A legacy name still parses — see
    # `resolve_mode`, which maps it onto the mode that inherited its behaviour.
    # Deliberately NOT restricted to the three live modes: a stored run or an
    # older client may still send "executive"/"audit", and rejecting those with a
    # 422 would break resume instead of mapping them forward.
    mode: str = Field(default="standard", max_length=20)
    # Chat identity: all questions in one chat share this id so the backend
    # appends them to one session instead of minting a session per question.
    # Optional for backward compatibility — absent means "generate one".
    session_id: str | None = Field(default=None, max_length=120)


def _finding_item(fact: Dict[str, Any]) -> Dict[str, Any]:
    """Wire shape of one findings item — shared by the stream, the verified
    re-emission and the resume path. Claim Inspector (3.6) detail fields are
    harmless when absent pre-verification."""
    return {
        "claim": fact.get("claim", ""),
        "source": fact.get("source", ""),
        "verified": fact.get("verified"),
        "verification_score": fact.get("verification_score"),
        "verification_reason": fact.get("verification_reason"),
        "agent": fact.get("agent", ""),
        "confidence": fact.get("confidence"),
    }


def _with_trace_capture(
    source: AsyncGenerator[str, None],
    database_url: str,
    run_id: str,
) -> AsyncGenerator[str, None]:
    """Pass the NDJSON stream through untouched while recording its frames.

    Capturing at the single consumption point means the 29 `event_line` call
    sites stay unaware of persistence, and every event type is recorded by
    construction — a frame cannot be added to the stream without also being
    recorded, which is the failure mode that would silently lose replay again.

    Frames are written once the stream finishes, so persistence cost never
    lands in the middle of a live run. A run cut off by a client disconnect
    keeps whatever had already been emitted rather than nothing.
    """
    captured: list[dict] = []

    async def _tee() -> AsyncGenerator[str, None]:
        try:
            async for line in source:
                stripped = line.strip()
                if stripped:
                    try:
                        parsed = json.loads(stripped)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, dict):
                        captured.append(parsed)
                yield line
        finally:
            # Best effort: a failed replay write must not break a run that has
            # already produced its answer.
            try:
                await save_trace_frames(database_url, run_id, captured)
            except Exception as exc:  # noqa: BLE001 - replay is non-critical
                logger.warning(
                    "[trace] could not persist frames for run %s: %s",
                    run_id, exc,
                    exc_info=exc,
                )

    return _tee()


async def _persist_save(db: str, run_id: str, facts: list) -> None:
    from app.db.sqlite import save_claims as _sc
    try:
        await _sc(db, run_id, facts)
    except Exception as exc:
        logger.warning("persistence_failed", error=str(exc), exc_info=exc)


async def _persist_record(db: str, run_id: str, iteration: int, critique: dict,
                          breakdown: dict | None = None) -> None:
    try:
        await record_event(db, run_id, "critic", "end", payload=json.dumps({"iteration": iteration}))
        await save_critic_review(db, run_id, iteration, critique, breakdown=breakdown or {})
    except Exception as exc:
        logger.warning("persistence_failed", error=str(exc), exc_info=exc)


async def _persist_complete(
    db: str, run_id: str, status: str, confidence: float, cost: float | None = None
) -> None:
    # `cost` is accepted for call-site compatibility but no longer stored — the
    # product does not track spend.
    try:
        await complete_research_run(db, run_id, status, confidence=confidence)
    except Exception as exc:
        logger.warning("persistence_failed", error=str(exc), exc_info=exc)


async def _persist_report(
    db: str, run_id: str, query: str, report: str, confidence: float, audit: str = ""
) -> None:
    try:
        await save_report(db, query, report, confidence)  # legacy table kept in sync
        await save_final_report(db, run_id, report, confidence, audit_markdown=audit)  # canonical
    except Exception as exc:
        logger.warning("persistence_failed", error=str(exc), exc_info=exc)
