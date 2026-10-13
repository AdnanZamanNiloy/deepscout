"""The Review Gate performs a real, deterministic review of the outline.

The gate runs on the AUTOMATIC path, where no one is watching, and every issue
it reports costs a full planner round-trip and can make the outline worse.
Three regressions shaped what it is allowed to flag:

* a section COUNT check bounced the plan when the model overshot the mode cap;
  the re-plan prompt then made the model drop sections, so reports came back
  with parts missing. The cap is now enforced by truncation in the architect,
  where it costs nothing.
* a section GROUNDING check demanded that half a heading's content words appear
  in the initial research. It flagged the ordinary analytical headings a strong
  planner writes ("Comparative Cost-Benefit Analysis…", "Future Directions and
  Open Problems"), because reasoning beyond the scraped text is exactly what a
  heading is for. Loosening it to a zero-overlap rule still flagged them, so it
  was removed rather than tuned.
* both checks, on a real outline, added planner round-trips for no quality gain
  (latency), and enough of them in deep mode raise MaxPlanRevisionsExceededError
  and fail the run.

What the gate still flags is unambiguously broken: an empty outline, or the same
section twice.
"""
from __future__ import annotations

from app.agents.report import _review_outline_issues, ReviewGate


def test_a_sound_outline_has_no_issues():
    sections = [
        "Nuclear capacity and reactor construction",
        "Grid stability and safety regulation",
        "Public opinion and financing cost",
    ]
    assert _review_outline_issues(sections) == []


def test_empty_outline_is_flagged():
    issues = _review_outline_issues([])
    assert issues and "empty" in issues[0]


def test_duplicate_sections_are_flagged():
    sections = ["Grid stability and regulation", "Grid stability and regulation today"]
    issues = _review_outline_issues(sections)
    assert any("overlap" in i for i in issues)


def test_an_over_cap_outline_is_not_a_review_issue():
    """The cap is the architect's job (truncate), not a reason to re-plan.

    Bouncing this back cost a planner round-trip and came back with sections
    dropped; the outline is ordered by importance, so trimming the tail loses
    nothing from the report.
    """
    sections = ["Alpha topic", "Beta topic", "Gamma topic"]
    assert _review_outline_issues(sections) == []


def test_analytical_headings_are_not_off_topic():
    """A heading that reasons beyond the scraped text is doing its job.

    These all scored as "off-topic" under the old grounding check and each one
    triggered a destructive re-plan.
    """
    research = (
        "Retrieval augmented generation combines a retriever with a generator. "
        "Vector databases store embeddings. BM25 is a sparse lexical baseline."
    )
    for heading in (
        "Comparative Cost-Benefit Analysis of Serving Architectures",
        "Regulatory Outlook and Compliance Trajectory for 2027",
        "Stakeholder Incentives and Adoption Dynamics",
        "Future Directions and Open Problems",
    ):
        assert _review_outline_issues([heading]) == [], heading
    # An unrelated heading is not flagged either: lexical overlap on a heading
    # is simply not a valid grounding test. The prose is what gets grounded.
    assert _review_outline_issues(["Piano teaching methods for children"]) == []
    _ = research  # documents the (deliberately unused) premise


def test_gate_accepts_a_sound_plan_and_revises_a_bad_one():
    import asyncio

    gate = ReviewGate(llm=None, cfg=None, sink=None)
    good = asyncio.run(gate.review_plan({
        "task": {"max_sections": 5},
        "initial_research": "nuclear energy capacity grid stability regulation cost",
        "sections": ["Nuclear capacity", "Grid stability", "Regulation cost"],
    }))
    assert good["human_feedback"] is None
    assert good["plan_revision_count"] == 0

    # Two headings that are the same section twice: unambiguously broken.
    bad = asyncio.run(gate.review_plan({
        "task": {"max_sections": 5},
        "initial_research": "nuclear energy capacity",
        "sections": ["Grid stability and regulation", "Grid stability and regulation today"],
    }))
    assert bad["human_feedback"]  # a feedback string routes to revise
    assert bad["plan_revision_count"] == 1


def test_an_over_cap_plan_is_accepted_rather_than_re_planned():
    """The reported regression: over-cap plans cost a round-trip and lost sections.

    The gate must accept the outline it is given — trimming is the architect's
    job now — so a single slow planner call is not spent re-deriving an outline
    that was already complete.
    """
    import asyncio

    gate = ReviewGate(llm=None, cfg=None, sink=None)
    result = asyncio.run(gate.review_plan({
        "task": {"max_sections": 2},
        "initial_research": "nuclear energy capacity grid stability regulation",
        "sections": ["Nuclear capacity", "Grid stability", "Regulation"],
    }))
    assert result["human_feedback"] is None
    assert result["plan_revision_count"] == 0


class _CaptureSink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, content, output, metadata=None):
        self.events.append((kind, content, output, metadata))


def test_interactive_review_pauses_then_applies_approval():
    """HITL: the gate streams a plan_review request and waits for the decision."""
    import asyncio
    from app.runtime import plan_review as pr

    sink = _CaptureSink()
    gate = ReviewGate(llm=None, cfg=None, sink=sink)
    state = {
        "task": {"hitl": True, "run_id": "run-xyz", "max_sections": 5,
                 "plan_review_timeout_sec": 5},
        "initial_research": "nuclear energy capacity grid stability regulation cost",
        "title": "Nuclear in Bangladesh",
        "sections": ["Nuclear capacity", "Grid stability"],
        "plan_alternatives": [["Nuclear capacity", "Grid stability"], ["Cost", "Policy"]],
    }

    async def _drive():
        # Run the gate and, once it is waiting, deliver the approval.
        gate_task = asyncio.create_task(gate.review_plan(state))
        for _ in range(200):
            if pr.has_pending("run-xyz"):
                break
            await asyncio.sleep(0.01)
        pr.submit_plan_decision("run-xyz", pr.PlanDecision(
            action="approve", sections=["Cost drivers", "Policy environment"],
            feedback="focus on cost and policy",
        ))
        return await gate_task

    result = asyncio.run(_drive())

    # A plan_review request went on the wire with the alternatives.
    reqs = [e for e in sink.events if e[0] == "plan_review" and e[1] == "request"]
    assert reqs and reqs[0][3]["alternatives"] == state["plan_alternatives"]

    # The user's edited outline and direction were adopted.
    assert result["sections"] == ["Cost drivers", "Policy environment"]
    assert result["plan_direction"] == "focus on cost and policy"
    assert result["human_feedback"] is None


def test_interactive_review_revise_sends_feedback():
    import asyncio
    from app.runtime import plan_review as pr

    sink = _CaptureSink()
    gate = ReviewGate(llm=None, cfg=None, sink=sink)
    state = {
        "task": {"hitl": True, "run_id": "run-rev", "max_sections": 5,
                 "plan_review_timeout_sec": 5},
        "sections": ["A", "B"],
        "plan_alternatives": [["A", "B"]],
    }

    async def _drive():
        gate_task = asyncio.create_task(gate.review_plan(state))
        for _ in range(200):
            if pr.has_pending("run-rev"):
                break
            await asyncio.sleep(0.01)
        pr.submit_plan_decision("run-rev", pr.PlanDecision(action="revise", feedback="too shallow"))
        return await gate_task

    result = asyncio.run(_drive())
    assert result["human_feedback"] == "too shallow"
    assert result["plan_revision_count"] == 1


def test_interactive_review_times_out_to_automatic():
    """No user answer -> fall back to the automatic review, never hang."""
    import asyncio

    gate = ReviewGate(llm=None, cfg=None, sink=None)
    result = asyncio.run(gate.review_plan({
        "task": {"hitl": True, "run_id": "run-timeout", "max_sections": 5,
                 "plan_review_timeout_sec": 0.05},
        "initial_research": "nuclear energy capacity grid stability",
        "sections": ["Nuclear capacity", "Grid stability"],
        "plan_alternatives": [["Nuclear capacity", "Grid stability"]],
    }))
    # Automatic review accepts a sound outline.
    assert result["human_feedback"] is None


def test_expired_review_window_is_announced_and_carries_the_deadline():
    """A run that continues without an answer must SAY so.

    The wire contract is what stops an auto-started report from looking like the
    user's approval was ignored, so the expiry has to be a frame the client can
    close the card on — and the request frame has to carry the real deadline so
    the countdown is the server's, not the browser's guess.
    """
    import asyncio
    import time

    sink = _CaptureSink()
    gate = ReviewGate(llm=None, cfg=None, sink=sink)
    state = {
        "task": {"hitl": True, "run_id": "run-exp", "max_sections": 5,
                 "plan_review_timeout_sec": 0.05},
        "initial_research": "nuclear energy capacity grid stability",
        "sections": ["Nuclear capacity"],
        "plan_alternatives": [["Nuclear capacity"]],
    }

    before = time.time()
    asyncio.run(gate.review_plan(state))

    plan_events = [e for e in sink.events if e[0] == "plan_review"]
    requests = [e for e in plan_events if e[1] == "request"]
    expiries = [e for e in plan_events if e[1] == "expired"]

    # The request advertises when the window actually closes.
    assert requests, "the review window must be announced"
    deadline = requests[0][3]["deadline_ts"]
    assert isinstance(deadline, float)
    assert before <= deadline <= time.time() + 0.05

    # ...and the run says out loud that it continued without an answer.
    assert len(expiries) == 1
    assert expiries[0][3]["run_id"] == "run-exp"


def test_answered_review_window_never_emits_expiry():
    """The inverse: an approval must not be followed by an expiry notice."""
    import asyncio
    from app.runtime import plan_review as pr

    sink = _CaptureSink()
    gate = ReviewGate(llm=None, cfg=None, sink=sink)
    state = {
        "task": {"hitl": True, "run_id": "run-answered", "max_sections": 5,
                 "plan_review_timeout_sec": 5},
        "initial_research": "nuclear energy capacity grid stability",
        "sections": ["Nuclear capacity"],
        "plan_alternatives": [["Nuclear capacity"]],
    }

    async def _drive():
        gate_task = asyncio.create_task(gate.review_plan(state))
        for _ in range(200):
            if pr.has_pending("run-answered"):
                break
            await asyncio.sleep(0.01)
        pr.submit_plan_decision("run-answered",
                                pr.PlanDecision(action="approve", sections=["Nuclear capacity"]))
        return await gate_task

    asyncio.run(_drive())
    assert not [e for e in sink.events if e[0] == "plan_review" and e[1] == "expired"]


def test_adapter_maps_both_plan_review_actions_to_one_frame_type():
    """The client opens the card from `request` and closes it from `expired`."""
    from app.runtime.event_adapter import PipelineEventAdapter

    adapter = PipelineEventAdapter("q")

    opening = adapter.to_frames({
        "activity": "plan_review", "action": "request", "run_id": "r1",
        "title": "T", "alternatives": [["A"]], "message": "review this",
        "deadline_ts": 1234.5,
    })
    assert len(opening) == 1
    assert opening[0]["type"] == "plan_review"
    assert opening[0]["status"] == "awaiting"
    assert opening[0]["alternatives"] == [["A"]]
    assert opening[0]["deadline_ts"] == 1234.5

    closing = adapter.to_frames({
        "activity": "plan_review", "action": "expired", "run_id": "r1",
        "message": "no decision in time",
    })
    assert len(closing) == 1
    assert closing[0]["type"] == "plan_review"
    assert closing[0]["status"] == "expired"
    # No alternatives: an expiry must never re-open the card it is closing.
    assert closing[0]["alternatives"] == []
    assert closing[0]["deadline_ts"] is None


# ---------------------------------------------------------------------------
# The cap is enforced by truncation in the architect, not by re-planning.
#
# The report sections were coming back missing because an over-cap outline was
# bounced to the architect with a narrow "fix exactly these issues" instruction,
# and the model returned a SHORTER plan. Truncating keeps the outline ordered by
# importance and costs no planner round-trip.
# ---------------------------------------------------------------------------

class _ScriptedLLM:
    """Returns one canned planning payload per call, counting the calls."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def call_model_json(self, prompt):
        self.calls += 1
        return self.payload


def _architect(payload):
    from app.agents.architect import ResearchArchitect

    llm = _ScriptedLLM(payload)
    return ResearchArchitect(llm, None, None), llm


def test_over_cap_outline_is_truncated_not_dropped_or_re_planned():
    import asyncio

    architect, llm = _architect({
        "title": "T",
        "sections": ["One", "Two", "Three", "Four", "Five", "Six"],
    })
    out = asyncio.run(architect.plan_research({
        "initial_research": "text",
        "task": {"max_sections": 3, "plan_alternatives": 1},
    }))
    # The report keeps the top of the outline, in order.
    assert out["sections"] == ["One", "Two", "Three"]
    # ...and it cost exactly one planner call: no revision loop.
    assert llm.calls == 1


def test_alternatives_are_capped_too():
    import asyncio

    architect, _ = _architect({
        "title": "T",
        "sections": ["One", "Two", "Three", "Four"],
        "alternatives": [["A", "B", "C"], ["D", "E", "F", "G"]],
    })
    out = asyncio.run(architect.plan_research({
        "initial_research": "text",
        "task": {"max_sections": 2, "plan_alternatives": 3},
    }))
    assert out["sections"] == ["One", "Two"]
    assert all(len(a) <= 2 for a in out["plan_alternatives"])


def test_a_revision_instruction_does_not_invite_a_shorter_plan():
    """The re-plan prompt must not read as "make it smaller".

    The narrow "fix exactly these issues" wording made the model treat the
    feedback as the whole brief. The prompt has to keep the full subject in
    scope while addressing the feedback.
    """
    from app.agents.architect import ResearchArchitect

    architect = ResearchArchitect(_ScriptedLLM({}), None, None)
    msgs = architect._planning_messages(
        "some research text", "sections 1 and 2 overlap", 5, 1
    )
    system = " ".join(m["content"] for m in msgs)
    assert "sections 1 and 2 overlap" in system
    assert "as fully as before" in system
