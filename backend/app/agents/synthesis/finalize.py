"""Finalization — ONE path, shared by single-pass and section-wise synthesis.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Assembles, guarantees, audits, budgets and orders the draft, and owns
the machine-note write-back, the profile resolver and the cross-section
refinement pass.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence, Tuple

from app.agents.answer_quality import length_band
from app.agents.research_quality import (
    IndependenceReport,
    ResearchQualityReport,
    TemporalProfile,
    assess_report_quality,
)
from app.core.synthesis_intelligence import (
    SynthesisIntelligenceReport,
    apply_synthesis_intelligence,
)

from app.agents.synthesis.citations import (
    _drop_invalid_markers,
    _invalid_markers,
    _legend_block,
    audit_citations,
)
from app.agents.synthesis.context_blocks import (
    _measured_evidence_block,
    _objection_blocks,
)
from app.agents.synthesis.postprocess import (
    _ensure_disambiguation,
    _reduce_redundant_audit_language,
    _sanitize_answer_text,
    _scrub_pipeline_telemetry,
)
from app.agents.synthesis.profiles import (
    PROFILES,
    ReportProfile,
    infer_query_type,
    select_profile,
)
from app.agents.synthesis.required_sections import (
    _add_reasoning_structure,
    _add_required_sections,
)
from app.agents.synthesis.sections import (
    _CANONICAL_HEADING,
    _count_words,
    _dedupe_canonical_sections,
    _dedupe_repeated_bullets,
    _merge_into_section,
    _reorder_sections,
    _section_map,
    _split_long_paragraphs,
    _strip_canonical_sections,
    _trim_to_budget,
)
from app.agents.synthesis.primitives import _safe_int
from app.agents.synthesis.types import CitationAudit, SynthesisResult


def _mirror_machine_notes(ctx: Dict[str, Any], caller_ctx: Dict[str, Any]) -> None:
    """Copy machine-owned provenance onto the caller's context dict.

    `synthesize` rebinds `ctx` to a copy, so the write-back inside `_finalize`
    and `_deterministic_report` would be invisible to the caller. This mirrors
    it to the dict the caller actually owns (the workflow, or a test).

    The definition and ranking audits ride here too. They are measurements of the
    delivered answer, so they belong with the other machine-owned provenance —
    an audit the caller cannot read is inert.
    """
    if caller_ctx is ctx:
        return
    notes = ctx.get("synthesis_machine_notes")
    if notes is not None:
        caller_ctx["synthesis_machine_notes"] = list(notes)
    for key in (
        "definition_audit",
        "ranking_audit",
        "definition_lock",
        "ranking_basis",
        "convergence_audit",
        "consistency_audit",
        "report_status",
        # The construction decision is machine-owned provenance too: the mode the
        # writer was given, and what it was and was not allowed to claim.
        "answer_construction",
        "answer_construction_audit",
    ):
        value = ctx.get(key)
        if value:
            caller_ctx[key] = value


def _resolve_profile(
    profile: ReportProfile | str | None,
    ctx: Dict[str, Any],
    fact_count: int,
) -> ReportProfile:
    if isinstance(profile, ReportProfile):
        return profile
    if isinstance(profile, str) and profile.strip().lower() in PROFILES:
        return PROFILES[profile.strip().lower()]
    return select_profile(ctx, fact_count=fact_count)


def _basis_candidates(basis_map: Dict[str, Any]) -> List[str]:
    """Named options the evidence supports as EXAMPLES, from the ranking basis.

    `assess_comparative_basis` already extracts these — it is what makes a
    SHORTLIST verdict mean "these are the candidates" rather than "nothing was
    identified". The conclusion renderers accept a `cluster=` argument and were
    never given it, so the supported-cluster wording could not be reached from a
    live run.

    Reads both shapes the basis is stored in: a flat dict, and the nested
    `{"basis": {...}}` that the router/audit path persists.
    """
    if not isinstance(basis_map, dict):
        return []
    source: Any = basis_map
    nested = basis_map.get("basis")
    if isinstance(nested, dict) and not basis_map.get("candidates"):
        source = nested
    raw = source.get("candidates") or ()
    if not isinstance(raw, (list, tuple, set)):
        return []
    seen: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        # Short labels only: these are rendered inline in the writer contract,
        # and a stray sentence-length candidate would blow the prompt up.
        if text and len(text) <= 80 and text.lower() not in {s.lower() for s in seen}:
            seen.append(text)
    return seen[:8]


def _length_hint(mode: str, ctx: Dict[str, Any]) -> str:
    """Length instruction bounded by the SAME band the quality gate enforces.

    The writer is never told to exceed what the gate will fail, and deep runs
    are allowed the depth that is their product.
    """
    band_lo, band_hi = length_band(mode)
    intent = ctx.get("intent") or {}
    level = str(intent.get("explanation_level", "") or "")

    if mode.startswith(("deep", "executive")):
        return (
            f"LENGTH: this is a deep-research brief — aim for {band_lo}-{band_hi} "
            "words in TOTAL. Go deeper per angle: mechanisms, numbers with "
            "context, and explicit treatment of conflicting evidence."
        )
    if level == "basic":
        return (
            f"LENGTH: keep the report under {min(600, band_hi)} words. The reader "
            "asked for a basic explanation: open with a plain-language "
            "explanation of the concept and include ONE simple analogy a "
            "non-expert would immediately grasp. Prefer explanation over "
            "statistics."
        )
    return f"LENGTH: keep the report under {band_hi} words."


def _integrity_note(
    audit: CitationAudit,
    quality: "ResearchQualityReport | None" = None,
) -> str:
    """Disclose what the audit found, in the report itself.

    A research system that silently repairs its own citations trains its users
    to trust output it has not earned. If the draft had holes, the reader is
    told which ones — including the research-quality findings (misattributed
    figures, overclaiming, internal conflicts, stale or echoed evidence),
    which are the defects an expert reader would otherwise find first.
    """
    quality_note = quality.render_note() if quality is not None else ""
    if audit.is_clean and not quality_note:
        return ""
    lines = ["## Evidence Integrity", ""]
    if audit.ungrounded_numbers:
        lines.append(
            "Figures without a matching source in the evidence pool (treat as "
            f"unverified): {', '.join(audit.ungrounded_numbers[:6])}."
        )
    if audit.invalid_markers:
        lines.append(
            f"{len(audit.invalid_markers)} citation marker(s) referenced a source "
            "outside the evidence list and were removed."
        )
    if audit.uncited_factual:
        lines.append(
            f"{len(audit.uncited_factual)} factual statement(s) carry no citation, "
            f"beginning: \"{audit.uncited_factual[0][:120]}\"."
        )
    if audit.weakly_supported:
        lines.append(
            f"{len(audit.weakly_supported)} statement(s) show weak overlap with the "
            "source cited beside them; verify those against the linked source."
        )
    if audit.total_sentences:
        lines.append(
            f"Citation density: {audit.citation_density:.0%} of factual sentences cited."
        )
    # Quality findings lead: a misattributed figure or an internal conflict is
    # what an expert reader would catch first, so the report must raise it
    # first rather than burying it under routine citation bookkeeping.
    audit_body = "\n\n".join(line for line in lines[2:] if line.strip())
    body = "\n\n".join(part for part in (quality_note, audit_body) if part.strip())
    return "## Evidence Integrity\n\n" + body


def apply_synthesis_intelligence_pass(
    answer: str,
    ctx: Dict[str, Any] | None = None,
    *,
    query: str = "",
) -> Tuple[str, SynthesisIntelligenceReport]:
    """Run the deterministic cross-section refinement layer on an assembled draft.

    Protected (machine-appended) sections are left whole — they describe
    measured state. Executive Summary and Key Findings are RECAP sections:
    their own text is preserved, but their claims are registered so deep-dive
    sections cannot restate them. Every other writer section is refined against
    the ledger: the first occurrence keeps its citation, an expansion with a new
    analytical dimension is kept intact, and a bare restatement is REWRITTEN
    into a contextual transition + analysis sentence (never deleted), so a
    section opening can never dangle. Evidence signals from `ctx`
    (contradictions, corroboration, primary share) select the analytical
    dimension; the transformation runs with no LLM and never empties a section.
    """
    from app.agents.sources import MACHINE_SECTIONS

    protected = tuple(MACHINE_SECTIONS) + tuple(
        f"## {_CANONICAL_HEADING[key]}"
        for key in (
            "evidence", "limitations", "open questions", "source ledger",
            "sources", "evidence integrity", "standing objections",
            "what would change", "reasoning",
        )
    )
    recap = ("## Executive Summary", "## Key Findings")
    ctx = ctx or {}
    intent = ctx.get("intent") or {}
    signals = {
        "contradicted": bool(ctx.get("contradictions")),
        "corroborated": bool(ctx.get("corroborated")),
        "primary": bool(ctx.get("primary_share")),
        # Adaptive move-selection signals (additive; the layer works without
        # them). The classified query + its type let a "should ... invest"
        # question resolve to a strategic move, a "what caused ..." question to
        # a causal one, and a "compare ..." question to a comparison — so the
        # refinement answers the question that was asked, not a generic one.
        "query": str(query or ""),
        "query_type": str(
            intent.get("query_type", "")
            or ctx.get("query_type", "")
            or infer_query_type(query)
        ),
        "domain": str(intent.get("domain", "") or ""),
    }
    return apply_synthesis_intelligence(
        answer,
        protect_headings=protected,
        recap_headings=recap,
        signals=signals,
    )


def _finalize(
    answer: str,
    *,
    query: str,
    ctx: Dict[str, Any],
    usable_facts: Sequence[Dict[str, Any]],
    contradictions: Sequence[Dict[str, Any]],
    cited_facts: Sequence[Dict[str, Any]],
    numbered: Sequence[Dict[str, Any]],
    profile: ReportProfile,
    mode: str,
    angles: Sequence[str],
    used_fallback: bool = False,
    temporal: TemporalProfile | None = None,
    independence: IndependenceReport | None = None,
) -> SynthesisResult:
    """Assemble, guarantee, audit, budget and order — in that order, once.

    Both synthesis paths previously carried their own copy of this sequence and
    had already drifted. The order here is deliberate:

      sanitize → scrub telemetry → disambiguation → required sections →
      reasoning → refinement pass → dedupe → readability → measured tail →
      trim writer prose to (band - tail) → audit → integrity → order

    The tail is measured BEFORE trimming, which is what makes the length band a
    real contract: the old order trimmed the body and then appended several
    hundred words of appendix, overshooting every time.
    """
    answer = _sanitize_answer_text(answer, query)
    # Voice-flattening cleanup. `_sanitize_answer_text` above is structural
    # (markdown block normalisation) and always runs. These two sand off
    # personality and repeated-limitation phrasing — valuable on the audit
    # profile, heavy-handed on a prose report. Enabled only when the run flags
    # strict cleanup (or the fixed-format audit profile runs).
    strict_cleanup = bool(ctx.get("synthesis_strict_cleanup")) or profile.name == "audit"
    if strict_cleanup:
        answer = _scrub_pipeline_telemetry(answer)
        answer = _reduce_redundant_audit_language(answer)
    answer = _ensure_disambiguation(answer, ctx)

    # Only the audit profile is a fixed-format artifact. For every other
    # profile the answer is the writer's prose: machine-owned accounting is NOT
    # injected into it (it would read as process noise and re-impose the
    # historical report shape). That content is returned separately as
    # `machine_notes` for the audit layer.
    adaptive_answer = profile.name != "audit"

    if not adaptive_answer:
        answer, machine_keys = _add_required_sections(
            answer,
            ctx=ctx,
            usable_facts=usable_facts,
            contradictions=contradictions,
            cited_facts=cited_facts,
            profile=profile,
        )
    else:
        machine_keys = set()

    if profile.include_reasoning and not adaptive_answer:
        answer, added = _add_reasoning_structure(answer, ctx=ctx)
        machine_keys |= added

    # Cross-section refinement (deterministic; no LLM). Runs AFTER the required
    # sections exist (so Key Findings is registered as a recap) and BEFORE the
    # trim. A bare restatement becomes a transition + analysis sentence (never
    # deleted) and keeps its exact [n] markers.
    answer, si_report = apply_synthesis_intelligence_pass(answer, ctx, query=query)

    answer = _dedupe_canonical_sections(answer)
    answer = _split_long_paragraphs(answer)
    answer = _dedupe_repeated_bullets(answer)

    # Measured tail: evidence accounting merged into the single evidence
    # section, plus the profile-gated objection blocks and the source legend.
    evidence_block = _measured_evidence_block(
        ctx, usable_facts, contradictions, temporal=temporal, independence=independence
    )
    tail_blocks: List[str] = []
    if profile.full_appendix:
        tail_blocks.extend(_objection_blocks(ctx))
    legend = _legend_block(numbered)
    tail_words = _count_words(evidence_block) + sum(
        _count_words(b) for b in tail_blocks
    ) + _count_words(legend)

    # Invalid markers are dropped BEFORE the audit, so the density the report
    # prints describes the text that actually shipped.
    valid_numbers = {_safe_int(s.get("n"), 0) for s in numbered}
    valid_numbers.discard(0)
    recorded_invalid = _invalid_markers(answer, valid_numbers)
    if recorded_invalid:
        answer = _drop_invalid_markers(answer, len(numbered))

    band_lo, band_hi = length_band(mode)
    budget = max(band_lo, band_hi - tail_words - 60)
    answer = _trim_to_budget(answer, budget)

    auditable = _strip_canonical_sections(answer, machine_keys | {"sources", "evidence integrity"})
    audit = audit_citations(auditable, numbered, cited_facts)
    if recorded_invalid:
        audit.invalid_markers = sorted(set(audit.invalid_markers) | set(recorded_invalid))

    # DEFINITION-LOCK AUDIT. The meaning was locked before research; this records
    # whether the delivered answer preserved it, promoted a proxy into the
    # concept, and honestly marked an evidence gap. Observational only — it
    # measures, it does not rewrite the writer's prose (same contract as the
    # research-quality layer below).
    try:
        from app.agents.definition_lock import assess_answer_definition

        ctx["definition_audit"] = assess_answer_definition(
            auditable, query, ctx.get("ambiguity") if isinstance(ctx.get("ambiguity"), dict) else {}
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("definition_audit_failed", exc_info=exc)

    # RANKING-BASIS AUDIT. Records whether a superlative question was answered
    # with a ranking the evidence cannot support — winner language used where
    # no comparison exists. Observational, like the definition audit above.
    try:
        from app.agents.ranking_basis import assess_answer_ranking, is_superlative_query

        if is_superlative_query(query):
            ctx["ranking_audit"] = assess_answer_ranking(auditable, query, usable_facts)
    except Exception as exc:
        logging.getLogger(__name__).warning("ranking_audit_failed", exc_info=exc)

    # CONVERGENCE AUDIT. Records whether a #1 was concluded indefensible and the
    # answer nevertheless named one. Observational, like the ranking audit.
    try:
        from app.agents.convergence import assess_convergence

        raw_convergence = ctx.get("convergence")
        convergence: Dict[str, Any] = (
            raw_convergence if isinstance(raw_convergence, dict) else {}
        )
        if convergence.get("identified"):
            ctx["convergence_audit"] = assess_convergence(
                auditable, query,
                [str((ctx.get("critique") or {}).get("reason", "") or "")],
                history=[],
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("convergence_audit_failed", exc_info=exc)

    # REPORT-CONSISTENCY AUDIT. Which sections used ranking language the evidence
    # cannot support. Per-section, so a violation names the section that broke
    # consistency rather than only recording that the report did.
    try:
        from app.agents.report_consistency import assess_report_consistency

        raw_convergence = ctx.get("convergence")
        convergence_map: Dict[str, Any] = (
            raw_convergence if isinstance(raw_convergence, dict) else {}
        )
        if convergence_map.get("identified"):
            ctx["consistency_audit"] = assess_report_consistency(
                query,
                _section_map(auditable),
                convergence=convergence_map,
                ranking_basis=ctx.get("ranking_basis") if isinstance(ctx.get("ranking_basis"), dict) else {},
                # Same cluster the writer was given. Without it the audit
                # classified any supported grouping as a violation, so a report
                # that correctly presented a supported cluster was penalised for
                # the very wording the contract asked for.
                cluster=_basis_candidates(
                    ctx.get("ranking_basis") if isinstance(ctx.get("ranking_basis"), dict) else {}
                ),
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("consistency_audit_failed", exc_info=exc)

    # ANSWER-CONSTRUCTION AUDIT. Did the delivered prose obey the mode it was
    # given — a SYNTHESIZED answer that says it is a synthesis and does not
    # assert a ranking, an INSUFFICIENT answer that names what cannot be
    # determined? Observational, like the ranking and definition audits: it
    # records, it never rewrites.
    try:
        from app.agents.answer_construction import assess_answer_construction

        construction_map = ctx.get("answer_construction")
        if isinstance(construction_map, dict) and construction_map.get("mode"):
            ctx["answer_construction_audit"] = assess_answer_construction(
                query, auditable, construction_map
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("answer_construction_audit_failed", exc_info=exc)

    # Research-quality layer: per-citation grounding, overclaiming, internal
    # consistency and per-section coverage. Run on the WRITER's prose only
    # (machine sections are measured state, not claims to be audited) and on
    # the section map, so a conflict between two sections is visible.
    quality = assess_report_quality(
        auditable,
        cited_facts=cited_facts,
        usable_facts=usable_facts,
        sections=_section_map(auditable),
        query_type=str(
            ((ctx.get("intent") or {}) if isinstance(ctx.get("intent"), dict) else {}).get(
                "query_type", ""
            )
            or ctx.get("query_type", "")
            or infer_query_type(query)
        ),
        temporal=temporal,
        independence=independence,
    )

    machine_notes: List[str] = []
    if adaptive_answer:
        # Preserve the measured provenance in the audit payload instead of the
        # answer. Auditable state is never lost — it is relocated.
        if evidence_block.strip():
            machine_notes.append(evidence_block.strip())
        machine_notes.extend(b.strip() for b in tail_blocks if b.strip())
    else:
        answer = _merge_into_section(answer, "evidence", evidence_block)
        for block in tail_blocks:
            answer = answer.rstrip() + "\n\n" + block

    integrity = _integrity_note(audit, quality)
    if integrity:
        if adaptive_answer:
            machine_notes.append(integrity.strip())
        else:
            answer = answer.rstrip() + "\n\n" + integrity

    answer = _strip_canonical_sections(answer, {"sources"})
    answer = answer.rstrip() + "\n\n" + legend
    # Canonical reordering only applies to the fixed-format audit report; an
    # adaptive answer keeps the order the writer chose for this question.
    if not adaptive_answer:
        answer = _reorder_sections(answer)

    # Hand the machine-owned provenance back to the caller through the context
    # dict it already passed (no signature change, no module-level state). The
    # workflow merges this into the audit layer.
    if isinstance(ctx, dict):
        ctx["synthesis_machine_notes"] = list(machine_notes)

    return SynthesisResult(
        answer=answer,
        sources=list(numbered),
        audit=audit,
        used_fallback=used_fallback,
        angles=list(dict.fromkeys(a for a in angles if a)),
        synthesis_intelligence=si_report.to_dict(),
        profile=profile.name,
        word_count=_count_words(answer),
        quality=quality.to_dict(),
        machine_notes=machine_notes,
    )
