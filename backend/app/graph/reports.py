"""Report and audit rendering.

Split out of `workflow.py`. These build the delivered artifacts from state:
the primary answer document, and the separate audit/trace. Keeping them apart
from the graph makes the important invariant legible in one place -- the primary
answer carries no process metadata, and everything the pipeline wants to say
about itself goes to the audit instead.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.core import depth_controller
from app.core.decision import build_decision_layer
from app.core.logging import get_logger

from app.graph.evidence import _prepare_supporting_evidence
from app.graph.state import ResearchState

logger = get_logger(__name__)


def build_direct_answer_report(state: ResearchState) -> str:
    """Report for a direct (no-research) answer: the answer text only.

    The provenance (no sources, model self-assessment, capped confidence) is
    already surfaced on the stream via the direct_answer event and in the
    confidence score. Emitting it again as Limitations/Confidence sections
    inside the report duplicated that disclosure and buried the answer, so
    the report body is exactly the answer.
    """
    return str(state.get("direct_answer", "") or "").strip()


def build_conversation_report(state: ResearchState) -> str:
    """Reply report for a social/meta turn (R5).

    A greeting is not a research question, so this carries no evidence,
    confidence scoring or limitations — only the fixed reply. Keeping it
    distinct from build_direct_answer_report avoids labelling a "hello" with
    "no external sources were searched", which would be absurd.
    """
    reply = str(state.get("direct_answer", "") or "").strip()
    return f"{reply}\n" if reply else ""


def build_markdown_report(
    state: ResearchState,
    decision_options: List[Dict[str, Any]] | None = None,
) -> str:
    """Return the PRIMARY ANSWER exactly as the synthesizer wrote it.

    This function used to wrap the synthesized answer in a universal report
    skeleton (`# Final Answer` → `# Supporting Evidence` → `# Contradictions` →
    `# Decision Layer` → `# Answer Quality` → `# Limitations` → `# Confidence
    Score`) for EVERY research run. That made the delivered answer read like a
    filled-in form regardless of question type, and it re-injected process
    metadata (quality score, confidence float, pipeline caveats) that the
    synthesizer's own prompt forbids in the body.

    The structure now emerges from the question and the evidence in the
    synthesizer; this function does not add headings. All of the disclosure
    content that used to live here is rendered by `build_answer_audit` into the
    separate audit layer, so nothing is lost and nothing leaks.
    """
    synthesized = str(state.get("synthesized_answer", "")).strip()
    if synthesized:
        # Strip inline citation markers and thematic breaks HERE, after
        # `evaluate_answer` has scored citation density from them (line ~2172)
        # and only from the writer's prose. Doing it earlier would zero the
        # density signal; doing it later would leave the delivered answer
        # carrying scoring scaffolding the reader never asked for.
        from app.agents.sources import clean_writer_prose

        return clean_writer_prose(synthesized)
    # Empty synthesis: a short, honest fallback — never a report skeleton with
    # placeholder sections. `build_answer_audit` still carries the measured
    # reason and the evidence, so the failure stays auditable.
    return (
        "A confident synthesis could not be generated from the available "
        "evidence. See the research audit for what was collected."
    )


def build_answer_audit(
    state: ResearchState,
    decision_options: List[Dict[str, Any]] | None = None,
) -> str:
    """Render the machine-owned audit/trace layer for a research run.

    Everything here is metadata about the RESEARCH, not part of the answer:
    evidence ledger, source conflicts, strategic options, the five-axis quality
    score, overall confidence and its caveats, and research provenance. It is
    emitted as a separate markdown document (the `final_audit` field) so a
    consumer that wants the primary answer only is never shown pipeline
    mechanics, and one that wants full traceability gets all of it.

    Nothing is hidden that was disclosed before — it moved, it did not vanish.
    """
    facts = _prepare_supporting_evidence(state.get("facts", []))
    critique = state.get("critique", {})
    confidence = float(state.get("confidence", 0.0))
    quality = state.get("quality") or {}
    if decision_options is None:
        decision_options = build_decision_layer(state)

    q = quality if isinstance(quality, dict) else {}

    # Compact five-axis summary. The per-axis numbers are audit material; the
    # answer itself expresses uncertainty in words.
    quality_line = ""
    if q.get("overall") is not None:
        quality_line = (
            f"Accuracy {q.get('accuracy', 0)}/100 · Relevance {q.get('relevance', 0)}/100 · "
            f"Evidence {q.get('evidence', 0)}/100 · Clarity {q.get('clarity', 0)}/100 · "
            f"Reasoning {q.get('reasoning', 0)}/100 — overall {q.get('overall', 0)}/100 "
            + ("(passed the quality gate)." if q.get("passed") else "(BELOW THRESHOLD).")
        )

    lines: List[str] = ["# Audit & Trace", ""]
    lines.extend(
        [
            "## Research confidence",
            f"{confidence:.2f}",
            "Estimated from evidence quality and critic assessment, not formal "
            "verification.",
            "",
        ]
    )
    if quality_line:
        lines.extend(["## Answer quality (measured)", quality_line, ""])

    # Answer conformance: whether the prose fit the question's shape, calibrated
    # inference, synthesised its conflicts, had proportional depth (Phase 12),
    # and was proportional to the QUESTION — answer-first, non-peripheral,
    # balanced comparison, complete decision (Phase 13). Measured state,
    # audit-only — never part of the primary answer.
    conformance = state.get("answer_conformance") or {}
    if isinstance(conformance, dict) and conformance.get("query_type"):
        conformance_line = (
            f"Question shape: {conformance.get('query_type')} · "
            f"conformance score {conformance.get('score', 0)} "
            f"(shape={'ok' if conformance.get('shape_conformant', True) else 'missed'}, "
            f"inference={'ok' if conformance.get('inference_calibrated', True) else 'missed'}, "
            f"conflict={'ok' if conformance.get('contradiction_synthesised', True) else 'missed'}, "
            f"uncertainty={'ok' if conformance.get('uncertainty_proportionate', True) else 'missed'}, "
            f"depth={'ok' if conformance.get('depth_fit', True) else 'missed'}, "
            f"answer-first={'ok' if conformance.get('central_conclusion_first', True) else 'missed'}, "
            f"proportional={'ok' if conformance.get('peripheral_proportionate', True) else 'missed'}, "
            f"comparison-balance={'ok' if conformance.get('comparison_balanced', True) else 'missed'}, "
            f"decision={'ok' if conformance.get('decision_complete', True) else 'missed'})."
        )
        lines.extend(["## Answer conformance (measured)", conformance_line, ""])

    # Thesis fidelity: whether the answer actually carried the analyst's brief —
    # its thesis, its insights, the relationships between them, the
    # counter-evidence — rather than restating the source material. Measured
    # state, audit-only, same contract as conformance above. It was computed and
    # stored on every run but had no renderer, so a run could silently lose the
    # analyst's framing and nothing in the audit recorded it.
    fidelity = state.get("thesis_fidelity") or {}
    if isinstance(fidelity, dict) and fidelity.get("score") is not None:
        fidelity_line = (
            f"Fidelity score {fidelity.get('score', 0)} · "
            f"thesis {'carried' if fidelity.get('thesis_reflected', False) else 'missed'} · "
            f"insights {fidelity.get('insights_carried', 0)}"
            f"/{fidelity.get('insights_total', 0)} · "
            f"relationships {fidelity.get('relationships_kept', 0)}"
            f"/{fidelity.get('relationships_total', 0)} · "
            f"counter-evidence {'kept' if fidelity.get('counter_kept', False) else 'dropped'} · "
            f"{'not' if fidelity.get('not_recitation', True) else ''}"
            f"{' ' if fidelity.get('not_recitation', True) else ''}recitation"
        )
        if fidelity.get("failures"):
            fidelity_line += f" — {len(fidelity['failures'])} issue(s): " + "; ".join(
                str(f) for f in fidelity["failures"][:3]
            )
        lines.extend(["## Thesis fidelity (measured)", fidelity_line, ""])

    # Research provenance: measured evidence accounting, conflicts, caveats.
    is_sufficient = bool(critique.get("is_sufficient", False))
    critique_reason = str(critique.get("reason", "")).strip()
    notes: List[str] = []
    if not is_sufficient and critique_reason:
        notes.append(critique_reason)
    early_stop_note = depth_controller.stop_reason(state)
    if early_stop_note:
        notes.append(early_stop_note)
    try:
        from app.agents.citation_check import citation_health_note

        health_note = citation_health_note(state.get("citation_health"))
        if health_note:
            notes.append(health_note)
    except Exception as exc:
        logger.warning("citation_health_note_failed", error=str(exc), exc_info=exc)
    notes.append(
        "Free-tier and public APIs were used; no paywalled or private databases "
        "were accessed."
    )
    if notes:
        lines.extend(["## Research notes", *[f"- {n}" for n in notes], ""])

    # Supporting evidence ledger (measured, with source).
    evidence_lines = [
        f"- {item.get('claim', '').strip()} ({item.get('source', '').strip()})"
        for item in facts[:10]
        if item.get("claim") and item.get("source")
    ]
    if evidence_lines:
        lines.extend(["## Evidence ledger", *evidence_lines, ""])

    contradictions = state.get("contradictions", [])
    if contradictions:
        contradiction_lines = []
        for c in contradictions[:5]:
            contradiction_lines.append(
                f"- \"{c.get('claim_a', '')[:140]}\" ({c.get('source_a', '')})"
            )
            contradiction_lines.append(
                f"  conflicts with \"{c.get('claim_b', '')[:140]}\" ({c.get('source_b', '')})"
            )
            if c.get("resolved"):
                contradiction_lines.append(f"  RESOLVED: {c.get('resolution', '')}")
        lines.extend(["## Source conflicts", *contradiction_lines, ""])

    if decision_options:
        decision_lines = []
        for o in decision_options:
            marker = " (RECOMMENDED)" if o.get("is_recommended") else ""
            decision_lines.append(
                f"- Option {o.get('option_label', '?')}{marker}: {o.get('description', '')}"
            )
            if o.get("rationale"):
                decision_lines.append(f"  Rationale: {o['rationale']}")
            if o.get("risk_note"):
                decision_lines.append(f"  Risk: {o['risk_note']}")
        lines.extend(["## Decision layer", *decision_lines, ""])

    # Measured provenance the synthesizer kept out of the answer (evidence
    # accounting, integrity findings). This is where it is rendered.
    machine_notes = state.get("synthesis_machine_notes") or []
    for note in machine_notes:
        text = str(note or "").strip()
        if text:
            lines.extend([text, ""])

    return "\n".join(lines).strip()
