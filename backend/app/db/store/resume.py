from __future__ import annotations

import aiosqlite
import json

from app.db.store.records import (
    load_trace_frames,
)
from app.db.store.schema import (
    _connect,
)


async def load_state_for_resume(database_path: str, run_id: str) -> dict | None:
    """Durable checkpointing (Phase 3.3): rebuild a ResearchState from the
    persisted rows of a failed/timeout run so resume can skip planner/search.

    Returns None if the run doesn't exist or is not resumable.
    """
    async with _connect(database_path) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT * FROM research_runs WHERE id = ? AND status IN ('failed', 'timeout')",
            (run_id,),
        )
        run_row = await cur.fetchone()
        if run_row is None:
            return None

        async def _all(query: str, params: tuple = ()) -> list:
            c = await db.execute(query, params)
            return [dict(r) for r in await c.fetchall()]

        tasks = await _all(
            "SELECT question, axis, search_type, priority FROM agent_tasks WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        sources = await _all(
            "SELECT url, reliability_score, source_domain, publisher, "
            "retrieval_provider, retrieval_engine "
            "FROM sources WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        claims = await _all(
            "SELECT claim, source_url, confidence, verified, agent FROM claims WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        review_rows = await _all(
            "SELECT iteration, is_sufficient, reason, confidence, improved_queries FROM critic_reviews "
            "WHERE run_id = ? ORDER BY iteration DESC LIMIT 1",
            (run_id,),
        )
        report_rows = await _all(
            "SELECT report_markdown, confidence FROM final_reports WHERE run_id = ? LIMIT 1",
            (run_id,),
        )

    last_review = review_rows[0] if review_rows else None
    iteration = int(last_review["iteration"]) if last_review else 0

    sub_questions = [
        {
            "id": i + 1,
            "question": t["question"],
            "axis": t["axis"] or "general",
            "search_type": t["search_type"] or "encyclopedia",
            "priority": int(t["priority"] or 2),
            "depends_on": [],
            "coverage_goal": "",
            "domain": "general",
            "minimum_sources": 2,
            "stop_condition": "sufficient evidence for this axis",
        }
        for i, t in enumerate(tasks)
    ]

    # Identity/provenance are carried through a resumed run so a replay labels
    # its sources exactly like the original run did. Rows written before these
    # columns existed read back as "" and the UI falls back to the URL's domain.
    search_results = [
        {
            "url": s["url"],
            "reliability_score": s["reliability_score"] or 0.0,
            "snippet": "",
            "source_domain": s.get("source_domain") or "",
            "publisher": s.get("publisher") or "",
            "retrieval_provider": s.get("retrieval_provider") or "",
            "retrieval_engine": s.get("retrieval_engine") or "",
        }
        for s in sources
    ]
    facts = [
        {
            "claim": c["claim"],
            "source": c["source_url"],
            "confidence": c["confidence"] or 0.0,
            "verified": bool(c["verified"]),
            "agent": c.get("agent", "") or "",
        }
        for c in claims
    ]

    state: dict = {
        "query": run_row["query"],
        "session_id": run_row["session_id"] if "session_id" in run_row.keys() else None,
        "sub_questions": sub_questions,
        "search_results": search_results,
        "facts": facts,
        "critique": {},
        "critique_feedback": "",
        "iteration": iteration,
        "max_iterations": int(run_row["max_iterations"] or 3)
        if "max_iterations" in run_row.keys() else 3,
        "final_report": "",
        "synthesized_answer": "",
        "confidence": run_row["confidence"] or 0.0,
        "confidence_history": [run_row["confidence"] or 0.0] if run_row["confidence"] else [],
        "orchestration": {
            "complexity_level": run_row["complexity"] or "unknown",
            "target_agents": run_row["agent_count"] or 3,
            "max_parallel_agents": 3,
            "clamped": False,
            "deep_research": False,
            "notes": [],
        },
        "resumed_from": run_id,
    }
    if last_review:
        try:
            improved = json.loads(last_review.get("improved_queries") or "[]")
        except (TypeError, ValueError):
            improved = []
        if not isinstance(improved, list):
            improved = []
        state["critique"] = {
            "is_sufficient": bool(last_review["is_sufficient"]),
            "reason": last_review["reason"] or "",
            "improved_queries": [str(q) for q in improved if str(q).strip()],
            "confidence": last_review["confidence"] or 0.0,
        }
        state["critique_feedback"] = last_review["reason"] or ""
    if report_rows:
        state["final_report"] = report_rows[0]["report_markdown"]

    return state


async def mark_run_resumable_reset(database_path: str, run_id: str) -> None:
    """Flip a failed/timeout run back to 'running' when a resume starts."""
    async with _connect(database_path) as db:
        await db.execute(
            "UPDATE research_runs SET status = 'running', completed_at = NULL WHERE id = ?",
            (run_id,),
        )
        await db.commit()


async def get_run_trace(database_path: str, run_id: str) -> dict | None:
    """Research Replay (Phase 3.4): read-only reconstruction of a run.

    Joins agent_events (node timing, retries, fallbacks, failures) with
    agent_tasks / sources / claims so the trace answers "why did this report
    reach this confidence", not just "what was the answer".
    """
    async with _connect(database_path) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute("SELECT * FROM research_runs WHERE id = ?", (run_id,))
        run_row = await cur.fetchone()
        if run_row is None:
            return None

        async def _all(query: str, params: tuple = ()) -> list:
            c = await db.execute(query, params)
            return [dict(r) for r in await c.fetchall()]

        tasks = await _all(
            "SELECT id, question, axis, search_type, priority, created_at "
            "FROM agent_tasks WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        sources = await _all(
            "SELECT id, url, reliability_score, fetched_at, source_domain, "
            "publisher, retrieval_provider, retrieval_engine "
            "FROM sources WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        claims = await _all(
            "SELECT id, claim, source_url, confidence, verified, agent, challenged, created_at "
            "FROM claims WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        events = await _all(
            "SELECT id, node, event_type, payload, started_at, ended_at "
            "FROM agent_events WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        critic_reviews = await _all(
            "SELECT id, iteration, is_sufficient, reason, confidence, breakdown, created_at "
            "FROM critic_reviews WHERE run_id = ? ORDER BY iteration, id",
            (run_id,),
        )
        for review in critic_reviews:
            try:
                review["breakdown"] = json.loads(review.get("breakdown") or "{}")
            except (TypeError, ValueError):
                review["breakdown"] = {}
        evidence = await _all(
            "SELECT id, source_id, raw_snippet, created_at "
            "FROM evidence WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        decisions = await _all(
            "SELECT id, option_label, description, is_recommended, rationale, risk_note, created_at "
            "FROM decisions WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        contradictions = await _all(
            "SELECT id, claim_a, source_a, claim_b, source_b, created_at "
            "FROM contradictions WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        verification_results = await _all(
            "SELECT id, claim, verified, score, reason, created_at "
            "FROM verification_results WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        citations = await _all(
            "SELECT id, marker, domain, url, created_at "
            "FROM citations WHERE run_id = ? ORDER BY marker, id",
            (run_id,),
        )
        cur = await db.execute(
            "SELECT report_markdown, audit_markdown, confidence, generated_at FROM final_reports WHERE run_id = ?",
            (run_id,),
        )
        final_report_row = await cur.fetchone()

        return {
            "run_id": run_id,
            "query": run_row["query"],
            "status": run_row["status"],
            "session_id": run_row["session_id"] if "session_id" in run_row.keys() else None,
            "complexity": run_row["complexity"],
            "agent_count": run_row["agent_count"],
            "confidence": run_row["confidence"],
            "estimated_cost": run_row["estimated_cost"],
            "created_at": run_row["created_at"],
            "completed_at": run_row["completed_at"],
            "plan": tasks,
            "sources": sources,
            "claims": claims,
            "events": events,
            # The wire frames the UI's pipeline trace renders. Absent for runs
            # persisted before this existed, and for purged traces; the
            # frontend treats that as "no trace recorded" rather than an error.
            "frames": await load_trace_frames(database_path, run_id),
            "critic_reviews": critic_reviews,
            "evidence": evidence,
            "decisions": decisions,
            "contradictions": contradictions,
            "verification_results": verification_results,
            "citations": citations,
            "final_report": dict(final_report_row) if final_report_row else None,
        }
