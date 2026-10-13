"""The research agent.

A :class:`Researcher` is one research unit: given a query (and optionally a
parent query for a report section), it chooses a role, gathers evidence, and
writes a report. The agent team creates one for the initial pass and one per
report section, each with its own visited-URL set.

It talks to the outside world through exactly two seams — the LLM funnel
(:class:`app.research.llm.LLMFunnel`, backed by the Model Control Center's
:class:`~app.core.llm.LLMClient`) and the retriever (:class:`SearchClientRetriever`,
backed by :class:`~app.retrieval.client.SearchClient`). It holds no provider
configuration of its own.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

import json_repair

from app.core.llm import LLMClient
from app.common.logging import get_logger
from app.research.config import ResearchConfig
from app.research.enums import ReportSource, ReportType, Tone
from app.research.llm import LLMFunnel
from app.research.prompts import PromptFamily
from app.research.skills.browser import BrowserManager
from app.research.skills.context import ContextManager
from app.research.skills.curator import SourceCurator
from app.research.skills.researcher import ResearchConductor
from app.research.skills.writer import ReportGenerator
from app.common.streaming import EventSink

logger = get_logger(__name__)

_DEFAULT_AGENT_ROLE = (
    "You are an AI critical thinker research assistant. Your sole purpose is to write "
    "well written, critically acclaimed, objective and structured reports on given text."
)


class Researcher:
    """Gathers evidence for one query and writes its report."""

    def __init__(
        self,
        query: str,
        *,
        llm: LLMClient,
        cfg: ResearchConfig,
        retrievers: List[type],
        sink: Optional[EventSink] = None,
        report_type: str = ReportType.ResearchReport.value,
        report_source: str = ReportSource.Web.value,
        tone: Tone = Tone.Objective,
        parent_query: str = "",
        source_urls: Optional[list] = None,
        document_urls: Optional[list] = None,
        visited_urls: Optional[set] = None,
        verbose: bool = False,
        max_subtopics: int = 5,
        prompt_family: Optional[type[PromptFamily]] = None,
    ):
        self.query = query
        self.llm = LLMFunnel(llm)
        self.cfg = cfg
        self.retrievers = retrievers
        self.sink = sink
        self.report_type = report_type
        self.report_source = report_source or ReportSource.Web.value
        self.tone = tone if isinstance(tone, Tone) else Tone.Objective
        self.parent_query = parent_query
        self.source_urls = source_urls
        self.document_urls = document_urls
        self.query_domains: List[str] = []
        self.visited_urls = visited_urls if visited_urls is not None else set()
        self.verbose = verbose
        self.max_subtopics = max_subtopics
        self.prompt_family = (prompt_family or PromptFamily)(self.cfg)

        # Agent identity, resolved lazily by choose_agent().
        self.agent: Optional[str] = None
        self.role: Optional[str] = self.cfg.agent_role

        # Accumulated state.
        self.context: Any = []
        self.research_sources: List[dict] = []
        self.research_images: List[dict] = []

        # Skills.
        self.context_manager = ContextManager(self)
        self.scraper_manager = BrowserManager(self)
        self.source_curator = SourceCurator(self)
        self.research_conductor = ResearchConductor(self)
        self.report_generator = ReportGenerator(self)

    # --- agent identity -----------------------------------------------------

    async def choose_agent(self) -> None:
        """Pick an agent name + role for the query, falling back to a default."""
        if self.agent and self.role:
            return
        query = f"{self.parent_query} - {self.query}" if self.parent_query else self.query
        response = await self.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": self.prompt_family.auto_agent_instructions()},
                {"role": "user", "content": f"task: {query}"},
            ],
            temperature=0.15,
            cost_callback=self.add_costs,
        )
        server, role = self._parse_agent(response)
        self.agent = server
        self.role = role

    @staticmethod
    def _parse_agent(response: str) -> tuple[str, str]:
        """Extract (server, role) from the model's JSON, with regex fallback."""
        payload = None
        if response:
            try:
                payload = json_repair.loads(response)
            except Exception as exc:
                # A non-JSON config response means "no override", not a failure;
                # logged so a permanently malformed config is visible.
                logger.debug("[Researcher] config response was not JSON", exc_info=exc)
                payload = None
        if isinstance(payload, dict) and payload.get("server") and payload.get("agent_role_prompt"):
            return payload["server"], payload["agent_role_prompt"]

        if response:
            match = re.search(r"{.*}", response, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                    if data.get("server") and data.get("agent_role_prompt"):
                        return data["server"], data["agent_role_prompt"]
                except json.JSONDecodeError:
                    pass
        return "Default Agent", _DEFAULT_AGENT_ROLE

    # --- research -----------------------------------------------------------

    async def conduct_research(self) -> Any:
        """Gather evidence for the query and return the accumulated context."""
        if not self.role:
            await self.choose_agent()
        self.context = await self.research_conductor.conduct_research()
        return self.context

    async def write_report(
        self,
        existing_headers: list = [],
        relevant_written_contents: list = [],
        ext_context=None,
        custom_prompt: str = "",
    ) -> str:
        """Write the report body from accumulated (or external) context."""
        return await self.report_generator.write_report(
            existing_headers=existing_headers,
            relevant_written_contents=relevant_written_contents,
            ext_context=ext_context or self.context,
            custom_prompt=custom_prompt,
        )

    async def write_report_conclusion(self, report_body: str) -> str:
        return await self.report_generator.write_report_conclusion(report_body)

    async def write_introduction(self) -> str:
        return await self.report_generator.write_introduction()

    # --- accumulators -------------------------------------------------------

    def add_research_sources(self, sources: List[dict]) -> None:
        self.research_sources.extend(sources)

    def add_research_images(self, images: List) -> None:
        self.research_images.extend(images)

    def get_research_images(self, top_k: int = 10) -> List:
        return self.research_images[:top_k]

    def add_costs(self, cost: float) -> None:
        """Cost-callback no-op.

        The LLM client accepts a ``cost_callback`` on every call; real spend is
        tracked by the usage ledger, not here, so this only satisfies the
        callback contract.
        """
        return None
