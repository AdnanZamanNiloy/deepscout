from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

from app.agents.sources.urls import (
    extract_domain,
)

TIER_OFFICIAL = "official"        # statute/statistic/filing publisher; the record itself


TIER_PEER_REVIEWED = "peer_reviewed"  # journals, indexed proceedings


TIER_PREPRINT = "preprint"        # arXiv/bioRxiv/SSRN: primary but unrefereed


TIER_REFERENCE = "reference"      # encyclopedias, standards references


TIER_INDUSTRY = "industry"        # trade bodies, vendor docs, research firms


TIER_MEDIA = "media"              # journalism


TIER_SECONDARY = "secondary"      # aggregators, general web


TIER_LOW = "low"                  # UGC, SEO farms, social


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


PRIMARY_TIERS: frozenset[str] = frozenset(
    {TIER_OFFICIAL, TIER_PEER_REVIEWED, TIER_PREPRINT}
)


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
