"""Evidence hygiene, scoring, deduplication and citation support checks.

This is the evidence package's public surface: it re-exports the helpers from
the single-responsibility submodules (`domain`, `text`, `numbers`, `polarity`,
`filtering`, `dedupe`, `support`, `cleaning`, `dates`, `stats`) plus the source
registry, so consumers import one stable module rather than reaching into the
submodules directly.
"""
from __future__ import annotations

# Source-registry helpers re-exported here for convenience, so callers (e.g.
# `canonical_url`) import from one place.
from app.evidence.sources import (  # noqa: F401
    LOW_TRUST_DOMAINS,
    TIER_PEER_REVIEWED,
    authority_score,
    canonical_url,
    classify_source,
    documentary_authority,
    evidence_freshness,
    is_primary_source,
    primary_source_share,
)

from app.evidence.domain import (  # noqa: F401
    extract_domain,
    is_high_quality_domain,
    source_reliability_score,
)
from app.evidence.text import (  # noqa: F401
    normalize_claim_text,
    _tokenize,
    _semantic_similarity,
    semantic_similarity,
)
from app.evidence.numbers import (  # noqa: F401
    _NUMBER_RE,
    _SCALE,
    _UNIT_ALIASES,
    _CURRENCY_RE,
    Quantity,
    extract_numbers,
    _significant_quantities,
    numbers_grounded,
    numeric_conflict,
)
from app.evidence.polarity import (  # noqa: F401
    _NEGATION_TOKENS,
    _DIRECTION_UP,
    _DIRECTION_DOWN,
    claim_polarity,
)
from app.evidence.filtering import (  # noqa: F401
    filter_facts_by_domain,
)
from app.evidence.dedupe import (  # noqa: F401
    logger,
    _REPRESENTATIVE_CONFIDENCE_MARGIN,
    _source_authority,
    _pick_representative,
    dedupe_semantic_facts,
)
from app.evidence.support import (  # noqa: F401
    CITATION_RE,
    SOURCES_HEADING_RE,
    LEGEND_RE,
    SUPPORT_THRESHOLD,
    SYNTHESIS_SUPPORT_FLOOR,
    parse_answer_legend,
    verify_answer_support,
)
from app.evidence.cleaning import (  # noqa: F401
    DATE_STAMP_RE,
    LEADING_HASH_RE,
    LINK_TEXT_RE,
    WIKI_NAV_RE,
    EXCERPT_SEAM_RE,
    LINK_TITLE_PAREN_RE,
    HEADING_MARK_RE,
    BLOCKQUOTE_RE,
    LATEX_SOUP_RE,
    BOILERPLATE_LEAD_RE,
    DANGLING_END_RE,
    TITLE_PREFIX_RE,
    WIKI_EDIT_MARK_RE,
    CONSENT_NOISE_RE,
    NAV_RUN_RE,
    MIN_CLEAN_CLAIM_CHARS,
    _SENTENCE_SPLIT_RE,
    _looks_like_title_prefix,
    split_into_sentences,
    clean_snippet_text,
    claim_query_overlap,
)
from app.evidence.dates import (  # noqa: F401
    parse_published_date,
)
from app.evidence.stats import (  # noqa: F401
    evidence_stats,
    _NON_WESTERN_MARKERS,
    _is_non_western_source,
)
