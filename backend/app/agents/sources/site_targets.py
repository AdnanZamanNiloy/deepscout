from __future__ import annotations

import re
from typing import List, NamedTuple, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

from app.agents.sources.jurisdiction import (
    host_jurisdiction,
    jurisdiction_is_admissible,
    jurisdiction_site_terms,
    question_jurisdiction,
)
from app.agents.sources.primary import (
    primary_intent_terms,
    primary_source_hints,
    wants_a_primary_source,
)

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


_AUTHORITATIVE_SUFFIXES: Tuple[str, ...] = ("gov", "edu", "int", "gov.uk")


AUTHORITATIVE_INTENT_TERMS: Tuple[str, ...] = (
    "official report", "government data", "dataset", "peer-reviewed study",
)


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
