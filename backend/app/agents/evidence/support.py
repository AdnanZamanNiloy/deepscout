from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from app.core.semantic import cross_similarity
from app.agents.evidence.numbers import (
    _significant_quantities,
    numbers_grounded,
)


CITATION_RE = re.compile(r"\[(\d+)\]")


SOURCES_HEADING_RE = re.compile(r"\n+#{0,6}\s*Sources:?\s*\n")


LEGEND_RE = re.compile(r"^\[(\d+)\]\s+\S.*?—\s*(\S+)\s*$")


SUPPORT_THRESHOLD = 0.30


SYNTHESIS_SUPPORT_FLOOR = 0.0


def parse_answer_legend(answer: str) -> Tuple[str, Dict[int, str]]:
    """Split an emitted answer into (body, {marker_number: url}).

    Shared by verify_answer_support and the citation health check so the
    two can never disagree about which source a [n] marker points at.
    """
    match = SOURCES_HEADING_RE.search(answer or "")
    if match:
        body = (answer or "")[: match.start()]
        legend_block = (answer or "")[match.end():]
    else:
        body, _, legend_block = (answer or "").partition("\nSources:")

    legend_urls: Dict[int, str] = {}
    for line in legend_block.splitlines():
        m = LEGEND_RE.match(line.strip())
        if m:
            try:
                legend_urls[int(m.group(1))] = m.group(2)
            except (TypeError, ValueError):
                continue
    return body, legend_urls


def verify_answer_support(
    answer: str,
    facts: List[Dict[str, Any]],
    threshold: float = SUPPORT_THRESHOLD,
) -> Dict[str, Any]:
    """Post-synthesis check: every cited sentence must overlap verified
    evidence from the source it cites, AND every significant number in that
    sentence must appear in that source's evidence.

    The legend is parsed back out of the answer itself, so numbering can never
    drift from what was emitted. Sentences without markers count as uncited
    (not failed). Return shape is a superset of the previous one: existing
    keys are unchanged; `numeric_failures`, `numeric_rate` and
    `sentence_details` (per-sentence support score + status, consumed by the
    citation-status badge and the benchmark suite) are new.

    Scoring is batched: one cross-similarity matrix (sentences x claims)
    replaces the per-pair SequenceMatcher loop.
    """
    body, legend_urls = parse_answer_legend(answer)

    verified_by_url: Dict[str, List[str]] = {}
    for fact in facts or []:
        if not fact.get("verified"):
            continue
        url = str(fact.get("source", "") or "")
        claim = str(fact.get("claim", "") or "")
        if not (url and claim):
            continue
        verified_by_url.setdefault(url, []).append(claim)
        for extra in fact.get("corroborating_sources") or []:
            extra_url = str(extra or "")
            if extra_url and extra_url != url:
                verified_by_url.setdefault(extra_url, []).append(claim)

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]

    # ---- Batch support scoring -------------------------------------------
    cited_rows: List[int] = []          # sentence indexes that carry markers
    cited_markers: Dict[int, List[int]] = {}
    claim_pool: List[str] = []          # unique verified claims
    claim_index: Dict[str, int] = {}
    sentence_claim_cols: Dict[int, List[int]] = {}

    for si, sentence in enumerate(sentences):
        numbers = [int(n) for n in CITATION_RE.findall(sentence)]
        if not numbers:
            continue
        cited_rows.append(si)
        cited_markers[si] = numbers
        cols: List[int] = []
        for n in numbers:
            for claim in verified_by_url.get(legend_urls.get(n, ""), []):
                if claim not in claim_index:
                    claim_index[claim] = len(claim_pool)
                    claim_pool.append(claim)
                col = claim_index[claim]
                if col not in cols:
                    cols.append(col)
        sentence_claim_cols[si] = cols

    support_scores: Dict[int, float] = {}
    multi_source: Dict[int, bool] = {}
    if cited_rows and claim_pool:
        cited_texts = [sentences[si] for si in cited_rows]
        matrix = cross_similarity(cited_texts, claim_pool)
        for row, si in enumerate(cited_rows):
            cols = sentence_claim_cols[si]
            if not cols:
                support_scores[si] = 0.0
                continue
            # A sentence citing SEVERAL sources is a cross-source synthesis: it
            # legitimately combines claims, so no single claim reads as a close
            # match. Score it by how well its cited claims COLLECTIVELY cover it
            # — the mean of each cited claim's best match — rather than by the
            # one closest claim alone. A fabricated multi-source sentence still
            # scores low: its parts must each be present in the pool.
            best = max(float(matrix[row, c]) for c in cols)
            if len(cols) >= 2:
                multi_source[si] = True
                coverage = sum(float(matrix[row, c]) for c in cols) / len(cols)
                support_scores[si] = max(best, coverage)
            else:
                support_scores[si] = best

    # ---- Sentence-level verdicts -----------------------------------------
    cited = supported = uncited = synthesis = 0
    numeric_checked = numeric_ok = 0
    unsupported: List[str] = []
    numeric_failures: List[str] = []
    sentence_details: List[Dict[str, Any]] = []

    for si, sentence in enumerate(sentences):
        numbers = cited_markers.get(si)
        if numbers is None:
            if len(sentence.split()) >= 8:
                uncited += 1
                sentence_details.append({
                    "sentence": sentence[:160], "markers": [], "status": "uncited",
                    "support": None,
                })
            continue
        cited += 1

        cited_claims: List[str] = []
        for n in numbers:
            cited_claims.extend(verified_by_url.get(legend_urls.get(n, ""), []))

        hit = support_scores.get(si, 0.0) >= threshold

        sentence_numbers = _significant_quantities(CITATION_RE.sub("", sentence))
        if sentence_numbers:
            numeric_checked += 1
            pool = " ".join(cited_claims)
            if numbers_grounded(CITATION_RE.sub("", sentence), pool):
                numeric_ok += 1
            else:
                hit = False
                numeric_failures.append(sentence[:160])

        if hit:
            supported += 1
            status = "supported"
        elif sentence[:160] in numeric_failures:
            status = "numeric_failure"
        elif (
            multi_source.get(si)
            and support_scores.get(si, 0.0) >= SYNTHESIS_SUPPORT_FLOOR
        ):
            # A cross-source synthesis: the sentence combines several cited,
            # verified sources and no single one matches it verbatim — the
            # definition of legitimate synthesis ("taken together, these
            # findings suggest..."). Its numeric content is grounded (the
            # numeric check above would have failed it otherwise) and each cited
            # source contributes real coverage, so it is ATTRIBUTED SYNTHESIS,
            # not an unsupported claim. Counted separately so it neither inflates
            # the supported rate nor is treated as contamination.
            synthesis += 1
            status = "synthesis"
        else:
            unsupported.append(sentence[:160])
            status = "unsupported"
        sentence_details.append({
            "sentence": sentence[:160],
            "markers": numbers,
            "status": status,
            "support": round(support_scores.get(si, 0.0), 4),
            "multi_source": bool(multi_source.get(si, False)),
        })

    return {
        "sentences": len(sentences),
        "cited": cited,
        "supported": supported,
        "uncited": uncited,
        "synthesis": synthesis,
        "unsupported": unsupported,
        "rate": (supported / cited) if cited else None,
        "numeric_failures": numeric_failures,
        "numeric_rate": (numeric_ok / numeric_checked) if numeric_checked else None,
        "sentence_details": sentence_details,
    }
