"""Synthesis Agent — turns a verified evidence pool into a cited report.

This revision keeps every integrity guarantee of the previous version and
fixes the thing that made its output hard to read: it wrote the SAME report
for every question.

What changed and why
--------------------
1. THE REPORT SHAPE NOW MATCHES THE QUESTION. `REQUIRED_SECTIONS` was applied
   unconditionally, so "what is a transformer?" shipped with Counterarguments,
   Open Questions, an Auditable Source Ledger and a three-sentence lecture
   about the absence of a counter-evidence search. A `ReportProfile`
   (`direct` / `brief` / `analytical` / `audit`) now selects which sections are
   mandatory, which are added ONLY when they carry real content, and how
   verbose the findings bullets are. Nothing is hidden: a section is dropped
   only when it would have said "none detected", and any profile below `audit`
   still surfaces limitations and conflicts whenever they actually exist.

2. QUESTION-TYPE AWARE WRITING. A comparison, a how-to, a causal "why", a
   decision and a definition are five different documents. `_format_guidance`
   injects the shape the reader expects (criteria + verdict; ordered steps;
   mechanism chains; options + recommendation; plain definition + analogy)
   instead of the single "angle per section" template.

3. SECTIONS ARE ORDERED, NOT APPENDED. Missing sections used to be appended at
   the END in dict order — a report could end with its Executive Summary.
   `_reorder_sections` puts every section in canonical reading order after
   assembly, keeping the writer's own deep-dive sections in their original
   sequence.

4. ONE EVIDENCE SECTION, NOT THREE. "Evidence Strength", the appendix's
   "## Source ledger" and "Auditable Source Ledger" all shipped together
   because the alias tables overlapped and the appendix ran after the presence
   check. Measured accounting is now merged INTO a single
   "## Evidence & Confidence", and the full ledger appears only in the `audit`
   profile.

5. THE AUDIT NO LONGER GRADES THE MACHINE'S OWN PROSE. Deterministic sections
   are tracked as they are added and stripped before auditing, so their counts
   ("A=3, B=5") stop being reported as ungrounded numbers and their bullets
   stop deflating citation density. Invalid markers are dropped BEFORE the
   audit, so the density printed in the report describes the text that shipped.

6. THE LENGTH BAND IS ACTUALLY ENFORCED. The appendix and legend were appended
   after `_trim_to_band`. The tail is now measured first and subtracted from
   the budget, and only writer sections are trimmed — a required section can
   never be silently emptied.

7. ONE FINALIZE PATH. The single-pass and section-wise paths duplicated ~40
   lines of assembly that had already drifted. Both now call `_finalize`.

8. CORRECTNESS FIXES: `_compress_to_themes` no longer discards the
   corroboration count it just incremented; `_value_grounded` no longer accepts
   a wrong year (relative tolerance made 2024 ≈ 2025); `_scrub_pipeline_telemetry`
   is genuinely sentence-granular (it was deleting whole paragraphs, because
   sanitization joins a paragraph into one line); every `int()` on external
   data is guarded; the legend cap no longer silently discards facts.

Refactor note
-------------
The implementation now lives in the `app.agents.synthesis` package, split into
single-responsibility modules (`orchestrator`, `finalize`, `section_writer`,
`citations`, `sections`, `required_sections`, `context_blocks`, `deterministic`,
`ranking`, `findings`, `postprocess`, `primitives`, `types`, `profiles`,
`prompts`). This module is the stable facade: it re-exports every public and
private name the rest of the codebase (and the tests) import from
`app.agents.synthesizer`, so the import surface is UNCHANGED.

Backward compatibility: `synthesizer_agent(llm, query, facts, context=None)`
still returns the report as a plain string, and `synthesize()` still returns
`SynthesisResult` with all its previous fields (plus `profile` and
`word_count`). One rename: the mandatory-section key "Evidence Strength" is now
"Evidence & Confidence" (the old name remains a recognized alias).
"""
from __future__ import annotations

from app.core.logging import get_logger

from app.agents.synthesis.types import (  # noqa: F401
    CitationAudit,
    SynthesisResult,
)
from app.agents.synthesis.primitives import (  # noqa: F401
    _ANALYSIS_LEAD_RE,
    _DISAMBIG_LINE_RE,
    _FACTUAL_HINT_RE,
    _HEADING_RE,
    _TRIVIAL_NUMBERS,
    _corroboration,
    _safe_float,
    _safe_int,
)
from app.agents.synthesis.profiles import (  # noqa: F401
    PROFILE_ANALYTICAL,
    PROFILE_AUDIT,
    PROFILE_BRIEF,
    PROFILE_DIRECT,
    PROFILES,
    _CONTESTED_QUERY_TYPES,
    _FORMAT_GUIDANCE,
    _format_guidance,
    _LIGHTWEIGHT_QUERY_TYPES,
    _QUERY_TYPE_PATTERNS,
    _WRITER_OWNED_ALWAYS,
    ReportProfile,
    infer_query_type,
    select_profile,
)
from app.agents.synthesis.prompts import (  # noqa: F401
    SYNTHESIZER_SYSTEM_PROMPT,
    _REASONING_DEPTH_BLOCK,
    _REASONING_DEPTH_INSTRUCTION,
    _render_audit_contract,
    _render_structure_contract,
)
from app.agents.synthesis.sections import (  # noqa: F401
    REQUIRED_SECTIONS,
    _ALIAS_TO_KEY,
    _CANONICAL_ALIASES,
    _CANONICAL_HEADING,
    _MAX_PARA_SENTENCES,
    _MAX_PARA_WORDS,
    _REQUIRED_KEY_BY_HEADING,
    _SECTION_RANK,
    _Section,
    _WRITER_RANK,
    _canonical_key,
    _count_words,
    _dedupe_canonical_sections,
    _dedupe_heading,
    _dedupe_repeated_bullets,
    _merge_into_section,
    _normalize_heading,
    _normalized_aliases,
    _present_section_keys,
    _render_report,
    _reorder_sections,
    _section_map,
    _shorten_heading,
    _split_long_paragraphs,
    _split_sections,
    _strip_canonical_sections,
    _strip_duplicate_section_heading,
    _trim_to_budget,
)
from app.agents.synthesis.citations import (  # noqa: F401
    MAX_LEGEND_SOURCES,
    MAX_LEGEND_SOURCES_HARD,
    _FACT_CAP_LADDER,
    _apply_adjudication,
    _assign_numbers,
    _audit_units,
    _cite_token,
    _drop_invalid_markers,
    _invalid_markers,
    _is_year,
    _legend_block,
    _legend_budget,
    _number_facts,
    _render_evidence_block,
    _render_ranges_block,
    _source_lines,
    _strip_sections,
    _value_grounded,
    _with_citation,
    audit_citations,
)
from app.agents.synthesis.ranking import (  # noqa: F401
    _angles_of,
    _compress_to_themes,
    _distinct_quantities,
    _fact_confidence,
    _has_numeric_facts,
    _quantity_signature,
    _section_title,
    _stratified_top_facts,
)
from app.agents.synthesis.findings import (  # noqa: F401
    _finding_confidence,
    _finding_grade,
    _finding_justification,
    _ledger_warnings,
    _missing_skeptical_angles,
    _render_finding_line,
)
from app.agents.synthesis.required_sections import (  # noqa: F401
    _add_reasoning_structure,
    _add_required_sections,
    _has_reasoning_section,
    _render_required_section,
    _section_has_substance,
)
from app.agents.synthesis.context_blocks import (  # noqa: F401
    _measured_evidence_block,
    _objection_blocks,
    _render_ambiguity_block,
    _render_analytical_guidance,
    _render_context_block,
    _render_interpretations_block,
)
from app.agents.synthesis.postprocess import (  # noqa: F401
    MAX_AUDIT_LANGUAGE_SENTENCES,
    _BULLET_SEP_RE,
    _PIPELINE_TELEMETRY_RE,
    _REDUNDANT_AUDIT_RE,
    _deterministic_disambiguation,
    _ensure_disambiguation,
    _normalize_query_concept,
    _reduce_redundant_audit_language,
    _sanitize_answer_text,
    _scrub_pipeline_telemetry,
)
from app.agents.synthesis.deterministic import (  # noqa: F401
    _confidence_statement,
    _deterministic_report,
    _gap_stats,
    _gaps_section,
)
from app.agents.synthesis.finalize import (  # noqa: F401
    _basis_candidates,
    _finalize,
    _integrity_note,
    _length_hint,
    _mirror_machine_notes,
    _resolve_profile,
    apply_synthesis_intelligence_pass,
)
from app.agents.synthesis.section_writer import (  # noqa: F401
    _ranked_section_groups,
    _synthesize_sectioned,
)
from app.agents.synthesis.orchestrator import (  # noqa: F401
    synthesize,
    synthesizer_agent,
)

# The module's original logger, kept for import-surface parity.
logger = get_logger(__name__)

__all__ = [
    "CitationAudit",
    "SynthesisResult",
    "ReportProfile",
    "PROFILE_DIRECT",
    "PROFILE_BRIEF",
    "PROFILE_ANALYTICAL",
    "PROFILE_AUDIT",
    "PROFILES",
    "REQUIRED_SECTIONS",
    "SYNTHESIZER_SYSTEM_PROMPT",
    "select_profile",
    "infer_query_type",
    "audit_citations",
    "apply_synthesis_intelligence_pass",
    "synthesize",
    "synthesizer_agent",
]
