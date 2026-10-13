"""Prompt construction for every stage of a research run.

This module is the single place where the instructions sent to the model are
written. Prompts dominate report quality, so the guidance here is deliberate and
must not drift silently: citation discipline, source-quality rules, date
handling, cross-section uniqueness and scope limits are all encoded as explicit
constraints, not left to the model to infer.

Two kinds of prompt live here:

* **report builders**, chosen by report type through
  :func:`builder_for` — the whole-report body and a single section;
* **stage prompts**, called directly by the skills: search-query planning, the
  introduction and conclusion, source curation, and the document renderer.

The wording is original to this project. The problem it solves — turning
gathered sources into a cited, non-repetitive, well-scoped report — is a shared
one; the constraints below were written for this pipeline's quality bar.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List

from app.research.enums import EvidenceScope, ReportKind, Tone
from app.common.models import Document


def _as_of_date() -> str:
    """The run date, phrased as an explicit anchor the model can cite."""
    return datetime.now(timezone.utc).strftime("%B %d, %Y")


class PromptKit:
    """The instruction set used by every research run.

    Every method is a ``@staticmethod`` so callers use ``PromptKit.method(...)``
    without holding an instance. ``__init__`` still accepts a config because a
    few call sites construct one, but no method depends on it.
    """

    def __init__(self, config: Any = None):
        self.cfg = config

    # --- evidence gathering -------------------------------------------------

    @staticmethod
    def generate_search_queries_prompt(
        question: str,
        parent_query: str,
        report_kind: str,
        max_iterations: int = 3,
        context: List[Dict[str, Any]] = [],
    ) -> str:
        """Ask for ``max_iterations`` plain-language search queries."""
        subject = (
            f"{parent_query} — {question}"
            if report_kind
            in (ReportKind.Expanded.value, ReportKind.Section.value)
            else question
        )

        if context:
            grounding = (
                f"\nWhat is already known about \"{subject}\":\n{context}\n\n"
                "Use those findings to aim the queries more precisely: build on "
                "what they cover and reach for the specifics they leave open — "
                "named figures, dates, events or organisations worth pinning down.\n"
            )
        else:
            grounding = ""

        example = ", ".join(f'"query {i + 1}"' for i in range(max_iterations))

        return (
            f'Produce {max_iterations} web-search queries that together cover the '
            f'research task: "{subject}".\n'
            f"{grounding}\n"
            "Write each query as ordinary descriptive language, the way a person "
            "would type it. Do not use search-engine operators — no site:, "
            "filetype:, inurl:, intitle:, and no OR / AND / NOT — because many "
            "backends ignore or misread them and return nothing.\n\n"
            f"If the task is time-sensitive, treat {_as_of_date()} as the current "
            "date.\n\n"
            "Answer with a bare list of strings and nothing else, in exactly this "
            f"shape: [{example}]\n"
        )

    # --- report bodies ------------------------------------------------------

    @staticmethod
    def generate_report_prompt(
        question: str,
        context,
        evidence_scope: str,
        report_format="apa",
        target_words=1000,
        tone=None,
        language="english",
    ) -> str:
        """Instruction for the full research report (the deep-dive kind)."""
        if evidence_scope == EvidenceScope.Web.value:
            citation_rules = """
End the report with a "## References" heading. List each source exactly once —
no repeats.

Every entry must point at the specific work you used, naming it and linking to it:

    - Title of the specific work (Year). Publisher or site. [link text](full URL)

Cite these rules literally:
- Use the work's real title, taken from the source itself. Never substitute a
  placeholder — no "Author not specified", no "Untitled", no blank author slot.
- Never list an aggregator, repository, index or search page as a reference
  ("Europe PMC", "Semantic Scholar", "Google Scholar", "PubMed", "arXiv:1234",
  or a bare DOI/PMID page with no title). Name the work behind it and link to
  its specific page.
- If an author is named, put them before the year ("Smith, J. (2024). Title.
  Site."). If no author is recoverable, drop the author field entirely rather
  than inventing or placeholder-filling one.
- The visible link text is the title or the site name — never a raw URL.
"""
        else:
            citation_rules = """
End the report with a references list naming every document you drew on. Include
each source once only — no duplicates.
"""

        tone_line = f"Write the whole report in a {tone.value} tone." if tone else ""

        return f"""
Source material:
"{context}"
---

Write a detailed report answering: "{question}"

Make it structured, substantial and specific — include concrete facts and numbers
wherever the sources provide them, and aim for at least {target_words} words. Use
all the relevant material available rather than padding.

Hold to every one of these rules:
- Form your own concrete reading of the evidence. Do not retreat into vague,
  content-free conclusions.
- Write in markdown, formatted to {report_format}.
- Structure with markdown headers — a single # title, ## for major sections, ###
  for subsections.
- Present comparisons and structured data as markdown tables.
- Prefer reliable, authoritative sources over weak ones, and recent reporting
  over stale reporting when the source is trustworthy.
- Do not include a table of contents. Headers alone provide the structure.
- Cite in {report_format} form using markdown links placed right after the
  claim they support, like this: ([in-text citation](url)).
- Every substantive claim, figure or quotation must carry an inline citation to
  a source that actually appears above. Never cite a source that is not present.
- Close with the reference list in {report_format} form.
- Match each claim's strength to its evidence. If something is a method, a
  prototype or a dataset rather than a demonstrated real-world result, say so —
  do not present it as established fact or proven demand.
- {citation_rules}
- {tone_line}
Write the report in {language}.
Treat {date.today().strftime('%d %B %Y')} as today's date.
"""

    @staticmethod
    def generate_section_prompt(
        current_subtopic,
        existing_headers: list,
        relevant_written_contents: list,
        main_topic: str,
        context,
        report_format: str = "apa",
        max_subsections=5,
        target_words=800,
        tone: Tone = Tone.Objective,
        language: str = "english",
    ) -> str:
        """Instruction for one section of a larger report (no intro or conclusion)."""
        return f"""
Context:
"{context}"

You are writing one section of a larger report.
Main topic: {main_topic}
Your subtopic: {current_subtopic}

Using the freshest evidence available, write this subtopic in depth. Use at most
{max_subsections} subsections.

What to cover:
- Answer the subtopic directly, in a structured, informative way, with the facts
  and figures the sources support.
- Write in markdown following the {report_format.upper()} format.
- Use markdown tables for comparisons and structured data.
- Prefer primary, authoritative sources (government bodies, statistics agencies,
  peer-reviewed work, official reports) over secondhand summaries and blog posts.
  Never state a number, statistic or incident without citing a source above; if
  the only backing is weak or secondhand, say so plainly ("according to X, a
  secondary source") or leave it out.
- Any sentence carrying a number, a date or a factual assertion ends with an
  inline citation to the exact source behind it. Do not stretch one citation
  across claims from different sources, and do not cite a source for something
  it does not say.

Do not repeat the rest of the report:
- This matters: your section must add new material, not restate what is already
  written.
- Read the existing headers and written content below before you begin.
- Do not reuse an existing header as a subsection header.
- Do not reproduce facts, examples or arguments already present, in any wording.
- State each statistic, example or argument once, cite it, then build on it —
  never re-state it in a later paragraph.
- If you add nested subsections, make sure they too are new.
- The "Existing written content" below is part of THIS report. Refer back to it in
  passing if useful ("as the previous section showed…"), but do not redevelop its
  arguments, repeat its figures, or re-cite its sources for the same point.

Stay on your subtopic:
- Write only about "{current_subtopic}". Every paragraph should serve it.
- Do not wander into adjacent subjects, unrelated sector examples or regions that
  do not bear on this subtopic. A weakly-related finding belongs elsewhere or is
  best omitted — if you keep it, say in one line why it matters.

Comparisons and rankings:
- If the subtopic compares items, ranks them, or claims something is
  "most/most important/best", state the criteria up front and judge every item by
  the same criteria. Claiming X is the most demanding requires evidence measuring
  X against its alternatives on the same scale — naming X alone is not evidence.

Readability:
- Keep paragraphs to 4-5 sentences at most; one idea each.
- Prefer plain language. Define an unavoidable technical term once, in plain
  words, and do not stack jargon.
- Lead each subsection with its point, then the support — do not bury the finding.

"Existing subtopic reports" — headers already used:

    {existing_headers}

"Existing subtopic reports" — content already written:

    {relevant_written_contents}

Formatting:
- This is one part of a larger report: include only the body, split into suitable
  subsections. Add no introduction and no conclusion.

- Link to source URLs inline wherever you reference them, e.g.:

    ### Section heading

    This is sample prose ([in-text citation](url)).

- Use ## for your subtopic heading and ### for subsections.
- Do not use the largest heading (H1) — that belongs to the whole report.
- Organise the content so it complements, rather than overlaps, the existing
  sections.
- If a new subsection is close to one already written, give it the real name of
  what it covers and note the difference in your own words. Never emit a
  placeholder heading like "New header" or "similar to existing header".

Citations:
- Every inline link must point at a specific publication or page — its title,
  author/site and URL — not a database, a search page or a repository home.
  Never cite "Google Scholar", a results page, or a title-less DOI page.
- If you cannot identify the specific work behind a figure, attribute it to the
  source reporting it ("according to <named source>") or omit it. Never leave a
  number unattributed.

Dates:
- Treat {_as_of_date()} as the current date if needed. Give every time-sensitive
  statement an explicit as-of date drawn from the evidence ("as of March 2026"),
  never a vague "currently" or "today". Never describe a future state or an
  undated event as though it has already occurred.

Finally:
- Write in {language}.
- Keep the focus on the main topic; exclude anything unrelated to it.
- Include no introduction, conclusion, summary or reference section.
- Cite in {report_format.upper()} form using markdown links placed right after
  the claim, like this: ([in-text citation](url)).
- Where a subsection overlaps an earlier one, title it with its real subject and
  explain the distinction in prose — do not write "New header" or "similar to
  existing header".
- Minimum length {target_words} words.
- Keep the tone {tone.value} throughout.

Do not add a conclusion section.
"""

    @staticmethod
    def generate_report_introduction(
        question: str,
        research_summary: str = "",
        language: str = "english",
        report_format: str = "apa",
    ) -> str:
        return f"""{research_summary}

Using the material above, write the introduction to a report on: {question}.
- Keep it short, clear and structured, in markdown.
- It becomes part of a larger report, so include nothing else a report would
  normally contain.
- Start with an H1 heading naming the whole report's subject.
- Cite in {report_format.upper()} form using markdown links placed right after
  the claim, like this: ([in-text citation](url)).
- Length: 2-3 short paragraphs. Say what the question is, why it matters and what
  the report covers — then stop. Do not summarise findings (the body does that)
  and do not preview numbers.
- Do not repeat the H1 as an "## Introduction" heading — the report template adds
  that itself.
Treat {_as_of_date()} as the current date if needed.
Write in {language}.
"""

    @staticmethod
    def generate_report_conclusion(
        query: str,
        report_content: str,
        language: str = "english",
        report_format: str = "apa",
    ) -> str:
        return f"""
The research task was: {query}

The report body so far:

{report_content}

Write a short conclusion drawing out what the findings mean.

The conclusion should:
1. Revisit the report's main thrust.
2. Bring out the single most important findings.
3. Say what follows from them — implications, open questions, or next steps.
4. Run about 2-3 paragraphs.

Requirements:
- Synthesise; do not re-list. Do not repeat statistics or arguments already made
  in the body — gesture at them and draw the implication instead.
- Do not restate the introduction. The conclusion answers "so what?", not "what?".
- Do not emit a "## Introduction" heading, and do not repeat a "## Conclusion"
  heading — the template adds it.

If the body has no "## Conclusion" title at its end, put one at the top of your
output.
Cite in {report_format.upper()} form using markdown links placed right after the
claim, like this: ([in-text citation](url)).

Write the conclusion entirely in {language}.

Conclusion:
"""

    # --- curation and rendering ---------------------------------------------

    @staticmethod
    def rank_sources(query, sources, max_results=10) -> str:
        """Ask the model to rank and prune the retrieved sources."""
        return f"""Select the strongest sources for this research task: "{query}"

Keep the sources that carry relevant, high-quality information — above all ones
with statistics, numbers or hard data.

The kept list becomes the context for a research report, so weigh sources this
way:
- Preserve as much original detail as possible, favouring sources with
  quantitative data or unusual insight.
- Include a range of viewpoints and perspectives.
- Drop only what is clearly irrelevant or unusable.

How to judge each source:
1. Relevance — keep sources connected to the task directly or partly; when in
   doubt, keep.
   Credibility — favour authoritative sources, but keep others unless they are
   plainly untrustworthy.
   Recency — prefer recent material unless older data is essential.
   Objectivity — keep biased sources if they add a distinct angle.
   Data — rank sources with statistics or concrete figures highest.
2. Choosing:
   - Return as many useful sources as you can, up to {max_results}, for breadth
     and variety.
   - Prefer sources with statistics, numbers or checkable facts.
   - Overlap is fine if it adds depth, especially where data is involved.
   - Drop a source only if it is entirely irrelevant, badly outdated, or too
     poor to use.
3. Preserving content:
   - Do NOT rewrite, summarise or shorten any source content.
   - Keep all usable information; clean up only obvious noise or broken
     formatting.
   - Keep borderline or partial sources if they hold valuable data.

SOURCES TO EVALUATE:
{sources}

Reply with the sources in the EXACT same JSON list shape you received. No
markdown, no surrounding prose, no ```json fences — just the JSON list.
"""

    @staticmethod
    def persona_instructions() -> str:
        """Instruction for the agent-role selection call."""
        return """
You are choosing a reporting persona for a research task, whatever its topic or
difficulty. Pick a named expert role suited to the task's field, and describe it
in one sentence.

Reply with JSON only, in this shape:
{
    "server": "<an emoji and a short role name, e.g. '💰 Finance Agent'>",
    "agent_role_prompt": "<one sentence describing how this expert writes reports>"
}

Examples of the mapping:
{
    "server": "💰 Finance Agent",
    "agent_role_prompt": "You are a veteran financial analyst writing rigorous, even-handed, well-organised reports grounded in the supplied data and market trends."
}
{
    "server": "📈 Business Analyst Agent",
    "agent_role_prompt": "You are an experienced business analyst producing thorough, balanced, clearly structured reports grounded in the supplied business data, market trends and strategic context."
}
{
    "server": "🌍 Travel Agent",
    "agent_role_prompt": "You are a widely travelled guide writing engaging, balanced, well-structured accounts of places, covering their history, sights and culture."
}
"""

    @staticmethod
    def render_documents(docs: list[Document], top_n: int | None = None) -> str:
        """Render documents as the context block the model reads."""
        return "\n".join(
            f"Source: {d.metadata.get('source')}\n"
            f"Title: {d.metadata.get('title')}\n"
            f"Content: {d.page_content}\n"
            for i, d in enumerate(docs)
            if top_n is None or i < top_n
        )

    @staticmethod
    def merge_scopes(docs_context: str, web_context: str) -> str:
        """Join local-document context with web context (mixed evidence scope)."""
        return f"Context from local documents: {docs_context}\n\nContext from web sources: {web_context}"


# Report kind -> the PromptKit method that builds its prompt.
_KIND_BUILDER = {
    ReportKind.DeepDive.value: "generate_report_prompt",
    ReportKind.Section.value: "generate_section_prompt",
}


def builder_for(
    report_kind: str,
    prompt_family: type[PromptKit] | PromptKit = PromptKit,
):
    """Return the report-building callable for ``report_kind``.

    Unknown kinds resolve to the deep-dive builder, which is a safe default for
    any query.
    """
    method_name = _KIND_BUILDER.get(report_kind) or _KIND_BUILDER[
        ReportKind.DeepDive.value
    ]
    return getattr(prompt_family, method_name)
