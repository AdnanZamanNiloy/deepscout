"""Source intelligence: primary-source registry, tiering, and freshness.

Why this module exists
----------------------
Before this, source quality was a five-branch TLD guess (`.gov` -> 0.92,
`.com` -> 0.58) plus a 19-entry authority set. That conflates two different
things a research system must keep separate:

  authority  — how much weight a domain's word carries
  primacy    — whether the domain PUBLISHED the fact or REPORTED on it

A statistics agency releasing a number and a blog quoting that number can
score identically on authority heuristics, yet only one is citable evidence.
This module classifies every URL on both axes, so the planner can steer
searches at primary publishers, the ranker can prefer them, and the
confidence engine can report what share of a report rests on primary
evidence.

Everything here is pure, offline, and dependency-free: no network, no LLM.
`evidence_utils.source_reliability_score` delegates to `authority_score`,
and every score the old function returned for a given URL is preserved
(see tests/test_agents_facade.py) so
existing thresholds elsewhere in the pipeline keep their meaning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

TIER_OFFICIAL = "official"        # statute/statistic/filing publisher; the record itself
TIER_PEER_REVIEWED = "peer_reviewed"  # journals, indexed proceedings
TIER_PREPRINT = "preprint"        # arXiv/bioRxiv/SSRN: primary but unrefereed
TIER_REFERENCE = "reference"      # encyclopedias, standards references
TIER_INDUSTRY = "industry"        # trade bodies, vendor docs, research firms
TIER_MEDIA = "media"              # journalism
TIER_SECONDARY = "secondary"      # aggregators, general web
TIER_LOW = "low"                  # UGC, SEO farms, social

# Authority ceilings per tier. Deliberately aligned with the legacy scale
# so downstream thresholds (0.55 keep, 0.60 strong, 0.62 facts) still mean
# what they meant before this module existed.
TIER_AUTHORITY: Dict[str, float] = {
    TIER_OFFICIAL: 0.95,
    TIER_PEER_REVIEWED: 0.95,
    TIER_PREPRINT: 0.88,
    TIER_REFERENCE: 0.86,
    TIER_INDUSTRY: 0.70,
    TIER_MEDIA: 0.68,
    TIER_SECONDARY: 0.58,
    TIER_LOW: 0.0,
}

# Tiers whose documents ARE the evidence rather than commentary on it.
PRIMARY_TIERS: frozenset[str] = frozenset(
    {TIER_OFFICIAL, TIER_PEER_REVIEWED, TIER_PREPRINT}
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Statistical / regulatory / treaty publishers. These publish the numbers
# everyone else quotes, which is exactly why a research system should reach
# them directly.
OFFICIAL_DOMAINS: Set[str] = {
    # Inter-governmental & multilateral
    "who.int", "un.org", "unctad.org", "unesco.org", "unep.org", "undp.org",
    "iaea.org", "irena.org", "iea.org", "oecd.org", "worldbank.org",
    "data.worldbank.org", "imf.org", "bis.org", "wto.org", "ilo.org",
    "fao.org", "ipcc.ch", "itu.int", "wipo.int", "eia.gov",
    # Statistics agencies & central banks
    "ec.europa.eu", "eurostat.ec.europa.eu", "ecb.europa.eu",
    "federalreserve.gov", "bls.gov", "census.gov", "bea.gov", "cbo.gov",
    "gao.gov", "sec.gov", "cdc.gov", "nih.gov", "fda.gov", "epa.gov",
    "energy.gov", "nrel.gov", "nasa.gov", "noaa.gov", "usgs.gov",
    "ons.gov.uk", "bankofengland.co.uk", "gov.uk", "statcan.gc.ca",
    "abs.gov.au", "rbi.org.in", "mospi.gov.in", "stat.go.jp", "boj.or.jp",
    "destatis.de", "bundesbank.de", "insee.fr", "istat.it",
    "bbs.gov.bd", "bb.org.bd",
    # Standards & registries
    "iso.org", "ietf.org", "rfc-editor.org", "w3.org", "ieee.org",
    "nist.gov", "iec.ch", "unicode.org", "ecma-international.org",
    "clinicaltrials.gov", "eur-lex.europa.eu", "congress.gov",
    "federalregister.gov", "supremecourt.gov", "courtlistener.com",
}

# Suffix-matched official patterns: a host ending in any of these is treated
# as an official publisher even when the exact host is not registered.
OFFICIAL_SUFFIXES: Tuple[str, ...] = (
    ".gov", ".gov.uk", ".gov.au", ".gov.in", ".gov.bd", ".gov.ca",
    ".gc.ca", ".gouv.fr", ".go.jp", ".go.kr", ".govt.nz", ".gov.sg",
    ".europa.eu", ".int", ".mil",
)

PEER_REVIEWED_DOMAINS: Set[str] = {
    "nature.com", "science.org", "sciencemag.org", "cell.com",
    "thelancet.com", "nejm.org", "bmj.com", "jamanetwork.com",
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "pnas.org",
    "sciencedirect.com", "springer.com", "link.springer.com",
    "wiley.com", "onlinelibrary.wiley.com", "tandfonline.com",
    "sagepub.com", "journals.sagepub.com", "cambridge.org",
    "academic.oup.com", "oup.com", "acm.org", "dl.acm.org",
    "ieeexplore.ieee.org", "aps.org", "journals.aps.org", "iopscience.iop.org",
    "aeaweb.org", "jstor.org", "plos.org", "journals.plos.org",
    "frontiersin.org", "mdpi.com", "elifesciences.org", "aclanthology.org",
    "jmlr.org", "neurips.cc", "proceedings.mlr.press", "openreview.net",
    "doi.org", "crossref.org", "semanticscholar.org", "nber.org",
    "royalsocietypublishing.org", "annualreviews.org",
}

PREPRINT_DOMAINS: Set[str] = {
    "arxiv.org", "biorxiv.org", "medrxiv.org", "chemrxiv.org",
    "ssrn.com", "papers.ssrn.com", "osf.io", "preprints.org",
    "researchsquare.com", "hal.science", "econpapers.repec.org", "repec.org",
}

REFERENCE_DOMAINS: Set[str] = {
    "britannica.com", "plato.stanford.edu", "iep.utm.edu",
    "encyclopedia.com", "oxfordreference.com", "merriam-webster.com",
    "en.wikipedia.org", "wikipedia.org", "wikidata.org", "ourworldindata.org",
    "mathworld.wolfram.com", "routledge.com",
}

INDUSTRY_DOMAINS: Set[str] = {
    "gartner.com", "forrester.com", "idc.com", "mckinsey.com",
    "bcg.com", "deloitte.com", "pwc.com", "kpmg.com", "ey.com",
    "statista.com", "spglobal.com", "moodys.com", "fitchratings.com",
    "bloomberg.com", "woodmac.com", "bnef.com", "rystadenergy.com",
    "seia.org", "iea-pvps.org", "epri.com", "lazard.com",
    "huggingface.co", "paperswithcode.com", "github.com", "openml.org",
    "mlcommons.org", "kaggle.com", "developer.mozilla.org",
    "docs.python.org", "python.org", "pytorch.org", "tensorflow.org",
    "kubernetes.io", "postgresql.org", "sqlite.org", "openai.com",
    "anthropic.com", "deepmind.google", "research.google",
}

MEDIA_DOMAINS: Set[str] = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "ft.com",
    "economist.com", "wsj.com", "nytimes.com", "washingtonpost.com",
    "theguardian.com", "npr.org", "cnbc.com", "axios.com", "politico.com",
    "arstechnica.com", "theverge.com", "wired.com", "technologyreview.com",
    "ieee-spectrum.org", "spectrum.ieee.org", "nikkei.com", "scmp.com",
    "aljazeera.com", "dw.com", "france24.com", "thedailystar.net",
}

# User-generated / SEO / social. Superset of the legacy LOW_QUALITY_DOMAINS
# list, which stays exported from evidence_utils for compatibility.
LOW_TRUST_DOMAINS: Set[str] = {
    "reddit.com", "quora.com", "zhihu.com", "baidu.com", "sohu.com",
    "csdn.net", "medium.com", "blogspot.com", "substack.com",
    "wordpress.com", "youtube.com", "youtu.be", "tiktok.com",
    "pinterest.com", "whatfix.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "linkedin.com", "tumblr.com", "wattpad.com",
    "answers.com", "ask.com", "wikihow.com", "ehow.com", "geeksforgeeks.org",
    "w3schools.com", "tutorialspoint.com", "javatpoint.com",
    "simplilearn.com", "coursera.org", "udemy.com", "scribd.com",
    "slideshare.net", "academia.edu", "chegg.com", "coursehero.com",
    "studocu.com", "brainly.com", "amazon.com", "ebay.com", "alibaba.com",
    "pinterest.co.uk", "vk.com", "weibo.com", "telegra.ph",
}

# Content-farm shapes that no domain list can keep up with. Matched against
# the full URL, not the host, so "/blog/top-10-best-..." style paths are
# recognized on otherwise-neutral domains.
SEO_PATH_RE = re.compile(
    r"/(?:top-?\d+|best-\d+|\d+-best|listicle|coupon|deals?|"
    r"casino|betting|essay-?writing|buy-?now)/",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceProfile:
    """Everything the pipeline knows about a URL before reading its text."""

    url: str
    domain: str
    tier: str
    authority: float
    is_primary: bool
    reasons: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "url": self.url,
            "domain": self.domain,
            "tier": self.tier,
            "authority": round(self.authority, 3),
            "is_primary": self.is_primary,
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

_TRACKING_PARAM_RE = re.compile(
    r"^(?:utm_|ga_|gclid$|fbclid$|mc_|ref$|ref_src$|referrer$|source$|"
    r"spm$|_hsenc$|_hsmi$|igshid$|si$|amp$)",
    re.IGNORECASE,
)


def extract_domain(url: str) -> str:
    """Lowercase host with a leading `www.` removed, or "" when unparseable."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return ""
    host = (parsed.netloc or "").lower().split("@")[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def canonical_url(url: str) -> str:
    """Collapse the many URLs that name one document into a single key.

    Strips tracking parameters, fragments, default ports, `amp` suffixes,
    duplicate slashes and a trailing slash, and normalizes the scheme to
    https. Without this, `?utm_source=...`, `#section`, and `http://` copies
    of the same page each consumed a separate fetch, a separate cache entry,
    and a separate slot in the "distinct sources" counts that gate the
    critic — inflating perceived source diversity with duplicates.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    if not parsed.netloc:
        return raw

    host = (parsed.netloc or "").lower().split("@")[-1]
    if host.endswith(":80") or host.endswith(":443"):
        host = host.rsplit(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]

    path = re.sub(r"/{2,}", "/", parsed.path or "")
    if path.endswith("/amp"):
        path = path[:-4]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query or "", keep_blank_values=True)
        if not _TRACKING_PARAM_RE.match(k)
    ]
    query_pairs.sort()

    return urlunparse(("https", host, path, "", urlencode(query_pairs), ""))


def _matches(domain: str, registry: Iterable[str]) -> bool:
    for entry in registry:
        entry = entry.lower().strip()
        if not entry:
            continue
        if domain == entry or domain.endswith(f".{entry}"):
            return True
    return False


def _has_suffix(domain: str, suffixes: Sequence[str]) -> bool:
    return any(domain == s.lstrip(".") or domain.endswith(s) for s in suffixes)


def classify_source(url: str) -> SourceProfile:
    """Assign tier, authority and primacy to a URL. Pure and deterministic."""
    domain = extract_domain(url)
    if not domain:
        return SourceProfile(url or "", "", TIER_LOW, 0.0, False, ("unparseable url",))

    reasons: List[str] = []

    if _matches(domain, LOW_TRUST_DOMAINS):
        return SourceProfile(url, domain, TIER_LOW, 0.0, False, ("low-trust domain",))
    if SEO_PATH_RE.search(url or ""):
        return SourceProfile(url, domain, TIER_LOW, 0.0, False, ("seo content-farm path",))

    tier: Optional[str] = None
    if _matches(domain, OFFICIAL_DOMAINS) or _has_suffix(domain, OFFICIAL_SUFFIXES):
        tier, why = TIER_OFFICIAL, "official publisher"
    elif _matches(domain, PEER_REVIEWED_DOMAINS):
        tier, why = TIER_PEER_REVIEWED, "peer-reviewed venue"
    elif _matches(domain, PREPRINT_DOMAINS):
        tier, why = TIER_PREPRINT, "preprint server"
    elif _matches(domain, REFERENCE_DOMAINS):
        tier, why = TIER_REFERENCE, "reference work"
    elif _matches(domain, INDUSTRY_DOMAINS):
        tier, why = TIER_INDUSTRY, "industry/vendor authority"
    elif _matches(domain, MEDIA_DOMAINS):
        tier, why = TIER_MEDIA, "established journalism"
    elif domain.endswith(".edu") or domain.endswith(".ac.uk") or ".edu." in domain:
        tier, why = TIER_PEER_REVIEWED, "academic institution"
    else:
        tier, why = TIER_SECONDARY, "unregistered domain"
    reasons.append(why)

    authority = TIER_AUTHORITY[tier]

    # Legacy TLD nudges, retained so unregistered domains keep their old
    # relative ordering (.org above .co above .com above the rest).
    if tier == TIER_SECONDARY:
        if domain.endswith(".org"):
            authority = 0.75
            reasons.append(".org nonprofit")
        elif domain.endswith(".co"):
            authority = 0.62
        elif domain.endswith(".com"):
            authority = 0.58
        else:
            authority = 0.55

    # Institutional floor: a university-hosted reference work (the Stanford
    # Encyclopedia, IEP) is not a mere tertiary source. Applied after tiering
    # so it lifts, never lowers.
    if domain.endswith(".edu") or domain.endswith(".ac.uk") or ".edu." in domain:
        if authority < 0.92:
            authority = 0.92
            reasons.append("academic institution floor")

    return SourceProfile(
        url=url,
        domain=domain,
        tier=tier,
        authority=round(authority, 3),
        is_primary=tier in PRIMARY_TIERS,
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# UNREGISTERED DOMAINS: citable on evidence, not on TLD
#
# Every unregistered `.com` used to score 0.58 purely for its suffix. That
# number clears three separate gates downstream:
#
#   is_high_quality_domain   requires authority > 0.0
#   verifier MIN_SOURCE_SCORE = 0.55
#   filter_search_results_by_domain's FALLBACK path admits >= 0.55 whenever
#     there are fewer than three strong results from two domains — i.e. on
#     exactly the hard queries where the junk matters most
#
# So a content farm on a neutral domain was reliably admitted, and the domain
# list could never keep up with new ones.
#
# TLD is not evidence of anything, so an unregistered domain is demoted on
# POSITIVE evidence that it is not a source: a hostname built from keywords, or
# a page whose own text says it is a buying guide / affiliate listicle. Absent
# that, the legacy suffix score stands.
#
# The asymmetry is the whole design. Demoting on the ABSENCE of documentary
# markers was tried first and was wrong: ordinary journalism lives on
# unregistered domains and carries no DOI or methodology section, so every
# unlisted news outlet was pushed below the citable bar and legitimate reporting
# was discarded with the junk. Absence of a marker is not evidence of absence,
# and a demotion rule that cannot tell a news report from a content farm is not
# safe to ship.
#
# `documentary_authority` is therefore used only to EXCLUDE on positive
# evidence, and only ever rescues. With no text to judge by, nothing is
# demoted: `classify_source` stays a pure URL classifier and every score it
# already returned is preserved.
# ---------------------------------------------------------------------------

# Authority floor for an unregistered domain with positive evidence of being
# non-evidential. Below verifier's 0.55, the summarizer filter's 0.55 fallback,
# and the 0.60/0.62 strong cuts, so it cannot be cited however it was retrieved.
UNDOCUMENTED_AUTHORITY = 0.34

# Persistent identifiers: a document that has one is a document.
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

# Vocabulary of a document that publishes data or a ruling, as opposed to a page
# commenting on one.
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

# A page that says what it is: a buying guide, an affiliate roundup, a sponsored
# listicle. Positive evidence of non-evidential content, unlike the absence of a
# marker.
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

# A hostname that is mostly hyphen-separated keywords is a domain built for a
# query, not a publisher. Four labels minimum: two-word brands with hyphens
# ("state-of-the-art-labs.com") are common and legitimate.
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


# ---------------------------------------------------------------------------
# JURISDICTION
#
# A `site:` operator is an EXCLUSION as much as an inclusion. Scoping a query
# to one publisher removes every other candidate from the result set, including
# the one that actually holds the answer — so a wrong guess does not merely
# rank badly, it returns nothing and spends a retrieval slot proving it.
#
# `PRIMARY_SOURCE_HINTS` is keyed on (search_type, domain), a pair that carries
# no information about WHICH COUNTRY a question is about. That produced
# systematically wrong national targeting:
#
#   statistical:general -> (worldbank.org, oecd.org, census.gov)
#       "population of Malawi", "electricity access in Bangladesh" were scoped
#       to the UNITED STATES Census Bureau, which publishes neither figure.
#   statistical:policy  -> (oecd.org, eurostat.ec.europa.eu, gov.uk)
#       questions about US and Indian policy were scoped to EU/UK publishers.
#   academic:general    -> (arxiv.org, doi.org, semanticscholar.org)
#       arXiv is physics/CS/mathematics; a humanities, legal or policy question
#       scoped to it cannot reach the law review or working paper that answers it.
#   news:general        -> (reuters.com, apnews.com, ft.com)
#       national primaries were never targetable at all.
#
# So national targeting is made JURISDICTION-AWARE: a publisher bound to one
# country may only be targeted by a question about that country (or about no
# country in particular), and a question that DOES name a country is pointed at
# that country's own official suffix family instead. Multilateral and
# supranational publishers (worldbank.org, imf.org, arxiv.org, doi.org,
# eurostat) publish globally or regionally and stay targetable everywhere.
#
# Asymmetric on purpose: mistaking a national publisher for a global one only
# ever makes a query BROADER (harmless). Mistaking a global publisher for a
# national one is what silently excluded the right answer.
# ---------------------------------------------------------------------------

# Country / territory -> the government and official-statistics suffix families
# that host its primary publishers. Used to aim a jurisdiction-specific query at
# the right country's own agencies instead of another country's.
#
# The broad family comes first in each tuple: a country's statistics office,
# regulator and ministries all sit under it, whereas a named ministry host
# (`bund.de`) is one publisher among dozens and excludes the rest. Entries are
# `site:`-ready, so the leading dot is dropped at use time.
COUNTRY_OFFICIAL_SUFFIX: Dict[str, Tuple[str, ...]] = {
    "ae": ("gov.ae",), "af": ("gov.af", "af"), "al": ("gov.al",),
    "am": ("gov.am",), "ao": ("gov.ao",), "ar": ("gob.ar", "gov.ar"),
    "at": ("gv.at", "at"), "au": ("gov.au",), "az": ("gov.az",),
    "ba": ("gov.ba",), "bd": ("gov.bd", "org.bd"), "be": ("gouv.be",),
    "bf": ("gov.bf",), "bg": ("government.bg", "bg"), "bh": ("gov.bh", "com.bh"),
    "bi": ("gov.bi",), "bj": ("gouv.bj",), "bo": ("gob.bo", "gov.bo"),
    "br": ("gov.br", "org.br"), "bw": ("gov.bw",), "by": ("gov.by", "by"),
    "ca": ("gc.ca", "gov.ca"), "cd": ("gouv.cd",), "cf": ("gov.cf",),
    "cg": ("gov.cg",), "ch": ("admin.ch", "ch"), "ci": ("gouv.ci",),
    "cl": ("gob.cl", "gov.cl"), "cm": ("gov.cm",), "cn": ("gov.cn",),
    "co": ("gov.co",), "cr": ("gob.cr", "go.cr"), "cu": ("gov.cu",),
    "cv": ("gov.cv",), "cy": ("gov.cy",), "cz": ("gov.cz",),
    "de": ("de",), "dj": ("gouv.dj",), "dk": ("gov.dk",),
    "do": ("gob.do", "gov.do"), "dz": ("gov.dz",), "ec": ("gob.ec", "gov.ec"),
    "ee": ("riik.ee", "ee"), "eg": ("gov.eg",), "er": ("gov.er",),
    "es": ("gob.es", "gov.es"), "et": ("gov.et",), "eu": ("europa.eu",),
    "fi": ("gov.fi",), "fj": ("gov.fj",), "fr": ("gouv.fr",),
    "ga": ("gouv.ga",), "gb": ("gov.uk", "ac.uk"), "ge": ("gov.ge",),
    "gh": ("gov.gh",), "gm": ("gov.gm",), "gn": ("gov.gn",),
    "gq": ("gob.gq",), "gr": ("gov.gr", "ge"), "gt": ("gob.gt", "gov.gt"),
    "gw": ("gov.gw",), "hk": ("gov.hk",), "hn": ("gob.hn",),
    "hr": ("gov.hr",), "ht": ("gouv.ht",), "hu": ("gov.hu",),
    "id": ("go.id",), "ie": ("gov.ie",), "il": ("gov.il",),
    "in": ("gov.in", "nic.in"), "iq": ("gov.iq",), "ir": ("gov.ir",),
    "is": ("is",), "it": ("gov.it",), "jm": ("gov.jm",),
    "jo": ("gov.jo", "jo"), "jp": ("go.jp",), "ke": ("go.ke",),
    "kg": ("gov.kg",), "kh": ("gov.kh",), "km": ("gouv.km",),
    "lk": ("gov.lk",),
    "kp": ("gov.kp",), "kr": ("go.kr",), "kw": ("gov.kw",),
    "kz": ("gov.kz",), "la": ("gov.la",), "lb": ("gov.lb",),
    "lr": ("gov.lr",), "ls": ("gov.ls",), "lt": ("gov.lt",),
    "lu": ("gouv.lu",), "lv": ("gov.lv",), "ly": ("gov.ly",),
    "ma": ("gov.ma",), "md": ("gov.md",), "me": ("gov.me",),
    "mg": ("gov.mg",), "mk": ("gov.mk",), "ml": ("gouv.ml",),
    "mm": ("gov.mm",), "mn": ("gov.mn",), "mo": ("gov.mo",),
    "mt": ("gov.mt",), "mu": ("gov.mu",), "mv": ("gov.mv",),
    "mw": ("gov.mw",), "mx": ("gob.mx", "gob.mx"),
    "my": ("gov.my",), "mz": ("gov.mz",), "na": ("gov.na",),
    "ne": ("gouv.ne",), "ng": ("gov.ng",), "ni": ("gob.ni",),
    "nl": ("gov.nl", "rijksoverheid.nl"), "no": ("regjeringen.no", "no"),
    "np": ("gov.np", "com.np"), "nz": ("govt.nz", "govt.nz"),
    "om": ("gov.om",), "pa": ("gob.pa",), "pe": ("gob.pe", "gov.pe"),
    "pg": ("gov.pg",), "ph": ("gov.ph",), "pk": ("gov.pk",),
    "pl": ("gov.pl",), "ps": ("gov.ps",), "pt": ("gov.pt",),
    "py": ("gov.py",), "qa": ("gov.qa",), "ro": ("gov.ro",),
    "rs": ("gov.rs",), "ru": ("gov.ru",), "rw": ("gov.rw",),
    "sa": ("gov.sa",), "sd": ("gov.sd",), "se": ("gov.se",),
    "sg": ("gov.sg",), "si": ("gov.si",), "sk": ("gov.sk",),
    "sl": ("gov.sl",), "sm": ("gov.sm",), "sn": ("gouv.sn",),
    "so": ("gov.so",), "sr": ("gov.sr",), "ss": ("gov.ss",),
    "sv": ("gob.sv", "gov.sv"), "sy": ("gov.sy",), "sz": ("gov.sz",),
    "td": ("gouv.td",), "tg": ("gouv.tg",), "th": ("go.th",), "tj": ("gov.tj",),
    "tl": ("gov.tl",), "tm": ("gov.tm",), "tn": ("gov.tn",),
    "to": ("gov.to",), "tr": ("gov.tr",), "tt": ("gov.tt",),
    "tw": ("gov.tw",), "tz": ("go.tz",), "ua": ("gov.ua", "kiev.ua"),
    "ug": ("gov.ug",), "us": ("gov",), "uy": ("gub.uy", "gov.uy"),
    "uz": ("gov.uz",), "ve": ("gov.ve",), "vn": ("gov.vn",),
    "ye": ("gov.ye",), "za": ("gov.za",), "zm": ("gov.zm",),
    "zw": ("gov.zw",),
}

# Country name / adjective / demonym / common alias -> ISO2. Reference data in
# the same spirit as OFFICIAL_SUFFIXES: it describes jurisdictions, never a
# subject area, so it applies to every query that names a country.
#
# The bare ISO2 code is deliberately NOT an alias. Two-letter codes are English
# words ("in", "is", "at", "be", "no", "us", "do"), and matching them would
# resolve almost every question to some country. Only names and demonyms are
# matched against question text; codes are used for host lookup, where the
# position in the hostname disambiguates them.
_COUNTRY_ALIASES: Dict[str, str] = {}
_COUNTRY_BY_ISO2_ALIASES: Dict[str, Tuple[str, ...]] = {}
_KNOWN_COUNTRY_CODES: Set[str] = set()

# ccTLDs that differ from the country's ISO2 code. Needed because the host
# lookup reads the TLD while everything else speaks ISO2.
_CCTLD_TO_ISO2: Dict[str, str] = {"uk": "gb"}


def _register_country(iso2: str, *aliases: str) -> None:
    _KNOWN_COUNTRY_CODES.add(iso2)
    for alias in aliases:
        key = alias.lower()
        if len(key) < 3:
            # Too short to match safely in prose; usable for hosts only.
            continue
        if key in _COUNTRY_ALIASES:
            continue
        _COUNTRY_ALIASES[key] = iso2
        _COUNTRY_BY_ISO2_ALIASES[iso2] = _COUNTRY_BY_ISO2_ALIASES.get(iso2, ()) + (key,)


for _iso, *_aliases in (
    ("af", "afghanistan"), ("al", "albania", "albanian"),
    ("dz", "algeria", "algerian"),
    ("ec", "ecuador"),
    ("ar", "argentina", "argentine"), ("am", "armenia", "armenian"),
    ("ao", "angola"), ("at", "austria", "austrian"),
    ("au", "australia", "australian"), ("az", "azerbaijan", "azerbaijani"),
    ("bh", "bahrain"), ("bd", "bangladesh", "bangladeshi"),
    ("by", "belarus", "belarusian"),
    ("be", "belgium", "belgian"),
    ("bj", "benin"), ("bo", "bolivia", "bolivian"),
    ("ba", "bosnia"), ("bw", "botswana"), ("br", "brazil", "brazilian"),
    ("bg", "bulgaria", "bulgarian"), ("bf", "burkina faso"),
    ("bi", "burundi"), ("kh", "cambodia"), ("cm", "cameroon"),
    ("ca", "canada", "canadian"), ("cv", "cape verde"),
    ("cf", "central african republic"),
    ("td", "chad"), ("cl", "chile", "chilean"), ("cn", "china", "chinese"),
    ("co", "colombia", "colombian"), ("km", "comoros"),
    ("cg", "congo"), ("cd", "democratic republic of the congo", "drc"),
    ("cr", "costa rica"), ("ci", "cote divoire"),
    ("hr", "croatia", "croatian"), ("cu", "cuba"), ("cy", "cyprus"),
    ("cz", "czechia", "czech"), ("dk", "denmark", "danish"),
    ("dj", "djibouti"), ("do", "dominican republic"), ("eg", "egypt", "egyptian"),
    ("sv", "el salvador"), ("gq", "equatorial guinea"), ("er", "eritrea"),
    ("ee", "estonia", "estonian"), ("sz", "eswatini"), ("et", "ethiopia", "ethiopian"),
    ("fj", "fiji"), ("fi", "finland", "finnish"), ("fr", "france", "french"),
    ("ga", "gabon"), ("gm", "gambia"), ("ge", "georgia", "georgian"),
    ("de", "germany", "german"), ("gh", "ghana", "ghanese"), ("gr", "greece", "greek"),
    ("gt", "guatemala"), ("gn", "guinea"), ("gw", "guinea-bissau"),
    ("ht", "haiti"), ("hn", "honduras"), ("hk", "hong kong"),
    ("hu", "hungary", "hungarian"), ("is", "iceland", "icelandic"),
    ("in", "india", "indian"), ("id", "indonesia", "indonesian"),
    ("ir", "iran", "iranian"), ("iq", "iraq", "iraqi"), ("ie", "ireland", "irish"),
    ("il", "israel", "israeli"), ("it", "italy", "italian"),
    ("jm", "jamaica"), ("jp", "japan", "japanese"), ("jo", "jordan"),
    ("kz", "kazakhstan", "kazakh"), ("ke", "kenya", "kenyan"),
    ("kw", "kuwait"), ("kg", "kyrgyzstan"),
    ("la", "laos"), ("lv", "latvia", "latvian"), ("lb", "lebanon"),
    ("ls", "lesotho"), ("lr", "liberia"), ("ly", "libya"), ("lt", "lithuania", "lithuanian"),
    ("lu", "luxembourg"), ("mo", "macau"), ("mg", "madagascar"),
    ("mw", "malawi", "malawian"), ("my", "malaysia", "malaysian"),
    ("mv", "maldives"), ("ml", "mali"), ("mt", "malta"), ("mu", "mauritius"),
    ("mx", "mexico", "mexican"), ("md", "moldova"), ("mn", "mongolia", "mongolian"),
    ("me", "montenegro"), ("ma", "morocco", "moroccan"), ("mz", "mozambique"),
    ("mm", "myanmar", "burma"), ("na", "namibia"), ("np", "nepal", "nepalese"),
    ("nl", "netherlands", "dutch"), ("nz", "new zealand"),
    ("ni", "nicaragua"), ("ne", "niger"), ("ng", "nigeria", "nigerian"),
    ("kp", "north korea"), ("kr", "south korea", "korea", "korean"),
    ("mk", "north macedonia", "macedonia"),
    ("no", "norway", "norwegian"), ("om", "oman"), ("pk", "pakistan", "pakistani"),
    ("ps", "palestine", "palestinian"), ("pa", "panama"), ("pg", "papua new guinea"),
    ("py", "paraguay"), ("pe", "peru", "peruvian"), ("ph", "philippines", "filipino"),
    ("pl", "poland", "polish"), ("pt", "portugal", "portuguese"),
    ("qa", "qatar"), ("ro", "romania", "romanian"), ("ru", "russia", "russian"),
    ("rw", "rwanda", "rwandan"), ("sa", "saudi arabia", "saudi"),
    ("sn", "senegal"), ("rs", "serbia", "serbian"), ("sl", "sierra leone"), ("sm", "san marino"),
    ("sg", "singapore", "singaporean"), ("sk", "slovakia", "slovak"),
    ("si", "slovenia", "slovenian"), ("so", "somalia", "somali"),
    ("za", "south africa", "south african"), ("ss", "south sudan"),
    ("es", "spain", "spanish"), ("lk", "sri lanka"), ("sd", "sudan"),
    ("sr", "suriname"), ("se", "sweden", "swedish"), ("ch", "switzerland", "swiss"),
    ("sy", "syria"), ("tw", "taiwan"), ("tj", "tajikistan"),
    ("tz", "tanzania", "tanzanian"), ("th", "thailand", "thai"),
    ("tl", "timor-leste", "east timor"), ("tg", "togo"), ("to", "tonga"),
    ("tt", "trinidad and tobago"), ("tn", "tunisia", "tunisian"),
    ("tr", "turkey", "turkish", "turkiye"), ("tm", "turkmenistan"),
    ("ug", "uganda", "ugandan"), ("ua", "ukraine", "ukrainian"),
    ("ae", "united arab emirates", "uae", "dubai"),
    ("gb", "united kingdom", "britain", "british", "england", "english", "scotland", "wales"),
    ("us", "united states", "u.s.", "usa", "america", "american"),
    ("uy", "uruguay"), ("uz", "uzbekistan"), ("ve", "venezuela"),
    ("vn", "vietnam", "vietnamese"), ("ye", "yemen"), ("zm", "zambia", "zambian"),
    ("zw", "zimbabwe", "zimbabwean"),
    ("eu", "european union"),
):
    _register_country(_iso, *_aliases)

# Multicountry / supranational bodies whose data is not tied to one member's
# statistics office. Targeting them never narrows a question away from its
# answer, so they are admissible whatever jurisdiction the question names.
SUPRANATIONAL_JURISDICTIONS: frozenset = frozenset({"eu"})

# Bare TLDs that LOOK like country codes but are used generically worldwide.
# Reading `.co` as Colombia or `.io` as a territory would misjudge almost every
# commercial host, so these are excluded from the bare-TLD jurisdiction path.
_PSEUDO_TLDS: frozenset = frozenset({
    "com", "net", "org", "info", "biz", "io", "co", "ai", "tv", "cc", "ws",
    "fm", "am", "sh", "st", "gg", "im", "name", "dev", "app", "online",
    "site", "tech", "store", "blog", "xyz", "top", "live", "world", "today",
    "cloud", "digital", "design", "email", "link", "media", "news", "group",
    "center", "zone", "network", "systems", "social", "expert", "solutions",
})

# Longest-suffix-first: ".gov.uk" must win over the bare-".uk" fallback, and
# ".europa.eu" over ".eu". Suffix rules exist because a country's own agencies
# do not live under its bare ccTLD.
_JURISDICTION_SUFFIX_RULES: Tuple[Tuple[str, str], ...] = (
    (".europa.eu", "eu"), (".gov.uk", "gb"), (".gov.au", "au"),
    (".govt.nz", "nz"), (".ac.uk", "gb"),
    (".gov.in", "in"), (".nic.in", "in"), (".co.in", "in"),
    (".gov.bd", "bd"), (".gov.sg", "sg"), (".gouv.fr", "fr"),
    (".govt.gr", "gr"), (".gc.ca", "ca"), (".gov.ca", "ca"),
    (".go.jp", "jp"), (".go.kr", "kr"), (".or.kr", "kr"), (".ne.jp", "jp"),
    (".go.id", "id"), (".go.th", "th"), (".go.ke", "ke"),
    (".gob.ar", "ar"), (".gob.br", "br"), (".gob.cl", "cl"), (".gob.mx", "mx"),
    (".gob.pe", "pe"), (".gov.gr", "gr"), (".bund.de", "de"),
    (".admin.ch", "ch"), (".gouv.be", "be"), (".gouv.lu", "lu"),
    (".gov.pl", "pl"), (".gov.pt", "pt"), (".gov.se", "se"),
    (".gov.za", "za"), (".gov.ng", "ng"), (".gov.gh", "gh"),
    (".gov.es", "es"), (".gob.es", "es"), (".gov.it", "it"), (".gov.il", "il"),
    (".gov.my", "my"), (".gov.ph", "ph"), (".gov.pk", "pk"), (".gov.vn", "vn"),
    (".gov.ir", "ir"), (".gov.sa", "sa"), (".gov.eg", "eg"), (".gov.tr", "tr"),
    (".gov.tw", "tw"), (".gov.hk", "hk"), (".gov.ie", "ie"), (".gov.nl", "nl"),
    (".gov.dk", "dk"), (".gov.no", "no"), (".gov.hu", "hu"), (".gov.cz", "cz"),
    (".gov.ro", "ro"), (".gov.gr", "gr"), (".gov.lv", "lv"), (".gov.lt", "lt"),
    (".gov.hr", "hr"), (".gov.rs", "rs"), (".gov.fi", "fi"), (".gov.ee", "ee"),
    (".gov.at", "at"), (".gov.si", "si"), (".gov.sk", "sk"), (".gov.lk", "lk"),
    (".gov.kz", "kz"), (".gov.uz", "uz"), (".gov.ge", "ge"), (".gov.am", "am"),
    (".gov.by", "by"), (".gov.md", "md"), (".gov.ma", "ma"), (".gov.dz", "dz"),
    (".gov.co", "co"), (".gov.pe", "pe"), (".gov.uy", "uy"), (".gov.py", "py"),
    (".gov.bo", "bo"), (".gov.ve", "ve"), (".gov.cu", "cu"), (".gov.do", "do"),
    (".gov", "us"), (".mil", "us"),
)

_TWO_PART_SUFFIXES: frozenset = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "gov.au", "edu.au", "co.nz", "govt.nz", "co.jp", "or.jp", "ne.jp",
    "go.jp", "co.kr", "or.kr", "go.kr", "re.kr", "com.br", "gov.br",
    "org.br", "com.cn", "gov.cn", "org.cn", "co.in", "gov.in", "nic.in",
    "org.in", "co.za", "gov.za", "org.za", "com.bd", "gov.bd", "org.bd",
    "com.tr", "gov.tr", "com.sg", "gov.sg", "com.my", "gov.my", "com.pk",
    "gov.pk", "com.ph", "gov.ph", "co.id", "go.id", "co.th", "go.th",
    "com.mx", "gob.mx", "com.ar", "gob.ar", "com.co", "gov.co", "com.pe",
    "gob.pe", "co.ke", "go.ke", "co.il", "gov.il", "com.ng", "gov.ng",
    "co.ke", "com.es", "gob.es", "com.pl", "gov.pl", "co.at", "gv.at",
})


def _registrable(host: str) -> str:
    """Registrable domain of a bare host: the last two labels, or three for the
    common two-part public suffixes (.co.uk, .org.bd, .gov.uk).

    A full public-suffix list would be a dependency this module deliberately
    does not have (see the module docstring); the explicit set above covers the
    suffixes that decide jurisdiction in practice.
    """
    host = (host or "").lower().strip().strip(".")
    if not host or "." not in host:
        return host
    parts = host.split(".")
    if len(parts) >= 3 and f"{parts[-2]}.{parts[-1]}" in _TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def host_jurisdiction(host_or_url: str) -> Optional[str]:
    """ISO2 code of the single country a publisher is bound to, else None.

    None means "not jurisdiction-bound": a multilateral body (worldbank.org,
    imf.org), a global publisher (arxiv.org, doi.org), a reference work, or a
    bare commercial host. Callers must NOT read None as "somewhere else" — it
    means "carries no constraint", which is what keeps those hosts targetable
    for every question.
    """
    text = (host_or_url or "").strip()
    if not text:
        return None
    domain = extract_domain(text) if "//" in text else _registrable(text)
    if not domain:
        return None
    for suffix, iso2 in _JURISDICTION_SUFFIX_RULES:
        if domain.endswith(suffix):
            return iso2
    tld = domain.rsplit(".", 1)[-1]
    if len(tld) != 2 or tld in _PSEUDO_TLDS:
        return None
    if tld in _CCTLD_TO_ISO2:
        return _CCTLD_TO_ISO2[tld]
    return tld if tld in _KNOWN_COUNTRY_CODES else None


# A domain written inside a question ("data.gov.bd", "ons.gov.uk") names its own
# jurisdiction and is the most reliable signal available.
_DOMAIN_IN_TEXT_RE = re.compile(
    r"\b([a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+)\b", re.I
)

# A two-letter country code in CAPITALS mid-sentence is the country, not an
# English word: "US and China policy" means the United States, while lowercase
# "us" is a pronoun. Codes are therefore matched case-sensitively and only when
# written in full capitals, which is what keeps "in"/"is"/"at" out of the prose
# matcher above.
_UPPER_CODE_RE = re.compile(r"(?<![A-Za-z])(\b[A-Z]{2}\b)(?![A-Za-z])")


def question_jurisdiction(text: str) -> Tuple[str, ...]:
    """ISO2 codes for every country a question or claim names, in MENTION order.

    Mention order is what makes the first target right: "Germany vs France" must
    aim at German publishers first, and a registry-ordered scan returned France
    for it. Ordering by match position also means the country the question leads
    with is the country whose agencies lead the query.

    Returns () when the question names no country, which callers must treat as
    "do not narrow by jurisdiction" — not as "no jurisdiction exists". Most
    questions are global, and narrowing those would be the same bug in reverse.
    """
    raw = text or ""
    blob = raw.lower()
    if not blob.strip():
        return ()
    hits: List[Tuple[int, str]] = []
    for iso2, aliases in _COUNTRY_BY_ISO2_ALIASES.items():
        for alias in aliases:
            m = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", blob)
            if m:
                hits.append((m.start(), iso2))
                break
    for code in _UPPER_CODE_RE.findall(raw):
        iso2 = code.lower()
        if iso2 in _KNOWN_COUNTRY_CODES:
            hits.append((raw.find(code), iso2))
    for candidate in _DOMAIN_IN_TEXT_RE.findall(blob):
        found_iso2 = host_jurisdiction(candidate)
        if found_iso2:
            hits.append((blob.find(candidate), found_iso2))
    seen: Set[str] = set()
    out: List[str] = []
    for _pos, iso2 in sorted(hits):
        if iso2 not in seen:
            seen.add(iso2)
            out.append(iso2)
    return tuple(out)


def jurisdiction_is_admissible(host: str, jurisdictions: Sequence[str]) -> bool:
    """May `host` be a `site:` target for a question about `jurisdictions`?

    True when the host is not bound to one country (multilateral, global, or a
    bare commercial domain), when the question names no country, when the host's
    country is one the question asked about, or when the host is supranational.
    """
    if not jurisdictions:
        return True
    bound = host_jurisdiction(host)
    if bound is None or bound in SUPRANATIONAL_JURISDICTIONS:
        return True
    return bound in jurisdictions


def jurisdiction_site_terms(
    jurisdictions: Sequence[str], max_sites: int = 2
) -> Tuple[str, ...]:
    """Official suffix families for the countries a question names.

    These are the strongest available primary targets for a country-specific
    question: the country's own statistics office, regulator and ministries,
    which no foreign publisher substitutes for.
    """
    out: List[str] = []
    for iso2 in jurisdictions or ():
        for suffix in COUNTRY_OFFICIAL_SUFFIX.get(iso2, ()):  # type: ignore[arg-type]
            term = suffix.lstrip(".")
            # A bare ccTLD would match every host under it; keep the full family.
            if term not in out:
                out.append(term)
            if len(out) >= max(1, max_sites):
                return tuple(out)
    return tuple(out)


def grounded_site_targets(
    question: str,
    search_type: str,
    domain: str = "general",
    max_sites: int = 2,
    attempt: int = 0,
    allow_registry_fallback: bool = False,
) -> Tuple[str, ...]:
    """`site:`-ready targets the QUESTION justifies, jurisdiction first.

    Order of preference:
      1. the country the question names, via its own official suffix family;
      2. registered publisher hints, minus any bound to a country the question
         is not about;
      3. (only with `allow_registry_fallback`) the generic authoritative
         registry, filtered the same way.

    Returns () when nothing is grounded, and callers must then emit a query with
    NO `site:` operator at all rather than guessing — an ungrounded guess is what
    excluded the right publisher in the first place. The registry fallback is
    therefore opt-in: `build_primary_source_query` must still be able to report
    "no target for this question" so the caller skips the extra search rather
    than firing an over-constrained one.
    """
    juris = question_jurisdiction(question)
    out: List[str] = []
    # One slot per country the question names, then the remaining slots to
    # registered hints. Letting the jurisdiction consume the whole budget would
    # discard hints that are equally valid (Reuters for a Bangladesh news
    # question, the World Bank for a Bangladesh statistical one), so the
    # jurisdiction is added rather than substituted. A question naming two
    # countries ("Germany vs France") needs a target for each, so it claims two.
    own = jurisdiction_site_terms(
        juris, max_sites=min(len(juris) or 1, max(1, max_sites))
    )
    for term in own:
        if term not in out:
            out.append(term)
    for hint in primary_source_hints(search_type, domain):
        if len(out) >= max(1, max_sites):
            break
        if hint in out or not jurisdiction_is_admissible(hint, juris):
            continue
        out.append(hint)
    if not out and allow_registry_fallback:
        pool = authoritative_site_terms(
            max_sites=max(1, max_sites) + 2, offset=max(0, attempt)
        )
        for term in pool:
            if jurisdiction_is_admissible(term, juris) and term not in out:
                out.append(term)
            if len(out) >= max(1, max_sites):
                break
    return tuple(out[: max(0, max_sites)])


# ---------------------------------------------------------------------------
# Search steering: which publishers to aim a query at
# ---------------------------------------------------------------------------
# Search steering: which publishers to aim a query at
# ---------------------------------------------------------------------------

# Domain hints per (search_type, domain) used to build `site:`-scoped query
# variants. Two or three hosts per bucket, never a wall of operators: an
# over-constrained query returns nothing, which is worse than a broad one.
PRIMARY_SOURCE_HINTS: Dict[str, Tuple[str, ...]] = {
    "statistical:economics": ("worldbank.org", "imf.org", "oecd.org"),
    "statistical:policy": ("oecd.org", "europa.eu", "gov.uk"),
    "statistical:science": ("nasa.gov", "noaa.gov", "who.int"),
    "statistical:general": ("worldbank.org", "oecd.org", "census.gov"),
    "statistical:machine_learning": ("paperswithcode.com", "mlcommons.org"),
    "statistical:software": ("stackoverflow.blog", "github.blog"),
    "academic:machine_learning": ("arxiv.org", "aclanthology.org", "openreview.net"),
    "academic:science": ("nature.com", "science.org", "pubmed.ncbi.nlm.nih.gov"),
    "academic:economics": ("nber.org", "repec.org", "aeaweb.org"),
    "academic:philosophy": ("plato.stanford.edu", "iep.utm.edu"),
    "academic:general": ("arxiv.org", "doi.org", "semanticscholar.org"),
    "academic:legal": ("eur-lex.europa.eu", "courtlistener.com"),
    "academic:policy": ("oecd.org", "nber.org"),
    "news:general": ("reuters.com", "apnews.com", "ft.com"),
    "comparison:general": (),
    "encyclopedia:general": ("britannica.com",),
    "encyclopedia:philosophy": ("plato.stanford.edu",),
}

# Terms that steer a general web search toward primary documents even when
# no site: operator applies.
PRIMARY_INTENT_TERMS: Dict[str, Tuple[str, ...]] = {
    "statistical": ("official statistics", "dataset", "annual report"),
    "academic": ("peer-reviewed study", "paper", "doi"),
    "news": ("press release", "official announcement"),
    "comparison": ("benchmark results", "side-by-side"),
    "encyclopedia": ("definition", "overview"),
}


def primary_source_hints(search_type: str, domain: str = "general") -> Tuple[str, ...]:
    """Preferred publisher hosts for a (search_type, domain) pair."""
    st = (search_type or "").strip().lower() or "encyclopedia"
    dm = (domain or "").strip().lower() or "general"
    return (
        PRIMARY_SOURCE_HINTS.get(f"{st}:{dm}")
        or PRIMARY_SOURCE_HINTS.get(f"{st}:general")
        or ()
    )


def primary_intent_terms(search_type: str) -> Tuple[str, ...]:
    return PRIMARY_INTENT_TERMS.get((search_type or "").strip().lower(), ())


def wants_a_primary_source(question: str) -> bool:
    """Can this question have a PRIMARY source at all?

    "What is retrieval augmented generation?" does not. There is no document
    that published the definition, so any primary-source query built for it is
    fiction — and when it was built anyway it aimed the reserved primary slot at
    whatever the generic registry offered (the World Bank, WHO) for a question
    about neither, spending a retrieval slot to return nothing.

    The test is whether the question demands a document that can exist as
    evidence: a filing, a dataset, a study, a statute, a recent event, a
    comparison. A question whose ONLY demand is orientation gets no primary
    query, and `contract_queries` reclaims the slot for a real one.

    Any typed signal at all counts, however weak. "What is the growth?" reads
    like a definition but "growth" is a statistical demand and the official
    series is precisely the primary document for it; gating on the 1.5
    `required_types` threshold instead classified it as having no primary
    source and dropped the slot.
    """
    from app.agents.evidence_type import EV_ENCYCLOPEDIC, classify_evidence_need

    need = classify_evidence_need(question or "")
    if any(ev != EV_ENCYCLOPEDIC for ev in need.scores):
        return True
    return not need.asks_definition


def build_primary_source_query(
    question: str, search_type: str, domain: str = "general", max_sites: int = 2
) -> str:
    """A `site:`-scoped variant of `question` aimed at primary publishers.

    Targets are GROUNDED in the question (see `grounded_site_targets`): the
    country the question names gets that country's own official suffix family,
    and a registered hint bound to some other country is dropped rather than
    allowed to exclude the publisher that actually holds the answer.

    Returns "" when nothing is grounded, so callers skip the extra search instead
    of firing a duplicate of the plain query or an over-constrained one. Use
    `build_dimension_primary_query` when a guaranteed primary-source query is
    required for EVERY research dimension (see its docstring).
    """
    text = re.sub(r"\s+", " ", (question or "")).strip()
    if not text:
        return ""
    targets = grounded_site_targets(
        text, search_type, domain, max_sites=max(0, max_sites)
    )
    if not targets:
        return ""
    return f"{text} " + " OR ".join(f"site:{t}" for t in targets)


def build_dimension_primary_query(
    question: str,
    search_type: str,
    domain: str = "general",
    max_sites: int = 2,
    attempt: int = 0,
) -> str:
    """A primary-source query guaranteed for EVERY dimension.

    `build_primary_source_query` returns "" when the (search_type, domain) pair
    has no registered host hint — which, live, meant the dimensions whose
    evidence is most likely to be secondary (comparative/exploratory angles)
    never issued a targeted primary query at all, and the report's primary
    share stayed low. This wrapper keeps the precise host scoping when a hint
    exists, and otherwise falls back to the DETERMINISTIC authoritative
    publisher registry (`site:gov/edu/int` + named agencies/journals) plus the
    dimension's own primary-intent vocabulary. It therefore never fabricates a
    query for an empty question, and always returns a usable one otherwise.

    The result is what `_contract` stores as `primary_source_query`, so every
    delegation contract reserves a primary/official retrieval slot (search's
    `contract_queries` holds one for it) rather than only the dense statistical
    and academic contracts.

    `attempt` rotates the fallback hosts so successive expansion passes for a
    dimension reach publishers an earlier pass did not. Rotation only ever
    chooses among targets the question's own jurisdiction admits, so a later
    pass cannot drift into another country's agencies.

    Returns "" for a question that cannot have a primary source (a pure
    definition — see `wants_a_primary_source`). That is a deliberate hole in the
    "guaranteed for every dimension" contract: spending the reserved slot on a
    fabricated target was the bug, and the caller reclaims the slot instead.
    """
    text = re.sub(r"\s+", " ", (question or "")).strip()
    if not text:
        return ""
    scoped = build_primary_source_query(text, search_type, domain, max_sites=max_sites)
    if scoped:
        return scoped
    if not wants_a_primary_source(text):
        return ""
    juris = question_jurisdiction(text)
    # A dimension about a named country goes to that country's own agencies
    # before the generic registry is consulted.
    targets = jurisdiction_site_terms(juris, max_sites=max(1, max_sites))
    if not targets:
        pool = authoritative_site_terms(
            max_sites=max(1, max_sites), offset=max(0, attempt)
        )
        targets = tuple(t for t in pool if jurisdiction_is_admissible(t, juris))
    if not targets:
        return text
    terms = primary_intent_terms(search_type) or AUTHORITATIVE_INTENT_TERMS
    intent = " ".join(terms[:2])
    site_clause = " OR ".join(f"site:{t}" for t in targets)
    query = f"{text} {intent} ({site_clause})".strip() if intent else f"{text} ({site_clause})"
    return re.sub(r"\s+", " ", query).strip()


# ---------------------------------------------------------------------------
# Corroboration targeting: authoritative publishers to seek a SECOND source
# ---------------------------------------------------------------------------

# The single curated set of independent AUTHORITATIVE publishers a
# corroboration query should be pointed at. It is DERIVED from the same
# tiering registries used by classify_source (not a parallel list): the
# inter-governmental/statistical agencies, standards bodies, peer-reviewed
# venues and preprint servers are exactly the publishers whose word can
# independently corroborate an important claim. Ordering is stable and
# priority-ordered (highest-signal agencies first) so a capped query always
# carries the strongest targets and never wobbles between runs.
#
# `site:` operators for the suffix-matched families (.gov/.edu/.int) are
# appended by `authoritative_site_terms` because Google-style site: accepts a
# TLD suffix and the search providers translate site: to include_domains.
_AUTHORITATIVE_HOSTS: Tuple[str, ...] = (
    # Inter-governmental / statistical agencies: they publish the numbers.
    "worldbank.org", "who.int", "oecd.org", "un.org", "imf.org",
    "eurostat.ec.europa.eu", "ec.europa.eu", "gov.uk", "ons.gov.uk",
    "bls.gov", "census.gov", "eia.gov", "nasa.gov", "noaa.gov",
    "nist.gov", "iso.org", "ipcc.ch", "iea.org",
    # Peer-reviewed venues and preprint servers.
    "nature.com", "science.org", "thelancet.com", "nejm.org", "bmj.com",
    "pnas.org", "jstor.org", "arxiv.org", "biorxiv.org", "medrxiv.org",
    "nber.org", "doi.org",
    # Bounded institutional reference points.
    "ourworldindata.org",
)

# Bare site: suffixes proven to reach authoritative publishers without naming
# each host: government, military, international treaty bodies and academic
# institutions.
_AUTHORITATIVE_SUFFIXES: Tuple[str, ...] = ("gov", "edu", "int", "gov.uk")

# Query vocabulary that steers a general web search at primary documents even
# when site: scoping returns nothing.
AUTHORITATIVE_INTENT_TERMS: Tuple[str, ...] = (
    "official report", "government data", "dataset", "peer-reviewed study",
)


def build_substitution_query(
    question: str,
    search_type: str,
    blocked_domain: str,
    max_sites: int = 2,
    attempt: int = 0,
) -> str:
    """A query for an EQUIVALENT publisher when the primary host is unavailable.

    Used by the blocked-host recovery path, and deliberately NOT gated on
    `wants_a_primary_source`: there we already know an authoritative document
    exists and we simply could not read it, so "this question has no primary
    source" is irrelevant — returning nothing would mean substituting nothing.

    The target is chosen by JURISDICTION first. A run that lost `ons.gov.uk`
    needs another UK official publisher, and one that lost a World Bank page
    needs another multilateral publisher. Targeting the generic registry instead
    sent recovery at whichever agencies the rotation happened to reach, which for
    a national document meant publishers in the wrong country entirely.

    Always returns a usable query for a non-empty question.
    """
    text = re.sub(r"\s+", " ", (question or "")).strip()
    if not text:
        return ""
    blocked = (blocked_domain or "").strip().lower()
    bound = host_jurisdiction(blocked) if blocked else None
    blocked_juris: Tuple[str, ...] = (bound,) if bound else ()
    # The question's own countries lead; the failed host's country is the
    # fallback signal when the question itself named none.
    juris: Tuple[str, ...] = question_jurisdiction(text) or blocked_juris
    out: List[str] = []
    # Jurisdiction families are NOT filtered by the failed host. `go.kr` is the
    # family that contains a failed `go.kr` agency, and dropping it would send
    # recovery to multilateral publishers instead of the other Korean agencies
    # that are exactly what is wanted. The failed host is excluded from the
    # query text by `-site:`, so it cannot come back through this.
    for term in jurisdiction_site_terms(juris, max_sites=1):
        if term and term not in out:
            out.append(term)
    for term in authoritative_site_terms(
        max_sites=max(1, max_sites) + 2, offset=max(0, attempt)
    ):
        if len(out) >= max(1, max_sites):
            break
        if term in out or _blocks_host(term, blocked):
            continue
        if not jurisdiction_is_admissible(term, juris):
            continue
        out.append(term)
    if not out:
        return text
    terms = primary_intent_terms(search_type) or AUTHORITATIVE_INTENT_TERMS
    query = f"{text} {' '.join(terms[:2])} ({' OR '.join(f'site:{t}' for t in out)})"
    if blocked:
        query = f"{query} -site:{blocked}"
    return re.sub(r"\s+", " ", query).strip()


def _blocks_host(site_term: str, blocked_host: str) -> bool:
    """Would targeting `site_term` only re-find the host that just failed?

    Exact match only, on purpose. A registrable-domain comparison looked
    correct and was destructive: `site:gov.uk` was rejected whenever any host
    under it had failed, so losing one `ons.gov.uk` page discarded EVERY UK
    government publisher — the exact substitution the recovery path exists to
    perform. The failed host itself is already excluded from the query text by
    `-site:<host>`, so this guard only needs to avoid naming it as a target.
    """
    if not blocked_host:
        return False
    term = (site_term or "").strip().lower().lstrip(".")
    return bool(term) and term == blocked_host.lower()


class SiteTargets(NamedTuple):
    """A query's `site:` terms, sorted by how strongly they may be enforced."""

    hard: Tuple[str, ...]      # justified by the query's own subject; may filter
    soft: Tuple[str, ...]      # steering preference only; must NOT filter
    excluded: Tuple[str, ...]  # `-site:` terms; must be passed as exclusions


_SITE_TERM_RE = re.compile(r"(-?)site:(\S+)", re.IGNORECASE)


def partition_site_targets(text: str) -> SiteTargets:
    """Split a query's `site:` terms into hard, soft and excluded targets.

    HARD means "the query's own subject justifies excluding everything else": a
    country official suffix family for a country the question actually named
    (`population of Malawi` -> `site:gov.mw`). Scoping there loses nothing,
    because the answer lives in that family.

    SOFT means "we would like results from here": a registry hint, a rotated
    corroboration target, a journal or agency guessed from (search_type,
    domain). Handing those to a provider as a domain filter converts a mild
    preference into a hard exclusion, which is how a mis-aimed hint produced an
    EMPTY result set and burned the retrieval slot reserved for the primary
    source. Soft targets are therefore stripped from the query text and applied
    as a ranking preference instead, where a wrong guess costs nothing.

    EXCLUDED are `-site:` terms. They are negatives and must never be read as
    targets: `build_corroboration_query` emits `-site:<domain>` precisely to
    obtain an INDEPENDENT publisher, and treating that as a preference would
    rank the one source we are required to move away from to the top.

    Every `site:` term is stripped from the query text either way; the split
    only decides what the caller may additionally filter on.
    """
    positive: List[str] = []
    excluded: List[str] = []
    for negated, match in _SITE_TERM_RE.findall(text or ""):
        for part in str(match).replace(",", " ").split():
            if part.upper() == "OR":
                continue
            term = part.strip().strip(",").strip("()").strip("-").split("/")[0].lower()
            if not term:
                continue
            bucket = excluded if negated else positive
            if term not in bucket:
                bucket.append(term)
    if not positive:
        return SiteTargets((), (), tuple(excluded))
    juris = question_jurisdiction(text)
    if not juris:
        # Nothing in the query names a country, so no term can be justified by
        # the query's subject: every site: hint is a guess.
        return SiteTargets((), tuple(positive), tuple(excluded))
    own = set(jurisdiction_site_terms(juris, max_sites=max(1, len(positive))))
    hard = tuple(t for t in positive if t in own)
    soft = tuple(t for t in positive if t not in own)
    return SiteTargets(hard, soft, tuple(excluded))


def authoritative_site_terms(
    max_sites: int = 4, include_suffixes: bool = True, offset: int = 0
) -> Tuple[str, ...]:
    """`site:`-ready authoritative hosts/suffixes, capped and deterministic.

    Prefers explicit hosts in priority order; when the cap allows, appends the
    generic authoritative suffixes (gov/edu/int) so any agency or university
    counts, not only the enumerated ones. `offset` rotates the starting host so
    successive corroboration attempts for one claim target DIFFERENT publishers
    instead of repeating the same scoped query.
    """
    pool: List[str] = list(_AUTHORITATIVE_HOSTS)
    if include_suffixes:
        pool.extend(_AUTHORITATIVE_SUFFIXES)
    if not pool:
        return ()
    start = offset % len(pool)
    rotated = pool[start:] + pool[:start]
    terms: List[str] = []
    for entry in rotated:
        if entry in terms:
            continue
        terms.append(entry)
        if len(terms) >= max(0, max_sites):
            break
    return tuple(terms)


def build_corroboration_query(
    claim_terms: str,
    exclude_domain: str = "",
    *,
    quantitative: bool = False,
    max_sites: int = 4,
    attempt: int = 0,
) -> str:
    """A query that seeks an INDEPENDENT authoritative publisher for a claim.

    Targets the authoritative registry (site:gov/edu/int plus named agencies,
    journals and datasets), excludes the claim's current publisher with
    `-site:<domain>` so the search cannot return the source we already hold,
    and — for quantitative claims — adds primary-document vocabulary where the
    corroborating figure is most likely to live.

    The POSITIVE targets are jurisdiction-filtered against the claim's own text.
    Previously they were emitted unconditionally, so corroborating a claim about
    a national regulator, a court, a central bank or a national statistics office
    aimed the follow-up at worldbank.org/oecd.org and their `-site:` exclusion
    then removed the one domain that actually held the corroboration. A claim
    naming a country now leads with that country's own agencies.

    `attempt` rotates the targeted hosts so a second procurement pass for the
    same claim reaches publishers the first pass did not.

    Deterministic and total: an empty claim yields "" (never invent a query).
    """
    text = re.sub(r"\s+", " ", (claim_terms or "")).strip()
    if not text:
        return ""
    juris = question_jurisdiction(text)
    sites: List[str] = list(jurisdiction_site_terms(juris, max_sites=1))
    pool = authoritative_site_terms(
        max_sites=max_sites + 2, offset=max(0, int(attempt))
    )
    for term in pool:
        if len(sites) >= max(1, max_sites):
            break
        if term in sites or not jurisdiction_is_admissible(term, juris):
            continue
        sites.append(term)
    if not sites:
        return ""
    site_clause = " OR ".join(f"site:{s}" for s in sites)
    intent = " ".join(AUTHORITATIVE_INTENT_TERMS) if quantitative else "independent source"
    query = f"{text} {intent} ({site_clause})"
    domain = (exclude_domain or "").strip()
    if domain:
        query = f"{query} -site:{domain}"
    return re.sub(r"\s+", " ", query).strip()


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

# Half-lives in days: how fast a claim of this kind goes stale. Used to turn
# a publish date into a 0-1 recency weight instead of the previous
# all-or-nothing "has a date / has no date".
FRESHNESS_HALF_LIFE: Dict[str, float] = {
    "news": 120.0,
    "statistical": 550.0,
    "comparison": 730.0,
    "academic": 1460.0,
    "encyclopedia": 2200.0,
    "default": 730.0,
}


def _as_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).date()
        except (TypeError, ValueError):
            continue
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except (TypeError, ValueError):
            continue
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
        return None


def freshness_score(
    published_at: str,
    search_type: str = "default",
    today: Optional[date] = None,
    unknown_score: float = 0.45,
) -> float:
    """Exponential-decay recency weight in [0, 1].

    `unknown_score` is deliberately mid-scale, not 0: an undated page is
    unknown, not stale, and penalizing it as stale would systematically
    demote primary PDFs (which rarely expose dates) in favour of blogs
    (which always do).
    """
    parsed = _as_date(published_at)
    if parsed is None:
        return unknown_score
    ref = today or datetime.now(timezone.utc).date()
    age_days = max(0.0, (ref - parsed).days)
    half_life = FRESHNESS_HALF_LIFE.get(
        (search_type or "default").strip().lower(), FRESHNESS_HALF_LIFE["default"]
    )
    return round(0.5 ** (age_days / half_life), 4)


def evidence_freshness(
    items: Sequence[Dict[str, object]],
    search_type_key: str = "search_type",
    date_key: str = "published_at",
    today: Optional[date] = None,
) -> float:
    """Mean freshness across items; 0.45 (unknown) when nothing is dated."""
    scores = [
        freshness_score(
            str(item.get(date_key, "") or ""),
            str(item.get(search_type_key, "default") or "default"),
            today=today,
        )
        for item in items or []
    ]
    return round(sum(scores) / len(scores), 4) if scores else 0.45


# ---------------------------------------------------------------------------
# Machine-generated report sections
# ---------------------------------------------------------------------------
# These headings are appended AFTER the writer finishes, from measured pipeline
# state (counts, confidence panels, contradiction ranges, source ledger). They
# describe the pipeline, not the evidence, so by design they carry no [n]
# citation markers. Any metric that measures "share of sentences cited" MUST
# exclude them or it measures the accounting as if it were unsupported prose —
# exactly the miscalibration that held the evidence sub-score near 45 while the
# writer's own body was 68% cited. The authoritative list lives here (a pure,
# offline module imported by both synthesizer and answer_quality) so the
# auditor and the quality gate can never disagree about what is machine-written.
MACHINE_SECTIONS: Tuple[str, ...] = (
    "## Evidence & Confidence",
    "## Evidence Strength",
    "## Limitations",
    "## Limitations & Unknowns",
    "## Counterarguments & Disputed Points",
    "## Evidence integrity",
    "## Source ledger",
    "## Standing objections",
    "## What Would Change Our Mind",
    "## Reasoning",
    "## Sources",
)


def strip_machine_sections(text: str) -> str:
    """Drop appended machine-generated sections, keeping the writer's prose.

    Splits on the canonical headings so a heading buried mid-document is
    handled the same as a trailing appendix. Case-sensitive on the exact
    heading text the synthesizer emits, so ordinary prose containing the word
    "Limitations" is never truncated by accident.
    """
    body = text or ""
    cut = len(body)
    for heading in MACHINE_SECTIONS:
        match = re.search(rf"(?m)^\s*{re.escape(heading)}\s*$", body)
        if match and match.start() < cut:
            cut = match.start()
    return body[:cut]


# Inline citation markers as the writer emits them: [3], [1][6], [4, 7].
_INLINE_CITE_RE = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\](?=\s*\[\d+)|\s*\[\d+(?:\s*,\s*\d+)*\]")
# Em dash (U+2014) used as a parenthetical or appositive separator. Deleting it
# outright welds two clauses together ("pattern holds 88% of firms..."), so it is
# replaced with punctuation that keeps the sentence grammatical. En dash (U+2013)
# is deliberately NOT touched: in a range like 2025-2026 it is correct typography.
_EM_DASH_RE = re.compile(r"\s*\u2014\s*")
# A thematic break on its own line: ---, ***, ___.
_HR_RE = re.compile(r"(?m)^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$")


def clean_writer_prose(text: str) -> str:
    """Strip presentation noise from the WRITER'S prose only.

    Two things the model emits that read as machine output rather than an
    answer, and that the reader should not have to see:

    1. Inline citation markers (`[4]`, `[1][6]`). These are load-bearing
       DURING the run — citation density is scored from them, and the evidence
       ledger enumerates by the same numbers — so they are removed here, after
       scoring, and only from prose. The ledger keeps its numbering.
    2. Thematic breaks (`---`). Headings already delimit sections, so a rule
       under every heading is redundant scaffolding.

    Both are applied to the writer's body only: `strip_machine_sections` runs
    first so the audit sections (source ledger, confidence panel) are never
    touched. Machine sections legitimately use rules to separate themselves.
    """
    body = strip_machine_sections(text or "")
    body = _INLINE_CITE_RE.sub("", body)
    body = _HR_RE.sub("", body)
    # Em dash -> comma. A colon would read better after a lead-in clause, but
    # that needs parsing; a comma is grammatical in both directions and never
    # produces a run-on.
    body = _EM_DASH_RE.sub(", ", body)
    # Collapse the blank-line runs the removals leave behind, and any run of
    # three or more newlines, without touching single paragraph breaks.
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    # Tidy the punctuation the dash swap can double up, and drop a comma that
    # would now sit directly before sentence-ending punctuation.
    body = re.sub(r",\s*([.,;:])", r"\1", body)
    body = re.sub(r"\(\s*,\s*", "(", body)
    body = re.sub(r"\s+,", ",", body)
    return body.strip()


# ---------------------------------------------------------------------------
# EVIDENCE-TYPE FIT
#
# Authority answers "how much does this publisher's word carry". Type fit
# answers the different question that actually decides usability: "is this the
# KIND of document the question needs?". An encyclopedia is a high-authority
# publisher and the wrong source for a revenue filing, a dataset, or a court
# ruling — and because it is high-authority it used to rank ABOVE the real
# primary source and crowd it out of the fetch budget.
#
# Values are additive adjustments applied to a result's score, keyed by tier.
# Negative entries are the load-bearing part: they push a confidently
# irrelevant publisher down instead of merely failing to promote a good one.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ORIGINAL-SOURCE RESOLUTION
#
# A secondary page that cites a study is not independent evidence from the
# study itself. Detecting the identifiers lets the ranker prefer the original
# and lets corroboration counting treat republications of one original as one.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ON-TOPIC CHECK
#
# The failure this exists to prevent: a question about one company's filing
# retrieving encyclopedia pages that define a phrase the question happens to
# use. Those pages score well on wording overlap while engaging none of the
# question's subject matter.
# ---------------------------------------------------------------------------

# Publishers label orientation pages in many ways: "Meaning", "How It Works",
# "Explained", "Glossary". Requiring one exact phrase missed the majority of the
# pages that actually caused the failure.
_DEFINITION_TITLE = re.compile(
    r"\b(what (?:is|are|was|were)\b|definition\b|definitions\b|meaning\b|"
    r"explained\b|glossary\b|overview\b|introduction\b|described as\b|"
    r"how (?:it|they) work\b|simple explanation\b|what does .{0,40}\bmean\b|"
    r"everything you need to know\b|commonly confused\b)",
    re.I,
)


def looks_like_definition_page(title: str, snippet: str = "") -> bool:
    """A glossary/definition page rather than a document about the subject."""
    blob = f"{title or ''} {snippet or ''}"
    return bool(_DEFINITION_TITLE.search(blob))


def definition_misfit(title: str, snippet: str, asks_definition: bool) -> float:
    """Penalty for a definition page returned to a non-definition question.

    0.0 when the page is on-type, or when the reader actually asked what
    something is — in which case a definition page is precisely the right hit.
    """
    if asks_definition:
        return 0.0
    return -0.30 if looks_like_definition_page(title, snippet) else 0.0


# ---------------------------------------------------------------------------
# TOPICALITY
#
# Authority answers "how much should we trust this publisher". It does not
# answer "does this document address what was asked" — and adding the second as
# a small bonus does not fix that, because the two are not the same magnitude.
# Authority spans 0.0-0.95 while relevance could only ever contribute 0.25, so
# an authoritative page about an entirely different subject outranked the
# on-topic answer by ~0.37. Nothing anywhere dropped it: the ranker filtered
# blocked hosts and duplicates, never irrelevance.
#
# So topicality is applied MULTIPLICATIVELY to authority. A document that
# engages none of the question's subject keeps only TOPICALITY_AUTHORITY_FLOOR
# of its authority credit, and one that engages it fully keeps all of it. That
# is what makes relevance able to outrank a tier gap when it must, while leaving
# on-topic ranking order otherwise unchanged.
# ---------------------------------------------------------------------------

# Fraction of a result's authority credit that survives ZERO topical engagement.
TOPICALITY_AUTHORITY_FLOOR = 0.35

# Below this share of the question's subject words a result counts as engaging
# nothing at all. Low on purpose: the floor exists to remove documents about a
# different subject, not to second-guess the ranker about weak matches. A page
# that lands one substantive subject word still counts as relevant and is
# ranked normally.
MIN_TOPICAL_ENGAGEMENT = 0.10

# Function words carry no subject. Excluded from the overlap denominator so
# "what is the population of Malawi" is not scored on "what/is/the/of".
_TOPICAL_STOPWORDS: frozenset = frozenset({
    "a", "about", "an", "and", "are", "as", "at", "be", "been", "by", "compared",
    "did", "do", "does", "during", "explain", "for", "from", "give", "has",
    "have", "how", "in", "into", "is", "it", "its", "list", "many", "much",
    "of", "on", "or", "overview", "per", "report", "summarize", "summarise",
    "than", "that", "the", "their", "them", "there", "these", "this", "those",
    "to", "was", "were", "what", "when", "where", "which", "who", "why",
    "with", "within", "without",
    # Meta-questions about the subject rather than the subject itself. "define
    # RAG" is a question ABOUT the term "RAG"; scoring a page on having the
    # word "define" would rate every glossary page as on-topic.
    "define", "defined", "defines", "definition", "mean", "means", "meaning",
    "called", "known", "term", "actually", "really",
})


def _topical_words(text: str) -> Set[str]:
    return {
        w for w in re.findall(r"[a-z0-9][a-z0-9'&.-]*", (text or "").lower())
        if w not in _TOPICAL_STOPWORDS and len(w) > 1
    }


def topical_engagement(
    query: str, text: str, entity_tokens: Sequence[str] = ()
) -> float:
    """How much of the question's subject a result actually engages, 0.0-1.0.

    Two independent readings, because either alone is easy to fool:

      * CONTENT-WORD OVERLAP — the share of the question's subject words that
        appear in the result. Fails on paraphrases and on proper nouns the
        result abbreviates.
      * ENTITY ENGAGEMENT — whether any subject the question NAMED (a person,
        an organisation, an acronym, a figure) appears at all. Survives
        paraphrase, and catches the page that shares the question's wording
        while being about something else.

    The entity reading wins when the question named anything, because a named
    subject is the part a substitute page is most likely to drop. A question
    with no nameable subject ("what is a quark") falls back to overlap alone.

    0.0 when the query carries no subject to engage with, so callers must treat
    it as "cannot judge" rather than "irrelevant" — the ranking floor checks the
    query is substantive before discarding anything on this basis.
    """
    q_words = _topical_words(query)
    t_words = _topical_words(text)
    lexical = (len(q_words & t_words) / len(q_words)) if q_words else 0.0
    if entity_tokens:
        low = (text or "").lower()
        named = 1.0 if any(tok.lower() in low for tok in entity_tokens) else 0.0
        return max(lexical, named)
    return lexical


def is_topically_irrelevant(query: str, text: str, entity_tokens: Sequence[str] = ()) -> bool:
    """Does this result engage NOTHING the question is about?

    The floor rule, kept separate from `topical_engagement` because it answers a
    different question. Engagement is a continuous score for ranking; this is
    the binary "is this document about a different subject" test that justifies
    discarding the result entirely.

    When the question named a subject, a hit on ANY of it is enough to keep the
    result, because a substitute page is most likely to drop the name while
    keeping the surrounding vocabulary. Only when every named subject is absent
    AND the remaining word overlap is negligible is it discarded. Requiring both
    is what keeps a real-but-partial match ("forward guidance" for a question
    about revenue guidance) in the pool for the ranker to place, while still
    removing a Mars-rover page returned to a question about Malawian air
    pollution deaths.
    """
    overlap = topical_engagement(query, text, ())
    if overlap >= MIN_TOPICAL_ENGAGEMENT:
        return False
    if entity_tokens:
        low = (text or "").lower()
        return not any(tok.lower() in low for tok in entity_tokens)
    return True


def topicality_floor_applies(query: str, entity_tokens: Sequence[str] = ()) -> bool:
    """Is this query specific enough that irrelevance is detectable?

    A question has to say something before "not about it" means anything.
    "population of Malawi" is two content words and entirely judgeable; "what is
    it" is not, and filtering on engagement there would discard results for no
    reason. The bar is deliberately low and only asks whether the query carries
    a subject at all.
    """
    if entity_tokens:
        return True
    return len(_topical_words(query)) >= 2


def entity_miss(query: str, tokens: Sequence[str], text: str) -> float:
    """Penalty when a result engages none of the question's subject tokens.

    Wording overlap alone can carry a result to the top while the document is
    about something else entirely. Requiring at least one subject token keeps
    the genuinely on-topic primary source competitive with it.
    """
    if not tokens:
        return 0.0
    low = (text or "").lower()
    if any(tok.lower() in low for tok in tokens):
        return 0.0
    # Scale with how much of the question's subject we are missing.
    return -0.22 if len(tokens) == 1 else -0.30


# ---------------------------------------------------------------------------
# FIRST-PARTY SOURCES
#
# An issuer's own domain is the primary source for that issuer's disclosures,
# and no static registry can know every issuer. It can be derived: if a result's
# host CONTAINS a subject token the question named ("Nvidia" -> nvidia.com,
# "investor.nvidia.com"), then that host is first-party for that subject.
#
# This is what lets an unlisted issuer's investor-relations page outrank an
# established financial aggregator when the question asked for the issuer's own
# filing, without hardcoding a single company.
# ---------------------------------------------------------------------------

_HOST_SPLIT = re.compile(r"[.\-_]+")


def _host_labels(domain: str) -> Set[str]:
    """Candidate tokens for a host, ignoring the public suffix.

    `investor.nvidia.com` -> {investor, nvidia, com}. Public suffixes are not
    excluded by a full list (that would need a dependency); instead a token is
    only usable if it is not a bare TLD, which is enough to stop ".com" from
    matching a question that happens to contain "com".
    """
    labels: Set[str] = set()
    for part in _HOST_SPLIT.split((domain or "").lower()):
        if len(part) >= 3 and not part.isdigit():
            labels.add(part)
    return labels


def first_party_match(url: str, entity_tokens: Sequence[str]) -> Optional[str]:
    """The subject token this host is first-party for, if any.

    Requires the token to be at least 4 characters so a short acronym does not
    match by accident, and to appear as a whole host label.
    """
    if not entity_tokens:
        return None
    labels = _host_labels(extract_domain(url))
    if not labels:
        return None
    for tok in entity_tokens:
        t = re.sub(r"[^\w]+", "", (tok or "").lower())
        if len(t) >= 4 and t in labels:
            return t
    return None


def first_party_bonus(url: str, entity_tokens: Sequence[str]) -> float:
    """Reward a host that belongs to the question's own subject.

    Small and unconditional on tier: it says "this publisher is the subject",
    which is orthogonal to how authoritative it is. An issuer's own IR page and
    an official statistics agency are both first-party for their question.
    """
    return 0.22 if first_party_match(url, entity_tokens) else 0.0


# ---------------------------------------------------------------------------
# INDEPENDENCE
#
# Corroboration is only evidence when the agreeing sources are INDEPENDENT.
# Five outlets running the same wire story, or five summaries of one study, are
# one source repeated — counting them as five agreeing sources inflates
# confidence on a single unverified claim.
#
# These helpers group facts by the original they ultimately rest on, so
# confidence is computed over independent sources rather than over pages.
# ---------------------------------------------------------------------------


def underlying_groups(
    items: Iterable[Tuple[str, str, str, str]],
) -> List[List[str]]:
    """Group (url, title, snippet, content) tuples by underlying source.

    Returns the groups in first-seen order. Anything without a usable URL forms
    its own group so an unidentifiable fact is never silently merged away.
    """
    order: List[str] = []
    groups: Dict[str, List[str]] = {}
    for url, title, snippet, content in items or ():
        u = (url or "").strip()
        if not u:
            order.append(f"\x00anon{len(order)}")
            groups[order[-1]] = [u]
            continue
        key = underlying_source_key(u, title or "", snippet or "", content or "")
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(u)
    return [groups[k] for k in order]


def independence_ratio(
    items: Iterable[Tuple[str, str, str, str]],
) -> float:
    """Independent sources / total facts. 1.0 = every fact stands alone.

    A report built from one study plus four summaries of it scores 0.2; the
    same five documents retrieved as five genuinely separate findings scores
    1.0. This is what stops repetition from reading as corroboration.
    """
    rows = [t for t in (items or ()) if (t[0] or "").strip()]
    if not rows:
        return 1.0
    groups = underlying_groups(rows)
    if not groups:
        return 1.0
    return len(groups) / len(rows)


def independent_primary_share(
    items: Iterable[Tuple[str, str, str, str]],
) -> float:
    """Share of INDEPENDENT sources that are primary or first-rate authority.

    The independence-aware counterpart to `primary_source_share`: one primary
    study quoted by four aggregators scores 1.0 here (one independent source,
    and it is primary) while counting four separate primaries, which is the
    number that actually flattered the old measure.
    """
    rows = [t for t in (items or ()) if (t[0] or "").strip()]
    if not rows:
        return 0.0
    groups = underlying_groups(rows)
    if not groups:
        return 0.0
    good = 0
    for group in groups:
        url = group[0]
        prof = classify_source(url)
        # Prefer the strongest document in the group: a group containing the
        # original is as primary as its original.
        if prof.is_primary or prof.authority >= 0.85:
            good += 1
    return good / len(groups)
