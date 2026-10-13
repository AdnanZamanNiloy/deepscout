"""Report assembly agents: body writer, fact checker, visualizer, publisher.

After the sections are researched, the writer produces the introduction and
conclusion (the body sections already exist as drafts), the fact checker
reviews the assembled draft and can send it back once, the visualizer proposes a
single diagram, and the publisher assembles the final markdown document.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List

from app.agents.routing import is_none_accept_response
from app.common.logging import get_logger
from app.common.streaming import stream_output

logger = get_logger(__name__)

_SAMPLE_LAYOUT = """
{
  "table_of_contents": A table of contents in markdown syntax (using '-') based on the research headers and subheaders,
  "introduction": An indepth introduction to the topic in markdown syntax and hyperlink references to relevant sources,
  "conclusion": A conclusion to the entire research based on all research data in markdown syntax and hyperlink references to relevant sources,
  "sources": A list with strings of all used source links in the entire research data in markdown syntax and apa citation format. For example: ['-  Title, year, Author [source url](source)', ...]
}
"""


class NarrativeWriter:
    """Write the report introduction and conclusion."""

    def __init__(self, llm, cfg, sink=None):
        self.llm = llm
        self.cfg = cfg
        self.sink = sink

    @staticmethod
    def get_headers(research_state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": research_state.get("title"),
            "date": "Date",
            "introduction": "Introduction",
            "table_of_contents": "Table of Contents",
            "conclusion": "Conclusion",
            "references": "References",
        }

    async def write_sections(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        query = research_state.get("title")
        data = research_state.get("research_data")
        task = research_state.get("task") or {}
        follow_guidelines = task.get("follow_guidelines")
        guidelines = task.get("guidelines")
        fact_notes = research_state.get("fact_check_notes")
        fact_notes_str = (
            f"Fact Checker Notes: {fact_notes}. You MUST revise the introduction and "
            "conclusion to address these issues.\n"
            if fact_notes
            else ""
        )

        prompt = [
            {
                "role": "system",
                "content": "You are a research writer. Your sole purpose is to write a well-written "
                "research reports about a topic based on research findings and information.\n ",
            },
            {
                "role": "user",
                "content": f"Today's date is {datetime.now().strftime('%d/%m/%Y')}\n."
                f"Query or Topic: {query}\n"
                f"Research data: {str(data)}\n"
                f"{fact_notes_str}"
                f"Your task is to write an in depth, well written and detailed "
                f"introduction and conclusion to the research report based on the provided research data. "
                f"Do not include headers in the results.\n"
                f"The introduction must frame the question and preview the report; the conclusion "
                f"must synthesise the findings. Neither may repeat the body sections' arguments or "
                f"restate the same statistics — reference them, do not duplicate them.\n"
                f"Every statistic, date or factual assertion you include MUST cite the exact source "
                f"it came from with an inline markdown hyperlink; never introduce a number that is "
                f"not present in the research data, and never cite a source for a claim it does not support.\n"
                f"Date any time-sensitive statement with an explicit as-of date from the evidence "
                f"(e.g. 'as of March 2026'); do not write a vague 'currently' or a future event as settled.\n"
                f"You MUST include any relevant sources to the introduction and conclusion as markdown hyperlinks -"
                f"For example: 'This is a sample text. ([url website](url))'\n\n"
                f"{f'You must follow the guidelines provided: {guidelines}' if follow_guidelines else ''}\n"
                f"You MUST return nothing but a JSON in the following format (without json markdown):\n"
                f"{_SAMPLE_LAYOUT}\n\n",
            },
        ]
        return await self.llm.call_model_json(prompt)

    async def run(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        if self.sink:
            await stream_output(
                "logs",
                "writing_report",
                "Writing final research report based on research data...",
                self.sink,
            )
        layout = await self.write_sections(research_state)
        headers = self.get_headers(research_state)
        return {**layout, "headers": headers}


class EvidenceAuditor:
    """Review the assembled draft for factual issues; can request one revision."""

    def __init__(self, llm, cfg, sink=None):
        self.llm = llm
        self.cfg = cfg
        self.sink = sink

    async def check_facts(self, research_state: Dict[str, Any]) -> Any:
        intro = research_state.get("introduction", "")
        conclusion = research_state.get("conclusion", "")
        data = research_state.get("research_data", [])
        draft = f"Introduction:\n{intro}\n\nSections Data:\n{data}\n\nConclusion:\n{conclusion}"
        prompt = [
            {
                "role": "system",
                "content": "You are a diligent Fact Checker Agent. Your goal is to review a research "
                "report draft, identify potential factual inaccuracies, hallucinations, or "
                "inconsistencies based on the provided research context, and decide if it needs "
                "to be sent back to the writer for revision.",
            },
            {
                "role": "user",
                "content": f"Review the following draft for factual accuracy:\n\n{draft}\n\n"
                "If you find critical errors, provide a list of them so the writer can revise the draft. "
                "If the draft is factually sound, return exactly the string 'None'. Do not return any "
                "other text if it is accepted.",
            },
        ]
        return await self.llm.create_chat_completion(messages=prompt)

    async def run(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        if self.sink:
            await stream_output(
                "logs", "fact_checking", "Checking facts in the assembled draft...", self.sink
            )
        review = await self.check_facts(research_state)
        if is_none_accept_response(review):
            review = None
        out: Dict[str, Any] = {"fact_check_notes": review}
        if review is not None:
            out["fact_check_revision_count"] = (
                research_state.get("fact_check_revision_count", 0) + 1
            )
        return out


class DiagramDesigner:
    """Propose a single Mermaid diagram from the research data."""

    def __init__(self, llm, cfg, sink=None):
        self.llm = llm
        self.cfg = cfg
        self.sink = sink

    async def generate_visualizations(self, research_state: Dict[str, Any]) -> Any:
        data = research_state.get("research_data", [])
        prompt = [
            {
                "role": "system",
                "content": "You are a Data Visualizer Agent. Your job is to extract key data points, "
                "processes, or timelines from the provided research data and generate a single relevant "
                "Mermaid.js diagram (such as a pie chart, flowchart, or timeline) to make the report "
                "more user-friendly.",
            },
            {
                "role": "user",
                "content": f"Research Data:\n{data}\n\nPlease generate a Mermaid diagram block "
                "representing the most interesting or structural aspect of this data. Ensure it uses "
                "standard markdown ```mermaid ... ``` syntax. If there is absolutely no suitable data "
                "for a diagram, return the exact string 'None'.",
            },
        ]
        return await self.llm.create_chat_completion(messages=prompt)

    async def run(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        if self.sink:
            await stream_output(
                "logs", "visualizing", "Generating visualizations for the research report...", self.sink
            )
        diagram_output = await self.generate_visualizations(research_state)
        diagrams = []
        if not is_none_accept_response(diagram_output):
            diagrams.append(diagram_output)
        return {"diagrams": diagrams}


class ReviewGate:
    """Plan-review gate.

    Two modes:

    * **Interactive (human-in-the-loop)** — when the task sets ``hitl`` and a
      ``run_id``, the gate streams the proposed outlines to the UI and *pauses*
      until the user approves (optionally with edited sections and a free-text
      direction) or sends it back for revision. The run waits on the approval
      registry (:mod:`app.runtime.plan_review`); on timeout or no answer it
      falls back to the automatic review below so a run never hangs.
    * **Automatic** — the default for API/headless callers. The gate runs a
      deterministic review of the outline (non-empty, within the mode cap,
      distinct, grounded) and either accepts or sends concrete feedback back to
      the Research Architect.
    """

    def __init__(self, llm, cfg, sink=None):
        self.llm = llm
        self.cfg = cfg
        self.sink = sink

    async def review_plan(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        task = research_state.get("task") or {}
        sections = [str(s).strip() for s in (research_state.get("sections") or []) if str(s).strip()]
        plan_revision_count = research_state.get("plan_revision_count", 0)

        # The outline lives under whatever key the planner actually populated.
        # `sections` is the documented one, but a re-plan or an alternate
        # planner payload can land the outline under `plan_alternatives`
        # instead. Reading only `sections` made the gate report "the outline is
        # empty" on EVERY run and spend a pointless planner round-trip (in deep
        # mode, enough of them fail the run with MaxPlanRevisionsExceededError).
        if not sections:
            alts = research_state.get("plan_alternatives") or []
            if isinstance(alts, list) and alts and isinstance(alts[0], (list, tuple)):
                sections = [str(s).strip() for s in alts[0] if str(s).strip()]

        if task.get("hitl"):
            decision = await self._interactive_review(research_state)
            if decision is not None:
                if decision.action == "revise":
                    plan_revision_count += 1
                    return {
                        "human_feedback": decision.feedback or "the user asked for a different outline",
                        "plan_revision_count": plan_revision_count,
                    }
                # Approved: adopt the user's outline (edited or chosen) and their
                # free-text direction, which flows into section research.
                approved = [s for s in (decision.sections or []) if str(s).strip()]
                out: Dict[str, Any] = {
                    "human_feedback": None,
                    "plan_revision_count": plan_revision_count,
                }
                if approved:
                    out["sections"] = approved
                if decision.feedback:
                    out["plan_direction"] = decision.feedback
                return out
            # No answer (timeout): fall through to the automatic review.

        # An EMPTY outline here is a planner failure, not a reviewable plan:
        # the architect already has a deterministic fallback for a failed or
        # empty LLM response (AGENTS.md 4.7), so re-planning from the gate just
        # spends another slow round-trip per run. Only flag a plan that EXISTS
        # and is structurally wrong (e.g. the same section twice).
        issues = _review_outline_issues(sections) if sections else []

        if issues and self.sink is not None:
            await stream_output(
                "plan_review", "revise",
                "Plan review found issues: " + "; ".join(issues),
                self.sink,
            )
        elif self.sink is not None:
            await stream_output(
                "plan_review", "accept",
                f"Plan accepted: {len(sections)} distinct section"
                f"{'s' if len(sections) != 1 else ''}.",
                self.sink,
            )

        if issues:
            plan_revision_count += 1
            # A non-None human_feedback is the routing signal to revise; the
            # architect consumes it as the feedback instruction on re-plan.
            return {
                "human_feedback": "; ".join(issues),
                "plan_revision_count": plan_revision_count,
            }
        return {
            "human_feedback": None,
            "plan_revision_count": plan_revision_count,
        }

    async def _interactive_review(self, research_state: Dict[str, Any]):
        """Pause the run and wait for the user's plan decision.

        Streams the proposed outlines to the UI, registers the wait, and blocks
        until the approval endpoint delivers a decision or the timeout expires.
        Returns the :class:`PlanDecision`, or ``None`` on timeout / no channel.

        The expiry is announced on the wire. A run that quietly resumes after the
        window closes is indistinguishable from a run that ignored the user, so
        the client is told both that the window closed and what happens next.
        """
        from app.runtime import plan_review as pr

        task = research_state.get("task") or {}
        run_id = str(task.get("run_id") or "")
        if not run_id:
            return None

        alternatives = research_state.get("plan_alternatives") or []
        if not alternatives and research_state.get("sections"):
            alternatives = [research_state["sections"]]

        timeout = task.get("plan_review_timeout_sec")
        deadline_ts = time.time() + float(timeout) if timeout else None
        # Register the wait BEFORE announcing the request, so a fast client
        # cannot deliver its decision before the run is listening (which would
        # drop the answer).
        pr.register_plan_wait(run_id)
        try:
            # Stream the outline(s) so the UI can render the review UI. The
            # deadline travels with it so the client can show the real countdown
            # rather than guessing from its own mount time.
            if self.sink is not None:
                await stream_output(
                    "plan_review",
                    "request",
                    "Review the proposed research outline before research begins.",
                    self.sink,
                    metadata={
                        "run_id": run_id,
                        "title": research_state.get("title") or "",
                        "alternatives": alternatives,
                        "deadline_ts": deadline_ts,
                    },
                )
            decision = await pr.await_plan_decision(run_id, timeout)
            if decision is None and self.sink is not None:
                waited = f"{int(timeout)}s" if timeout else "the review window"
                await stream_output(
                    "plan_review",
                    "expired",
                    "No plan review decision in time — continuing with the "
                    "automatic review.",
                    self.sink,
                    metadata={"run_id": run_id, "waited_sec": timeout},
                )
                logger.info("[plan_review] run %s: no decision after %s", run_id, waited)
            return decision
        finally:
            pr.clear_plan_decision(run_id)


# Two section headings this similar are treated as covering the same ground.
_SECTION_DUPLICATE_JACCARD = 0.6


def _review_outline_issues(sections: List[str]) -> List[str]:
    """Deterministic, unambiguous problems with ``sections``.

    Deliberately narrow. This gate runs on the AUTOMATIC path, where nobody is
    watching, and every issue it reports sends the whole plan back through
    another planner round-trip — so a false positive costs latency AND risks a
    worse outline (the re-plan can drop sections), and in deep mode enough of
    them raise ``MaxPlanRevisionsExceededError`` and fail the run outright.

    All but one of the checks were removed after measuring them against real
    outlines:

    * the section COUNT is no longer checked here. It is enforced by truncation
      in ``ResearchArchitect.plan_research``, which is free and keeps the
      outline ordered by importance; a re-plan to shave a section off the end
      was pure cost.
    * the section GROUNDING check is gone entirely, not merely loosened. Its
      premise — that a heading's words should appear in the initial research —
      is wrong for a HEADING: even a zero-overlap rule flags the abstract,
      forward-looking titles a strong planner writes ("Future Directions and
      Open Problems" shares no content word with a research blob about RAG
      implementation). Headings are meant to reason beyond the scraped text;
      what must be grounded is the prose beneath them, which the relevance gate
      and evidence grading already police.

    What remains is genuinely broken rather than merely imperfect: an empty
    outline, or two headings that are the same section twice.
    """
    from app.common.primitives import jaccard
    from app.evidence.text import _tokenize

    if not sections:
        return ["the outline is empty"]

    issues: List[str] = []

    section_tokens = [_tokenize(s) for s in sections]
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            if section_tokens[i] and section_tokens[j] and (
                jaccard(section_tokens[i], section_tokens[j]) >= _SECTION_DUPLICATE_JACCARD
            ):
                issues.append(
                    f"sections {i + 1} and {j + 1} overlap: "
                    f"\"{sections[i]}\" vs \"{sections[j]}\""
                )

    return issues


class ReportAssembler:
    """Assemble the final markdown report from the researched state."""

    def __init__(self, llm, cfg, output_dir: str, sink=None):
        self.llm = llm
        self.cfg = cfg
        self.output_dir = output_dir
        self.sink = sink

    def generate_layout(self, research_state: Dict[str, Any]) -> str:
        sections = []
        research_data = research_state.get("research_data") or []
        if not isinstance(research_data, (list, tuple)):
            research_data = [research_data]
        for subheader in research_data:
            if isinstance(subheader, dict):
                for _key, value in subheader.items():
                    sections.append(f"{value}")
            else:
                sections.append(f"{subheader}")
        sections_text = "\n\n".join(sections)

        sources = research_state.get("sources") or []
        if not isinstance(sources, (list, tuple)):
            sources = [sources] if sources else []
        references = "\n".join(f"{reference}" for reference in sources)

        headers = research_state.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}

        diagrams_text = ""
        diagrams = research_state.get("diagrams") or []
        if not isinstance(diagrams, (list, tuple)):
            diagrams = [diagrams] if diagrams else []
        if diagrams:
            diagrams_text = (
                "\n## Visualizations\n" + "\n\n".join(str(d) for d in diagrams) + "\n"
            )

        title = (
            headers.get("title")
            or research_state.get("title")
            or research_state.get("task", {}).get("query", "")
        )
        date_value = research_state.get("date") or ""
        # ``headers["date"]`` is the literal label "Date", not a value; only
        # render the line when a real date exists. The value is already
        # human-readable ("11 October 2026"), so no ambiguous numeric format.
        date_line = f"*{date_value}*\n" if str(date_value).strip() else ""

        return f"""# {title}
{date_line}
## {headers.get("introduction")}
{research_state.get('introduction')}

## {headers.get("table_of_contents")}
{research_state.get('table_of_contents')}

{sections_text}

## {headers.get("conclusion")}
{research_state.get('conclusion')}
{diagrams_text}
## {headers.get("references")}
{references}
"""

    async def run(self, research_state: Dict[str, Any]) -> Dict[str, Any]:
        if self.sink:
            await stream_output(
                "logs",
                "publishing",
                "Publishing final research report based on retrieved data...",
                self.sink,
            )
        layout = self.generate_layout(research_state)
        return {"report": layout}
