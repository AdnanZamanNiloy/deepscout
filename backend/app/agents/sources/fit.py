from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

from app.agents.sources.tiers import (
    SourceProfile,
    TIER_INDUSTRY,
    TIER_LOW,
    TIER_MEDIA,
    TIER_OFFICIAL,
    TIER_PEER_REVIEWED,
    TIER_PREPRINT,
    TIER_REFERENCE,
    TIER_SECONDARY,
    classify_source,
)
from app.agents.sources.urls import (
    canonical_url,
    extract_domain,
)

TIER_FIT: Dict[str, Dict[str, float]] = {
    # a filing/official disclosure is the record itself
    "filing": {
        TIER_OFFICIAL: +0.30, TIER_INDUSTRY: +0.10,
        TIER_REFERENCE: -0.35, TIER_PEER_REVIEWED: -0.20, TIER_PREPRINT: -0.20,
        TIER_MEDIA: -0.15, TIER_SECONDARY: -0.10,
    },
    # official counts and datasets
    "statistical": {
        TIER_OFFICIAL: +0.30, TIER_REFERENCE: +0.10, TIER_INDUSTRY: +0.05,
        TIER_PEER_REVIEWED: -0.10, TIER_MEDIA: -0.10, TIER_SECONDARY: -0.05,
    },
    # studies and trials
    "academic": {
        TIER_PEER_REVIEWED: +0.30, TIER_PREPRINT: +0.22, TIER_REFERENCE: +0.05,
        TIER_OFFICIAL: -0.10, TIER_MEDIA: -0.20, TIER_SECONDARY: -0.15,
        TIER_LOW: -0.30,
    },
    # statutes, regulations, rulings
    "legal": {
        TIER_OFFICIAL: +0.35,
        TIER_REFERENCE: -0.20, TIER_MEDIA: -0.15, TIER_SECONDARY: -0.15,
        TIER_LOW: -0.30,
    },
    # what just happened
    "current": {
        TIER_MEDIA: +0.25, TIER_OFFICIAL: +0.20, TIER_INDUSTRY: +0.10,
        TIER_REFERENCE: -0.20, TIER_PEER_REVIEWED: -0.10,
    },
    # weighed against alternatives
    "comparison": {
        TIER_INDUSTRY: +0.22, TIER_OFFICIAL: +0.20, TIER_PEER_REVIEWED: +0.10,
        TIER_REFERENCE: -0.05, TIER_LOW: -0.25,
    },
    # definitions and orientation
    "encyclopedic": {
        TIER_REFERENCE: +0.28, TIER_OFFICIAL: +0.12,
        TIER_LOW: -0.30, TIER_SECONDARY: -0.10,
    },
}


def evidence_fit(profile: "SourceProfile", evidence_types: Iterable[str]) -> Tuple[float, Tuple[str, ...]]:
    """How well a source's tier matches the evidence the question demands.

    Returns the summed adjustment and the reason strings, so a low-scoring
    result can be explained rather than silently discarded. An empty
    requirement (question type not recognised) fits everything: this steers
    ranking, it never blocks retrieval on its own.
    """
    total = 0.0
    why: List[str] = []
    for ev in evidence_types or ():
        table = TIER_FIT.get(ev)
        if not table:
            continue
        delta = table.get(profile.tier, 0.0)
        if delta:
            total += delta
            why.append(f"{ev} fit {profile.tier} {delta:+.2f}")
    return round(total, 3), tuple(why)


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b", re.I)


_ARXIV_RE = re.compile(r"\barxiv[:\s/]*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)


_DOI_URL_RE = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s\"'<>]+)", re.I)


_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.I)


_PMID_RE = re.compile(r"\bpmid[:\s]*(\d{6,9})\b", re.I)


_ISBN_RE = re.compile(r"\bisbn[:\s]*((?:97[89])?\d{9}[\dxX])\b", re.I)


def detect_primary_refs(*texts: str) -> Tuple[str, ...]:
    """Stable identifiers for originals referenced in the given text.

    Looks at URLs and bare strings alike, so "as shown in doi:10.1234/x" and a
    doi.org link resolve the same. Normalised (DOIs lower-cased, arXiv version
    suffixes dropped) so the same original found two ways collapses to one key.
    """
    blob = " ".join(t for t in texts if t)
    if not blob:
        return ()
    found: List[str] = []

    for m in _DOI_URL_RE.finditer(blob):
        found.append("doi:" + m.group(1).rstrip(".,;)").lower())
    for m in _DOI_RE.finditer(blob):
        found.append("doi:" + m.group(0).rstrip(".,;)").lower())

    for m in _ARXIV_URL_RE.finditer(blob):
        found.append("arxiv:" + m.group(1))
    for m in _ARXIV_RE.finditer(blob):
        ver = m.group(1)
        found.append("arxiv:" + ver.split("v")[0])

    for rx, pre in ((_PMID_RE, "pmid:"), (_ISBN_RE, "isbn:")):
        for m in rx.finditer(blob):
            found.append(pre + m.group(1).lower())

    return tuple(dict.fromkeys(found))


def underlying_source_key(url: str, title: str = "", snippet: str = "", content: str = "") -> str:
    """Identity of the UNDERLYING source, not of the page quoting it.

    Preference order matters: an explicit identifier names the original
    outright, so it beats a title guess. Two pages carrying the same key are
    republications of one source and must not be counted as independent
    corroboration.
    """
    refs = detect_primary_refs(url, title, snippet, content)
    if refs:
        return refs[0]
    return canonical_url(url) or (url or "").strip().lower()


def is_original_source(url: str, title: str = "", snippet: str = "", content: str = "") -> bool:
    """True when this page IS the original rather than a page about it."""
    domain = extract_domain(url)
    refs = detect_primary_refs(url, title, snippet, content)
    if not refs:
        # No identifier to check against: judge by whether the host is itself a
        # recognised original-publisher or repository.
        prof = classify_source(url)
        return prof.tier in (TIER_OFFICIAL, TIER_PEER_REVIEWED, TIER_PREPRINT)
    host = domain.lower()
    if refs[0].startswith("doi:"):
        return "doi.org" in host
    if refs[0].startswith("arxiv:"):
        return "arxiv.org" in host
    if refs[0].startswith("pmid:"):
        return any(d in host for d in ("pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"))
    return False
