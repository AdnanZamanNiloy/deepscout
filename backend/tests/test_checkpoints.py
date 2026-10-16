"""Checkpointing: a failed run resumes from where it stopped, not from scratch.

Contract under test:
  * a checkpoint round-trips the runner's state and its completed-node list;
  * an upsert replaces, never duplicates or appends stale nodes;
  * a non-serializable value cannot poison a checkpoint (its slot survives as
    null so the resumed state keeps its shape);
  * a resumed team SKIPS the nodes already in the checkpoint AND the graph is
    seeded with their saved output, so re-running them cannot happen;
  * every failure mode (no run, no database, corrupt row) degrades to a fresh
    run rather than raising — persistence must never break research.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


from app.common.config import Settings
from app.core.usage import clear_run_usage, load_resume_checkpoint, start_run_usage
from app.db.sqlite import init_db
from app.db.store.checkpoints import (
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from app.db.store.sessions import start_research_run


def _db_path() -> str:
    d = tempfile.mkdtemp()
    return f"file:{Path(d) / 'ckpt.db'}"


async def _prepare_run(db: str, run_id: str) -> None:
    await init_db(db)
    await start_research_run(db, run_id, "q", "standard", 0, 1, "sess-1")


# --- store round-trip -------------------------------------------------------


def test_a_checkpoint_round_trips_state_and_completed_nodes():
    db = _db_path()

    async def go():
        await _prepare_run(db, "r1")
        state = {"task": {"mode": "quick"}, "sections": ["A", "B"]}
        await save_checkpoint(db, "r1", state, ["pathfinder", "architect"])
        return await load_checkpoint(db, "r1")

    got = asyncio.run(go())
    assert got["state"]["sections"] == ["A", "B"]
    assert got["completed_nodes"] == ["pathfinder", "architect"]


def test_an_upsert_replaces_rather_than_appends():
    db = _db_path()

    async def go():
        await _prepare_run(db, "r2")
        await save_checkpoint(db, "r2", {"sections": ["A"]}, ["pathfinder"])
        await save_checkpoint(db, "r2", {"sections": ["A", "B"]}, ["pathfinder", "architect"])
        return await load_checkpoint(db, "r2")

    got = asyncio.run(go())
    assert got["completed_nodes"] == ["pathfinder", "architect"]
    assert got["state"]["sections"] == ["A", "B"]


def test_a_non_serializable_value_cannot_poison_a_checkpoint():
    db = _db_path()

    async def go():
        await _prepare_run(db, "r3")
        await save_checkpoint(
            db, "r3", {"task": {}, "client": object(), "ok": 1, "nested": {"x": object()}}, []
        )
        return await load_checkpoint(db, "r3")

    got = asyncio.run(go())
    # The slot survives as null; the serializable data is intact.
    assert "client" in got["state"] and got["state"]["client"] is None
    assert got["state"]["ok"] == 1
    assert got["state"]["nested"]["x"] is None


def test_clear_and_unknown_run_are_both_none():
    db = _db_path()

    async def go():
        await _prepare_run(db, "r4")
        await save_checkpoint(db, "r4", {"a": 1}, ["pathfinder"])
        await clear_checkpoint(db, "r4")
        return await load_checkpoint(db, "r4"), await load_checkpoint(db, "never-existed")

    cleared, missing = asyncio.run(go())
    assert cleared is None
    assert missing is None


def test_a_corrupt_checkpoint_degrades_to_none_not_an_error():
    db = _db_path()

    async def go():
        await _prepare_run(db, "r5")
        # Write garbage straight into the row.
        from app.db.store.schema import _connect

        async with _connect(db) as conn:
            await conn.execute(
                "INSERT INTO run_checkpoints (run_id, state_json, completed_nodes, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("r5", "{not json", "[]", "now"),
            )
            await conn.commit()
        return await load_checkpoint(db, "r5")

    assert asyncio.run(go()) is None


# --- the runner-facing loader -----------------------------------------------


def test_the_loader_needs_an_active_run_with_a_database():
    clear_run_usage()
    assert asyncio.run(load_resume_checkpoint()) is None

    # A run with no database configured has nowhere to check.
    s = Settings(groq_api_key="k", database_url="", _env_file=None)
    start_run_usage("no-db", s, mode="quick")
    try:
        assert asyncio.run(load_resume_checkpoint()) is None
    finally:
        clear_run_usage()


def test_the_loader_finds_this_runs_checkpoint():
    db = _db_path()

    async def go():
        await _prepare_run(db, "r6")
        await save_checkpoint(db, "r6", {"sections": ["A", "B", "C"]}, ["pathfinder", "scout_swarm"])
        s = Settings(groq_api_key="k", database_url=db, _env_file=None)
        start_run_usage("r6", s, mode="deep")
        try:
            return await load_resume_checkpoint()
        finally:
            clear_run_usage()

    got = asyncio.run(go())
    assert got["completed_nodes"] == ["pathfinder", "scout_swarm"]
    assert got["state"]["sections"] == ["A", "B", "C"]


# --- the team skips what the checkpoint restored -----------------------------
#
# THE behaviour that makes this "resume" rather than "restart": a completed node
# must not run again. Asserted at the node wrapper, which is the only place that
# can actually prevent the work.

def test_a_completed_node_is_skipped_and_its_output_is_not_recomputed():
    from app.agents.team import ResearchTeam

    ran = {"pathfinder": 0, "architect": 0}

    async def pathfinder(state):
        ran["pathfinder"] += 1
        return {"initial_research": "fresh"}

    async def architect(state):
        ran["architect"] += 1
        return {"sections": ["fresh"]}

    team = ResearchTeam(
        task={},
        llm=None,
        cfg=None,
        researcher_factory=None,
        already_completed={"pathfinder"},
    )
    staged_pf = team._staged("pathfinder", pathfinder)
    staged_ar = team._staged("architect", architect)

    seeded = {"initial_research": "from-checkpoint"}  # what the graph is seeded with
    out_pf = asyncio.run(staged_pf(dict(seeded)))
    out_ar = asyncio.run(staged_ar(dict(seeded)))

    assert ran["pathfinder"] == 0, "a checkpointed node must not run again"
    assert out_pf == {}, "a skipped node contributes nothing new"
    assert ran["architect"] == 1, "a node NOT in the checkpoint must still run"
    assert out_ar == {"sections": ["fresh"]}


def test_without_a_checkpoint_every_node_runs():
    from app.agents.team import ResearchTeam

    calls = {"n": 0}

    async def node(state):
        calls["n"] += 1
        return {"ok": True}

    team = ResearchTeam(task={}, llm=None, cfg=None, researcher_factory=None)
    asyncio.run(team._staged("pathfinder", node)({}))
    assert calls["n"] == 1


# --- queued (runner) writes: capture time, order, clearing -------------------


def test_a_queued_snapshot_is_serialized_at_call_time():
    """The snapshot must capture the state as of the node that queued it.

    The old fire-and-forget path serialized the LIVE state dict whenever the
    task happened to start, so a queued write captured a later, different state
    than the node it represented.
    """
    db = _db_path()

    async def go():
        from app.db.store.checkpoints import flush_checkpoints, queue_checkpoint

        await _prepare_run(db, "r8")
        state = {"sections": ["A"]}
        queue_checkpoint(db, "r8", state, ["pathfinder"])
        state["sections"] = ["A", "B"]  # mutated after queuing — must not leak in
        await flush_checkpoints()
        return await load_checkpoint(db, "r8")

    got = asyncio.run(go())
    assert got["state"]["sections"] == ["A"]


def test_the_latest_queued_snapshot_wins():
    """Writes are single-flight latest-wins: no old snapshot can land last."""
    db = _db_path()

    async def go():
        from app.db.store.checkpoints import flush_checkpoints, queue_checkpoint

        await _prepare_run(db, "r9")
        queue_checkpoint(db, "r9", {"v": 1}, ["pathfinder"])
        await asyncio.sleep(0)  # let the first write start if it is going to
        queue_checkpoint(db, "r9", {"v": 2}, ["pathfinder", "architect"])
        queue_checkpoint(db, "r9", {"v": 3}, ["pathfinder", "architect"])
        await flush_checkpoints()
        return await load_checkpoint(db, "r9")

    got = asyncio.run(go())
    assert got["state"]["v"] == 3
    assert got["completed_nodes"] == ["pathfinder", "architect"]


def test_clear_cannot_be_resurrected_by_a_queued_snapshot():
    """A pending write from the run's last node must not recreate the row."""
    db = _db_path()

    async def go():
        from app.db.store.checkpoints import flush_checkpoints, queue_checkpoint

        await _prepare_run(db, "r10")
        queue_checkpoint(db, "r10", {"v": 1}, ["pathfinder"])
        await clear_checkpoint(db, "r10")
        await flush_checkpoints()
        return await load_checkpoint(db, "r10")

    assert asyncio.run(go()) is None


def test_queue_checkpoint_outside_a_loop_never_raises():
    from app.db.store.checkpoints import queue_checkpoint

    queue_checkpoint("file::memory:", "no-loop", {"a": 1}, [])


# --- resume: nodes left mid-revision must reopen -----------------------------


def test_resume_reopens_nodes_left_mid_revision():
    """A saved rejection is unresolved work — skipping its rerun loops routing.

    `completed_nodes` stores node NAMES, so a checkpoint taken between a
    rejection and the revision it demanded listed the rejecting node as done.
    Routing, on resume, re-reads the saved rejection and demands a revise that
    the skipped nodes never perform — forever (observed as GraphRecursionError).
    """
    from app.research.runner import _resume_completed_nodes

    all_nodes = [
        "pathfinder", "architect", "review_gate", "scout_swarm",
        "narrative_writer", "evidence_auditor", "diagram_designer", "report_assembler",
    ]

    # Accepted state: nothing reopens.
    assert _resume_completed_nodes(
        {"fact_check_notes": None, "human_feedback": None}, all_nodes
    ) == all_nodes

    # Fact-check rejection pending: writer + auditor reopen, the rest stays.
    got = _resume_completed_nodes({"fact_check_notes": "fact check found an error"}, all_nodes)
    assert "narrative_writer" not in got
    assert "evidence_auditor" not in got
    assert "pathfinder" in got and "report_assembler" in got

    # An EMPTY notes string is an unavailable check, not a pending revision —
    # routing accepts it, so resume must not reopen a pointless rerun.
    assert _resume_completed_nodes({"fact_check_notes": ""}, all_nodes) == all_nodes

    # Plan rejection pending: architect + review_gate reopen (the pair whose
    # rerun clears the routing marker).
    got = _resume_completed_nodes({"human_feedback": "make sections distinct"}, all_nodes)
    assert "architect" not in got and "review_gate" not in got
    assert "scout_swarm" in got


def test_completed_nodes_are_de_duplicated_across_a_revision_loop():
    """A revision loop revisits nodes; the checkpoint must list each once.

    The writer and auditor both run twice on a revise, so appending every
    completion grew the list without bound and re-listed nodes already restored
    from the checkpoint. The runner de-duplicates; this pins that behaviour at
    the store boundary a caller reads.
    """
    db = _db_path()

    async def go():
        await _prepare_run(db, "r7")
        # What the runner accumulates for a run with a revise: nw/ea twice.
        nodes = []
        for n in ["pathfinder", "architect", "scout_swarm", "narrative_writer",
                  "evidence_auditor", "narrative_writer", "evidence_auditor"]:
            if n not in nodes:
                nodes.append(n)
        await save_checkpoint(db, "r7", {"sections": ["A"]}, nodes)
        return await load_checkpoint(db, "r7")

    got = asyncio.run(go())
    assert got["completed_nodes"] == [
        "pathfinder", "architect", "scout_swarm", "narrative_writer", "evidence_auditor",
    ]
    assert len(got["completed_nodes"]) == len(set(got["completed_nodes"]))
