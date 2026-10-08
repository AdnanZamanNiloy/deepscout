"""High-level synthesis orchestration: the public entry points.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). `synthesize` wires the decision-grade contracts (definition lock,
ranking basis, convergence, report consistency, answer construction, evidence
balance), runs the section-wise path or the single-pass writer, and hands the
result to `_finalize`. `synthesizer_agent` is the string-returning public
entry point.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.agents.evidence_utils import (
    dedupe_semantic_facts,
    filter_facts_by_domain,
)
from app.agents.epistemics import EpistemicReport, assess_epistemics
from app.agents.outline import (
    AnswerOutline,
    build_blueprint,
    build_outline,
    render_blueprint,
    render_outline,
)
from app.agents.research_quality import (
    assess_independence,
    apply_independence,
    temporal_profile,
    render_quality_contract,
)
from app.core.degradation import EVIDENCE_WEAK, PROVIDER_TRANSIENT, record_fallback
from app.core.llm import AllProvidersFailedError, LLMClient, PromptTooLargeError
from app.core.logging import get_logger
from app.core.schemas import SynthesizerAnswerModel
from app.core.usage import run_seconds_remaining, set_stage_hint

from app.agents.synthesis.citations import (
    _FACT_CAP_LADDER,
    _apply_adjudication,
    _number_facts,
    _render_evidence_block,
    _render_ranges_block,
    _render_source_excerpts,
    _source_lines,
)
from app.agents.synthesis.context_blocks import (
    _render_ambiguity_block,
    _render_analytical_guidance,
    _render_context_block,
    _render_interpretations_block,
)
from app.agents.synthesis.deterministic import _deterministic_report
from app.agents.synthesis.finalize import (
    _basis_candidates,
    _finalize,
    _length_hint,
    _mirror_machine_notes,
    _resolve_profile,
)
from app.agents.synthesis.postprocess import _normalize_query_concept
from app.agents.synthesis.profiles import (
    ReportProfile,
    _format_guidance,
    infer_query_type,
)
from app.agents.synthesis.prompts import (
    SYNTHESIZER_SYSTEM_PROMPT,
    _REASONING_DEPTH_BLOCK,
    _render_structure_contract,
)
from app.agents.synthesis.primitives import _safe_float
from app.agents.synthesis.ranking import (
    _angles_of,
    _compress_to_themes,
    _has_numeric_facts,
    _stratified_top_facts,
)
from app.agents.synthesis.section_writer import _synthesize_sectioned
from app.agents.synthesis.types import SynthesisResult

logger = get_logger(__name__)


async def synthesizer_agent(
    llm: LLMClient,
    query: str,
    facts: List[Dict[str, Any]],
    context: Dict[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    """Synthesize the final report and return it as markdown text.

    Kept as the public entry point with its original signature and return type.
    `context` carries the decision-grade inputs the workflow already computed:
    contradictions to flag, overall confidence, degraded stages, red-team
    findings, and evidence counts. Extra keyword arguments (outline,
    section_wise, compress_context, profile) pass through to `synthesize` —
    callers that pass none keep the old behaviour.
    """
    result = await synthesize(llm, query, facts, context, **kwargs)
    return result.answer


async def synthesize(
    llm: LLMClient,
    query: str,
    facts: List[Dict[str, Any]],
    context: Dict[str, Any] | None = None,
    *,
    outline: AnswerOutline | None = None,
    section_wise: bool | None = None,
    compress_context: bool | None = None,
    compress_threshold: float | None = None,
    profile: ReportProfile | str | None = None,
) -> SynthesisResult:
    """The same synthesis, returning the audit and legend alongside the text.

    Answer-first outline: the report's section shape is derived from the query
    + evidence BEFORE writing, so a broad question is answered dimension by
    dimension instead of dumping the top-ranked claims. `section_wise` writes
    each outline section as its own call and assembles the result; it degrades
    cleanly to a single pass when unsupported. `compress_context` merges
    near-duplicate claims into one thematic entry (never dropping distinct
    claims) before the writer sees them. `profile` selects the report shape;
    when omitted it is derived from mode, query type and evidence volume.
    """
    usable_facts = dedupe_semantic_facts(filter_facts_by_domain(facts))
    ctx = context or {}
    # The caller's dict, captured before any rebinding of `ctx`, so machine-
    # owned provenance can be written back where the caller can see it.
    caller_ctx = ctx
    resolved_profile = _resolve_profile(profile, ctx, len(usable_facts))

    if not usable_facts:
        concept = _normalize_query_concept(query)
        if isinstance(ctx, dict):
            ctx["synthesis_machine_notes"] = []
        _mirror_machine_notes(ctx, caller_ctx)
        return SynthesisResult(
            answer=(
                f"No reliable evidence could be retrieved for {concept}, so this "
                "question cannot be answered from research at this time. The "
                "sources returned did not survive verification — that is a finding "
                "about the available evidence, not about the subject.\n\n"
                "A narrower question, a specific named source or document, or a "
                "different phrasing of the key terms is the most likely way to "
                "get a usable answer."
            ),
            used_fallback=True,
            profile=resolved_profile.name,
        )

    contradictions = [c for c in (ctx.get("contradictions") or []) if isinstance(c, dict)]

    # Adjudicate BEFORE any of it reaches the page. The numeric detector fires
    # on magnitude plus topic similarity, so it cannot tell "$2bn in 2022 vs
    # $3bn in 2024" (growth) from "$2bn vs $3bn for the same year" (a real
    # disagreement). Printing the first as the range "$2-3bn" is a factual
    # error the pipeline invents on its own, so non-conflicts are stripped out
    # here and the genuine ones arrive carrying the rule that decided them.
    epistemics: EpistemicReport = ctx.get("epistemics") if isinstance(
        ctx.get("epistemics"), EpistemicReport
    ) else assess_epistemics(query, usable_facts, contradictions)
    contradictions = _apply_adjudication(contradictions, epistemics)
    # `ctx` is rebound to a copy below; `caller_ctx` holds the caller's dict.
    ctx = {**ctx, "epistemics": epistemics}

    # Options may arrive as explicit kwargs (tests, direct callers) or inside
    # `context` (the workflow passes them there to keep this entry point's
    # signature stable for test doubles). Explicit wins; context is default.
    if compress_context is None:
        compress_context = bool(ctx.get("compress_context", True))
    if compress_threshold is None:
        compress_threshold = _safe_float(ctx.get("compress_threshold", 0.72), 0.72) or 0.72
    if outline is None:
        ctx_outline = ctx.get("outline")
        if isinstance(ctx_outline, AnswerOutline):
            outline = ctx_outline

    if compress_context:
        usable_facts = _compress_to_themes(usable_facts, similarity_threshold=compress_threshold)

    if outline is None:
        outline = build_outline(
            query,
            usable_facts,
            ctx.get("sub_questions") or [],
            intent=ctx.get("intent") or {},
        )

    mode = str(ctx.get("mode", "standard") or "standard")
    length_hint = _length_hint(mode, ctx)
    intent = ctx.get("intent") or {}

    # Adaptive answer blueprint: the presentation strategy for THIS question,
    # derived from intent + the evidence actually in hand. It replaces a fixed
    # heading list with a strategy the writer applies. For the audit profile the
    # fixed contract still governs, so the blueprint's strategy guidance is
    # suppressed there (the audit format is the point of that profile).
    blueprint = build_blueprint(
        query,
        usable_facts,
        ctx.get("sub_questions") or [],
        intent=intent,
        outline=outline,
        mode=mode,
        contradictions=ctx.get("contradictions") or [],
    )
    blueprint_block = render_blueprint(blueprint)
    ctx["blueprint"] = blueprint.to_dict()

    ambiguity_block = _render_ambiguity_block(intent)
    if ambiguity_block:
        length_hint = f"{length_hint}\n\n{ambiguity_block}"
    interpretations_block = _render_interpretations_block(
        intent, ctx.get("ambiguity") if isinstance(ctx.get("ambiguity"), dict) else None
    )
    if interpretations_block:
        length_hint = f"{length_hint}\n\n{interpretations_block}"

    # LOCKED DEFINITION CONTRACT. The meaning was fixed before research; this
    # tells the writer it must survive into the answer, that a proxy metric may
    # not become the definition, and what shape to use when the evidence cannot
    # answer the locked question. Without it the writer was free to let the
    # evidence choose the meaning — how "most demanding" became a ranking by
    # preparation because preparation data were the ones available.
    # The ambiguity policy is read by two blocks below (the locked definition and
    # the evidence balance). Resolved once, so they cannot disagree about which
    # policy this run used.
    raw_ambiguity = ctx.get("ambiguity")
    ambiguity_policy: Dict[str, Any] = raw_ambiguity if isinstance(raw_ambiguity, dict) else {}

    # Process contracts are ALWAYS computed and kept on the run's audit/trace.
    # Whether they are ALSO injected into the writer prompt is configurable: a
    # strong model writes cleaner prose with fewer stacked constraints, and the
    # post-hoc audits already enforce the same conclusions. Set
    # synthesis_writer_process_contracts=True to steer the writer explicitly.
    writer_contracts = bool(
        getattr(getattr(llm, "settings", None), "synthesis_writer_process_contracts", False)
    )
    # Voice-flattening cleanup (telemetry scrub + repeated-limitation collapse)
    # is off unless explicitly enabled; the audit profile always applies it.
    # An explicit ctx value (tests, callers) wins over settings.
    if "synthesis_strict_cleanup" not in ctx:
        ctx["synthesis_strict_cleanup"] = bool(
            getattr(getattr(llm, "settings", None), "synthesis_strict_cleanup", False)
        )

    try:
        from app.agents.definition_lock import definition_lock, render_lock_contract

        lock = definition_lock(query, ambiguity_policy)
        lock_contract = render_lock_contract(lock)
        if writer_contracts and lock_contract:
            length_hint = f"{length_hint}\n\n{lock_contract}"
        ctx["definition_lock"] = lock.to_dict()
    except Exception as exc:  # guidance must never break synthesis
        logging.getLogger(__name__).warning("definition_lock_failed", exc_info=exc)

    # RANKING BASIS CONTRACT. A "most/best/highest/worst" question is a
    # comparison, so its answer may only rank when the evidence actually
    # compares the candidates. Two unrelated findings are examples, not an
    # ordering, and a superlative must not be inferred from them. Writer
    # guidance only for superlative queries; every other question is unaffected.
    try:
        from app.agents.ranking_basis import (
            assess_comparative_basis,
            is_superlative_query,
            render_ranking_contract,
        )

        if is_superlative_query(query):
            basis = assess_comparative_basis(query, facts)
            basis_contract = render_ranking_contract(basis, query)
            if writer_contracts and basis_contract:
                length_hint = f"{length_hint}\n\n{basis_contract}"
            ctx["ranking_basis"] = basis.to_dict()
    except Exception as exc:  # guidance must never break synthesis
        logging.getLogger(__name__).warning("ranking_basis_failed", exc_info=exc)

    # CONVERGENCE CONTRACT. The loop concluded that the evidence cannot answer
    # the question as asked. For a #1 question that means saying so plainly, not
    # manufacturing a winner from a proxy or an analogy — and dropping the
    # indirect material retrieved along the way.
    try:
        from app.agents.convergence import (
            FundamentalGap,
            render_convergence_contract,
        )

        raw_convergence = ctx.get("convergence")
        convergence: Dict[str, Any] = (
            raw_convergence if isinstance(raw_convergence, dict) else {}
        )
        basis_map: Dict[str, Any] = (
            ctx.get("ranking_basis") if isinstance(ctx.get("ranking_basis"), dict) else {}
        )
        # The named options the evidence actually supports as examples. This is
        # what turns a "no defensible #1" verdict into a SUPPORTED CLUSTER
        # rather than a flat "the evidence supports no answer". The ranking-basis
        # assessment computed these all along and they were then never handed to
        # either renderer below, so the cluster wording was unreachable from a
        # live run and only tests could reach it.
        supported_cluster = _basis_candidates(basis_map)
        if convergence.get("identified"):
            gap = FundamentalGap(
                identified=True,
                rounds=int(convergence.get("rounds", 0) or 0),
                reason=str(convergence.get("reason", "") or ""),
                signature=str(convergence.get("signature", "") or ""),
                missing_evidence=str(convergence.get("missing_evidence", "") or ""),
            )
            contract = render_convergence_contract(query, gap, cluster=supported_cluster)
            if writer_contracts and contract:
                length_hint = f"{length_hint}\n\n{contract}"
    except Exception as exc:  # guidance must never break synthesis
        logging.getLogger(__name__).warning("convergence_contract_failed", exc_info=exc)

    # REPORT-WIDE CONSISTENCY. One evidence conclusion, obeyed by EVERY section.
    # The convergence contract above governs the answer; this names the ranking
    # vocabulary that must not appear and the cluster wording to use instead, and
    # it travels with each per-section prompt too — the Executive Summary obeying
    # the conclusion while another section named a "leading" option was the
    # reported inconsistency.
    try:
        from app.agents.report_consistency import (
            report_status,
            render_consistency_contract,
        )

        status = report_status(
            query,
            convergence=convergence,
            ranking_basis=basis_map,
            cluster=supported_cluster,
        )
        consistency = render_consistency_contract(status)
        if writer_contracts and consistency:
            length_hint = f"{length_hint}\n\n{consistency}"
        ctx["report_status"] = status.to_dict()
    except Exception as exc:  # guidance must never break synthesis
        logging.getLogger(__name__).warning("report_consistency_failed", exc_info=exc)

    # ANSWER-CONSTRUCTION CONTRACT. The middle case: no source answers the
    # question directly, but the evidence covers its dimensions well enough to
    # build a defensible answer. Without this the run has only two reachable
    # outcomes — report a source, or refuse — and the refusal branch absorbed
    # the synthesis case, which is why "no source ranks these" collapsed into
    # "no answer can be given". Decided HERE, after the ranking basis and the
    # report status exist, and before any prose is written, so the contract
    # governs the writer rather than describing it after the fact.
    try:
        from app.agents.answer_construction import (
            classify as classify_answer_construction,
            render_construction_contract,
        )

        construction = classify_answer_construction(
            query,
            facts=facts,
            plan=ctx.get("sub_questions") if isinstance(ctx.get("sub_questions"), list) else [],
            ranking_basis=basis_map,
            convergence=convergence,
            contradictions=ctx.get("contradictions") if isinstance(ctx.get("contradictions"), list) else [],
            definition_lock=ctx.get("definition_lock") if isinstance(ctx.get("definition_lock"), dict) else {},
            # A degraded run cannot enter SYNTHESIZED: the extraction itself was
            # degenerate, so the pool cannot attest that the evidence was read
            # correctly, which is exactly what a synthesis claims. Uses the same
            # in-run fallback list the degraded banner and the confidence cap
            # read, so the three can never disagree about what "degraded" means.
            degraded=bool(ctx.get("degraded")),
        )
        construction_contract = render_construction_contract(construction)
        if writer_contracts and construction_contract:
            length_hint = f"{length_hint}\n\n{construction_contract}"
        ctx["answer_construction"] = construction.to_dict()
    except Exception as exc:  # guidance must never break synthesis
        logging.getLogger(__name__).warning("answer_construction_failed", exc_info=exc)

    # EVIDENCE BALANCE ACROSS READINGS. The chosen reading is fixed before
    # research; this ensures availability does not redefine the question. If the
    # reading actually asked about came back thin while another gathered more,
    # the writer must report the gap rather than quietly answer the easier one —
    # and may offer the other only as a clearly labelled alternative.
    try:
        from app.agents.ambiguity import assess_reading_evidence, evidence_balance_guidance

        policy = ambiguity_policy
        if str(policy.get("action", "") or "") in ("assume", "separate"):
            balance = assess_reading_evidence(facts, policy)
            balance_guidance = evidence_balance_guidance(balance)
            if writer_contracts and balance_guidance:
                length_hint = f"{length_hint}\n\n{balance_guidance}"
            ctx["reading_evidence"] = balance.to_dict()
    except Exception as exc:  # guidance must never break synthesis
        logging.getLogger(__name__).warning(
            "reading_evidence_assessment_failed", exc_info=exc
        )

    guidance = _format_guidance(query, intent)

    # Evidence quality measured BEFORE the writer sees anything, because two of
    # these results change what the writer is allowed to say. Recency decides
    # whether "currently" is a legitimate word in this report; independence
    # decides whether a claim carried by three syndicated copies may be
    # presented as corroborated. Measuring them afterwards would leave the
    # report asserting things the evidence cannot support and only footnoting
    # the problem underneath.
    resolved_query_type = str(
        (intent or {}).get("query_type", "")
        or ctx.get("query_type", "")
        or infer_query_type(query)
    )
    temporal = temporal_profile(usable_facts, query_type=resolved_query_type)
    independence = assess_independence(usable_facts)
    usable_facts = apply_independence(usable_facts, independence)
    quality_contract = render_quality_contract(
        profile=temporal,
        confidence=ctx.get("confidence") if isinstance(ctx.get("confidence"), (int, float)) else None,
        query_type=resolved_query_type,
    )

    # Section-wise synthesis for broad questions: write each outline section as
    # its own bounded call and assemble. This is what stops a broad query from
    # collapsing into one narrow thesis, and it keeps every prompt small enough
    # to survive size-capped providers. Opt-in via the caller (the workflow
    # gates it on mode); when any section fails the whole report falls back to
    # the single-pass writer — never a mixed report and never a crash.
    if section_wise is None:
        section_wise = bool(ctx.get("section_wise", False))
    if section_wise and outline.broad and len(outline.sections) > 1:
        sectioned = await _synthesize_sectioned(
            llm,
            query,
            usable_facts,
            ctx,
            outline,
            contradictions,
            length_hint,
            resolved_profile,
            guidance,
            quality_contract,
            temporal,
            independence,
            blueprint=blueprint,
        )
        if sectioned is not None:
            _mirror_machine_notes(ctx, caller_ctx)
            return sectioned
        logger.warning("[Synthesizer] section-wise path failed; falling back to single pass")

    # Adaptive fact-cap ladder: the writer prompt carries up to 40 facts plus
    # the legend; on providers that cap request size the first attempt can be
    # rejected whole. Shrinking the evidence view keeps synthesis LLM-written
    # instead of degrading to the extractive fallback.
    angles: List[str] = []
    top_facts: List[Dict[str, Any]] = []
    numbered: List[Dict[str, Any]] = []
    cited_facts: List[Dict[str, Any]] = []
    payload: Dict[str, Any] = {}
    cap_index = 0
    timeout_second_chance = True
    # The single-pass writer is the primary path (section-wise is opt-in). It
    # carries the WHOLE evidence view the model can fit: the ladder starts at
    # the configured single-pass cap and shrinks only when a provider rejects
    # the prompt size, so a large-context model sees the widest pool we can
    # afford and synthesises across sources instead of summarising a slice.
    configured_cap = int(
        getattr(getattr(llm, "settings", None), "synthesis_single_pass_fact_cap", 0) or 0
    ) or _FACT_CAP_LADDER[0]
    cap_ladder = (configured_cap, *[c for c in _FACT_CAP_LADDER if c < configured_cap])
    if not cap_ladder:
        cap_ladder = _FACT_CAP_LADDER
    while cap_index < len(cap_ladder):
        fact_cap = cap_ladder[cap_index]
        # per_angle is bounded by fact_cap so a run with few angles still
        # reaches the cap instead of being limited to per_angle x num_angles.
        top_facts = _stratified_top_facts(usable_facts, per_angle=max(10, fact_cap), cap=fact_cap)
        numbered, cited_facts = _number_facts(top_facts)
        angles = _angles_of(cited_facts)

        # PRIMARY guidance: the AnalystBrief and the SynthesisPlan lead the
        # prompt. A large evidence block placed before them let the evidence
        # dump overshadow the analytical synthesis — the writer summarised the
        # evidence instead of writing the answer the brief implies. The brief
        # now comes FIRST, evidence comes SECOND, and the mechanical
        # constraints sit between them.
        primary_guidance = _render_analytical_guidance(ctx)

        user_prompt = (
            f"Main query: {query}\n\n"
            f"{length_hint}\n\n"
            + primary_guidance
            + (f"CONSTRAINTS\n{guidance}\n\n" if guidance else "")
            + (f"{quality_contract}\n\n" if quality_contract else "")
            + _render_structure_contract(
                resolved_profile,
                angles=angles,
                ambiguous=bool(isinstance(intent, dict) and intent.get("ambiguity")),
                has_figures=_has_numeric_facts(cited_facts),
            )
            + "\n\n"
            + (
                f"PRESENTATION STRATEGY:\n{blueprint_block}\n\n"
                if blueprint_block else ""
            )
            + (
                # The outline is supporting material for coverage, not a
                # mandated heading list. Angles are dimensions to cover, not
                # one-section-each.
                "Evidence-grounded dimensions the research covers (weave in the "
                "relevant ones; do not force one section per item):\n"
                + "\n".join(f"- {s.title}" for s in outline.sections if s.title and s.title != "Answer")
                + "\n\n"
                if outline.sections and outline.sections[0].title != "Answer"
                else render_outline(outline)
            )
            + _REASONING_DEPTH_BLOCK
            + "SUPPORTING EVIDENCE — cite each line by the number it begins with. "
            "Use only the evidence needed to support the brief above; do not "
            "enumerate every line:\n"
            + _render_evidence_block(cited_facts, limit=fact_cap)
            + "\n\n"
            + _render_source_excerpts(
                cited_facts,
                ctx.get("source_excerpts") if isinstance(ctx.get("source_excerpts"), dict) else None,
            )
            + _render_ranges_block(contradictions)
            + _render_context_block(ctx)
            + f"Sources (cite by number only):\n{_source_lines(numbered)}\n\n"
            "Return JSON in this schema: "
            '{"answer": "<final synthesized report with [n] citations>"}'
        )
        try:
            set_stage_hint("synthesizer")
            payload = await llm.generate_json(
                SYNTHESIZER_SYSTEM_PROMPT,
                user_prompt,
                response_model=SynthesizerAnswerModel,
            )
            break
        except PromptTooLargeError:
            logger.warning(
                "[Synthesizer] provider rejected the prompt at cap=%d facts; retrying smaller",
                fact_cap,
            )
            cap_index += 1
            continue
        except AllProvidersFailedError as exc:
            if "timeout" in str(exc).lower():
                # Slowness, not size or rate. One budget-aware second chance at
                # the SMALLEST evidence view: a flaky provider may still
                # complete a small prompt inside the run's remaining wall-clock.
                # Never more than one.
                if (
                    timeout_second_chance
                    and cap_index < len(cap_ladder) - 1
                    and run_seconds_remaining() > 120.0
                ):
                    timeout_second_chance = False
                    cap_index = len(cap_ladder) - 1
                    logger.warning(
                        "[Synthesizer] provider stalled; one second chance at the smallest fact cap"
                    )
                    continue
                logger.warning(
                    "[Synthesizer] provider too slow (timeout); using deterministic fallback"
                )
                record_fallback("synthesizer", reason=PROVIDER_TRANSIENT)
                payload = {}
                break
            # Rate-limited wall: shrink the evidence view — a smaller prompt
            # needs fewer tokens and can still fit a TPM-starved window.
            logger.warning(
                "[Synthesizer] providers unavailable at cap=%d facts (%s); retrying smaller",
                fact_cap, str(exc)[:140],
            )
            cap_index += 1
            continue
        except Exception as exc:
            logger.warning("[Synthesizer] LLM call failed, using deterministic fallback", exc_info=exc)
            record_fallback("synthesizer", reason=PROVIDER_TRANSIENT)
            payload = {}
            break

    answer = str(payload.get("answer", "")).strip() if isinstance(payload, dict) else ""
    if not answer:
        record_fallback("synthesizer", reason=EVIDENCE_WEAK)
        result = _deterministic_report(
            query, usable_facts, top_facts, ctx, angles, resolved_profile
        )
        _mirror_machine_notes(ctx, caller_ctx)
        return result

    result = _finalize(
        answer,
        query=query,
        ctx=ctx,
        usable_facts=usable_facts,
        contradictions=contradictions,
        cited_facts=cited_facts,
        numbered=numbered,
        profile=resolved_profile,
        mode=mode,
        angles=angles,
        temporal=temporal,
        independence=independence,
    )
    _mirror_machine_notes(ctx, caller_ctx)
    return result
