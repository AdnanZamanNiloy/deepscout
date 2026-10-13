"""Pipeline event adapter: no misleading "sources" counts.

Regression: the adapter mapped the Pathfinder stage's initial-research CHARACTER
count (and the Scout Swarm stage's SECTION count) onto `search_progress.snippets`,
which the UI renders as "N sources". A run showed "24229 sources" — actually a
24 KB character count — while the real source total was in the tens. The adapter
now surfaces those as honest progress messages and reports the real source
count only in the terminal final_report frame.
"""
from __future__ import annotations

from app.runtime.event_adapter import PipelineEventAdapter, final_report_frame


def _stage_event(phase: str, node: str) -> dict:
    """The event shape the runner emits for a stage lifecycle signal.

    `_sink_event` starts from a blank node/stage and the sink payload fills the
    real node in, so the mapper sees the node on the event — which is why the
    signal has to be handled before the `stage`-falls-back-to-`node` gate.
    """
    return {
        "node": "", "stage": "", "kind": "pipeline_stage",
        "activity": "pipeline_stage", "action": phase, "message": "",
        **{"node": node, "phase": phase},
    }


def test_pathfinder_stage_emits_progress_not_a_source_count():
    adapter = PipelineEventAdapter("q")
    frames = adapter.to_frames({
        "node": "pathfinder",
        "stage": "retrieval",
        "initial_research_chars": 24229,
    })
    # No search_progress frame claiming 24229 sources.
    assert not any(f["type"] == "search_progress" for f in frames)
    progress = [f for f in frames if f["type"] == "progress"]
    assert len(progress) == 1
    assert "characters" in progress[0]["message"]
    assert "24229" in progress[0]["message"].replace(",", "")


def test_scout_swarm_stage_reports_sections_not_sources():
    adapter = PipelineEventAdapter("q")
    frames = adapter.to_frames({
        "node": "scout_swarm",
        "stage": "retrieval",
        "research_sections": 5,
    })
    assert not any(f["type"] == "search_progress" for f in frames)
    progress = [f for f in frames if f["type"] == "progress"]
    assert progress and "5 section" in progress[0]["message"]


def test_final_report_frame_carries_the_real_source_count():
    fr = final_report_frame(report="# R", sources=["http://a", "http://b", "http://c"])
    assert fr["type"] == "final_report"
    assert fr["source_count"] == 3


def test_final_report_source_count_is_zero_when_no_sources():
    fr = final_report_frame(report="# R", sources=[])
    assert fr["source_count"] == 0


def test_final_report_frame_forwards_audit_fields():
    """The audit bridge's fields reach the wire frame the panel renders."""
    fr = final_report_frame(
        report="# R",
        sources=["http://a"],
        confidence=0.62,
        confidence_breakdown={"source_quality": 0.9},
        answer_support={"rate": 0.8, "sentence_details": []},
        quality={"findings": []},
        contradictions=[{"kind": "numeric"}],
    )
    assert fr["confidence"] == 0.62
    assert fr["confidence_breakdown"] == {"source_quality": 0.9}
    assert fr["answer_support"] == 0.8  # reduced to the rate the UI reads
    assert fr["quality"] == {"findings": []}
    assert fr["contradictions"] == [{"kind": "numeric"}]


def test_final_report_frame_defaults_are_empty_not_invented():
    fr = final_report_frame(report="# R", sources=[])
    assert fr["answer_support"] is None
    assert fr["quality"] == {}
    assert fr["contradictions"] == []


# ---------------------------------------------------------------------------
# Stage lifecycle — the live rail and the live trace must share one signal.
#
# Regression: a run that had streamed for a minute showed all eight agents as
# "Queued" and the header read "0 agents working" while its trace was visibly
# working, because node completions were the only thing the wire reported and
# nothing had completed yet. The client could not know a stage had started.
# ---------------------------------------------------------------------------

def test_stage_start_and_end_are_both_reported_with_node_identity():
    adapter = PipelineEventAdapter("q")

    # The signal arrives with the node on the event, not the collapsed stage.
    started = adapter.to_frames(_stage_event("start", "pathfinder"))
    assert len(started) == 1
    assert started[0]["type"] == "stage"
    assert started[0]["node"] == "pathfinder"
    assert started[0]["status"] == "running"
    assert started[0]["label"] == "Searching the web"

    ended = adapter.to_frames(_stage_event("end", "pathfinder"))
    assert len(ended) == 1
    assert ended[0]["type"] == "stage"
    assert ended[0]["status"] == "done"


def test_pathfinder_and_scout_swarm_are_distinguishable_stages():
    """Both map onto the `retrieval` STAGE; the wire must still separate them."""
    adapter = PipelineEventAdapter("q")
    pathfinder = adapter.to_frames(_stage_event("start", "pathfinder"))[0]
    swarm = adapter.to_frames(_stage_event("start", "scout_swarm"))[0]
    assert pathfinder["node"] != swarm["node"]
    assert pathfinder["label"] != swarm["label"]


def test_a_stage_signal_without_a_node_is_dropped():
    adapter = PipelineEventAdapter("q")
    frames = adapter.to_frames({
        "node": "", "stage": "", "activity": "pipeline_stage",
        "action": "start", "message": "",
    })
    assert frames == []


def test_stage_frames_carry_a_timestamp():
    adapter = PipelineEventAdapter("q")
    frame = adapter.to_frames(_stage_event("start", "architect"))[0]
    # The rail needs a real clock to time a stage, and the audit trail persists
    # this stamp verbatim — "now, when the row was written" gave zero spans.
    assert isinstance(frame["ts"], int) and frame["ts"] > 0


def test_stage_frame_progress_line_survives_the_shared_dedupe_set():
    """Announcing a stage must not silence the retrieval note for that node.

    `stage_frame` and the "Gathered ~N characters" note both key on the node
    name. When they shared one dedupe set, whichever ran first silenced the
    other and the run lost a progress line.
    """
    adapter = PipelineEventAdapter("q")
    assert adapter.stage_frame("pathfinder") is not None
    # ...and the retrieval note still gets through afterwards.
    frames = adapter.to_frames({
        "node": "pathfinder", "stage": "retrieval", "initial_research_chars": 1200,
    })
    assert any("characters" in f.get("message", "") for f in frames)


def _sink_recorder():
    events: list[tuple[str, str, str, object]] = []

    class _Sink:
        async def emit(self, kind, content, output, metadata=None):
            events.append((kind, content, output, metadata))

    return _Sink(), events


def _team():
    from app.agents.team import ResearchTeam

    sink, events = _sink_recorder()
    team = ResearchTeam(
        task={"query": "q", "max_sections": 3},
        llm=None, cfg=None, researcher_factory=None, sink=sink,
    )
    return team, events


def test_every_graph_node_is_wrapped_so_it_announces_itself():
    """All eight stages must be wrapped — an unwrapped node stays invisible.

    The client can only show a stage as working because the node announced its
    start; a node added to the graph without the wrapper would silently never
    light up. This asserts the wiring itself, not just the wrapper.
    """
    team, _ = _team()
    workflow = team.build_graph()

    expected = {
        "pathfinder", "architect", "review_gate", "scout_swarm",
        "narrative_writer", "evidence_auditor", "diagram_designer",
        "report_assembler",
    }
    assert expected <= set(workflow.nodes)
    for node in expected:
        # LangGraph wraps the callable; the wrapper is the coroutine it holds.
        func = getattr(workflow.nodes[node].runnable, "afunc", None)
        assert func is not None, f"{node} has no callable"
        assert getattr(func, "__name__", "") == f"staged_{node}", (
            f"{node} is not stage-wrapped, so it never announces start/end"
        )


def test_a_stage_announces_start_then_end_around_its_work():
    import asyncio

    team, events = _team()
    order: list[str] = []

    async def work(state):
        order.append("work")
        return {"sections": ["A"]}

    async def _run():
        await team._staged("architect", work)({"task": {"query": "q"}})

    asyncio.run(_run())

    phases = [(e[3]["node"], e[1]) for e in events if e[0] == "pipeline_stage"]
    assert phases == [("architect", "start"), ("architect", "end")]
    # The work happened BETWEEN the two signals, not after them.
    assert order == ["work"]


def test_a_failing_node_still_announces_its_end():
    """A node that raises must not leave the rail spinning forever on it."""
    import asyncio

    team, events = _team()

    async def _boom(state):
        raise RuntimeError("agent exploded")

    async def _run():
        try:
            await team._staged("pathfinder", _boom)({"task": {"query": "q"}})
        except RuntimeError:
            return
        raise AssertionError("the failure should have propagated")

    asyncio.run(_run())
    phases = [(e[3]["node"], e[1]) for e in events if e[0] == "pipeline_stage"]
    assert phases == [("pathfinder", "start"), ("pathfinder", "end")]


def test_a_stage_signal_survives_a_sink_that_raises():
    """A broken transport must not take the run down with it."""
    import asyncio

    from app.agents.team import ResearchTeam

    class _BrokenSink:
        async def emit(self, kind, content, output, metadata=None):
            raise OSError("socket gone")

    async def _run():
        team = ResearchTeam(
            task={"query": "q", "max_sections": 3},
            llm=None, cfg=None, researcher_factory=None, sink=_BrokenSink(),
        )

        async def work(state):
            return {"sections": ["A"]}

        return await team._staged("architect", work)({"task": {"query": "q"}})

    assert asyncio.run(_run()) == {"sections": ["A"]}
