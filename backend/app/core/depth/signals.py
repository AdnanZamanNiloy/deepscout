from __future__ import annotations

from typing import Any, Dict, List

from app.core.logging import get_logger

logger = get_logger(__name__)


from app.core.depth.constants import (
    AXIS_DOMINANCE_THRESHOLD,
    DEFAULT_MINIMUM_SOURCES,
    SEVERE_SEVERITY,
)


def _planned_axes(state: Dict[str, Any]) -> List[str]:
    return sorted({
        str(q.get("axis", "general"))
        for q in state.get("sub_questions", [])
        if isinstance(q, dict) and str(q.get("axis", "")).strip()
    })


def _verified_facts(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = state.get("facts", [])
    if not any("verified" in f for f in facts):
        return list(facts)  # verification never ran; treat all as evidence
    return [f for f in facts if f.get("verified")]


def _url_to_axis(state: Dict[str, Any]) -> Dict[str, str]:
    """Map source URL -> axis via the search result's sub_question text and
    the planner's delegation contracts (sub_question -> axis)."""
    contract_axis: Dict[str, str] = {}
    for q in state.get("sub_questions", []):
        if isinstance(q, dict):
            question = str(q.get("question", "")).strip()
            axis = str(q.get("axis", "")).strip()
            if question and axis:
                contract_axis[question] = axis

    url_axis: Dict[str, str] = {}
    for result in state.get("search_results", []) or []:
        url = str(result.get("url", "")).strip()
        sub_q = str(result.get("sub_question", "")).strip()
        if url and sub_q in contract_axis:
            url_axis[url] = contract_axis[sub_q]
    return url_axis


def _axis_coverage(state: Dict[str, Any], minimum_sources: int) -> Dict[str, int]:
    """Verified-fact count per planned axis, attributed via the fact's source URL.

    Attribution order (the second is the fix for false "uncovered axis"
    reports): a fact is credited to the planner axis of its source URL's
    sub-question; when the URL mapping is unavailable (the search results that
    would supply it have been trimmed, or the fact was re-sourced during
    corroboration), the fact's OWN `axis` field is used. Without the fallback a
    fully-researched pool whose URL→axis map was empty reported every axis as a
    hard hole, and those false holes flowed on as "unknown" noise.
    """
    verified = _verified_facts(state)
    url_axis = _url_to_axis(state)
    counts: Dict[str, int] = {axis: 0 for axis in _planned_axes(state)}
    if not counts:
        return counts
    for fact in verified:
        url = str(fact.get("source", "")).strip()
        axis = url_axis.get(url, "")
        if axis not in counts:
            # Fall back to the fact's own axis (stamped by the summarizer from
            # its contract) before treating the fact as unattributed.
            axis = str(fact.get("axis", "") or "").strip().lower()
        if axis in counts:
            counts[axis] += 1
    return counts


def _axes_below_threshold(state: Dict[str, Any], minimum_sources: int) -> List[str]:
    counts = _axis_coverage(state, minimum_sources)
    return [axis for axis, n in counts.items() if n < minimum_sources]


def _axes_covered(state: Dict[str, Any], minimum_sources: int) -> List[str]:
    """Planned axes that have at least `minimum_sources` verified facts."""
    counts = _axis_coverage(state, minimum_sources)
    return [axis for axis, n in counts.items() if n >= minimum_sources]


def _uncovered_axes(state: Dict[str, Any]) -> List[str]:
    """Planned axes with ZERO verified facts attributed — hard coverage holes.

    Distinct from `_axes_below_threshold` (which uses the per-contract source
    floor): an axis nobody has any evidence for is a hole in the research, and
    must block a soft stop regardless of how confident the pool looks overall.
    """
    counts = _axis_coverage(state, DEFAULT_MINIMUM_SOURCES)
    return [axis for axis, n in counts.items() if n <= 0]


def _severe_contradictions(state: Dict[str, Any]) -> int:
    """Unresolved contradictions strong enough to block a confident finish.

    A contradiction resolved by the Fix C pass (different period/scope/metric)
    is an EXPLAINED spread, not a disagreement — it must not keep driving
    expansion or blocking a stop.
    """
    total = 0
    for c in state.get("contradictions", []) or []:
        if not isinstance(c, dict):
            continue
        if c.get("resolved"):
            continue
        kind = str(c.get("kind", "") or "")
        try:
            severity = float(c.get("severity", 0.0) or 0.0)
        except (TypeError, ValueError):
            severity = 0.0
        if severity >= SEVERE_SEVERITY or kind in ("numeric", "polarity"):
            total += 1
    return total


def _needs_corroboration_count(state: Dict[str, Any]) -> int:
    """Important (quantitative/definitional) claims still resting on one
    publisher. Pure helper over the evidence spine; grading failure is neutral."""
    facts = [f for f in state.get("facts", []) or [] if isinstance(f, dict)]
    if not facts:
        return 0
    try:
        from app.core.evidence_grade import grade_facts

        graded = grade_facts(facts, contradictions=state.get("contradictions") or [])
    except Exception as exc:  # grading must never change routing
        logger.warning("depth_corroboration_grading_failed", error=str(exc), exc_info=exc)
        return 0
    count = 0
    for g in graded:
        ev = g.get("evidence") if isinstance(g, dict) else None
        if isinstance(ev, dict) and ev.get("needs_corroboration"):
            count += 1
    return count


def _summary_claims(state: Dict[str, Any]) -> List[str]:
    """Claim texts the executive summary / key findings will draw on.

    Mirrors `workflow._summary_claim_texts` (kept local to avoid importing the
    graph module into the stopping hot path). Sourced from explicit state keys
    when present; deterministic, bounded, and empty when synthesis has not run
    yet — callers then rank on the other impact signals.
    """
    texts: List[str] = []
    for key in ("summary_claims", "key_findings", "headline_claims"):
        for item in state.get(key) or []:
            if isinstance(item, dict):
                text = str(item.get("claim", "") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                texts.append(text)
    answer = str(state.get("synthesized_answer", "") or "")
    if answer:
        for raw in answer.splitlines():
            line = raw.strip().lstrip("-*# ").strip()
            if 20 <= len(line) <= 200:
                texts.append(line)
            if len(texts) >= 12:
                break
    return texts[:12]


def _high_impact_uncorroborated(state: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    """High-impact single-publisher claims, ranked, with their impact score.

    Impact is the same deterministic policy the corroboration procurement uses
    (`evidence_completion.rank_completion_targets`): quantitative claims and
    claims the executive summary / key findings uses lead. `needs_corroboration`
    alone is a broad net (a peripheral definitional remark qualifies); the
    adaptive loop should spend its remaining passes on the claims that would
    change the ANSWER. Returns [] on empty/ungradeable input — never raises, so
    grading can never change routing on failure.
    """
    facts = [f for f in state.get("facts", []) or [] if isinstance(f, dict)]
    if not facts:
        return []
    try:
        from app.core.evidence_completion import rank_completion_targets

        ranked = rank_completion_targets(
            facts,
            state.get("contradictions") or [],
            summary_claims=_summary_claims(state),
            limit=max(1, int(limit)),
        )
    except Exception as exc:  # ranking must never change routing
        logger.warning("depth_completion_ranking_failed", error=str(exc), exc_info=exc)
        return []
    out: List[Dict[str, Any]] = []
    for record in ranked:
        if not isinstance(record, dict):
            continue
        claim = str(record.get("claim", "") or "").strip()
        if not claim:
            continue
        out.append({
            "claim": claim[:200],
            "impact": int(record.get("impact", 0) or 0),
            "has_numbers": bool(record.get("has_numbers", False)),
        })
    return out


def _exhausted_claim_keys(state: Dict[str, Any]) -> set:
    """Normalized keys of claims recorded as EXHAUSTED in investigation state.

    An exhausted claim was already targeted for corroboration, its attempt
    budget is spent and it is STILL single-source — it is an acknowledged
    limitation, not an actionable gap. The depth controller must not keep
    expanding for it. Keys reuse `investigation_state.investigation_key`
    (= `planner.normalize_text`) so they join the high-impact claim list
    without a parallel key scheme.

    Total/fail-safe: a missing, None, garbage or empty state yields an empty
    set; a keying/import failure is logged and also yields an empty set — so
    routing can never be changed by a malformed state (AGENTS.md 4.4).
    """
    raw = state.get("investigation_state")
    if not raw:
        return set()
    try:
        from app.core.investigation_state import (
            STATUS_EXHAUSTED,
            investigation_key,
            sanitize_investigation_state,
        )

        inv = sanitize_investigation_state(raw)
        keys = set()
        for key, entry in inv.items():
            if not isinstance(entry, dict) or entry.get("status") != STATUS_EXHAUSTED:
                continue
            claim = str(entry.get("claim", "") or "")
            keys.add(key)
            if claim:
                # The stored key is already the normalized claim; adding the
                # re-derived key makes a hand-built state with a drifted key
                # still match the claim text.
                derived = investigation_key(claim)
                if derived:
                    keys.add(derived)
        return keys
    except Exception as exc:
        logger.warning("depth_investigation_lookup_failed", error=str(exc), exc_info=exc)
        return set()


def _norm_claim_key(claim: str) -> str:
    try:
        from app.agents.planner import normalize_text

        return normalize_text(claim)
    except Exception:
        return " ".join(str(claim or "").lower().split())


def _thin_dimensions(state: Dict[str, Any], min_facts: int = 1) -> List[str]:
    """Planned research dimensions whose evidence is too thin to finalize on.

    A dimension is thin when:
      * it is a planned axis/sub-question (from `sub_questions`) with fewer than
        `min_facts` verified facts attributed to it (source-URL → axis), OR
      * it has facts but a primary-source share at/below
        `evidence_completion.PRIMARY_THIN_THRESHOLD` (the same threshold the
        primary-source follow-up channel uses — no parallel system).

    Deterministic and total: a grading/import failure yields [] so a bug can
    never wedge the loop. Unattributed facts (no sub_question) never create a
    thin dimension; a dimension with no facts at all is already an uncovered
    axis and is reported separately.
    """
    facts = [f for f in state.get("facts", []) or [] if isinstance(f, dict)]
    if not facts:
        return []
    planned = {
        str(q.get("question", "") or "").strip()
        for q in state.get("sub_questions", []) or []
        if isinstance(q, dict) and str(q.get("question", "") or "").strip()
    }
    if not planned:
        return []
    # Dimension attribution needs the summarizer's `sub_question` stamp. When
    # NO fact carries it (hand-built states, legacy runs, resume rebuilds), the
    # input is absent — thinness is unmeasurable, so report none rather than
    # falsely flag every planned dimension (backwards-compatible rule).
    stamped = [
        f for f in facts if str(f.get("sub_question", "") or "").strip()
    ]
    if not stamped:
        return []
    thin: List[str] = []
    # (a) planned dimensions with no verified fact attributed at all.
    have: Dict[str, int] = {}
    for f in stamped:
        dim = str(f.get("sub_question", "") or "").strip()
        have[dim] = have.get(dim, 0) + 1
    for dim in sorted(planned):
        if have.get(dim, 0) < min_facts:
            thin.append(dim)
    # (b) planned dimensions that have facts but are primary-source thin.
    try:
        from app.core.evidence_completion import (
            PRIMARY_THIN_THRESHOLD,
            dimension_primary_share,
        )

        shares = dimension_primary_share(stamped)
        for dim in sorted(planned):
            if dim in thin:
                continue
            if dim in shares and shares[dim] <= PRIMARY_THIN_THRESHOLD:
                thin.append(dim)
    except Exception as exc:  # primary-share is an ADDITIONAL signal only
        logger.warning("depth_thin_dimension_share_failed", error=str(exc), exc_info=exc)
    return thin


def _axis_imbalance(state: Dict[str, Any]) -> bool:
    verified = _verified_facts(state)
    if not verified:
        return False
    url_axis = _url_to_axis(state)
    counts: Dict[str, int] = {}
    for f in verified:
        axis = url_axis.get(str(f.get("source", "")).strip(), "unattributed")
        counts[axis] = counts.get(axis, 0) + 1
    total = len(verified)
    return any(n / total > AXIS_DOMINANCE_THRESHOLD for n in counts.values())
