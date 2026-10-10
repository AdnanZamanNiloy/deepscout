from __future__ import annotations

import re
from typing import Any, List

from app.agents.sources import build_dimension_primary_query
from app.agents.searchkit.text import (
    _semantic_overlap,
)


def _split_query(query: Any) -> tuple[str, str]:
    """Split any accepted query shape into (question_text, search_type)."""
    if isinstance(query, dict):
        return (
            str(query.get("question", "") or "").strip(),
            str(query.get("search_type", "") or "").strip(),
        )
    if isinstance(query, (tuple, list)) and len(query) == 2:
        return (str(query[0] or "").strip(), str(query[1] or "").strip())
    return (str(query or "").strip(), "")


# A search engine matches keywords, not essay-length interrogatives. A live run
# showed the planner emitting 30-50 word compound questions
# ("What are the highest-strain open research problems in computer science as of
# 2026, ranked by community urgency and resource intensity, according to
# authoritative sources such as ACM, IEEE, CRA, and major conference
# keynote/roadmap reports?") which were sent VERBATIM to the provider, returned
# generic noise, and left the same angle unsourced across every critic round
# until the run degraded. The full question stays in the plan/UI; the search
# gets a compact keyword query derived from it.
_MAX_SEARCH_WORDS = 14

# Clause boundaries: a compound question is split here and the FIRST clause
# (the actual ask) is kept, with the trailing qualifiers dropped. Sentence
# punctuation, parenthetical citations, and the "according to / such as / with
# evidence such as" tails are the usual noise.
_CLAUSE_SPLIT_RE = re.compile(r"\s*[;?]\s*|\s+[—–]\s+|\s+\((?:e\.g\.|i\.e\.|such as)[^)]*\)")
_TAIL_RE = re.compile(
    r"\s*,?\s*(?:according to|as (?:reported|documented|measured|distinct)|"
    r"with (?:quantitative )?evidence|based on|ranked by|including)\b.*$",
    re.IGNORECASE,
)
_LEAD_RE = re.compile(
    r"^\s*(?:what|which|who|where|when|why|how)\s+"
    r"(?:are|is|was|were|does|do|did|can|could|would|should|has|have|will)\s+",
    re.IGNORECASE,
)


def _compact_search_query(question: str) -> str:
    """A search-ready keyword query derived from an over-long question.

    Keeps the subject and the ask, drops the trailing qualifier clauses that no
    search engine can match. Only compacts when the question is over the word
    cap; a short, already-search-ready question is returned unchanged.
    """
    text = re.sub(r"\s+", " ", (question or "")).strip()
    if len(text.split()) <= _MAX_SEARCH_WORDS:
        return text
    head = _CLAUSE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    head = _TAIL_RE.sub("", head).strip().rstrip(" ,")
    # Strip a leading interrogative ("What are ..." -> "...") so the query reads
    # as a keyword phrase; keep it when stripping would leave too little.
    stripped = _LEAD_RE.sub("", head, count=1).strip()
    if len(stripped.split()) >= 3:
        head = stripped
    if not head:
        return text
    words = head.split()
    if len(words) > _MAX_SEARCH_WORDS:
        head = " ".join(words[:_MAX_SEARCH_WORDS])
    return head.strip(" ,;?.")


def contract_queries(contract: Any, max_queries: int = 3) -> List[str]:
    """Every query one delegation contract should actually run.

    Base question, then the planner's variants, then the primary-source-scoped
    variant. Variants exist precisely because different phrasings retrieve
    different documents, and until now they were parsed, validated, stored and
    never used. Capped so a 5-contract plan cannot fan out to 20 searches.

    The primary-source-scoped variant is RESERVED a slot, not appended last and
    truncated: it is the query aimed at the publisher that owns the fact
    (site:worldbank.org, site:arxiv.org, ...), and it was being dropped on any
    contract the model had already given two variants — which is every
    high-value contract. Reserving the slot is what actually raises the
    independent/primary-source yield the source ledger reports.

    When the contract carries no primary query but its search_type/domain has a
    registered publisher hint, one is BUILT here rather than skipped: a primary
    procurement slot is guaranteed for every dimension that can have one, so a
    missing planner field can never silently drop the primary search again.
    """
    question, _ = _split_query(contract)
    queries: List[str] = []
    if question:
        # Issue a search-ready query, not an essay-length interrogative (see
        # `_compact_search_query`). The full question is preserved in the plan.
        queries.append(_compact_search_query(question))
    primary = ""
    if isinstance(contract, dict):
        primary = re.sub(r"\s+", " ", str(contract.get("primary_source_query", "") or "")).strip()
    if not primary and question:
        # Defensive per-dimension guaranteed primary query. Builds a plain
        # hint-scoped variant first, then falls back to the authoritative
        # registry, so a contract with no primary query still gets a slot.
        search_type = str(contract.get("search_type", "") or "") if isinstance(contract, dict) else ""
        domain = str(contract.get("domain", "") or "") if isinstance(contract, dict) else ""
        primary = build_dimension_primary_query(question, search_type, domain)
    budget = max(1, max_queries)
    # Hold one slot for the primary query whenever it exists and there is room
    # for more than the base question.
    variant_budget = budget
    if primary and budget > 1:
        variant_budget = budget - 1
    if isinstance(contract, dict):
        for variant in contract.get("variants") or ():
            text = re.sub(r"\s+", " ", str(variant or "")).strip()
            if text and text.lower() != question.lower():
                queries.append(text)
            if len(queries) >= variant_budget:
                break
    if primary:
        queries.append(primary)
    deduped: List[str] = []
    for q in queries:
        if not any(_semantic_overlap(q, kept) >= 0.92 for kept in deduped):
            deduped.append(q)
        if len(deduped) >= budget:
            break
    return deduped
