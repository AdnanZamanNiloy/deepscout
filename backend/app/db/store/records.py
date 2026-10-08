from __future__ import annotations

import aiosqlite
import json
import re

from app.db.store.schema import (
    _connect,
)
from app.db.store.sessions import (
    _now,
)


async def save_agent_tasks(database_path: str, run_id: str, sub_questions: list) -> None:
    rows = [
        (
            run_id,
            str(q.get("question", "")).strip(),
            str(q.get("axis", "")),
            str(q.get("search_type", "")),
            int(q.get("priority", 2) or 2),
            _now(),
        )
        for q in sub_questions
        if isinstance(q, dict) and str(q.get("question", "")).strip()
    ]
    if not rows:
        return
    async with _connect(database_path) as db:
        await db.executemany(
            "INSERT INTO agent_tasks (run_id, question, axis, search_type, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()


async def save_sources(database_path: str, run_id: str, search_results: list) -> None:
    seen = set()
    rows = []
    for item in search_results or []:
        url = str(item.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append((run_id, url, float(item.get("reliability_score", 0.0) or 0.0), _now()))
    if not rows:
        return
    async with _connect(database_path) as db:
        await db.executemany(
            "INSERT INTO sources (run_id, url, reliability_score, fetched_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        await db.commit()


async def save_claims(database_path: str, run_id: str, facts: list) -> None:
    rows = [
        (
            run_id,
            str(f.get("claim", "")).strip(),
            str(f.get("source") or f.get("source_url") or "").strip(),
            float(f.get("confidence", 0.0) or 0.0),
            1 if f.get("verified") else 0,
            str(f.get("agent", "") or ""),
            _now(),
        )
        for f in facts
        if str(f.get("claim", "")).strip() and str(f.get("source") or f.get("source_url") or "").strip()
    ]
    if not rows:
        return
    async with _connect(database_path) as db:
        await db.executemany(
            "INSERT INTO claims (run_id, claim, source_url, confidence, verified, agent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()


async def mark_challenged_claims(database_path: str, run_id: str, contradictions: list) -> int:
    """Flag claims that appear on either side of a detected contradiction.

    Called once per run at finalization, when contradictions are known —
    claims are saved incrementally, contradictions only at the end.
    Returns the number of rows flagged. Matching is normalized-text
    equality against both contradiction sides.
    """
    sides = set()
    for c in contradictions or []:
        if not isinstance(c, dict):
            continue
        for key in ("claim_a", "claim_b"):
            text = re.sub(r"\s+", " ", str(c.get(key, "") or "").strip().lower())
            if text:
                sides.add(text)
    if not sides:
        return 0
    flagged = 0
    async with _connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, claim FROM claims WHERE run_id = ?", (run_id,))
        ids = [
            r["id"] for r in await cur.fetchall()
            if re.sub(r"\s+", " ", str(r["claim"] or "").strip().lower()) in sides
        ]
        for claim_id in ids:
            await db.execute("UPDATE claims SET challenged = 1 WHERE id = ?", (claim_id,))
            flagged += 1
        await db.commit()
    return flagged


async def record_event(
    database_path: str,
    run_id: str,
    node: str,
    event_type: str,
    payload: str = "",
    started_at: str = "",
    ended_at: str = "",
) -> None:
    async with _connect(database_path) as db:
        await db.execute(
            "INSERT INTO agent_events (run_id, node, event_type, payload, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, node, event_type, payload, started_at or _now(), ended_at or _now()),
        )
        await db.commit()


async def save_trace_frames(
    database_path: str, run_id: str, frames: list[dict]
) -> None:
    """Persist the exact NDJSON frames a run emitted, in emission order.

    Written once per frame batch from the stream loop rather than per frame:
    the trace is read back only on session restore, so there is nothing to gain
    from 40-odd individual writes, and the batch keeps the stream latency flat.
    `seq` preserves order, which the trace relies on to rebuild the narrative.
    """
    if not frames:
        return
    async with _connect(database_path) as db:
        await db.executemany(
            "INSERT INTO run_trace_frames (run_id, seq, frame) VALUES (?, ?, ?)",
            [(run_id, i, json.dumps(f, ensure_ascii=False)) for i, f in enumerate(frames)],
        )
        await db.commit()


async def load_trace_frames(database_path: str, run_id: str) -> list[dict]:
    """Read a run's frames back in emission order, skipping any corrupt row.

    A single unreadable frame must not break replay of the whole run, so a
    decode failure skips that row instead of propagating.
    """
    async with _connect(database_path) as db:
        cur = await db.execute(
            "SELECT frame FROM run_trace_frames WHERE run_id = ? ORDER BY seq ASC",
            (run_id,),
        )
        rows = await cur.fetchall()
    out: list[dict] = []
    for (raw,) in rows:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


async def save_evidence(database_path: str, run_id: str, search_results: list) -> None:
    """Evidence = the raw material claims were derived from (distinct from
    the rewritten claims): one row per source with its raw snippet."""
    if not search_results:
        return
    async with _connect(database_path) as db:
        for item in search_results:
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            if not url or not snippet:
                continue
            cur = await db.execute(
                "SELECT id FROM sources WHERE run_id = ? AND url = ? LIMIT 1",
                (run_id, url),
            )
            row = await cur.fetchone()
            source_id = row[0] if row else None
            await db.execute(
                "INSERT INTO evidence (run_id, source_id, raw_snippet, created_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, source_id, snippet, _now()),
            )
        await db.commit()


async def save_critic_review(database_path: str, run_id: str, iteration: int, critique: dict,
                           breakdown: dict | None = None) -> None:
    """Persist EVERY critic iteration (not just the final verdict) — Replay
    needs the actual back-and-forth, not only the outcome. improved_queries
    persists too: resume reads the last review's follow-ups, and without them
    the depth controller can never expand a resumed run."""
    if not isinstance(critique, dict):
        return

    def _dump(value, default: str) -> str:
        try:
            return json.dumps(value if value is not None else default)
        except (TypeError, ValueError):
            return default

    breakdown_json = _dump(breakdown or {}, "{}")
    queries_json = _dump(critique.get("improved_queries") or [], "[]")
    async with _connect(database_path) as db:
        await db.execute(
            "INSERT INTO critic_reviews (run_id, iteration, is_sufficient, reason, confidence, breakdown, improved_queries, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                int(iteration),
                1 if critique.get("is_sufficient") else 0,
                str(critique.get("reason", "")),
                float(critique.get("confidence", 0.0) or 0.0),
                breakdown_json,
                queries_json,
                _now(),
            ),
        )
        await db.commit()


async def save_decisions(database_path: str, run_id: str, options: list) -> None:
    """One row per strategic option from the Decision Layer (3.5)."""
    rows = [
        (
            run_id,
            str(o.get("option_label", "")).strip(),
            str(o.get("description", "")).strip(),
            1 if o.get("is_recommended") else 0,
            str(o.get("rationale", "")),
            str(o.get("risk_note", "")),
            _now(),
        )
        for o in options
        if isinstance(o, dict) and str(o.get("option_label", "")).strip() and str(o.get("description", "")).strip()
    ]
    if not rows:
        return
    async with _connect(database_path) as db:
        await db.executemany(
            "INSERT INTO decisions (run_id, option_label, description, is_recommended, rationale, risk_note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()


async def save_contradictions(database_path: str, run_id: str, contradictions: list) -> int:
    """Persist detected contradiction pairs for replay and diagnosis."""
    rows = [
        (
            run_id,
            str(c.get("claim_a", "")).strip(),
            str(c.get("source_a", "") or "").strip(),
            str(c.get("claim_b", "")).strip(),
            str(c.get("source_b", "") or "").strip(),
            _now(),
        )
        for c in contradictions or []
        if isinstance(c, dict) and str(c.get("claim_a", "")).strip() and str(c.get("claim_b", "")).strip()
    ]
    if not rows:
        return 0
    async with _connect(database_path) as db:
        await db.executemany(
            "INSERT INTO contradictions (run_id, claim_a, source_a, claim_b, source_b, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()
    return len(rows)


async def save_verification_results(database_path: str, run_id: str, facts: list) -> int:
    """Persist per-fact verification detail (the claims table keeps only
    the verified flag; scores and reasons live here)."""
    rows = [
        (
            run_id,
            str(f.get("claim", "")).strip(),
            1 if f.get("verified") else 0,
            float(f.get("verification_score", 0.0) or 0.0)
            if isinstance(f.get("verification_score"), (int, float)) else None,
            str(f.get("verification_reason", "") or ""),
            _now(),
        )
        for f in facts or []
        if isinstance(f, dict) and str(f.get("claim", "")).strip()
    ]
    if not rows:
        return 0
    async with _connect(database_path) as db:
        await db.executemany(
            "INSERT INTO verification_results (run_id, claim, verified, score, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await db.commit()
    return len(rows)


async def save_citations(database_path: str, run_id: str, report_markdown: str) -> int:
    """Parse the emitted Sources legend back into citation rows, so the
    [n] markers in the report resolve to URLs without reparsing markdown.
    Handles both the current `## Sources` heading and the legacy bare
    `Sources:` line."""
    import re as _re

    count = 0
    text = report_markdown or ""
    match = _re.search(r"\n+#{0,6}\s*Sources:?\s*\n", text)
    if match:
        legend = text[match.end():]
    else:
        _, _, legend = text.partition("\nSources:")
    async with _connect(database_path) as db:
        for line in legend.splitlines():
            # Legend entries carry an optional tier annotation:
            # "[1] nature.com (peer_reviewed, primary) — https://..." — the
            # domain group must not swallow the parenthetical, and legacy
            # bare "[1] domain — url" lines must keep matching.
            match = _re.match(r"^\[(\d+)\]\s+(\S+)(?:\s*\([^)]*\))?\s+—\s*(\S+)\s*$", line.strip())
            if not match:
                continue
            await db.execute(
                "INSERT INTO citations (run_id, marker, domain, url, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, int(match.group(1)), match.group(2), match.group(3) or "", _now()),
            )
            count += 1
        await db.commit()
    return count


async def save_final_report(
    database_path: str,
    run_id: str,
    report_markdown: str,
    confidence: float,
    audit_markdown: str = "",
) -> None:
    """Canonical report row keyed by run_id (research_reports stays for
    backward compatibility). `audit_markdown` is the separate audit/trace
    document; it is additive and defaults to empty so existing callers and
    pre-migration rows keep working."""
    async with _connect(database_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO final_reports "
            "(run_id, report_markdown, confidence, audit_markdown, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, report_markdown, float(confidence), audit_markdown or "", _now()),
        )
        await db.commit()
