from __future__ import annotations

from datetime import datetime, timezone
import aiosqlite

from app.db.store.schema import (
    _connect,
)


async def save_report(database_path: str, query: str, report: str, confidence: float) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    async with _connect(database_path) as db:
        await db.execute(
            "INSERT INTO research_reports (query, report, confidence, created_at) VALUES (?, ?, ?, ?)",
            (query, report, confidence, created_at),
        )
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_session(database_path: str, session_id: str, title: str = "") -> None:
    """Idempotently ensure a session exists.

    `INSERT OR IGNORE` means a client-supplied session_id persists exactly
    once: re-using it across a chat's questions never creates duplicates,
    while the title only fills in on first creation.
    """
    async with _connect(database_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, str(title or "").strip()[:120], _now(), _now()),
        )
        await db.commit()


async def touch_session(database_path: str, session_id: str) -> None:
    async with _connect(database_path) as db:
        await db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        await db.commit()


async def list_sessions(database_path: str) -> list:
    """Sidebar list: one row per chat session, newest activity first.

    Derives status/preview from the latest run in the session so the UI can
    render a single stable chat entry instead of one entry per run.
    """
    async with _connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
        )
        sessions = [dict(r) for r in await cur.fetchall()]
        for session in sessions:
            run_cur = await db.execute(
                "SELECT id, query, status, confidence, estimated_cost, created_at "
                "FROM research_runs WHERE session_id = ? ORDER BY created_at, rowid",
                (session["id"],),
            )
            runs = [dict(r) for r in await run_cur.fetchall()]
            session["run_ids"] = [r["id"] for r in runs]
            session["run_count"] = len(runs)
            session["title"] = session.get("title") or (runs[0]["query"] if runs else "New Research")
            session["query"] = runs[0]["query"] if runs else ""
            latest = runs[-1] if runs else None
            session["status"] = latest["status"] if latest else "empty"
            session["confidence"] = latest["confidence"] if latest else None
    return sessions


async def get_session(database_path: str, session_id: str) -> dict | None:
    """Full session record: ordered messages (user turn + run result) and the
    runs that produced them, so the UI can restore the full chat on reload."""
    async with _connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        session = dict(row)
        run_cur = await db.execute(
            "SELECT id, query, status, confidence, estimated_cost, created_at, completed_at "
            "FROM research_runs WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        )
        runs = [dict(r) for r in await run_cur.fetchall()]
        final_cur = await db.execute(
            "SELECT run_id, report_markdown, audit_markdown, confidence, generated_at FROM final_reports "
            "WHERE run_id IN (SELECT id FROM research_runs WHERE session_id = ?)",
            (session_id,),
        )
        reports = {r["run_id"]: dict(r) for r in await final_cur.fetchall()}

    messages = []
    for run in runs:
        messages.append({
            "id": f"{run['id']}-user",
            "role": "user",
            "text": run["query"] or "",
            "created_at": run["created_at"],
            "run_id": run["id"],
        })
        report = reports.get(run["id"])
        messages.append({
            "id": f"{run['id']}-run",
            "role": "run",
            "run_id": run["id"],
            "status": run["status"],
            "confidence": run["confidence"],
            "created_at": run["completed_at"] or run["created_at"],
            "report": report.get("report_markdown") if report else "",
            "audit": report.get("audit_markdown") if report else "",
            "query": run["query"] or "",
        })
    session["messages"] = messages
    session["runs"] = runs
    session["run_ids"] = [r["id"] for r in runs]
    session["run_count"] = len(runs)
    session["title"] = session.get("title") or (runs[0]["query"] if runs else "New Research")
    session["query"] = runs[0]["query"] if runs else ""
    latest = runs[-1] if runs else None
    session["status"] = latest["status"] if latest else "empty"
    return session


async def start_research_run(database_path: str, run_id: str, query: str, complexity: str, agent_count: int,
                           max_iterations: int = 3, session_id: str | None = None) -> None:
    async with _connect(database_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO research_runs (id, session_id, query, complexity, agent_count, status, max_iterations, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
            (run_id, session_id, query, complexity, agent_count, int(max_iterations), _now()),
        )
        # First question names the chat; later messages leave the title alone.
        if session_id:
            await db.execute(
                "UPDATE sessions SET title = CASE WHEN title = '' THEN ? ELSE title END, "
                "updated_at = ? WHERE id = ?",
                (str(query or "").strip()[:120], _now(), session_id),
            )
        await db.commit()


async def complete_research_run(
    database_path: str,
    run_id: str,
    status: str,
    confidence: float,
    estimated_cost: float | None = None,
) -> None:
    # `estimated_cost` is accepted for backward compatibility (the column stays
    # in the schema) but the product no longer computes or reports cost.
    async with _connect(database_path) as db:
        await db.execute(
            "UPDATE research_runs SET status = ?, confidence = ?, completed_at = ? WHERE id = ?",
            (status, confidence, _now(), run_id),
        )
        await db.commit()
