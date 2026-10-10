"""Evidence hygiene, scoring, deduplication and citation support checks.

Refactor note
-------------
The implementation now lives in the `app.agents.evidence` package, split into
single-responsibility modules (`domain`, `text`, `numbers`, `polarity`,
`filtering`, `dedupe`, `support`, `cleaning`, `dates`, `stats`). This module is
the stable facade: it re-exports every public and private name the rest of the
codebase imports from `app.agents.evidence_utils`, so the import surface is
UNCHANGED.
"""
from __future__ import annotations

# Re-exported source-registry helpers: these were module-scope imports in the
# pre-refactor `evidence_utils`, so consumers that imported them from here
# (e.g. `canonical_url`) keep working unchanged.
from app.agents.sources import (  # noqa: F401
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

from app.agents.evidence.domain import (  # noqa: F401
    LOW_QUALITY_DOMAINS,
    extract_domain,
    is_high_quality_domain,
    source_reliability_score,
)
from app.agents.evidence.text import (  # noqa: F401
    normalize_claim_text,
    _tokenize,
    _jaccard,
    _semantic_similarity,
    semantic_similarity,
)
from app.agents.evidence.numbers import (  # noqa: F401
    _NUMBER_RE,
    _SCALE,
    _UNIT_ALIASES,
    _CURRENCY_RE,
    Quantity,
    extract_numbers,
    _significant_quantities,
    _ANCHOR_STOPWORDS,
    rare_content_tokens,
    numbers_grounded,
    numeric_conflict,
)
from app.agents.evidence.polarity import (  # noqa: F401
    _NEGATION_TOKENS,
    _DIRECTION_UP,
    _DIRECTION_DOWN,
    claim_polarity,
)
from app.agents.evidence.filtering import (  # noqa: F401
    filter_search_results_by_domain,
    filter_facts_by_domain,
)
from app.agents.evidence.dedupe import (  # noqa: F401
    logger,
    _REPRESENTATIVE_CONFIDENCE_MARGIN,
    _source_authority,
    _pick_representative,
    dedupe_semantic_facts,
)
from app.agents.evidence.support import (  # noqa: F401
    CITATION_RE,
    SOURCES_HEADING_RE,
    LEGEND_RE,
    SUPPORT_THRESHOLD,
    SYNTHESIS_SUPPORT_FLOOR,
    parse_answer_legend,
    verify_answer_support,
)
from app.agents.evidence.cleaning import (  # noqa: F401
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
    looks_truncated,
    clean_snippet_text,
    claim_query_overlap,
    claim_matches_only_a_generic_head,
    MIN_QUERY_OVERLAP,
    select_diverse,
)
from app.agents.evidence.dates import (  # noqa: F401
    parse_published_date,
)
from app.agents.evidence.stats import (  # noqa: F401
    evidence_stats,
    _NON_WESTERN_MARKERS,
    _is_non_western_source,
)
