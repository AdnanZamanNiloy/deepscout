from __future__ import annotations

from typing import Any, Dict, List

from app.agents.sources import canonical_url
from app.agents.sources import documentary_authority
from app.agents.evidence.domain import (
    extract_domain,
    is_high_quality_domain,
    source_reliability_score,
)
from app.agents.evidence.text import (
    normalize_claim_text,
)


def filter_search_results_by_domain(
    results: List[Dict[str, str]],
    min_score: float = 0.60,
    fallback_min_score: float = 0.55,
    fallback_limit: int = 8,
) -> List[Dict[str, str]]:
    strong: List[Dict[str, str]] = []
    fallback: List[Dict[str, Any]] = []

    for item in results or []:
        url = str(item.get("url", "")).strip()
        blob = f"{item.get('title', '')} {item.get('snippet', '')}"
        if not url or not is_high_quality_domain(url, text=blob):
            continue

        # Score the same way, so the threshold and the gate agree.
        score = documentary_authority(url, blob)
        if score >= min_score:
            strong.append(item)
            continue
        if score >= fallback_min_score:
            fallback.append({"score": score, "item": item})

    if strong:
        strong_domains = {
            extract_domain(str(item.get("url", ""))) for item in strong if item.get("url")
        }
        strong_domains.discard("")
        if len(strong) >= 3 and len(strong_domains) >= 2:
            return strong

        supplemented = list(strong)
        seen_urls = {
            canonical_url(str(item.get("url", ""))) for item in strong if item.get("url")
        }
        fallback.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

        for entry in fallback:
            item = entry.get("item")
            if not isinstance(item, dict):
                continue
            url = canonical_url(str(item.get("url", "")).strip())
            if not url or url in seen_urls:
                continue
            supplemented.append(item)
            seen_urls.add(url)
            if len(supplemented) >= max(3, min(fallback_limit, 10)):
                break
        return supplemented

    fallback.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    return [entry["item"] for entry in fallback[: max(1, fallback_limit)]]


def filter_facts_by_domain(
    facts: List[Dict[str, Any]],
    min_score: float = 0.62,
    fallback_min_score: float = 0.55,
) -> List[Dict[str, Any]]:
    strong: List[Dict[str, Any]] = []
    fallback: List[Dict[str, Any]] = []

    for item in facts or []:
        source = str(item.get("source", "")).strip()
        claim = normalize_claim_text(str(item.get("claim", "")))
        # No text conditioning here: a claim is an extracted sentence, not a
        # document, so testing it for identifiers or publisher vocabulary would
        # reject ordinary claims on ordinary sites. Source pages were already
        # screened at retrieval, where their title and snippet were available.
        if not source or not claim or not is_high_quality_domain(source):
            continue

        score = source_reliability_score(source)
        if score >= min_score:
            strong.append(item)
            continue
        if score >= fallback_min_score:
            fallback.append(item)

    if strong:
        strong_domains = {
            extract_domain(str(item.get("source", ""))) for item in strong if item.get("source")
        }
        strong_domains.discard("")
        if len(strong) >= 3 and len(strong_domains) >= 2:
            return strong

        supplemented = list(strong)
        seen_sources = {
            canonical_url(str(item.get("source", ""))) for item in strong if item.get("source")
        }
        fallback.sort(key=lambda x: float(x.get("confidence", 0.0) or 0.0), reverse=True)

        for item in fallback:
            source = canonical_url(str(item.get("source", "")).strip())
            if not source or source in seen_sources:
                continue
            supplemented.append(item)
            seen_sources.add(source)
            if len(supplemented) >= 8:
                break
        return supplemented

    fallback.sort(key=lambda x: float(x.get("confidence", 0.0) or 0.0), reverse=True)
    return fallback[:8]
