from __future__ import annotations

import re
from typing import Dict, Iterable

from app.core.logging import get_logger

logger = get_logger(__name__)

from app.agents.sources.tiers import (
    TIER_SECONDARY,
    classify_source,
)
from app.agents.sources.urls import (
    canonical_url,
    extract_domain,
)

UNDOCUMENTED_AUTHORITY = 0.34


_DOCUMENTARY_ID_RE = re.compile(
    r"\b(?:"
    r"10\.\d{4,9}/[-._;()/:a-z0-9]+"          # DOI
    r"|doi\.org/"                             # DOI link
    r"|arxiv[:\s/]*\d{4}\.\d{4,5}"            # arXiv id
    r"|pubmed[:\s]*\d{6,9}"                   # PMID
    r"|pmid[:\s]*\d{6,9}"
    r"|isbn[:\s]*\d{9,13}"                    # ISBN
    r"|issn[:\s]*\d{4}-\d{3}[\dxX]"
    r"|et al\.|\bpp?\. ?\d+[-–]\d+"           # academic citation furniture
    r")",
    re.I,
)


_DOCUMENTARY_VOCAB_RE = re.compile(
    r"\b(?:"
    r"official statistics|official data|government data|statistics office"
    r"|national statistics|statistical release|methodolog(?:y|ies)"
    r"|sample size|sampling frame|confidence interval|margin of error"
    r"|peer[- ]reviewed|in peer review|journal of|abstract|references"
    r"|bibliograph(?:y|ies)|annual report|quarterly report|financial statement"
    r"|balance sheet|prospectus|press release|all rights reserved"
    r"|ministry of|department of|central bank|regulator(?:y|s)?|statutory"
    r"|pursuant to|entered into force|official gazette|court of"
    r"|terms of use|privacy policy|cite this"
    r")",
    re.I,
)


_PROMOTIONAL_RE = re.compile(
    r"\b(?:"
    r"buy now|shop now|check price|best price|lowest price|discount|coupon"
    r"|promo code|deal[s]? of the day|affiliate|sponsored( content| post)?"
    r"|advertisement|buy our|click here to buy|add to cart|free shipping"
    r"|our top picks|top \d+ (?:best|picks|reasons)|buying guide"
    r"|what to look for when (?:you )?(?:buy|choosing)"
    r"|unboxing|product review|before you buy|is it worth it"
    r")",
    re.I,
)


_KEYWORD_STUFFED_HOST_RE = re.compile(r"^(?:[a-z0-9]+-){3,}[a-z0-9]+$", re.I)


def looks_keyword_stuffed(url: str) -> bool:
    """A hostname assembled from four or more hyphen-separated keywords.

    Only the leftmost label is judged: that is the part a domain squatter
    stuffs ("solar-panel-installers" in solar-panel-installers.co.uk), and it is
    the part that stays constant while the public suffix varies. Four labels
    minimum, because two-word brands with hyphens ("state-of-the-art-labs") are
    common and legitimate.
    """
    domain = extract_domain(url)
    if not domain or "." not in domain:
        return False
    return bool(_KEYWORD_STUFFED_HOST_RE.match(domain.split(".")[0]))


def has_documentary_evidence(*texts: str) -> bool:
    """Does this page carry the marks of a document rather than a commentary?"""
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return False
    return bool(_DOCUMENTARY_ID_RE.search(blob) or _DOCUMENTARY_VOCAB_RE.search(blob))


def looks_promotional(*texts: str) -> bool:
    """Does this page identify itself as a buying guide or affiliate page?"""
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return False
    return bool(_PROMOTIONAL_RE.search(blob))


def documentary_authority(url: str, *texts: str) -> float:
    """Authority for an unregistered domain, conditioned on positive evidence.

    Returns `classify_source(url).authority` unchanged for every registered
    publisher (the tier registries already say what a publisher is), and for an
    unregistered domain whose page shows no sign of being non-evidential or no
    text at all to judge by. Returns UNDOCUMENTED_AUTHORITY only when the
    hostname is keyword-stuffed or the page text is a buying guide/affiliate
    listicle, and documentary evidence rescues it either way.
    """
    profile = classify_source(url)
    if profile.tier != TIER_SECONDARY:
        return profile.authority
    if not any((t or "").strip() for t in texts):
        return profile.authority
    if has_documentary_evidence(*texts):
        return profile.authority
    if looks_keyword_stuffed(url) or looks_promotional(*texts):
        return UNDOCUMENTED_AUTHORITY
    return profile.authority


def authority_score(url: str) -> float:
    """0.0-1.0 authority. 0.0 means "do not cite" (blocked/low-trust)."""
    return classify_source(url).authority


def is_primary_source(url: str) -> bool:
    return classify_source(url).is_primary


def primary_source_share(urls: Iterable[str]) -> float:
    """Fraction of DISTINCT documents that are primary sources."""
    seen: Dict[str, bool] = {}
    for url in urls or []:
        key = canonical_url(url)
        if key and key not in seen:
            seen[key] = classify_source(url).is_primary
    if not seen:
        return 0.0
    return sum(1 for v in seen.values() if v) / len(seen)
