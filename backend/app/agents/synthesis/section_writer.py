"""Section-wise synthesis: write each outline section as its own bounded call.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Broad questions are answered dimension by dimension instead of
collapsing into one narrow thesis, and each prompt stays small enough to survive
size-capped providers. Any section failure abandons the whole path so the caller
re-runs the single-pass writer — never a mixed report, never a crash.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Sequence, Tuple

from app.agents.answer_quality import length_band
from app.agents.outline import AnswerBlueprint, AnswerOutline, group_facts_by_section, render_blueprint
from app.agents.research_quality import IndependenceReport, TemporalProfile
from app.core.llm import LLMClient
from app.core.logging import get_logger
from app.core.schemas import SynthesizerAnswerModel
from app.core.section_context import build_section_candidate_pool, select_section_facts
from app.core.usage import set_stage_hint

from app.agents.synthesis.citations import (
    _assign_numbers,
    _legend_budget,
    _render_evidence_block,
    _render_ranges_block,
    _source_lines,
)
from app.agents.synthesis.context_blocks import _render_context_block
from app.agents.synthesis.finalize import _finalize
from app.agents.synthesis.prompts import (
    SYNTHESIZER_SYSTEM_PROMPT,
    _REASONING_DEPTH_INSTRUCTION,
)
from app.agents.synthesis.profiles import ReportProfile
from app.agents.synthesis.ranking import _angles_of, _stratified_top_facts
from app.agents.synthesis.sections import _strip_duplicate_section_heading
from app.agents.synthesis.types import SynthesisResult

logger = get_logger(__name__)


def _ranked_section_groups(
    outline: AnswerOutline,
    usable_facts: Sequence[Dict[str, Any]],
) -> List[Tuple[Any, List[Dict[str, Any]]]]:
    """Pair each section with its ranked per-section context.

    Candidate pool for a section is its axis-grouped facts PLUS a BOUNDED,
    relevance-pre-ranked slice of the global pool, so the section can recover a
    relevant claim the coarse grouping placed under another axis without every
    section seeing every fact. The section's own facts are reserved a slot, and
    each section falls back to its axis-grouped facts on any failure, so a
    section is never empty when it had evidence.
    """
    pool = [f for f in (usable_facts or []) if isinstance(f, dict)]
    out: List[Tuple[Any, List[Dict[str, Any]]]] = []
    for section, own in group_facts_by_section(outline):
        candidates, own_ids = build_section_candidate_pool(section, own, pool)
        if not candidates:
            out.append((section, []))
            continue
        selected = select_section_facts(section, candidates, reserved_ids=own_ids)
        if not selected:
            selected = list(own)
        out.append((section, selected))
    return out


async def _synthesize_sectioned(
    llm: LLMClient,
    query: str,
    usable_facts: List[Dict[str, Any]],
    ctx: Dict[str, Any],
    outline: AnswerOutline,
    contradictions: List[Dict[str, Any]],
    length_hint: str,
    profile: ReportProfile,
    guidance: str,
    quality_contract: str = "",
    temporal: TemporalProfile | None = None,
    independence: IndependenceReport | None = None,
    blueprint: AnswerBlueprint | None = None,
) -> SynthesisResult | None:
    """Write each outline section as its own call, then assemble.

    Returns None (so the caller re-runs the single-pass writer) when there are
    fewer than two usable sections, when any section call fails, or when the
    assembled draft is empty. Every deterministic guarantee is applied by the
    shared `_finalize`, exactly as in the single-pass path.
    """
    groups = _ranked_section_groups(outline, usable_facts)
    if len(groups) < 2:
        return None

    # One shared legend across sections so markers stay stable and in range.
    # Number the ORIGINAL fact objects (a single legend + a per-fact marker),
    # then split them back per section. `_assign_numbers` preserves identity,
    # unlike `_number_facts` which copies. The legend budget is sized to the
    # sources actually present: capping it low used to orphan facts whose
    # source missed the legend, emptying sections and collapsing this path.
    all_section_facts: List[Dict[str, Any]] = []
    for _, facts in groups:
        all_section_facts.extend(facts)
    numbered, pairs = _assign_numbers(
        all_section_facts, max_sources=_legend_budget(all_section_facts)
    )
    cited_facts: List[Dict[str, Any]] = []
    marker_by_id: Dict[int, int] = {}
    for fact, index in pairs:
        item = dict(fact)
        item["citation"] = index
        cited_facts.append(item)
        marker_by_id[id(fact)] = index

    facts_by_section: List[List[Dict[str, Any]]] = []
    for _, facts in groups:
        section_cited: List[Dict[str, Any]] = []
        for fact in facts:
            index = marker_by_id.get(id(fact))
            if index is None:
                continue
            item = dict(fact)
            item["citation"] = index
            section_cited.append(item)
        facts_by_section.append(section_cited)

    source_lines = _source_lines(numbered)
    ranges_block = _render_ranges_block(contradictions)
    context_block = _render_context_block(ctx)

    # Per-section word budget so the assembled report lands inside the mode's
    # band. Reserve words for the Executive Summary and the machine-appended
    # sections, then divide the rest across the sections that will be written.
    mode = str(ctx.get("mode", "standard") or "standard")
    _, band_hi = length_band(mode)
    writable = [g for g, cited in zip(groups, facts_by_section) if cited]
    section_count = max(1, len(writable))
    heading_reserve = 300
    per_section_hi = max(110, (band_hi - heading_reserve) // section_count)
    section_length_hint = (
        f"LENGTH: {per_section_hi} words MAXIMUM for this section — a hard "
        f"limit, not a target. The report assembles to at most {band_hi} words "
        f"total across {section_count} sections plus the Executive Summary, so "
        "exceeding this makes the report fail its own length contract. Prefer "
        "one tight synthesis paragraph over two loose ones."
    )

    # Executive Summary first. The quality gate hard-fails clarity without it,
    # and the section-wise path previously assembled only outline sections, so
    # every sectioned report shipped without one. One dedicated writer call
    # over the highest-confidence facts; when it fails the deterministic
    # required-section pass supplies one rather than shipping a blank heading.
    exec_body = ""
    opening_facts = _stratified_top_facts(all_section_facts, per_angle=3, cap=10)
    opening_cited: List[Dict[str, Any]] = []
    for fact in opening_facts:
        index = marker_by_id.get(id(fact))
        if index is None:
            continue
        item = dict(fact)
        item["citation"] = index
        opening_cited.append(item)
    if opening_cited:
        exec_prompt = (
            f"Main query: {query}\n\n"
            f"{length_hint}\n\n"
            + (f"{guidance}\n\n" if guidance else "")
            + (f"{quality_contract}\n\n" if quality_contract else "")
            + (
                f"PRESENTATION STRATEGY:\n{render_blueprint(blueprint)}\n\n"
                if blueprint is not None else ""
            )
            + "Write ONLY the Executive Summary of a larger report. 4-6 sentences "
            "maximum, in your own words. The FIRST sentence must answer the main "
            "query directly in plain language — not describe what the report "
            "covers. If the query term has multiple distinct meanings, name them "
            "in the first sentence and keep them strictly separate. Do NOT state "
            "any pipeline metric, score or fact count. Do NOT emit a markdown "
            "heading — the assembler adds it. Cite with these exact [n] markers.\n\n"
            "Evidence:\n"
            + _render_evidence_block(opening_cited)
            + "\n\n"
            + ranges_block
            + context_block
            + f"Sources (cite by number only):\n{source_lines}\n\n"
            "Return JSON in this schema: "
            '{"answer": "<Executive Summary prose with [n] citations>"}'
        )
        try:
            set_stage_hint("synthesizer")
            exec_payload = await llm.generate_json(
                SYNTHESIZER_SYSTEM_PROMPT,
                exec_prompt,
                response_model=SynthesizerAnswerModel,
            )
            exec_body = (
                str(exec_payload.get("answer", "")).strip()
                if isinstance(exec_payload, dict) else ""
            )
        except Exception as exc:
            logger.warning(
                "[Synthesizer] executive summary call failed (%s); continuing without it",
                str(exc)[:120],
                exc_info=exc,
            )
            exec_body = ""
        exec_body = _strip_duplicate_section_heading(exec_body, "Executive Summary")

    section_bodies: List[str] = []
    written_sections = 0
    if exec_body:
        section_bodies.append(f"## Executive Summary\n\n{exec_body}")

    async def _write_section(section, section_cited: List[Dict[str, Any]]) -> str | None:
        """One section writer call. Returns the body, or None to abandon
        the section-wise path (the caller falls back to single-pass — the
        documented degradation, AGENTS 4.7)."""
        prompt = (
            f"Main query: {query}\n\n"
            f"{section_length_hint}\n\n"
            # The report-wide contract travels with EVERY section prompt, exactly
            # as it does with the Executive Summary prompt above. It used to be
            # omitted here, which is how the summary obeyed the evidence
            # conclusion while another section named a "leading" option — the
            # inconsistency this fixes.
            f"{length_hint}\n\n"
            + (f"{guidance}\n\n" if guidance else "")
            + (f"{quality_contract}\n\n" if quality_contract else "")
            + (
                f"PRESENTATION STRATEGY:\n{render_blueprint(blueprint)}\n\n"
                if blueprint is not None else ""
            )
            + f"You are writing ONE section of a larger answer, the section titled "
            f"\"{section.title}\" (dimension: {section.axis}).\n"
            + (f"Section goal: {section.coverage_goal}\n" if section.coverage_goal else "")
            + "Write 2-3 tight paragraphs of synthesis for THIS section only. "
            "Do NOT emit a markdown heading for this section — the assembler adds "
            f"the \"## {section.title}\" heading itself. Start directly with prose. "
            "Do not write an Executive Summary, a Sources list, or other sections — "
            "they are added separately. Use these exact [n] markers.\n\n"
            + _REASONING_DEPTH_INSTRUCTION
            + "\n\n"
            "Evidence:\n"
            + _render_evidence_block(section_cited)
            + "\n\n"
            + ranges_block
            + context_block
            + f"Sources (cite by number only):\n{source_lines}\n\n"
            "Return JSON in this schema: "
            '{"answer": "<section markdown with [n] citations>"}'
        )
        try:
            set_stage_hint("synthesizer")
            payload = await llm.generate_json(
                SYNTHESIZER_SYSTEM_PROMPT,
                prompt,
                response_model=SynthesizerAnswerModel,
            )
        except Exception as exc:
            logger.warning(
                "[Synthesizer] section '%s' failed (%s); abandoning section-wise path",
                section.title, str(exc)[:120],
                exc_info=exc,
            )
            return None
        body = str(payload.get("answer", "")).strip() if isinstance(payload, dict) else ""
        if not body:
            logger.warning(
                "[Synthesizer] section '%s' empty; abandoning section-wise path", section.title
            )
            return None
        return _strip_duplicate_section_heading(body, section.title)

    # Sections write CONCURRENTLY (AGENTS 4.6): the writes are independent
    # I/O and MAX_PARALLEL_LLM still bounds actual provider concurrency.
    # gather preserves submission order, so assembled section order matches
    # the outline regardless of completion order; any failed/empty section
    # abandons the whole path exactly as the serial loop did.
    jobs = [
        (section, section_cited)
        for (section, _), section_cited in zip(groups, facts_by_section)
        if section_cited
    ]
    if jobs:
        bodies = await asyncio.gather(
            *(_write_section(section, section_cited) for section, section_cited in jobs)
        )
        for (section, _), body in zip(jobs, bodies):
            if body is None:
                return None
            written_sections += 1
            section_bodies.append(f"## {section.title}\n\n{body}")

    if written_sections < 2:
        return None

    logger.info("[Synthesizer] section-wise report: %d sections assembled", len(section_bodies))
    return _finalize(
        "\n\n".join(section_bodies),
        query=query,
        ctx=ctx,
        usable_facts=usable_facts,
        contradictions=contradictions,
        cited_facts=cited_facts,
        numbered=numbered,
        profile=profile,
        mode=mode,
        angles=_angles_of(cited_facts),
        temporal=temporal,
        independence=independence,
    )
