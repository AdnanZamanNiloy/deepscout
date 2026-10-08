"""Synthesis intelligence layer — repetition tracking and refinement.

A deterministic cross-section bookkeeping layer: it tracks claims already used
and TRANSFORMS a bare restatement into a refinement sentence (never silently
dropping it), while leaving a repeat that adds a new analytical dimension
intact. No LLM, no network, no new model.

Refactor note
-------------
The implementation now lives in the `app.core.synthesis_intel` package, split
into single-responsibility modules (`constants`, `tokens`, `moves`, `units`,
`ledger`). This module is the stable facade: it re-exports every name the rest
of the codebase imports from `app.core.synthesis_intelligence`, so the import
surface is UNCHANGED.
"""
from __future__ import annotations

from app.core.synthesis_intel.constants import (  # noqa: F401
    _CITATION_RE,
    _STOPWORDS,
    _SUFFIXES,
    _MECHANISM_RE,
    _IMPLICATION_RE,
    _COMPARISON_RE,
    _UNCERTAINTY_RE,
    _DIMENSIONS,
    MOVES,
    _TRANSITION_BY_MOVE,
    _TOPIC_ANALYSIS,
    _ANALYSIS_BY_MOVE,
    _NEGATION_RE,
    _HEADING_RE,
    _BULLET_RE,
    _SENTENCE_SPLIT_RE,
    _TRIVIAL_RE,
    _MIN_CONTENT_TOKENS,
    RESTATEMENT_SIMILARITY,
    ANCHOR_OVERLAP_MIN,
    ANCHOR_SHARED_MIN,
    _GENERIC_ANCHORS,
    _QUANT_RE,
    _COMPARISON_AXIS_RE,
    _MECHANISM_AXIS_RE,
    _CAUSAL_AXIS_RE,
    _STRATEGIC_AXIS_RE,
    _DECISION_QUERY_RE,
    _CAUSAL_QUERY_RE,
    _COMPARISON_QUERY_RE,
    _MECHANISM_QUERY_RE,
    _MECHANISM_CLAIM_RE,
    _CAUSAL_CLAIM_RE,
    _MOVE_RULES,
    MAX_APPENDED_WORDS,
    _FRAGMENT_RE,
    _LABEL_DELIM_RE,
    _AUXILIARY_VERBS,
    _TRANSITION_MARKERS,
)
from app.core.synthesis_intel.tokens import (  # noqa: F401
    _stem,
    canonical_tokens,
    claim_key,
    claim_polarity,
    analytical_dimensions,
    anchors,
)
from app.core.synthesis_intel.moves import (  # noqa: F401
    _analysis_clause,
    _stable_pick,
    _is_comparative,
    _axis_text,
    _query_text,
    _is_authoritative,
    _choose_move,
    refine_restatement,
)
from app.core.synthesis_intel.units import (  # noqa: F401
    _has_finite_verb,
    _is_already_refined,
    _is_label_bullet,
    _is_refinable_sentence,
    _is_sentence_unit,
    split_report_sections,
    _iter_units,
    _similar_to_any,
    _fuzzy_restatement,
)
from app.core.synthesis_intel.ledger import (  # noqa: F401
    Refinement,
    ClaimUse,
    SynthesisIntelligenceReport,
    ClaimLedger,
    compress_section_text,
    apply_synthesis_intelligence,
    _has_writer_prose,
    _top_repeats,
    logger,
)
