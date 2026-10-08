"""Dynamic Research Depth / Adaptive Expansion (Phase 2.8 + Feature 11).

Refactor note
-------------
The implementation now lives in the `app.core.depth` package, split into
`constants`, `signals`, `checks` and `controller`. This module is the stable
facade: it re-exports every name the rest of the codebase imports from
`app.core.depth_controller`, so the import surface is UNCHANGED.
"""
from __future__ import annotations

from app.core.depth.signals import (  # noqa: F401
    _planned_axes,
    _verified_facts,
    _url_to_axis,
    _axis_coverage,
    _axes_below_threshold,
    _axes_covered,
    _uncovered_axes,
    _severe_contradictions,
    _needs_corroboration_count,
    _summary_claims,
    _high_impact_uncorroborated,
    _exhausted_claim_keys,
    _norm_claim_key,
    _thin_dimensions,
    _axis_imbalance,
)
from app.core.depth.checks import (  # noqa: F401
    _marginal_gain,
    _two_consecutive_stalls,
    _query_key,
    _similar_query,
    _searched_queries,
    _novel_followups,
    _budget_checks,
    _convergence_checks,
    _focus_checks,
    _dimension_attempts_left,
    _actionable_uncovered_axes,
    _confidence_target,
    _min_iterations,
)
from app.core.depth.controller import (  # noqa: F401
    evaluate,
    decide,
    decide_with_checks,
    hard_wall_reached,
    stop_reason,
)
from app.core.depth.constants import (  # noqa: F401
    DECISION,
    DEFAULT_MINIMUM_SOURCES,
    AXIS_DOMINANCE_THRESHOLD,
    MIN_AXES_COVERED,
    MODE_MIN_ITERATIONS,
    SEVERE_SEVERITY,
)
