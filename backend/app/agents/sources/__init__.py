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

Refactor note
-------------
The implementation now lives in the `app.agents.sources` package, split into
single-responsibility modules (`tiers`, `urls`, `documentary`, `jurisdiction`,
`primary`, `site_targets`, `freshness`, `prose`, `fit`, `topicality`). This
package `__init__` is the stable facade: it re-exports every public and private
name the rest of the codebase imports from `app.agents.sources`, so the import
surface is UNCHANGED.
"""
from __future__ import annotations

from app.agents.sources.tiers import (  # noqa: F401
    TIER_OFFICIAL,
    TIER_PEER_REVIEWED,
    TIER_PREPRINT,
    TIER_REFERENCE,
    TIER_INDUSTRY,
    TIER_MEDIA,
    TIER_SECONDARY,
    TIER_LOW,
    TIER_AUTHORITY,
    PRIMARY_TIERS,
    OFFICIAL_DOMAINS,
    OFFICIAL_SUFFIXES,
    PEER_REVIEWED_DOMAINS,
    PREPRINT_DOMAINS,
    REFERENCE_DOMAINS,
    INDUSTRY_DOMAINS,
    MEDIA_DOMAINS,
    LOW_TRUST_DOMAINS,
    SEO_PATH_RE,
    SourceProfile,
    _matches,
    _has_suffix,
    classify_source,
)
from app.agents.sources.urls import (  # noqa: F401
    _TRACKING_PARAM_RE,
    extract_domain,
    canonical_url,
)
from app.agents.sources.documentary import (  # noqa: F401
    UNDOCUMENTED_AUTHORITY,
    _DOCUMENTARY_ID_RE,
    _DOCUMENTARY_VOCAB_RE,
    _PROMOTIONAL_RE,
    _KEYWORD_STUFFED_HOST_RE,
    looks_keyword_stuffed,
    has_documentary_evidence,
    looks_promotional,
    documentary_authority,
    authority_score,
    is_primary_source,
    primary_source_share,
)
from app.agents.sources.jurisdiction import (  # noqa: F401
    COUNTRY_OFFICIAL_SUFFIX,
    _COUNTRY_ALIASES,
    _COUNTRY_BY_ISO2_ALIASES,
    _KNOWN_COUNTRY_CODES,
    _CCTLD_TO_ISO2,
    _register_country,
    SUPRANATIONAL_JURISDICTIONS,
    _PSEUDO_TLDS,
    _JURISDICTION_SUFFIX_RULES,
    _TWO_PART_SUFFIXES,
    _registrable,
    host_jurisdiction,
    _DOMAIN_IN_TEXT_RE,
    _UPPER_CODE_RE,
    question_jurisdiction,
    jurisdiction_is_admissible,
    jurisdiction_site_terms,
)
from app.agents.sources.primary import (  # noqa: F401
    PRIMARY_SOURCE_HINTS,
    PRIMARY_INTENT_TERMS,
    primary_source_hints,
    primary_intent_terms,
    wants_a_primary_source,
)
from app.agents.sources.site_targets import (  # noqa: F401
    _AUTHORITATIVE_HOSTS,
    _AUTHORITATIVE_SUFFIXES,
    AUTHORITATIVE_INTENT_TERMS,
    authoritative_site_terms,
    grounded_site_targets,
    build_primary_source_query,
    build_dimension_primary_query,
    build_substitution_query,
    _blocks_host,
    SiteTargets,
    _SITE_TERM_RE,
    partition_site_targets,
    build_corroboration_query,
)
from app.agents.sources.freshness import (  # noqa: F401
    FRESHNESS_HALF_LIFE,
    _as_date,
    freshness_score,
    evidence_freshness,
)
from app.agents.sources.prose import (  # noqa: F401
    MACHINE_SECTIONS,
    strip_machine_sections,
    _INLINE_CITE_RE,
    _EM_DASH_RE,
    _HR_RE,
    clean_writer_prose,
)
from app.agents.sources.fit import (  # noqa: F401
    TIER_FIT,
    evidence_fit,
    _DOI_RE,
    _ARXIV_RE,
    _DOI_URL_RE,
    _ARXIV_URL_RE,
    _PMID_RE,
    _ISBN_RE,
    detect_primary_refs,
    underlying_source_key,
    is_original_source,
)
from app.agents.sources.topicality import (  # noqa: F401
    _DEFINITION_TITLE,
    looks_like_definition_page,
    definition_misfit,
    TOPICALITY_AUTHORITY_FLOOR,
    MIN_TOPICAL_ENGAGEMENT,
    _TOPICAL_STOPWORDS,
    _topical_words,
    _ENTITY_TITLE_SALIENCE,
    _ENTITY_REPEAT_SALIENCE,
    _ENTITY_ONCE_SALIENCE,
    _ENTITY_HEAD_CHARS,
    _FIRST_PARTY_ENGAGEMENT_FLOOR,
    _SALIENT_MAX_PENALTY,
    _SALIENT_DISCOUNT,
    _entity_salience,
    topical_engagement,
    is_topically_irrelevant,
    topicality_floor_applies,
    entity_miss,
    _HOST_SPLIT,
    _host_labels,
    first_party_match,
    first_party_bonus,
    underlying_groups,
    independence_ratio,
    independent_primary_share,
)
