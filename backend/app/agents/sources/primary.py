from __future__ import annotations

from typing import Dict, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


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
    "academic:software": ("arxiv.org", "dl.acm.org", "openreview.net"),
    "academic:engineering": ("arxiv.org", "doi.org", "ieeexplore.ieee.org"),
    "news:general": ("reuters.com", "apnews.com", "ft.com"),
    "comparison:general": (),
    "encyclopedia:general": ("britannica.com",),
    "encyclopedia:philosophy": ("plato.stanford.edu",),
}

# (search_type, domain) pairs for which the generic IGO/statistical registry
# (World Bank, WHO, OECD, UN) is a plausible primary publisher. Those agencies
# publish human/economic/policy data; they publish NOTHING about a computer
# science research topic. Firing the registry for an academic/technical question
# was the mechanism behind the live drift ("site:who.int" queries returning
# prostate-biopsy bibliometrics and marine-litter papers).
_REGISTRY_APPROPRIATE_DOMAINS: frozenset = frozenset(
    {"economics", "policy", "general", "science"}
)


def registry_appropriate(search_type: str, domain: str = "general") -> bool:
    """May the generic IGO/statistical registry be used for this question?

    True only for statistical/news/policy-shaped questions whose domain is not
    an academic/technical field. An `academic` search type never consults the
    IGO registry — its primary publishers are preprint servers, journals and
    conferences, which `primary_source_hints` already names.
    """
    st = (search_type or "").strip().lower()
    dm = (domain or "").strip().lower() or "general"
    if st == "academic":
        return False
    return dm in _REGISTRY_APPROPRIATE_DOMAINS


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
