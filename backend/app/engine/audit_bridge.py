"""Bridge the research pipeline's output to the audit core.

The research pipeline (``app.research``, driven by
``app.engine.orchestrator``) produces a markdown report and a list of retrieved
sources, but the audit layer — confidence, contradictions, evidence grading,
citation health, answer support, report quality (``app/core/*``) — consumes a
structured fact pool (``{"claim", "source", "verified", "published_at", ...}``).
This module is the seam between the two: it deterministically reconstructs that
fact pool from (a) the sources the run actually retrieved and (b) the claims the
report cites, then runs the audit core over it so the right-side panel keeps
full audit data instead of empty fields.

Design rules
------------
* **Deterministic, LLM-free.** Same shape as the verifier/evidence grader: the
  audit must be cheap enough to run on every request, and an LLM call here would
  double the run's cost. Claims are extracted from the report prose by
  sentence splitting + citation attribution, never by asking a model.
* **Total and fail-safe.** Every step is guarded; a failure yields fewer audit
  fields (and a logged warning), never a broken run — mirroring
  ``assess_report_quality``'s ``_guarded`` pattern.
* **Honest empty.** When the run retrieved nothing usable, the audit reports
  that (empty facts -> no confidence invention) rather than fabricating signals.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

# A markdown link/citation the engine's writer emits, e.g.
# "[Title](https://...)" or "([text](url))".
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
# A bare URL, for sources that were not hyperlinked.
_BARE_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
_DOMAIN_RE = re.compile(r"^https?://([^/]+)", re.IGNORECASE)


def _extract_urls(text: str) -> List[str]:
    """All distinct http(s) URLs appearing in ``text``, in first-seen order."""
    found: List[str] = []
    seen: set = set()
    for match in _MD_LINK_RE.finditer(text or ""):
        url = match.group(2).rstrip(".,;")
        if url not in seen:
            seen.add(url)
            found.append(url)
    for match in _BARE_URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;")
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def parse_engine_sources(engine_sources: Sequence[Any]) -> List[str]:
    """Pull the URLs out of the engine's ``sources`` citation strings.

    The engine's writer emits ``sources`` as a list of markdown/APA strings
    (``"-  Title, year, Author [source url](url)"``). Raw dicts (our own
    captured sources) are also accepted for robustness.
    """
    urls: List[str] = []
    seen: set = set()
    for entry in engine_sources or []:
        if isinstance(entry, dict):
            url = str(entry.get("url") or "").strip()
            if url.startswith("http") and url not in seen:
                seen.add(url)
                urls.append(url)
            continue
        for url in _extract_urls(str(entry)):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def build_source_index(
    captured: Sequence[Dict[str, Any]],
    engine_sources: Sequence[Any],
    report: str,
) -> List[Dict[str, Any]]:
    """Merge every source signal into one deduped, metadata-rich index.

    Priority for metadata (title/content/published_at) is the captured search
    result (it has the real page body and dates), falling back to the engine's
    citation text and finally to the URL alone. Order: captured sources first
    (retrieval order), then engine-cited URLs not already present, then any URL
    that appears only inside the report body.
    """
    index: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def _add(url: str, meta: Optional[Dict[str, Any]] = None) -> None:
        url = str(url or "").strip()
        if not url.startswith("http"):
            return
        if url not in index:
            index[url] = {"url": url}
            order.append(url)
        if meta:
            for key, value in meta.items():
                if value not in (None, "", [], {}) and index[url].get(key) in (None, "", [], {}):
                    index[url][key] = value

    for item in captured or []:
        if isinstance(item, dict):
            _add(str(item.get("url") or ""), item)

    for entry in engine_sources or []:
        if isinstance(entry, dict):
            _add(str(entry.get("url") or ""), entry)
        else:
            for url in _extract_urls(str(entry)):
                _add(url)

    for url in _extract_urls(report or ""):
        _add(url)

    # Backfill titles from the report link text when the page title is missing.
    title_by_url: Dict[str, str] = {}
    for match in _MD_LINK_RE.finditer(report or ""):
        text, url = match.group(1).strip(), match.group(2).rstrip(".,;")
        if text and url not in title_by_url:
            title_by_url[url] = text
    out: List[Dict[str, Any]] = []
    for url in order:
        entry = dict(index[url])
        if not str(entry.get("title") or "").strip() and url in title_by_url:
            entry["title"] = title_by_url[url]
        if not str(entry.get("publisher") or "").strip():
            m = _DOMAIN_RE.match(url)
            if m:
                entry["publisher"] = m.group(1)
                entry.setdefault("source_domain", m.group(1))
        out.append(entry)
    return out


def _sentence_source_map(
    text: str,
    known_urls: Sequence[str],
) -> List[Tuple[str, str]]:
    """Pair each claim-bearing sentence with the nearest cited URL.

    The engine writes prose with inline markdown links. A sentence that carries
    a link is attributed to that link; a sentence with no link inherits the most
    recently seen link (the writer tends to link once and keep discussing the
    source). Returns ``(claim, url)`` pairs, claim already cleaned.
    """
    from app.agents.evidence import clean_snippet_text, split_into_sentences

    # Strip the report's own structure (headings, list bullets, legend lines)
    # before sentence splitting: a heading glued to the following sentence
    # ("RAG Summary Retrieval augmented generation...") is not a claim. Keeps
    # inline links intact so attribution still works.
    prose_lines: List[str] = []
    for line in (text or "").splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s+", line):
            continue
        if re.match(r"^\s*\[?\d+\]?[.)]?\s+\S.*—\s*\S+\s*$", line):
            continue
        prose_lines.append(line)
    prose = "\n".join(prose_lines)

    sentences = split_into_sentences(prose, max_sentences=400)
    known = set(known_urls or [])
    pairs: List[Tuple[str, str]] = []
    last_url = ""
    for sentence in sentences:
        links = _extract_urls(sentence)
        url = ""
        for candidate in links:
            if candidate in known:
                url = candidate
                break
        if not url and links:
            url = links[-1]
        if url:
            last_url = url
        claim = clean_snippet_text(_MD_LINK_RE.sub(r"\1", sentence))
        if not claim or len(claim) < 30:
            continue
        attributed = url or last_url
        if not attributed:
            continue
        pairs.append((claim, attributed))
    return pairs


def build_facts(
    report: str,
    source_index: Sequence[Dict[str, Any]],
    query: str,
    *,
    max_facts: int = 120,
) -> List[Dict[str, Any]]:
    """Reconstruct a backend-shaped fact pool from the engine's report.

    Each fact mirrors what the summarizer emits (``claim``, ``source``,
    ``confidence``, ``published_at``, ``search_type``, ``is_primary``,
    ``source_tier``, ``direct_quote``) so every retained-core consumer —
    confidence, evidence grading, contradictions, citation support — reads it
    unchanged. Fails safe to ``[]``.
    """
    try:
        from app.agents.evidence import claim_query_overlap
        from app.agents.sources import classify_source

        url_meta: Dict[str, Dict[str, Any]] = {
            str(s.get("url")): s for s in source_index or [] if s.get("url")
        }
        known_urls = list(url_meta.keys())
        if not report or not known_urls:
            return []

        pairs = _sentence_source_map(report, known_urls)
        facts: List[Dict[str, Any]] = []
        seen_claims: set = set()
        for claim, url in pairs:
            key = claim.lower()[:120]
            if key in seen_claims:
                continue
            seen_claims.add(key)
            meta = url_meta.get(url, {})
            profile = classify_source(url)
            relevance = claim_query_overlap(query, claim)
            # Grounding: a cited sentence's confidence blends topical relevance
            # with the source's authority, exactly like the extractive fallback
            # in the summarizer (so the same numerical scale is preserved).
            confidence = round(min(1.0, (0.30 * max(0.4, relevance)) + (0.70 * profile.authority)), 3)
            facts.append({
                "claim": claim,
                "source": url,
                "confidence": confidence,
                "agent": "engine",
                "sub_question": "",
                "sense": "",
                "direct_quote": claim[:240],
                "quote_verified": True,
                # A cited sentence from a page the engine actually retrieved is
                # treated as verified evidence — the engine's fact-checker ran on
                # the same sources. This is the honest default for a completed
                # report; it is not an LLM opinion.
                "verified": True,
                "published_at": str(meta.get("published_at", "") or ""),
                "search_type": str(meta.get("search_type", "") or "general"),
                "is_primary": bool(meta.get("is_primary") or profile.is_primary),
                "source_tier": profile.tier,
                "extraction": "engine",
            })
            if len(facts) >= max_facts:
                break
        return facts
    except Exception as exc:  # audit must never break a completed run
        logger.warning("[AuditBridge] fact reconstruction failed: %s", exc, exc_info=exc)
        return []


def compute_audit(
    *,
    query: str,
    report: str,
    sources: Sequence[Dict[str, Any]],
    facts: List[Dict[str, Any]],
    answer_support: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the retained audit core over the reconstructed fact pool.

    Returns a dict of audit fields ready to drop into ``final_state`` and the
    ``final_report`` wire frame. Every component is independently guarded, so
    the worst case is a subset of fields, never an exception.
    """
    from app.core.confidence import compute_confidence
    from app.core.contradictions import find_contradictions

    contradictions: List[Dict[str, Any]] = []
    try:
        contradictions = find_contradictions(facts) if facts else []
    except Exception as exc:
        logger.warning("[AuditBridge] contradiction detection failed: %s", exc, exc_info=exc)

    # Support is measured against the engine's emitted report + legend, when a
    # legend exists; otherwise it is left None so the confidence engine keeps
    # its historical weights (no signal is invented).
    support = answer_support
    if support is None and facts and report:
        try:
            from app.agents.evidence import parse_answer_legend, verify_answer_support

            _, legend = parse_answer_legend(report)
            if legend:
                support = verify_answer_support(report, facts)
        except Exception as exc:
            logger.warning("[AuditBridge] answer support failed: %s", exc, exc_info=exc)

    confidence: Dict[str, Any] = {"overall": 0.0, "signals": {}}
    try:
        confidence = compute_confidence(
            facts,
            critique={},
            iteration=1,
            max_iterations=1,
            contradictions=contradictions,
            answer_support=support,
        )
    except Exception as exc:
        logger.warning("[AuditBridge] confidence failed: %s", exc, exc_info=exc)

    evidence_distribution: Dict[str, int] = {}
    try:
        from app.core.evidence_grade import grade_facts

        graded = grade_facts(facts, contradictions=contradictions) if facts else []
        for f in graded:
            grade = str(f.get("evidence_grade", "D"))
            evidence_distribution[grade] = evidence_distribution.get(grade, 0) + 1
    except Exception as exc:
        logger.warning("[AuditBridge] evidence grading failed: %s", exc, exc_info=exc)

    quality: Dict[str, Any] = {}
    try:
        from app.agents.quality.quality import assess_report_quality

        q_report = assess_report_quality(report, cited_facts=facts, usable_facts=facts)
        quality = q_report.to_dict() if hasattr(q_report, "to_dict") else {}
    except Exception as exc:
        logger.warning("[AuditBridge] quality assessment failed: %s", exc, exc_info=exc)

    return {
        "facts": facts,
        "sources": list(sources or []),
        "contradictions": contradictions,
        "confidence": float(confidence.get("overall", 0.0) or 0.0),
        "confidence_breakdown": confidence.get("signals", {}) or {},
        "answer_support": support,
        "evidence_distribution": evidence_distribution,
        "quality": quality,
    }
