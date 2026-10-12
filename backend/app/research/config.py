"""Runtime settings for the research pipeline.

Model selection is NOT configured here — every generation call goes through
:class:`app.core.llm.LLMClient`, which owns the provider, model, temperature and
retry policy selected in the Model Control Center. Retrieval likewise runs
through :class:`app.agents.search.SearchClient`. What remains here are the few
knobs that shape *how* a run is composed: report length, language, how many
sources to gather per query, and how much content to hand the model.

Values come from :class:`app.core.config.Settings` (env + .env) with
conservative defaults, so a run is fully specified even with no extra
configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0"
)


@dataclass
class ResearchConfig:
    """Composition settings for a single research run.

    Constructed fresh per run (see :class:`app.research.runner.ResearchRunner`)
    so a caller can override any field for one request without touching global
    state.
    """

    # Report shape.
    total_words: int = 1200
    report_format: str = "APA"
    language: str = "english"
    report_source: str = "web"

    # Token budgets handed to the model per call site. The client clamps these
    # against the selected provider's limits, so they are upper bounds, not
    # hard requirements.
    smart_token_limit: int = 12000
    strategic_token_limit: int = 8000

    # Retrieval.
    max_search_results_per_query: int = 5
    max_iterations: int = 3

    # Context filtering: auto | keyword | none. "auto" resolves to keyword
    # (BM25) — the dependency-free default. The heavier embedding/API-backed
    # filters were dropped with the retrieval consolidation; keyword is the
    # mode the live path uses.
    context_filter: str = "auto"
    compression_threshold: int = 8000
    keyword_relative_threshold: float = 0.5
    keyword_max_results: int = 25

    # Scraping of pages that still need fetching (retrieval pre-fetches most).
    user_agent: str = _DEFAULT_USER_AGENT
    max_scraper_workers: int = 15
    scraper_rate_limit_delay: float = 0.0

    # How many report sections are researched at once. Sections in a wave cannot
    # see each other's drafts; a smaller number means tighter dedup (later waves
    # reference earlier ones) at the cost of latency.
    max_parallel_agents: int = 3

    # Prompt family: "default" only. Kept for forward compatibility with
    # model-specific prompt sets.
    prompt_family: str = "default"

    # Local-source mode: directory to walk when report_source is local/hybrid.
    doc_path: str = "./my-docs"

    # Whether to run the model-based source-curation pass before writing.
    curate_sources: bool = False

    # Optional explicit agent role; None lets the pipeline pick one.
    agent_role: Optional[str] = None

    verbose: bool = False


def load_config(**overrides) -> ResearchConfig:
    """Build a :class:`ResearchConfig` from app settings with per-call overrides.

    Values come from :class:`app.core.config.Settings` (the single source of
    truth for configuration — env + .env; AGENTS.md rule 5). Unknown override
    keys are ignored so a caller can pass a loose dict without a TypeError.
    """
    from app.core.config import get_settings

    s = get_settings()
    base = {
        "total_words": s.research_total_words,
        "report_format": s.research_report_format,
        "language": s.research_language,
        "report_source": s.research_report_source,
        "smart_token_limit": s.research_smart_token_limit,
        "strategic_token_limit": s.research_strategic_token_limit,
        "max_search_results_per_query": s.research_max_results_per_query,
        "max_iterations": s.max_iterations,
        "context_filter": s.research_context_filter,
        "compression_threshold": s.research_compression_threshold,
        "keyword_relative_threshold": s.research_keyword_relative_threshold,
        "keyword_max_results": s.research_keyword_max_results,
        "user_agent": s.research_user_agent,
        "max_scraper_workers": s.research_max_scraper_workers,
        "scraper_rate_limit_delay": s.research_scraper_rate_limit_delay,
        "max_parallel_agents": s.max_parallel_agents,
        "doc_path": s.research_doc_path,
        "curate_sources": s.research_curate_sources,
        "prompt_family": "default",
        "agent_role": None,
    }
    base.update({k: v for k, v in overrides.items() if k in ResearchConfig.__dataclass_fields__})
    return ResearchConfig(**base)
