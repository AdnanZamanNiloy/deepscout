"""One section's research unit and its review/revision loop.

A :class:`SectionResearchAgent` runs a full research pass for a single report
section (a subtopic of the main query) using the shared researcher factory, so
each section gets its own evidence and its own draft. The reviewer and reviser
polish that draft against the task's guidelines before it joins the report.
"""
from __future__ import annotations

from typing import Any, Dict

from app.agents.routing import is_none_accept_response, log_agent
from app.common.streaming import stream_output

_REVIEW_TEMPLATE = (
    "You are an expert research article reviewer. "
    "Your goal is to review research drafts and provide feedback to the reviser "
    "only based on specific guidelines. "
)

_SAMPLE_REVISION_NOTES = """
{
  "draft": { 
    draft title: The revised draft that you are submitting for review 
  },
  "revision_notes": Your message to the reviewer about the changes you made to the draft based on their feedback
}
"""


class SectionResearchAgent:
    """Research a single report section into a draft."""

    def __init__(self, llm, cfg, researcher_factory, sink=None, tone=None):
        self.llm = llm
        self.cfg = cfg
        self.researcher_factory = researcher_factory
        self.sink = sink
        self.tone = tone

    async def run_depth_research(self, draft_state: Dict[str, Any]) -> Dict[str, Any]:
        """Research the section's topic and return its draft."""
        task = draft_state.get("task") or {}
        topic = draft_state.get("topic")
        parent_query = task.get("query")
        source = task.get("source", "web")
        sibling_sections = draft_state.get("sibling_sections") or []

        if self.sink:
            await stream_output(
                "logs",
                "depth_research",
                f"Running in depth research on the following report topic: {topic}",
                self.sink,
            )

        # Coverage map for the writer/subtopic prompt: the sections already
        # completed this run (their titles AND written text), so this section
        # does not restate a statistic or argument a sibling already made.
        # Falls back to sibling titles when no prior content exists yet.
        existing_headers = draft_state.get("existing_headers")
        if not existing_headers:
            existing_headers = [
                {"subtopic task": section, "note": "covered by another section of this report"}
                for section in sibling_sections
            ]
        relevant_written_contents = draft_state.get("relevant_written_contents") or []

        researcher = self.researcher_factory(
            query=topic,
            report_type="subtopic_report",
            parent_query=parent_query,
            report_source=source,
            tone=self.tone,
        )
        try:
            await researcher.conduct_research()
            report = await researcher.write_report(
                existing_headers=existing_headers,
                relevant_written_contents=relevant_written_contents,
            )
        except Exception as e:
            log_agent("RESEARCHER", f"Error researching topic {topic}: {e}")
            report = None
        return {"draft": {topic: report}}


class ReviewerAgent:
    """Review a section draft against the task guidelines."""

    def __init__(self, llm, cfg, sink=None):
        self.llm = llm
        self.cfg = cfg
        self.sink = sink

    async def review_draft(self, draft_state: Dict[str, Any]) -> Any:
        task = draft_state.get("task") or {}
        raw_guidelines = task.get("guidelines") or []
        if not isinstance(raw_guidelines, (list, tuple)):
            raw_guidelines = [raw_guidelines] if raw_guidelines else []
        guidelines = "- ".join(str(g) for g in raw_guidelines if g is not None)
        revision_notes = draft_state.get("revision_notes")

        revise_prompt = f"""The reviser has already revised the draft based on your previous review notes with the following feedback:
{revision_notes}\n
Please provide additional feedback ONLY if critical since the reviser has already made changes based on your previous feedback.
If you think the article is sufficient or that non critical revisions are required, please aim to return None.
"""
        review_prompt = f"""You have been tasked with reviewing the draft which was written by a non-expert based on specific guidelines.
Please accept the draft if it is good enough to publish, or send it for revision, along with your notes to guide the revision.
If not all of the guideline criteria are met, you should send appropriate revision notes.
If the draft meets all the guidelines, please return None.
{revise_prompt if revision_notes else ""}

Guidelines: {guidelines}\nDraft: {draft_state.get("draft")}\n
"""
        response = await self.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _REVIEW_TEMPLATE},
                {"role": "user", "content": review_prompt},
            ]
        )
        if task.get("verbose") and self.sink:
            await stream_output(
                "logs", "review_feedback", f"Review feedback is: {response}...", self.sink
            )
        if is_none_accept_response(response):
            return None
        return response

    async def run(self, draft_state: Dict[str, Any]) -> Dict[str, Any]:
        task = draft_state.get("task") or {}
        review = None
        if task.get("follow_guidelines"):
            review = await self.review_draft(draft_state)
        out: Dict[str, Any] = {"review": review}
        if review is not None:
            out["draft_revision_count"] = draft_state.get("draft_revision_count", 0) + 1
        return out


class ReviserAgent:
    """Revise a section draft based on the reviewer's notes."""

    def __init__(self, llm, cfg, sink=None):
        self.llm = llm
        self.cfg = cfg
        self.sink = sink

    async def revise_draft(self, draft_state: Dict[str, Any]) -> Dict[str, Any]:
        review = draft_state.get("review")
        draft_report = draft_state.get("draft")
        prompt = [
            {
                "role": "system",
                "content": "You are an expert writer. Your goal is to revise drafts based on reviewer notes.",
            },
            {
                "role": "user",
                "content": f"""Draft:\n{draft_report}\nReviewer's notes:\n{review}\n\n
You have been tasked by your reviewer with revising the following draft, which was written by a non-expert.
If you decide to follow the reviewer's notes, please write a new draft and make sure to address all of the points they raised.
Please keep all other aspects of the draft the same.
You MUST return nothing but a JSON in the following format:
{_SAMPLE_REVISION_NOTES}
""",
            },
        ]
        return await self.llm.call_model_json(prompt)

    async def run(self, draft_state: Dict[str, Any]) -> Dict[str, Any]:
        log_agent("REVISOR", "Rewriting draft based on feedback...")
        revision = await self.revise_draft(draft_state)
        return {
            "draft": revision.get("draft"),
            "revision_notes": revision.get("revision_notes"),
        }
