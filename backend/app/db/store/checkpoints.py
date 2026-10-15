"""Node-level run checkpoints: persist pipeline state so a run can resume.

Why this exists
---------------
A research run takes minutes and costs model calls. Before this, a run that
failed or timed out was all-or-nothing: the resume endpoint could only restart
it from scratch, re-paying for every search and every model call already made.
The rows the runner already persists (agent_events, sources, claims) describe
WHAT happened but not enough to skip work — they do not reconstruct the graph's
in-memory state.

This module stores the runner's accumulated state after each completed node,
tagged with the ordered list of nodes already folded into it. Resume loads the
checkpoint, seeds the graph with it, and skips the completed nodes.

Storage choice: one row per run, upserted. The state is bounded by the report
and its evidence (tens of KB), never the raw fetched page text — the runner
releases that during the run (AGENTS.md §5), so a checkpoint cannot become a
memory or disk problem the way unbounded per-node snapshots would.

Failure policy: a checkpoint write must NEVER fail a run. Persistence is an
optimisation for the NEXT attempt, not part of producing this one; every write
is best-effort and logged.
"""
from __future__ import annotations

import aiosqlite
import json

from app.db.store.schema import _connect
from app.db.store.sessions import _now
from app.common.logging import get_logger

logger = get_logger(__name__)

# Nodes whose output is safe to persist and replay. Kept explicit (rather than
# persisting whatever state exists) so a future node carrying un-serializable
# objects cannot quietly break resume — it is added here deliberately.
CHECKPOINTED_NODES = (
    "pathfinder",
    "architect",
    "review_gate",
    "scout_swarm",
    "narrative_writer",
    "evidence_auditor",
    "diagram_designer",
    "report_assembler",
)


async def save_checkpoint(
    database_path: str, run_id: str, state: dict, completed_nodes: list
) -> None:
    """Upsert the run's checkpoint. Best-effort — never raises to the caller."""
    if not run_id:
        return
    try:
        # Only JSON-serializable values are kept. Anything else (a client, a
        # live handle) is dropped rather than allowed to poison the whole
        # checkpoint; the resume path tolerates missing keys.
        safe = _jsonable(state)
        payload = json.dumps(safe, ensure_ascii=True, default=str)
    except Exception as exc:  # serialization is the one thing that can fail here
        logger.warning("[checkpoint] serialize failed for %s: %s", run_id, exc)
        return

    try:
        async with _connect(database_path) as db:
            await db.execute(
                "INSERT INTO run_checkpoints (run_id, state_json, completed_nodes, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "state_json=excluded.state_json, "
                "completed_nodes=excluded.completed_nodes, "
                "updated_at=excluded.updated_at",
                (run_id, payload, json.dumps(list(completed_nodes or [])), _now()),
            )
            await db.commit()
    except Exception as exc:
        # Persistence must never kill research (routes.py's _persist contract).
        logger.warning("[checkpoint] write failed for %s: %s", run_id, exc)


async def load_checkpoint(database_path: str, run_id: str) -> dict | None:
    """The run's checkpoint as {'state': dict, 'completed_nodes': [str]}, or None.

    Returns None when absent, unreadable, or corrupt — a resume then restarts
    from scratch rather than failing, which is the safe direction.
    """
    if not run_id:
        return None
    try:
        async with _connect(database_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT state_json, completed_nodes FROM run_checkpoints WHERE run_id = ?",
                (run_id,),
            )
            row = await cur.fetchone()
    except Exception as exc:
        logger.warning("[checkpoint] read failed for %s: %s", run_id, exc)
        return None
    if row is None:
        return None
    try:
        state = json.loads(row["state_json"] or "{}")
        nodes = json.loads(row["completed_nodes"] or "[]")
    except (TypeError, ValueError) as exc:
        logger.warning("[checkpoint] corrupt checkpoint for %s: %s", run_id, exc)
        return None
    if not isinstance(state, dict) or not isinstance(nodes, list):
        return None
    return {"state": state, "completed_nodes": nodes}


async def clear_checkpoint(database_path: str, run_id: str) -> None:
    """Drop the checkpoint once a run is terminal — it has served its purpose."""
    if not run_id:
        return
    try:
        async with _connect(database_path) as db:
            await db.execute("DELETE FROM run_checkpoints WHERE run_id = ?", (run_id,))
            await db.commit()
    except Exception as exc:
        logger.warning("[checkpoint] delete failed for %s: %s", run_id, exc)


def _jsonable(value):
    """Recursively keep only what JSON can carry, so one bad value cannot
    discard the whole checkpoint.

    An unknown object (a client, a live handle) becomes ``null`` and its KEY is
    preserved, so the resumed state has the same shape it had when written and
    the resume path can read every key it expects.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    # Unknown object: keep the slot, drop the value.
    return None
