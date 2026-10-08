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
        queries.append(question)
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
