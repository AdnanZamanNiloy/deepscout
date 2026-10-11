"""Research conductor skill.

Drives a single researcher's evidence gathering: initial search, sub-query
planning, per-sub-query retrieval and page fetching, context filtering, and
optional source curation. This is the web path the product uses; local and
hybrid source modes are supported in reduced form (no embedding vector store).
MCP, Azure and vector-store retrieval are not carried — the product's retrieval
is the unified search client.
"""
from __future__ import annotations

import asyncio
import logging
import random

import json_repair

from app.research.documents import DocumentLoader, OnlineDocumentLoader
from app.research.enums import ReportSource
from app.research.streaming import stream_output

logger = logging.getLogger(__name__)


def _normalize_sub_queries(parsed, fallback_query: str) -> list[str]:
    """Coerce a parsed model response into a flat list of query strings.

    The model may return a list, a dict (``{"queries": [...]}`` or a single
    ``{"query": "..."}``), a bare string, or nothing. Callers expect ``list[str]``
    and otherwise crash on ``.append`` / iteration, so normalize defensively.
    """
    if isinstance(parsed, dict):
        for key in ("queries", "sub_queries", "subQueries", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break
        else:
            single = parsed.get("query")
            parsed = [single] if isinstance(single, str) else []

    if isinstance(parsed, str):
        parsed = [parsed] if parsed.strip() else []

    if not isinstance(parsed, list):
        parsed = []

    queries = [str(item).strip() for item in parsed if str(item).strip()]
    if not queries and fallback_query.strip():
        return [fallback_query.strip()]
    return queries


class ResearchConductor:
    """Gather evidence for the owning researcher."""

    def __init__(self, researcher):
        self.researcher = researcher
        self.logger = logging.getLogger("research")
        self._initial_search_results = None

    async def _get_initial_search_results(self, query, query_domains=None):
        await stream_output(
            "logs",
            "planning_research",
            f"Browsing the web to learn more about the task: {query}...",
            self.researcher.sink,
        )
        retriever = self.researcher.retrievers[0]
        search_results = await self._search(retriever, query, query_domains)
        self.logger.info("Initial search results obtained: %s results", len(search_results))
        return search_results

    async def plan_research(self, query, query_domains=None, *, search_results=None):
        """Plan sub-queries, optionally from results already obtained for this pass."""
        if search_results is None:
            search_results = await self._get_initial_search_results(query, query_domains)

        await stream_output(
            "logs",
            "planning_research",
            "Planning the research strategy and subtasks...",
            self.researcher.sink,
        )
        outline = await self._plan_research_outline(query, search_results)
        self.logger.info("Research outline planned: %s", outline)
        return outline

    async def conduct_research(self):
        """Run the full evidence-gathering flow and return the context."""
        self.logger.info("Starting research for query: %s", self.researcher.query)

        research_data = []
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "starting_research",
                f"Starting the research task for '{self.researcher.query}'...",
                self.researcher.sink,
            )
            await stream_output(
                "logs",
                "agent_generated",
                self.researcher.agent,
                self.researcher.sink,
            )

        # Choose agent + role if not already set.
        if not (self.researcher.agent and self.researcher.role):
            await self.researcher.choose_agent()

        if self.researcher.source_urls:
            self.logger.info("Using provided source URLs")
            research_data = await self._get_context_by_urls(self.researcher.source_urls)
            if not research_data and self.researcher.verbose:
                await stream_output(
                    "logs",
                    "answering_from_memory",
                    "Unable to find relevant context in the provided sources...",
                    self.researcher.sink,
                )
        elif self.researcher.report_source == ReportSource.Web.value:
            self.logger.info("Using web search")
            research_data = await self._get_context_by_web_search(
                self.researcher.query, [], self.researcher.query_domains
            )
        elif self.researcher.report_source == ReportSource.Local.value:
            self.logger.info("Using local search")
            document_data = await DocumentLoader(self.researcher.cfg.doc_path).load()
            research_data = await self._get_context_by_web_search(
                self.researcher.query, document_data, self.researcher.query_domains
            )
        elif self.researcher.report_source == ReportSource.Hybrid.value:
            if self.researcher.document_urls:
                document_data = await OnlineDocumentLoader(self.researcher.document_urls).load()
            else:
                document_data = await DocumentLoader(self.researcher.cfg.doc_path).load()
            docs_context, web_context = await asyncio.gather(
                self._get_context_by_web_search(
                    self.researcher.query, document_data, self.researcher.query_domains
                ),
                self._get_context_by_web_search(
                    self.researcher.query, [], self.researcher.query_domains
                ),
            )
            research_data = self.researcher.prompt_family.join_local_web_documents(
                docs_context, web_context
            )

        self.researcher.context = research_data

        if self.researcher.cfg.curate_sources:
            self.logger.info("Curating sources")
            curated = await self.researcher.source_curator.curate_sources(research_data)
            if isinstance(curated, list):
                self.researcher.context = "\n\n".join(
                    "Title: {title}\nContent: {content}\nSource: {source}".format(
                        title=s.get("Title", "") if isinstance(s, dict) else "",
                        content=s.get("Content", "") if isinstance(s, dict) else "",
                        source=s.get("Source", "") if isinstance(s, dict) else "",
                    )
                    if isinstance(s, dict)
                    else str(s)
                    for s in curated
                )
            else:
                self.researcher.context = curated

        if self.researcher.verbose:
            await stream_output(
                "logs",
                "research_step_finalized",
                "Finalized research step.",
                self.researcher.sink,
            )
        self.logger.info(
            "Research completed. Context size: %s", len(str(self.researcher.context))
        )
        return self.researcher.context

    async def _get_context_by_urls(self, urls):
        """Fetch and filter context from an explicit list of URLs."""
        new_search_urls = await self._get_new_urls(urls)
        scraped_content = await self.researcher.scraper_manager.browse_urls(new_search_urls)
        return await self.researcher.context_manager.get_similar_content_by_query(
            self.researcher.query, scraped_content
        )

    async def _get_context_by_web_search(
        self, query, scraped_data: list | None = None, query_domains: list | None = None
    ):
        """Search ``query``, fetch results, and return filtered context."""
        self.logger.info("Starting web search for query: %s", query)
        if scraped_data is None:
            scraped_data = []
        if query_domains is None:
            query_domains = []

        initial_results = await self._get_initial_search_results(query, query_domains)
        sub_queries = await self.plan_research(
            query, query_domains, search_results=initial_results
        )
        self.logger.info("Generated sub-queries: %s", sub_queries)

        if self.researcher.report_type != "subtopic_report":
            sub_queries.append(query)

        if self.researcher.verbose:
            await stream_output(
                "logs",
                "subqueries",
                f"I will conduct my research based on the following queries: {sub_queries}...",
                self.researcher.sink,
            )

        try:
            # Read the sources that informed planning before later searches can
            # lose them. Keep supplied-document paths unchanged.
            initial_context = ""
            if not scraped_data and initial_results:
                initial_context = await self._get_context_from_initial_results(
                    query, query_domains, initial_results
                )
            context = await asyncio.gather(
                *[
                    self._process_sub_query(sub_query, scraped_data, query_domains)
                    for sub_query in sub_queries
                ]
            )
            self.logger.info("Gathered context from %s sub-queries", len(context))
            context = [c for c in [initial_context, *context] if c]
            if context:
                return " ".join(context)
            return []
        except Exception as e:
            self.logger.error("Error during web search: %s", e, exc_info=e)
            return []

    async def _process_sub_query(
        self, sub_query: str, scraped_data: list = [], query_domains: list = []
    ):
        """Search one sub-query, fetch its sources, and return filtered context."""
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "running_subquery_research",
                f"Running research for '{sub_query}'...",
                self.researcher.sink,
            )
        try:
            if not scraped_data:
                scraped_data = await self._scrape_data_by_urls(sub_query, query_domains)

            web_context = ""
            if scraped_data:
                web_context = await self.researcher.context_manager.get_similar_content_by_query(
                    sub_query, scraped_data
                )

            if not web_context and self.researcher.verbose:
                await stream_output(
                    "logs",
                    "subquery_context_not_found",
                    f"No content found for '{sub_query}'...",
                    self.researcher.sink,
                )
            return web_context
        except Exception as e:
            self.logger.error("Error processing sub-query %s: %s", sub_query, e, exc_info=e)
            return ""

    async def _get_context_from_initial_results(self, query, query_domains, initial_results):
        """Fetch the planning sources through normal retrieval."""
        try:
            data = await self._scrape_data_by_urls(
                query, query_domains, initial_results=initial_results
            )
            if data:
                return await self.researcher.context_manager.get_similar_content_by_query(
                    query, data
                )
        except Exception as e:
            # An unavailable planning source must not prevent follow-up research.
            self.logger.warning("Error reading initial search results: %s", e)
        return ""

    async def _search_relevant_source_urls(
        self, query, query_domains: list | None = None, *, initial_results=None
    ):
        """Search the retrievers for URLs; split into to-fetch vs pre-fetched."""
        new_search_urls = []
        prefetched_content = []
        if query_domains is None:
            query_domains = []

        retrievers = (
            self.researcher.retrievers
            if initial_results is None
            else self.researcher.retrievers[:1]
        )
        for retriever_class in retrievers:
            try:
                retriever = retriever_class(query, query_domains=query_domains)
                if initial_results is None:
                    search_results = await asyncio.to_thread(
                        retriever.search,
                        self.researcher.cfg.max_search_results_per_query,
                    )
                else:
                    search_results = initial_results

                if not search_results:
                    continue

                requires_scraping = getattr(retriever, "requires_scraping", None)

                for result in search_results:
                    url = result.get("href") or result.get("url")
                    raw_content = result.get("raw_content")
                    if not url:
                        continue

                    if requires_scraping is True:
                        new_search_urls.append(url)
                    elif requires_scraping is False:
                        if raw_content:
                            prefetched_content.append({"url": url, "raw_content": raw_content})
                            self.researcher.add_research_sources([{"url": url}])
                            self.researcher.visited_urls.add(url)
                        else:
                            new_search_urls.append(url)
                    elif raw_content and len(raw_content) > 100:
                        prefetched_content.append({"url": url, "raw_content": raw_content})
                        self.researcher.add_research_sources([{"url": url}])
                        self.researcher.visited_urls.add(url)
                    else:
                        new_search_urls.append(url)
            except Exception as e:
                self.logger.error("Error searching with %s: %s", retriever_class.__name__, e)

        new_search_urls = await self._get_new_urls(new_search_urls)
        random.shuffle(new_search_urls)
        return new_search_urls, prefetched_content

    async def _scrape_data_by_urls(
        self, sub_query, query_domains: list | None = None, *, initial_results=None
    ):
        """Search sub-query across retrievers and fetch the resulting URLs."""
        if query_domains is None:
            query_domains = []

        new_search_urls, prefetched_content = await self._search_relevant_source_urls(
            sub_query, query_domains, initial_results=initial_results
        )

        if self.researcher.verbose:
            await stream_output(
                "logs",
                "researching",
                "Researching for relevant information across multiple sources...",
                self.researcher.sink,
            )

        scraped_content = await self.researcher.scraper_manager.browse_urls(new_search_urls)
        scraped_content.extend(prefetched_content)
        return scraped_content

    async def _get_new_urls(self, url_set_input):
        """Return URLs not yet visited, marking them visited."""
        new_urls = []
        for url in url_set_input:
            if url not in self.researcher.visited_urls:
                self.researcher.visited_urls.add(url)
                new_urls.append(url)
                if self.researcher.verbose:
                    await stream_output(
                        "logs",
                        "added_source_url",
                        f"Added source url to research: {url}",
                        self.researcher.sink,
                    )
        return new_urls

    async def _search(self, retriever_class, query, query_domains=None):
        """Run one retriever's search from a worker thread."""
        retriever = retriever_class(query, query_domains=query_domains)
        try:
            return await asyncio.to_thread(
                retriever.search, self.researcher.cfg.max_search_results_per_query
            )
        except Exception as e:
            self.logger.error("Error searching with %s: %s", retriever_class.__name__, e)
            return []

    async def _plan_research_outline(self, query, search_results):
        """Ask the strategic model for the sub-queries to research."""
        prompt = self.researcher.prompt_family.generate_search_queries_prompt(
            query,
            self.researcher.parent_query,
            self.researcher.report_type,
            max_iterations=self.researcher.cfg.max_iterations,
            context=search_results,
        )
        response = await self.researcher.llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.researcher.cfg.strategic_token_limit,
            cost_callback=self.researcher.add_costs,
        )
        try:
            parsed = json_repair.loads(response) if response else None
        except Exception:
            parsed = None
        return _normalize_sub_queries(parsed, query)
