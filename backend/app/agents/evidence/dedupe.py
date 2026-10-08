from __future__ import annotations

from typing import Any, Dict, List

from app.agents.sources import canonical_url
from app.core.logging import get_logger
from app.core.semantic import similarity_matrix

from app.agents.evidence.domain import (
    extract_domain,
)
from app.agents.evidence.polarity import (
    claim_polarity,
)
from app.agents.evidence.text import (
    normalize_claim_text,
)

logger = get_logger(__name__)


_REPRESENTATIVE_CONFIDENCE_MARGIN = 0.05


def _source_authority(fact: Dict[str, Any]) -> float:
    """0..1 authority for a fact's source, from the shared source registry.

    Prefers an explicit `source_tier`/`is_primary` already stamped upstream and
    falls back to classifying the URL. Total: an unknown source yields 0.0 and
    never raises (AGENTS.md 4.4).
    """
    if not isinstance(fact, dict):
        return 0.0
    if bool(fact.get("is_primary", False)):
        return 1.0
    try:
        from app.agents.sources import authority_score

        return float(authority_score(str(fact.get("source", "") or "")))
    except Exception as exc:  # authority failure must never break dedup
        logger.warning("representative_authority_failed", error=str(exc), exc_info=exc)
        return 0.0


def _pick_representative(kept: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Choose which of two equivalent facts represents the merged claim.

    Both support the SAME claim (the similarity + polarity guard already
    passed), so this is a presentation choice, not a truth choice — either
    source is valid evidence. Rule (§6 source authority priority):

      * a primary source beats a non-primary source when their confidences are
        within `_REPRESENTATIVE_CONFIDENCE_MARGIN` (a primary document must not
        lose its citation slot to a news summary of it just because the summary
        scored slightly higher);
      * otherwise the higher-authority source wins within that margin;
      * a clearly higher-confidence copy still wins regardless of authority, so
        a better-extracted or better-verified fact is never discarded.

    A verified copy must never be represented by an unverified one; that guard
    is applied by the caller after this choice.
    """
    if not isinstance(kept, dict):
        return candidate
    if not isinstance(candidate, dict):
        return kept
    kept_conf = float(kept.get("confidence", 0.0) or 0.0)
    cand_conf = float(candidate.get("confidence", 0.0) or 0.0)
    if abs(cand_conf - kept_conf) > _REPRESENTATIVE_CONFIDENCE_MARGIN:
        # A clearly better-confidence copy wins; authority does not override it.
        return candidate if cand_conf > kept_conf else kept
    kept_auth = _source_authority(kept)
    cand_auth = _source_authority(candidate)
    if cand_auth > kept_auth:
        return candidate
    if cand_auth < kept_auth:
        return kept
    # Equal authority: keep the higher-confidence copy, tie-break on the
    # existing representative (stable across input order).
    return candidate if cand_conf > kept_conf else kept


def dedupe_semantic_facts(
    facts: List[Dict[str, Any]], threshold: float = 0.86
) -> List[Dict[str, Any]]:
    """Collapse restatements of the same claim, preserving every field.

    Two changes from the previous implementation, both consequential:

    * The kept fact is the ORIGINAL dict (copied), not a 5-key reconstruction.
      `verified`, `verification_score`, `direct_quote`, `published_at`,
      `search_type` and any future field survive dedup.
    * A merge is recorded rather than discarded. Independent corroboration
      across domains is the single strongest confidence signal available, and
      it used to be thrown away. Merged facts gain:
        corroborating_sources  — distinct source URLs asserting the claim
        corroboration_count    — len(corroborating_sources)
        merged_claims          — the alternate phrasings seen
      Confidence is nudged up (capped at 0.97) per independent domain, which
      is what "cross-source agreement" means operationally.

    Performance: the full n x n hybrid-similarity matrix is computed once
    (vectorized TF-IDF matmul + gated lexical enrichment) and the greedy
    merge loop then runs on O(1) matrix lookups instead of re-scoring every
    candidate/kept pair — deep runs with hundreds of facts drop from seconds
    of SequenceMatcher work to milliseconds.
    """
    # Pass 1: normalize/sanitize candidates, keep original order.
    # Independent corroboration is measured per PUBLISHER (registrable domain),
    # never per URL — two pages on one domain are one source. The helper lives
    # in evidence_grade; a lazy import keeps this module's import graph acyclic
    # (evidence_grade imports names from this module).
    from app.core.evidence_grade import distinct_publisher_count

    candidates: List[Dict[str, Any]] = []
    for item in facts or []:
        if not isinstance(item, dict):
            continue
        claim = normalize_claim_text(str(item.get("claim", "")))
        source = str(item.get("source", "")).strip()
        if not claim or not source:
            continue
        try:
            confidence = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        candidate = dict(item)
        candidate["claim"] = claim
        candidate["source"] = source
        candidate["confidence"] = confidence
        candidate.setdefault("agent", str(item.get("agent", "") or ""))
        candidate.setdefault("sub_question", str(item.get("sub_question", "") or ""))
        candidates.append(candidate)

    if len(candidates) <= 1:
        for candidate in candidates:
            prior_sources = [
                str(u) for u in (candidate.get("corroborating_sources") or []) if str(u).strip()
            ]
            if not any(canonical_url(u) == canonical_url(candidate["source"]) for u in prior_sources):
                prior_sources.append(candidate["source"])
            try:
                prior_count = int(candidate.get("corroboration_count", 1) or 1)
            except (TypeError, ValueError):
                prior_count = 1
            candidate["corroborating_sources"] = prior_sources
            # Independence is per PUBLISHER, never per URL (AGENTS.md bug class:
            # two pages on one domain are not corroboration).
            candidate["corroboration_count"] = max(
                distinct_publisher_count(prior_sources), prior_count, 1
            )
        return candidates

    # Pass 2: one vectorized similarity matrix over all claims.
    claims = [c["claim"] for c in candidates]
    sim = similarity_matrix(claims)
    # Polarity guard: "X" and "not X" score ~0.90 — above the merge
    # threshold — and dedup used to fold them together, counting the
    # negating source as CORROBORATION of the claim (benchmark-found bug).
    polarities = [claim_polarity(c) for c in claims]

    # Pass 3: greedy incremental merge on matrix lookups.
    deduped: List[Dict[str, Any]] = []   # merged facts in emission order
    kept_idx: List[int] = []             # original-claim index backing each kept row
    for i, candidate in enumerate(candidates):
        merge_row = -1
        for row, orig in enumerate(kept_idx):
            if float(sim[i, orig]) >= threshold:
                pa, pb = polarities[i], polarities[orig]
                if pa != 0 and pb != 0 and pa != pb:
                    continue  # a claim never merges with its own negation
                merge_row = row
                break

        if merge_row == -1:
            # Corroboration already established upstream (e.g. the same claim
            # was matched across pages during extraction) must not be reset to
            # 1 just because this pass saw the fact once. Dedup only ever adds
            # evidence of agreement; it never removes it.
            prior_sources = [
                str(u) for u in (candidate.get("corroborating_sources") or []) if str(u).strip()
            ]
            if not any(canonical_url(u) == canonical_url(candidate["source"]) for u in prior_sources):
                prior_sources.append(candidate["source"])
            try:
                prior_count = int(candidate.get("corroboration_count", 1) or 1)
            except (TypeError, ValueError):
                prior_count = 1
            candidate["corroborating_sources"] = prior_sources
            candidate["corroboration_count"] = max(
                distinct_publisher_count(prior_sources), prior_count, 1
            )
            deduped.append(candidate)
            kept_idx.append(i)
            continue

        kept = deduped[merge_row]
        source = candidate["source"]
        corroborating: List[str] = list(kept.get("corroborating_sources") or [])
        known_domains = {extract_domain(u) for u in corroborating}
        new_domain = extract_domain(source)
        incoming = [str(u) for u in (candidate.get("corroborating_sources") or []) if str(u).strip()]
        if source not in incoming:
            incoming.append(source)
        seen_documents = {canonical_url(u) for u in corroborating}
        for extra in incoming:
            if canonical_url(extra) not in seen_documents:
                corroborating.append(extra)
                seen_documents.add(canonical_url(extra))

        claim = candidate["claim"]
        variants: List[str] = list(kept.get("merged_claims") or [])
        if claim != str(kept.get("claim", "")) and claim not in variants:
            variants.append(claim)

        winner = _pick_representative(kept, candidate)
        merged = dict(winner)
        merged["corroborating_sources"] = corroborating
        merged["corroboration_count"] = max(1, distinct_publisher_count(corroborating))
        if variants:
            merged["merged_claims"] = variants[:5]

        # Independent-domain agreement lifts confidence; a second copy from
        # the same domain does not (self-syndication is not corroboration).
        if new_domain and new_domain not in known_domains:
            base = float(merged.get("confidence", 0.0) or 0.0)
            merged["confidence"] = round(min(0.97, base + 0.04), 4)

        # A verified copy must never be replaced by an unverified one.
        if kept.get("verified") and not merged.get("verified"):
            merged["verified"] = True
            merged["verification_score"] = kept.get("verification_score")
            merged["verification_reason"] = kept.get("verification_reason")

        # The kept row is now backed by the winner's original claim text.
        kept_idx[merge_row] = i if winner is candidate else kept_idx[merge_row]
        deduped[merge_row] = merged

    return deduped
