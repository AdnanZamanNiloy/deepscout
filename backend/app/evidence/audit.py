"""Bridge the research pipeline's output to the audit core.

The research pipeline (``app.research``, driven by
``app.runtime.orchestrator``) produces a markdown report and a list of retrieved
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

from app.common.logging import get_logger

logger = get_logger(__name__)

# A markdown link/citation the pipeline's writer emits, e.g.
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


def parse_source_citations(source_citations: Sequence[Any]) -> List[str]:
    """Pull the URLs out of the run's ``sources`` citation strings.

    The pipeline's writer emits ``sources`` as a list of markdown/APA strings
    (``"-  Title, year, Author [source url](url)"``). Raw dicts (our own
    captured sources) are also accepted for robustness.
    """
    urls: List[str] = []
    seen: set = set()
    for entry in source_citations or []:
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
    source_citations: Sequence[Any],
    report: str,
) -> List[Dict[str, Any]]:
    """Merge every source signal into one deduped, metadata-rich index.

    Priority for metadata (title/content/published_at) is the captured search
    result (it has the real page body and dates), falling back to the run's
    citation text and finally to the URL alone. Order: captured sources first
    (retrieval order), then cited URLs not already present, then any URL
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

    for entry in source_citations or []:
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


_HEADING_LINE_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LEGEND_LINE_RE = re.compile(r"^\s*\[?\d+\]?[.)]?\s+\S.*—\s*\S+\s*$")


def _match_section(heading: str, section_titles: Sequence[str]) -> str:
    """Map a report heading to the plan section title it stands for.

    Case/whitespace-insensitive exact match only — a fuzzy match could attach a
    claim to the wrong research angle, which is worse than no attribution. The
    raw heading is returned when nothing matches, so cross-source grouping still
    distinguishes the claims by section.
    """
    norm = lambda s: " ".join(str(s or "").lower().split())  # noqa: E731
    target = norm(heading)
    for title in section_titles or []:
        if norm(title) == target:
            return str(title).strip()
    return str(heading or "").strip()


def _sentence_source_map(
    text: str,
    known_urls: Sequence[str],
    section_titles: Sequence[str] | None = None,
) -> List[Tuple[str, List[str], str]]:
    """Pair each claim-bearing sentence with its cited URL(s) and section.

    The pipeline writes prose with inline markdown links. A sentence that carries
    a link is attributed to that link; a sentence with no link inherits the most
    recently seen link (the writer tends to link once and keep discussing the
    source). Returns ``(claim, urls, section)`` triples, claim already cleaned —
    ``urls`` lists every known link of the sentence in order, so the verifier
    can judge a multi-citation sentence against the union of its sources
    (attributing it to only the first citation marked cross-source synthesis
    unsupported by construction).

    Section attribution: an H2/H3 heading matching a plan section title (or an
    unmatched H2) starts a new section context; deeper unmatched headings keep
    the current one (a subsection belongs to its parent section). This is what
    lets the confidence engine group corroboration and coverage at the angle
    level instead of per claim.
    """
    from app.evidence import clean_snippet_text, split_into_sentences

    # Headings and legend lines are structure, not claims ("RAG Summary
    # Retrieval augmented generation..." must never become one claim), but the
    # headings themselves are TRACKED: each prose block is attributed to the
    # section heading above it.
    groups: List[Tuple[str, List[str]]] = []
    current_section = ""
    block: List[str] = []

    def _flush() -> None:
        if block:
            groups.append((current_section, list(block)))
            block.clear()

    for line in (text or "").splitlines():
        heading = _HEADING_LINE_RE.match(line)
        if heading:
            _flush()
            level, title = len(heading.group(1)), heading.group(2).strip()
            if level == 1:
                current_section = ""  # report title is not a research angle
                continue
            matched = _match_section(title, section_titles or [])
            if matched != title:
                current_section = matched  # a plan section title (any level)
            elif level == 2:
                current_section = title  # an unmatched H2 is its own angle
            # An unmatched H3+ is a subsection: it keeps its parent's context.
            continue
        if _LEGEND_LINE_RE.match(line):
            continue
        block.append(line)
    _flush()

    known = set(known_urls or [])
    pairs: List[Tuple[str, List[str], str]] = []
    last_url = ""
    for section, lines in groups:
        for sentence in split_into_sentences("\n".join(lines), max_sentences=400):
            links = _extract_urls(sentence)
            cited: List[str] = []
            for candidate in links:
                if candidate in known and candidate not in cited:
                    cited.append(candidate)
            if not cited and links:
                cited = [links[-1]]
            if cited:
                last_url = cited[0]
            claim = clean_snippet_text(_MD_LINK_RE.sub(r"\1", sentence))
            if not claim or len(claim) < 30:
                continue
            if not cited and last_url:
                cited = [last_url]
            if not cited:
                continue
            pairs.append((claim, cited, section))
    return pairs


def _attach_corroboration(facts: List[Dict[str, Any]]) -> None:
    """Attach cross-publisher corroborators for claims restated elsewhere.

    The confidence engine's cross-source agreement has a MEASURED path
    (``corroboration_count`` per fact) and a weak pairwise fallback for pools
    that lack the field. The arch-upgrade deleted the pass that wrote it, so
    live runs fell into the fallback and read ~0.07. This restores the input:
    for every un-corroborated fact, find a similar claim (the engine's own
    ``claim_similarity`` at its own corroboration band — one band, one metric)
    from a different registrable-domain publisher and attach it through
    ``apply_corroboration`` — the sanctioned write path that rejects
    same-publisher URLs.

    Deterministic, LLM-free, total: any failure leaves the pool untouched.
    """
    items = [
        f for f in facts or []
        if isinstance(f, dict)
        and str(f.get("claim", "")).strip()
        and str(f.get("source", "")).strip()
    ]
    if len(items) < 2:
        return
    try:
        from app.core.confidence import CORROBORATION_SIMILARITY, claim_similarity
        from app.core.evidence_grade import apply_corroboration, registrable_domain
    except Exception as exc:  # corroboration must never break the audit
        logger.warning("[AuditBridge] corroboration imports failed: %s", exc, exc_info=exc)
        return

    claims = [str(f.get("claim", "")) for f in items]
    domains = [
        registrable_domain(str(f.get("source", ""))) or ""
        for f in items
    ]
    attached = 0
    for i, fact in enumerate(items):
        try:
            if int(fact.get("corroboration_count", 1) or 1) >= 2:
                continue
        except (TypeError, ValueError):
            pass
        for j, other in enumerate(items):
            if i == j or domains[i] == domains[j] or not domains[j]:
                continue
            try:
                similar = claim_similarity(claims[i], claims[j]) >= CORROBORATION_SIMILARITY
            except Exception:
                continue
            if similar and apply_corroboration(fact, str(other.get("source", ""))):
                attached += 1
                break  # one independent corroborator is enough to score the angle
    if attached:
        logger.info("[AuditBridge] corroboration attached to %d claim(s)", attached)


def _classify_verify_reason(reason: str) -> str:
    """Primary failure category of one fact's verification reason.

    A failed verdict lists every failure it hit ("weighted overlap 0.12 <
    0.35; a figure in the claim does not appear in the source"); the FIRST
    category in this precedence is what most fundamentally blocks the claim,
    so the census counts each failing fact exactly once instead of
    double-counting multi-failure claims.
    """
    lowered = (reason or "").lower()
    if "content unavailable" in lowered:
        return "no_text"
    if "figure" in lowered:
        return "numeric"
    if "direction contradicts" in lowered:
        return "polarity"
    if "overlap" in lowered:
        return "overlap"
    return "other"


def _log_verification_mix(facts: Sequence[Dict[str, Any]]) -> None:
    """One-line census of why claims verified or failed.

    The confidence score's dominant drags are the citation-coverage and
    verification signals; without this line the mix of causes (missing source
    text vs lexical overlap vs numeric grounding) is invisible in production
    logs, and a change to the verifier or the source capture cannot be
    evaluated against the next run's realities.
    """
    if not facts:
        return
    counts = {"overlap": 0, "numeric": 0, "no_text": 0, "polarity": 0, "other": 0}
    verified = 0
    for fact in facts:
        if fact.get("verified"):
            verified += 1
        else:
            counts[_classify_verify_reason(str(fact.get("verification_reason", "")))] += 1
    logger.info(
        "[AuditBridge] verification: %d/%d verified; failures: "
        "overlap=%d, numeric=%d, no_text=%d, polarity=%d, other=%d",
        verified, len(facts), counts["overlap"], counts["numeric"],
        counts["no_text"], counts["polarity"], counts["other"],
    )


def build_facts(
    report: str,
    source_index: Sequence[Dict[str, Any]],
    query: str,
    *,
    sections: Sequence[str] | None = None,
    max_facts: int = 120,
) -> List[Dict[str, Any]]:
    """Reconstruct a backend-shaped fact pool from the run's report.

    Each fact mirrors what the summarizer emits (``claim``, ``source``,
    ``confidence``, ``published_at``, ``search_type``, ``is_primary``,
    ``source_tier``, ``direct_quote``) so every retained-core consumer —
    confidence, evidence grading, contradictions, citation support — reads it
    unchanged. Verification (``verified`` / ``verification_score`` /
    ``verification_reason``) is MEASURED per claim against the captured source
    text (app.evidence.verify) instead of stamped true — a claim whose source
    text is unavailable is explicitly unverified, not assumed good. Fails safe
    to ``[]``.
    """
    try:
        from app.evidence import claim_query_overlap
        from app.evidence.sources import classify_source
        from app.evidence.verify import source_text_map, verify_claim

        url_meta: Dict[str, Dict[str, Any]] = {
            str(s.get("url")): s for s in source_index or [] if s.get("url")
        }
        known_urls = list(url_meta.keys())
        if not report or not known_urls:
            return []

        text_map = source_text_map(source_index or [])
        pairs = _sentence_source_map(report, known_urls, sections or [])
        facts: List[Dict[str, Any]] = []
        seen_claims: set = set()
        for claim, urls, section in pairs:
            key = claim.lower()[:120]
            if key in seen_claims:
                continue
            seen_claims.add(key)
            url = urls[0]
            meta = url_meta.get(url, {})
            profile = classify_source(url)
            relevance = claim_query_overlap(query, claim)
            # Grounding: a cited sentence's confidence blends topical relevance
            # with the source's authority, exactly like the extractive fallback
            # in the summarizer (so the same numerical scale is preserved).
            confidence = round(min(1.0, (0.30 * max(0.4, relevance)) + (0.70 * profile.authority)), 3)
            verdict = verify_claim(claim, urls, text_map)
            fact = {
                "claim": claim,
                "source": url,
                "confidence": confidence,
                "agent": "pipeline",
                "sub_question": section,
                "sense": "",
                "direct_quote": claim[:240],
                "verified": bool(verdict.get("verified")),
                "verification_score": float(verdict.get("verification_score", 0.0) or 0.0),
                "verification_reason": str(verdict.get("verification_reason", "") or ""),
                "verification_checks": verdict.get("verification_checks") or {},
                "published_at": str(meta.get("published_at", "") or ""),
                "search_type": str(meta.get("search_type", "") or "general"),
                "is_primary": bool(meta.get("is_primary") or profile.is_primary),
                "source_tier": profile.tier,
                "extraction": "pipeline",
            }
            if len(urls) > 1:
                # Kept additively: `source` remains the primary (first) citation
                # for every existing consumer (grading, diversity, freshness);
                # `sources` records the full citation set verification used.
                fact["sources"] = list(urls)
            facts.append(fact)
            if len(facts) >= max_facts:
                break
        _log_verification_mix(facts)
        _attach_corroboration(facts)
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
    critique: Optional[Dict[str, Any]] = None,
    sub_questions: Optional[List[Dict[str, Any]]] = None,
    degraded: Optional[List[str]] = None,
    provider_degraded: bool = False,
) -> Dict[str, Any]:
    """Run the retained audit core over the reconstructed fact pool.

    Returns a dict of audit fields ready to drop into ``final_state`` and the
    ``final_report`` wire frame. Every component is independently guarded, so
    the worst case is a subset of fields, never an exception.

    ``critique`` and ``sub_questions`` are the run's real fact-check verdict
    and the plan's research angles. Passing them is what keeps
    ``critic_survival`` and ``axis_coverage`` from being constants: an empty
    critique reads as a hedge (0.6) on every run, and without the plan the
    engine can never tell a covered angle from an unplanned one.
    """
    from app.core.confidence import compute_confidence
    from app.core.contradictions import find_contradictions

    contradictions: List[Dict[str, Any]] = []
    try:
        contradictions = find_contradictions(facts) if facts else []
    except Exception as exc:
        logger.warning("[AuditBridge] contradiction detection failed: %s", exc, exc_info=exc)

    # Support is measured against the run's emitted report + legend, when a
    # legend exists; otherwise it is left None so the confidence engine keeps
    # its historical weights (no signal is invented).
    support = answer_support
    if support is None and facts and report:
        try:
            from app.evidence import parse_answer_legend, verify_answer_support

            _, legend = parse_answer_legend(report)
            if legend:
                support = verify_answer_support(report, facts)
        except Exception as exc:
            logger.warning("[AuditBridge] answer support failed: %s", exc, exc_info=exc)

    confidence: Dict[str, Any] = {"overall": 0.0, "signals": {}}
    # The run's degradation state reaches the engine from HERE, and it used to
    # reach it from nowhere. `compute_confidence` is the only place that applies
    # DEGRADED_CAP, so a call site that omits `degraded` silently disables the
    # guard — and this call site is the only production caller. That is exactly
    # the failure the cap was added for: an extractive fallback self-verifies,
    # so every evidence signal reads high and a run assembled without a model
    # would still look confident.
    #
    # Both inputs default to the request's live degradation record (a
    # non-destructive read — routes still reads it afterwards for the banner),
    # so no caller can disable the guard again by forgetting an argument.
    if degraded is None or not provider_degraded:
        try:
            from app.core.degradation import has_provider_degradation, take_fallbacks
        except Exception as exc:  # degradation state must never break the audit
            logger.warning("[AuditBridge] degradation state unreadable", exc_info=exc)
        else:
            if degraded is None:
                degraded = list(take_fallbacks())
            if not provider_degraded:
                provider_degraded = has_provider_degradation()
    try:
        confidence = compute_confidence(
            facts,
            critique=critique or {},
            iteration=1,
            max_iterations=1,
            contradictions=contradictions,
            answer_support=support,
            sub_questions=sub_questions,
            degraded=list(degraded or []),
            provider_degraded=bool(provider_degraded),
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
        from app.quality.metrics import assess_report_quality

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
        # The engine's own account of how it scored: which signals it could
        # not measure, and any cap it applied. Without these the panel shows a
        # number with no way to ask why — and a degraded cap silently costing
        # 0.3 points is precisely the unexplained number this project refuses
        # to ship.
        "confidence_notes": list(confidence.get("notes", []) or []),
        "answer_support": support,
        "evidence_distribution": evidence_distribution,
        "quality": quality,
    }
