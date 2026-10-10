"""Retriever factory and utilities for GPT Researcher.

This module provides functions to instantiate and manage various
search retriever implementations.
"""

import logging
from importlib.metadata import entry_points

logger = logging.getLogger(__name__)

#: Entry-point group that third-party packages use to register retrievers.
RETRIEVER_ENTRY_POINT_GROUP = "gpt_researcher.retrievers"


def get_retriever(retriever: str):
    """Get a retriever class by name.

    Args:
        retriever: The name of the retriever to get (e.g., 'google', 'tavily', 'duckduckgo').

    Returns:
        The retriever class if found, None otherwise.

    Supported retrievers:
        - google: Google Custom Search
        - searx: SearX search engine
        - searchapi: SearchAPI service
        - serpapi: SerpAPI service
        - serper: Serper API
        - duckduckgo: DuckDuckGo search
        - bing: Bing search
        - brave: Brave Search API
        - arxiv: arXiv academic search
        - tavily: Tavily search API
        - exa: Exa search
        - crw: fastCRW search (Firecrawl-compatible web scraper)
        - semantic_scholar: Semantic Scholar academic search
        - pubmed_central: PubMed Central medical literature
        - openalex: OpenAlex scholarly works catalog
        - custom: Custom user-defined retriever
        - mcp: Model Context Protocol retriever
        - xquik: Xquik X/Twitter search
        - getxapi: GetXAPI X/Twitter search

    Any other name is looked up among installed plugins registered under the
    ``gpt_researcher.retrievers`` entry-point group. Built-in names always win.
    """
    match retriever:
        case "google":
            from gptr.retrievers import GoogleSearch

            return GoogleSearch
        case "searx":
            from gptr.retrievers import SearxSearch

            return SearxSearch
        case "searchapi":
            from gptr.retrievers import SearchApiSearch

            return SearchApiSearch
        case "serpapi":
            from gptr.retrievers import SerpApiSearch

            return SerpApiSearch
        case "serper":
            from gptr.retrievers import SerperSearch

            return SerperSearch
        case "duckduckgo":
            from gptr.retrievers import Duckduckgo

            return Duckduckgo
        case "bing":
            from gptr.retrievers import BingSearch

            return BingSearch
        case "brave":
            from gptr.retrievers import BraveSearch

            return BraveSearch
        case "bocha":
            from gptr.retrievers import BoChaSearch

            return BoChaSearch
        case "arxiv":
            from gptr.retrievers import ArxivSearch

            return ArxivSearch
        case "tavily":
            from gptr.retrievers import TavilySearch

            return TavilySearch
        case "groundroute":
            from gptr.retrievers import GroundRouteSearch

            return GroundRouteSearch
        case "exa":
            from gptr.retrievers import ExaSearch

            return ExaSearch
        case "crw":
            from gptr.retrievers import CRWRetriever

            return CRWRetriever
        case "semantic_scholar":
            from gptr.retrievers import SemanticScholarSearch

            return SemanticScholarSearch
        case "pubmed_central":
            from gptr.retrievers import PubMedCentralSearch

            return PubMedCentralSearch
        case "custom":
            from gptr.retrievers import CustomRetriever

            return CustomRetriever
        case "mcp":
            from gptr.retrievers import MCPRetriever

            return MCPRetriever
        case "xquik":
            from gptr.retrievers import XquikSearch

            return XquikSearch
        case "openalex":
            from gptr.retrievers import OpenAlexSearch

            return OpenAlexSearch
        case "getxapi":
            from gptr.retrievers import GetXAPISearch

            return GetXAPISearch

        case _:
            return _load_plugin_retriever(retriever)


def _load_plugin_retriever(name: str):
    """Load a retriever class that an installed package registered by name."""
    for entry_point in entry_points(group=RETRIEVER_ENTRY_POINT_GROUP, name=name):
        try:
            return entry_point.load()
        except Exception as exc:
            logger.warning(f"Failed to load retriever plugin '{name}' ({entry_point.value}): {exc}")
            return None
    return None


def get_retrievers(headers: dict[str, str], cfg):
    """
    Determine which retriever(s) to use based on headers, config, or default.

    Args:
        headers (dict): The headers dictionary
        cfg: The configuration object

    Returns:
        list: A list of retriever classes to be used for searching.
    """
    # Check headers first for multiple retrievers
    if headers.get("retrievers"):
        retrievers = headers.get("retrievers").split(",")
    # If not found, check headers for a single retriever
    elif headers.get("retriever"):
        retrievers = [headers.get("retriever")]
    # If not in headers, check config for multiple retrievers
    elif cfg.retrievers:
        # Handle both list and string formats for config retrievers
        if isinstance(cfg.retrievers, str):
            retrievers = cfg.retrievers.split(",")
        else:
            retrievers = cfg.retrievers
    # If not found, check config for a single retriever
    elif cfg.retriever:
        retrievers = [cfg.retriever]
    # If still not set, use default retriever
    else:
        retrievers = [get_default_retriever().__name__]

    # Strip whitespace from each retriever name so comma-separated lists with
    # spaces (e.g. "tavily, exa" from a header or config) resolve correctly
    # instead of silently falling back to the default retriever.
    retrievers = [r.strip() for r in retrievers if r and r.strip()]

    if not retrievers:
        return [get_default_retriever()]

    # Convert retriever names to actual retriever classes
    # Use get_default_retriever() as a fallback for any invalid retriever names
    retriever_classes = [get_retriever(r) or get_default_retriever() for r in retrievers]
    
    return retriever_classes


def get_default_retriever():
    """Get the default retriever class.

    Returns:
        The TavilySearch retriever class as the default search provider.
    """
    from gptr.retrievers import TavilySearch

    return TavilySearch
