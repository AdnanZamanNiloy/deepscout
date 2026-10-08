"""Epistemics Agent — what the evidence actually licenses you to say.

Refactor note
-------------
The implementation now lives in the `app.agents.epistemic` package, split into
`primitives`, `claims`, `conflicts` and `asymmetry`. This module is the stable
facade: it re-exports every name the rest of the codebase imports from
`app.agents.epistemics`, so the import surface is UNCHANGED.
"""
from __future__ import annotations

from app.agents.epistemic.primitives import (  # noqa: F401
    _safe_float,
    _safe_int,
    _text,
    _guard,
)
from app.agents.epistemic.claims import (  # noqa: F401
    ClaimType,
    _CLAIM_PATTERNS,
    classify_claim,
    EvidenceStandard,
    EVIDENCE_STANDARDS,
    ClaimStandard,
    StandardsReport,
    _independent_sources,
    assess_standards,
)
from app.agents.epistemic.conflicts import (  # noqa: F401
    ConflictKind,
    _SCOPE_GROUPS,
    _TIME_HINT_RE,
    _TIME_VARYING_RE,
    _years_in,
    _fact_year,
    _scope_signature,
    _units_of,
    classify_conflict,
    Resolution,
    _ESTIMATE_RE,
    _MEASURED_RE,
    _side,
    _rule_primary,
    _rule_measured_over_estimated,
    _rule_verified,
    _rule_corroboration,
    _rule_recency,
    _rule_authority,
    _ADJUDICATION_RULES,
    adjudicate,
    _find_fact,
    adjudicate_all,
)
from app.agents.epistemic.asymmetry import (  # noqa: F401
    _COMPARISON_SPLIT_RE,
    _STOPWORDS,
    AsymmetryReport,
    _candidate_entities,
    coverage_asymmetry,
    EpistemicReport,
    assess_epistemics,
)

__all__ = [
    "ClaimType",
    "ConflictKind",
    "Resolution",
    "ClaimStandard",
    "StandardsReport",
    "AsymmetryReport",
    "EpistemicReport",
    "classify_claim",
    "classify_conflict",
    "adjudicate",
    "adjudicate_all",
    "assess_standards",
    "coverage_asymmetry",
    "assess_epistemics",
    "EVIDENCE_STANDARDS",
]
