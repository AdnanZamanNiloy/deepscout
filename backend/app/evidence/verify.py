"""Deterministic claim-vs-source verification for the audit reconstruction.

The arch-upgrade (commit 2af9fc22) removed the verifier that scored every
claim against its cited source's text. The audit bridge reconstructs facts
from the report and, until now, stamped ``verified: True`` unconditionally
while never writing ``verification_score`` — so the confidence engine's
claim-verification signal read a structural zero on every live run while
citation coverage read a structural 1.0.

This module restores the measurement at the audit seam, keeping the deleted
verifier's weighting semantics: rare/long terms count for more than boilerplate,
and numeric grounding and direction (polarity) are hard failures. LLM-free by
design — it runs over the whole reconstructed pool on every request.

One deliberate difference from the deleted verifier: source reliability is NOT
re-gated here. The confidence engine scores domain trust separately
(``source_quality``); folding it into the support verdict would double-penalize
the same signal.

Output keys mirror the deleted verifier's contract (``verified``,
``verification_score``, ``verification_reason``, ``verification_checks``) so
every existing consumer reads it unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Set

from app.evidence.numbers import numbers_grounded
from app.evidence.polarity import claim_polarity
from app.evidence.sources import canonical_url

# Weighted overlap at or above this ratio means the claim's key terms are
# present in the cited source's text.
MIN_OVERLAP_RATIO = 0.35

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "that", "this",
    "these", "those", "it", "its", "as", "by", "at", "from", "which",
    "who", "what", "when", "where", "why", "how", "can", "could", "will",
    "would", "has", "have", "had", "not", "no", "but", "into", "about",
    "over", "after", "before", "between", "more", "most", "also", "than",
    "then", "their", "there", "such", "may", "might", "must", "per",
}

# Very common in research prose; matching on these proves nothing.
_LOW_INFORMATION = {
    "research", "study", "studies", "data", "report", "reports", "analysis",
    "results", "based", "used", "using", "including", "important", "significant",
    "different", "various", "many", "several", "often", "generally", "however",
    "example", "examples", "information", "system", "systems", "process",
    "new", "high", "low", "large", "small", "year", "years", "time",
}


def _tokens(text: str) -> List[str]:
    return [
        t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
        if t not in _STOPWORDS
    ]


def _term_weight(term: str) -> float:
    """Rarity proxy: longer, non-boilerplate terms carry more evidentiary load.

    A real IDF table would need a corpus the pipeline does not have; length plus
    a stop-list of research boilerplate is the crude-but-effective substitute
    the deleted verifier used, so scores keep their historical scale.
    """
    if term in _LOW_INFORMATION:
        return 0.35
    if term.isdigit():
        return 1.6
    if len(term) >= 10:
        return 1.5
    if len(term) >= 7:
        return 1.2
    return 1.0


def _weighted_overlap(claim_terms: Sequence[str], source_text: str) -> float:
    if not claim_terms:
        return 0.0
    source_tokens: Set[str] = set(_tokens(source_text))
    total = 0.0
    hit = 0.0
    for term in claim_terms:
        weight = _term_weight(term)
        total += weight
        if term in source_tokens:
            hit += weight
    return (hit / total) if total else 0.0


def source_text_map(sources: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """Map source URL -> best available text (content, then snippet).

    Keyed by BOTH the raw and canonical URL: the claim's stored source string
    may differ cosmetically from the search result's (trailing slash, tracking
    parameter), and a lookup miss would mark a claim unverifiable purely for
    URL formatting.
    """
    mapping: Dict[str, str] = {}
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "") or "").strip()
        if not url:
            continue
        content = str(item.get("content", "") or "")
        snippet = str(item.get("snippet", "") or "")
        text = content if content.strip() else snippet
        if not text.strip():
            continue
        for key in {url, canonical_url(url)}:
            if key:
                existing = mapping.get(key, "")
                if len(text) > len(existing):
                    mapping[key] = text
    return mapping


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _polarity_check(claim: str, source_text: str) -> tuple[bool, str]:
    """Compare the claim's polarity to the most similar source sentence.

    Returns (ok, detail). Neutral claims and claims with no comparable sentence
    pass: a false positive here silently marks good evidence unverified.
    """
    claim_polarity_value = claim_polarity(claim)
    if claim_polarity_value == 0:
        return True, "claim is directionally neutral"

    claim_tokens = set(_tokens(claim))
    if not claim_tokens:
        return True, "no comparable terms"

    best_sentence = ""
    best_overlap = 0.0
    for sentence in _SENTENCE_SPLIT.split(source_text or "")[:400]:
        tokens = set(_tokens(sentence))
        if not tokens:
            continue
        overlap = len(claim_tokens & tokens) / len(claim_tokens)
        if overlap > best_overlap:
            best_overlap, best_sentence = overlap, sentence

    if best_overlap < 0.55 or not best_sentence:
        return True, "no closely matching passage to compare direction"

    source_polarity = claim_polarity(best_sentence)
    if source_polarity == 0 or source_polarity == claim_polarity_value:
        return True, "direction consistent"
    return False, f"source passage polarity {source_polarity} vs claim {claim_polarity_value}"


def verify_claim(
    claim: str,
    source: str | Sequence[str],
    sources: Dict[str, str],
) -> Dict[str, Any]:
    """Verify one claim against its cited source's text.

    ``source`` may be a single URL or SEVERAL cited URLs: a synthesized
    sentence can cite more than one page ("Taken together, X and Y..."), and
    judging it against only the first citation marked true cross-source
    reasoning as unsupported by construction — the same bug class the
    answer-support check already fixed for its synthesis sentences. With
    several citations the claim is judged against the UNION of their captured
    texts: a figure present in any cited source counts as grounded, and a
    claim contradicting the only comparable sentence still fails polarity.

    Returns the deleted verifier's output contract: ``verified`` (passed every
    hard check), ``verification_score`` (weighted lexical overlap, 0-1),
    ``verification_reason`` and ``verification_checks``. Total: malformed input
    yields an explicit failed verdict, never an exception.
    """
    candidates = [source] if isinstance(source, str) else list(source or [])
    texts: List[str] = []
    for url in candidates:
        url = str(url or "").strip()
        if not url:
            continue
        text = sources.get(url) or sources.get(canonical_url(url)) or ""
        if text.strip() and text not in texts:
            texts.append(text)
    union = "\n".join(texts)
    checks: Dict[str, Any] = {"source_reachable": bool(union)}
    if not union:
        return {
            "verified": False,
            "verification_score": 0.0,
            "verification_reason": "source content unavailable to verify claim",
            "verification_checks": checks,
        }
    text = union

    overlap = _weighted_overlap(_tokens(claim), text)
    numeric_ok = numbers_grounded(claim, text)
    polarity_ok, polarity_detail = _polarity_check(claim, text)
    checks.update({
        "lexical_overlap": round(overlap, 3),
        "numbers_grounded": numeric_ok,
        "polarity_consistent": polarity_ok,
    })

    failures: List[str] = []
    if overlap < MIN_OVERLAP_RATIO:
        failures.append(f"weighted overlap {overlap:.2f} < {MIN_OVERLAP_RATIO}")
    if not numeric_ok:
        failures.append("a figure in the claim does not appear in the source")
    if not polarity_ok:
        failures.append(f"claim direction contradicts the source ({polarity_detail})")

    verified = not failures
    reason = (
        f"verified: overlap {overlap:.2f}"
        + (", numbers grounded" if re.search(r"\d", claim or "") else "")
        if verified
        else "; ".join(failures)
    )
    return {
        "verified": verified,
        "verification_score": round(overlap, 3),
        "verification_reason": reason,
        "verification_checks": checks,
    }
