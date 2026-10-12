"""Report generation skill.

Writes the whole-report body, the introduction and the conclusion, and produces
per-section draft titles. Context comes from the researcher's accumulated
evidence; a run whose context is empty abstains with a visible message rather
than fabricating a confident, sourced-looking report.
"""
from __future__ import annotations


from app.research.prompts import get_prompt_by_report_type
from app.common.streaming import stream_output


class ReportGenerator:
    """Generate report text for the owning researcher."""

    def __init__(self, researcher):
        self.researcher = researcher

    async def write_report(
        self,
        existing_headers: list = [],
        relevant_written_contents: list = [],
        ext_context=None,
        custom_prompt: str = "",
    ) -> str:
        """Write the report body from the accumulated context."""
        context = ext_context or self.researcher.context

        # Guard against fabricating a report from nothing: if no research
        # content was gathered, abstain so it is visible.
        ctx_text = "\n".join(context) if isinstance(context, list) else str(context or "")
        if not ctx_text.strip():
            return (
                f'I could not gather any source material for "{self.researcher.query}". '
                "No sources were retrieved (searches may have returned nothing or been "
                "blocked), so I am not able to produce a reliable, sourced report."
            )

        if self.researcher.verbose:
            await stream_output(
                "logs",
                "writing_report",
                f"Writing report for '{self.researcher.query}'...",
                self.researcher.sink,
            )

        cfg = self.researcher.cfg
        if self.researcher.report_type == "subtopic_report":
            prompt_method = get_prompt_by_report_type("subtopic_report", self.researcher.prompt_family)
            content = prompt_method(
                self.researcher.query,
                existing_headers,
                relevant_written_contents,
                self.researcher.parent_query,
                context,
                report_format=cfg.report_format,
                tone=self.researcher.tone,
                total_words=cfg.total_words,
                language=cfg.language,
            )
        elif custom_prompt:
            content = f"{custom_prompt}\n\nContext: {context}"
        else:
            prompt_method = get_prompt_by_report_type(self.researcher.report_type, self.researcher.prompt_family)
            content = prompt_method(
                self.researcher.query,
                context,
                self.researcher.report_source,
                report_format=cfg.report_format,
                tone=self.researcher.tone,
                total_words=cfg.total_words,
                language=cfg.language,
            )

        report = await self._generate(content)
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "report_written",
                f"Report written for '{self.researcher.query}'",
                self.researcher.sink,
            )
        return report

    async def write_report_conclusion(self, report_content: str) -> str:
        """Write the conclusion for a finished report body."""
        prompt = self.researcher.prompt_family.generate_report_conclusion(
            query=self.researcher.query,
            report_content=report_content,
            language=self.researcher.cfg.language,
        )
        return await self.researcher.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": f"{self.researcher.cfg.agent_role or self.researcher.role}"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.25,
            max_tokens=self.researcher.cfg.smart_token_limit,
            cost_callback=self.researcher.add_costs,
        )

    async def write_introduction(self) -> str:
        """Write the report introduction from the accumulated context."""
        prompt = self.researcher.prompt_family.generate_report_introduction(
            question=self.researcher.query,
            research_summary=str(self.researcher.context),
            language=self.researcher.cfg.language,
        )
        return await self.researcher.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": f"{self.researcher.cfg.agent_role or self.researcher.role}"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.25,
            max_tokens=self.researcher.cfg.smart_token_limit,
            cost_callback=self.researcher.add_costs,
        )

    async def _generate(self, content: str) -> str:
        """Single report-generation call with a role-free fallback attempt."""
        messages = [
            {"role": "system", "content": f"{self.researcher.cfg.agent_role or self.researcher.role}"},
            {"role": "user", "content": content},
        ]
        report = await self.researcher.llm.create_chat_completion(
            messages=messages,
            temperature=0.35,
            max_tokens=self.researcher.cfg.smart_token_limit,
            cost_callback=self.researcher.add_costs,
        )
        if report:
            return report
        # Retry folding the role into the user message: some providers reject an
        # empty or unusual system role.
        return await self.researcher.llm.create_chat_completion(
            messages=[
                {
                    "role": "user",
                    "content": f"{self.researcher.cfg.agent_role or self.researcher.role}\n\n{content}",
                },
            ],
            temperature=0.35,
            max_tokens=self.researcher.cfg.smart_token_limit,
            cost_callback=self.researcher.add_costs,
        )
