"""Fact ranking, stratification and near-duplicate compression.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Pure, deterministic helpers over fact dicts — no LLM, no network.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Set, Tuple

from app.agents.evidence_utils import extract_numbers, semantic_similarity

from app.agents.synthesis.primitives import _corroboration
from app.agents.synthesis.sections import _shorten_heading


def _angles_of(cited_facts: Sequence[Dict[str, Any]]) -> List[str]:
    """Distinct sub-questions covered by the cited facts, in first-seen order."""
    angles: List[str] = []
    for fact in cited_facts:
        sub_question = str(fact.get("sub_question", "") or "").strip()
        if sub_question and sub_question not in angles:
            angles.append(sub_question)
    return angles


def _has_numeric_facts(facts: Sequence[Dict[str, Any]], minimum: int = 2) -> bool:
    count = 0
    for fact in facts:
        if re.search(r"\d", str(fact.get("claim", "") or "")):
            count += 1
            if count >= minimum:
                return True
    return False


def _stratified_top_facts(
    facts: Sequence[Dict[str, Any]], per_angle: int = 6, cap: int = 30
) -> List[Dict[str, Any]]:
    """Top facts round-robin across sub-questions instead of pure confidence
    order. Pure ranking lets one low-scoring source domain bury a whole angle
    (observed live: the market angle cut from a top-20 while two definition
    angles filled it). Every angle keeps up to `per_angle` facts.
    """
    ranked = sorted(facts or [], key=_fact_confidence, reverse=True)
    by_angle: Dict[str, List[Dict[str, Any]]] = {}
    for fact in ranked:
        key = str(fact.get("sub_question", "") or "").strip()
        by_angle.setdefault(key, []).append(fact)
    picked: List[Dict[str, Any]] = []
    for i in range(max(1, per_angle)):
        for bucket in by_angle.values():
            if len(bucket) > i:
                picked.append(bucket[i])
                if len(picked) >= max(1, cap):
                    return picked
    return picked


def _fact_confidence(fact: Any) -> float:
    try:
        return float(fact.get("confidence", 0.0))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _compress_to_themes(
    facts: Sequence[Dict[str, Any]],
    *,
    similarity_threshold: float = 0.72,
) -> List[Dict[str, Any]]:
    """Collapse near-duplicate claims into one thematic entry per group.

    Compression here only merges claims that are already near-identical, keeps
    one representative, and records how many sources asserted it in
    `corroboration_count`. Distinct claims pass through untouched, in their
    original order — dropping them would weaken the verification guarantees.

    Deterministic and pure — no LLM, no network. Safe to run on every path.
    """
    kept: List[Dict[str, Any]] = []
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        claim = str(fact.get("claim", "") or "").strip()
        if not claim:
            continue
        merged = False
        for existing in kept:
            if semantic_similarity(claim, str(existing.get("claim", ""))) < similarity_threshold:
                continue
            # Numeric guard: two claims that share wording but carry DIFFERENT
            # quantities are not the same assertion. Merging them silently
            # replaced specific evidence ("$11.5bn" vs "$12.7bn") with one
            # generic representative. Keep them separate.
            if _distinct_quantities(str(existing.get("claim", "")), claim):
                continue
            # Same assertion restated: keep the better-supported copy and count
            # the rest as corroboration rather than discarding them. The count
            # is computed BEFORE any swap and re-applied after — the previous
            # version incremented it and then wiped it with `clear()/update()`,
            # losing the corroboration it had just measured.
            total_corroboration = _corroboration(existing) + 1
            if _fact_confidence(fact) > _fact_confidence(existing):
                preserved_claim = existing["claim"]
                existing.clear()
                existing.update(fact)
                existing["claim"] = preserved_claim
            existing["corroboration_count"] = total_corroboration
            merged = True
            break
        if not merged:
            kept.append(dict(fact))
    return kept


def _quantity_signature(text: str) -> Set[Tuple[float, str]]:
    """(value, unit) pairs in a claim, unit-normalized for comparison."""
    return {
        (round(q.value, 4), str(q.unit or "").lower())
        for q in extract_numbers(text, limit=12)
    }


def _distinct_quantities(a: str, b: str) -> bool:
    """True when two claims carry non-overlapping quantity sets.

    Worded near-identically but quantifying differently (a different figure,
    year, or unit) means they are DIFFERENT evidence, not a restatement. Only
    claims with no numbers on either side, or with a shared quantity, are safe
    to merge.
    """
    sig_a = _quantity_signature(a)
    sig_b = _quantity_signature(b)
    if not sig_a or not sig_b:
        return False
    return not (sig_a & sig_b)


def _section_title(sub_question: str) -> str:
    """Humanize a grouping key for a `## ` header."""
    text = (sub_question or "").strip()
    if not text:
        return ""
    title = _shorten_heading(text)
    return title[0].upper() + title[1:] if title else ""
