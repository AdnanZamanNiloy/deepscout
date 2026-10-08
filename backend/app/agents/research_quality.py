"""Research quality: temporal profiles, independence, grounding and overclaiming.

Refactor note
-------------
The implementation now lives in the `app.agents.quality` package, split into
single-responsibility modules (`primitives`, `temporal`, `independence`,
`factual`, `quality`). This module is the stable facade: it re-exports every
public and private name the rest of the codebase imports from
`app.agents.research_quality`, so the import surface is UNCHANGED.
"""
from __future__ import annotations

from app.agents.quality.primitives import (  # noqa: F401
    _safe_float,
    _safe_int,
    _TRIVIAL_NUMBERS,
    _significant_values,
    _values_match,
    _is_year,
    _now,
    _markers,
    _strip_markers,
)
from app.agents.quality.temporal import (  # noqa: F401
    _DATE_PATTERNS,
    _TIME_SENSITIVE_TYPES,
    _STALE_DAYS_SENSITIVE,
    _STALE_DAYS_GENERAL,
    _parse_date,
    _fact_date,
    TemporalProfile,
    temporal_profile,
)
from app.agents.quality.independence import (  # noqa: F401
    _ECHO_SIMILARITY,
    IndependenceReport,
    assess_independence,
    apply_independence,
    independent_corroboration,
)
from app.agents.quality.factual import (  # noqa: F401
    _MONTHS,
    _NUMERIC_HINT_RE,
    _DATE_WORD_RE,
    _QUOTE_RE,
    _PROPER_NOUN_RE,
    _COMMON_CAPS,
    _ATTRIBUTION_RE,
    _EVENT_VERB_RE,
    is_factual_sentence,
)
from app.agents.quality.quality import (  # noqa: F401
    QualityFinding,
    _HEDGE_RE,
    _ASSERTIVE_RE,
    _STRONG_CLAIM_RE,
    check_citation_grounding,
    _sentence_units,
    detect_overclaims,
    _TOPIC_OVERLAP,
    detect_internal_conflicts,
    section_coverage,
    _ESTIMATIVE_BANDS,
    estimative_band,
    _CALIBRATION_CONTRACT,
    _PREMISE_CONTRACT,
    render_quality_contract,
    ResearchQualityReport,
    assess_report_quality,
    _guarded,
)

__all__ = [
    "TemporalProfile",
    "IndependenceReport",
    "QualityFinding",
    "ResearchQualityReport",
    "temporal_profile",
    "assess_independence",
    "apply_independence",
    "independent_corroboration",
    "is_factual_sentence",
    "check_citation_grounding",
    "detect_overclaims",
    "detect_internal_conflicts",
    "section_coverage",
    "assess_report_quality",
    "render_quality_contract",
    "estimative_band",
]
