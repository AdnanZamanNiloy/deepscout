"""Planner Agent — research strategy as enforceable delegation contracts.

Fixes and upgrades in this version
----------------------------------
1. THE PLAN NOW OBEYS THE ORCHESTRATOR. `final = cleaned[:5]` hard-capped every
   plan at five sub-questions regardless of what the orchestrator computed, so
   the adaptive agent count had no effect on the actual work done. The planner
   takes `target_count` and honours it.

2. REQUIRED AXES ARE ENFORCED, NOT REQUESTED. The prompt has always demanded a
   statistical angle (Phase C) and a criticism angle (Phase D) because those two
   are what separate research from recall. Nothing verified compliance, and
   models routinely returned four background questions. `enforce_axis_coverage`
   deterministically injects any missing required axis.

3. PRIORITY-AWARE TRUNCATION KEEPS DIVERSITY. Trimming a plan by priority alone
   discards whole information types when the model assigns priority 1 to
   everything. Truncation now guarantees axis and search_type spread first, then
   fills by priority.

4. DEPENDENCIES ARE VALIDATED AND USED. `depends_on` was parsed and then
   ignored, including self-references and cycles. It is now sanitized and turned
   into execution waves, so dependent angles can be researched with their
   prerequisite's findings in hand instead of blind.

5. CONTRACTS CARRY SOURCE PREFERENCES. Each contract gains `preferred_domains`
   from the primary-source registry and a `specialist` role, so search can aim
   at publishers and the summarizer can load the right domain overlay.

6. SEMANTIC DEDUP IS ACTUALLY SEMANTIC. The old version normalized text,
   deleted three specific words, and compared for exact equality — which meant
   "solar panel costs 2025" and "cost of solar panels in 2025" both survived as
   distinct angles, wasting a whole agent on a duplicate search.

Refactor note
-------------
The implementation now lives in the `app.agents.planning` package, split into
single-responsibility modules (`types`, `normalize`, `prompts`, `dimensions`,
`contracts`, `plan`, `agent`). This module is the stable facade: it re-exports
every public and private name the rest of the codebase (and the tests) import
from `app.agents.planner`, so the import surface is UNCHANGED.
"""
from __future__ import annotations

from app.core.logging import get_logger

from app.agents.planning.types import (  # noqa: F401
    AXIS_SEARCH_TYPE,
    COUNTER_EVIDENCE_AXIS,
    DEFAULT_MINIMUM_SOURCES,
    DEFAULT_STOP_CONDITION,
    DOMAIN_TO_SPECIALIST,
    FRONTIER_AXES,
    FRONTIER_AXIS_QUESTIONS,
    FRONTIER_AXIS_SET,
    VALID_AXES,
    VALID_DOMAINS,
    VALID_SEARCH_TYPES,
    VALID_TOOLS,
    PlannerOutput,
    SubQuestion,
    _HEURISTIC_DIMENSION_DEFAULTS,
    _HARD_NARROW_TOKENS,
    _REQUIRED_ANGLE_AXIS,
    _REQUIRED_ANGLE_TOKENS,
    _SOFT_NARROW_TOKENS,
    _STRONG_TRENDS_RE,
    _TRENDS_RE,
    _TRENDS_SCOPE_PHRASES,
)
from app.agents.planning.normalize import (  # noqa: F401
    _AXIS_ALIASES,
    _QUESTION_STOPWORDS,
    _clean_str_list,
    _question_signature,
    _question_similarity,
    axis_search_type,
    deduplicate_semantic,
    dimension_to_axis,
    diversity_coverage,
    is_valid_question,
    normalize_domain,
    normalize_text,
)
from app.agents.planning.prompts import (  # noqa: F401
    PLANNER_SYSTEM_PROMPT,
    PLANNING_DIRECTIVE_PROMPT,
)
from app.agents.planning.dimensions import (  # noqa: F401
    _angle_token_covered,
    _coarse_query_type,
    _dedupe_exact_dimensions,
    _heuristic_dimensions,
    _validated_heuristic_dimensions,
    plan_dimensions,
    validate_plan_dimensions,
)
from app.agents.planning.contracts import (  # noqa: F401
    _AXIS_FALLBACK_TEMPLATES,
    _QUESTION_FILLER_WORDS,
    _REANGLE_SEARCH_TYPES,
    _alternate_search_type,
    _assign_intent_senses,
    _contract,
    _intent_research_senses,
    _query_concept,
    _sense_concept,
    _subject_phrase,
    _trends_scope_wanted,
    _widen_dimension_query,
    _year_from,
    enforce_axis_coverage,
    enforce_frontier_axes,
    gap_contracts,
    synthesize_dimension_contract,
)
from app.agents.planning.plan import (  # noqa: F401
    execution_waves,
    fallback_plan,
    sanitize_dependencies,
    select_plan,
)
from app.agents.planning.agent import planner_agent  # noqa: F401

# The module's original logger, kept for import-surface parity.
logger = get_logger(__name__)

__all__ = [
    "SubQuestion",
    "PlannerOutput",
    "planner_agent",
    "fallback_plan",
    "select_plan",
    "sanitize_dependencies",
    "execution_waves",
    "plan_dimensions",
    "validate_plan_dimensions",
    "enforce_axis_coverage",
    "enforce_frontier_axes",
    "gap_contracts",
    "synthesize_dimension_contract",
    "normalize_text",
    "normalize_domain",
    "dimension_to_axis",
    "deduplicate_semantic",
]
