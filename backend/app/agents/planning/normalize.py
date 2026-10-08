"""Normalization, axis mapping and semantic dedup for the planner.

Extracted verbatim from `app/agents/planner.py` (refactor; no behaviour change).
Cheap, deterministic text helpers the validator, contract builder and the
broader pipeline share: `normalize_text`/`normalize_domain` (used by the graph,
depth controller and investigation planner), `dimension_to_axis`,
`axis_search_type`, and the content-token dedup that stops two sub-questions
searching for the same thing.

`planner.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from app.agents.planning.types import (
    AXIS_SEARCH_TYPE,
    VALID_DOMAINS,
    VALID_SEARCH_TYPES,
)


# Ordered (label-fragment, canonical-axis) aliases. Common wordings for the
# canonical retrieval categories are recognized so a model-chosen dimension
# ("head-to-head comparison") still maps onto the retrieval/section machinery,
# while genuinely query-specific dimensions keep their own label. Order
# matters: more specific fragments are matched first.
_AXIS_ALIASES: Tuple[Tuple[str, str], ...] = (
    ("counter-evidence", "criticism"),
    ("counterevidence", "criticism"),
    ("criticism", "criticism"),
    ("critique", "criticism"),
    ("limitation", "criticism"),
    ("drawback", "criticism"),
    ("downside", "criticism"),
    ("failure mode", "risk"),
    ("risk", "risk"),
    ("hazard", "risk"),
    ("safety", "risk"),
    ("head-to-head", "comparison"),
    ("comparison", "comparison"),
    ("compared", "comparison"),
    ("versus", "comparison"),
    ("trade-off", "comparison"),
    ("tradeoff", "comparison"),
    ("cost", "cost"),
    ("price", "cost"),
    ("financing", "cost"),
    ("financial", "cost"),
    ("funding", "cost"),
    ("budget", "cost"),
    ("efficiency", "cost"),
    ("mechanism", "mechanism"),
    ("how it works", "mechanism"),
    ("causal", "mechanism"),
    ("cause", "mechanism"),
    ("driver", "mechanism"),
    ("root cause", "mechanism"),
    ("post-mortem", "mechanism"),
    ("definition", "definition"),
    ("what is", "definition"),
    ("overview", "definition"),
    ("background", "definition"),
    ("outlook", "outlook"),
    ("forecast", "outlook"),
    ("projection", "outlook"),
    ("trend", "outlook"),
    ("future", "outlook"),
    ("evidence", "evidence"),
    ("data", "evidence"),
    ("statistic", "evidence"),
    ("quantitative", "evidence"),
    ("measurement", "evidence"),
    ("application", "application"),
    ("use case", "application"),
    ("implementation", "application"),
    ("history", "history"),
    ("origin", "history"),
    ("timeline", "history"),
    ("regulation", "regulation"),
    ("regulatory", "regulation"),
    ("policy", "regulation"),
    ("governance", "regulation"),
    ("legal", "regulation"),
)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def normalize_domain(domain: str) -> str:
    d = normalize_text(domain)
    return d if d in VALID_DOMAINS else "general"


def dimension_to_axis(dimension: str, search_type: str = "") -> str:
    """Canonical retrieval axis for a model-chosen dimension label.

    The dynamic dimensions are free-form text; this maps the ones that clearly
    name a canonical category onto that category's retrieval/section behavior,
    preferring a `search_type` implied by the dimension when it is explicit.
    A genuinely query-specific dimension ("policy options", "institutional
    failure") returns its normalized slug unchanged — downstream code holds
    unknown axes as first-class string keys, so it still gets its own section.
    """
    text = normalize_text(dimension)
    if not text:
        return "general"
    if search_type == "statistical" and not any(
        c in text for c in ("risk", "failure", "criticism", "counter")
    ):
        return "evidence"
    for fragment, axis in _AXIS_ALIASES:
        if fragment in text:
            return axis
    slug = text.replace(" ", "_")
    return slug


def axis_search_type(axis: str, *, default: str = "academic") -> str:
    """Retrieval type for an axis — canonical map first, else a text hint."""
    if axis in AXIS_SEARCH_TYPE:
        return AXIS_SEARCH_TYPE[axis]
    text = axis.replace("_", " ")
    if any(c in text for c in ("cost", "price", "financ", "evidence", "stat", "number", "data")):
        return "statistical"
    if any(c in text for c in ("comparison", "compared", "trade", "versus", "option", "altern")):
        return "comparison"
    if any(c in text for c in ("outlook", "forecast", "trend", "projection", "future", "recent")):
        return "news"
    if any(c in text for c in ("definition", "background", "overview", "history")):
        return "encyclopedia"
    return default


def is_valid_question(q: str) -> bool:
    return len((q or "").split()) >= 3


def _clean_str_list(values: Any, limit: int = 5) -> List[str]:
    """String-list parser guard: keeps short non-empty strings, drops junk."""
    if not isinstance(values, list):
        return []
    cleaned: List[str] = []
    for v in values:
        text = str(v or "").strip()
        if text and len(text) <= 120 and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def diversity_coverage(sub_questions: List[Dict[str, Any]]) -> set:
    """Distinct valid search_types in a plan — its diversity contract."""
    return {
        q.get("search_type") for q in sub_questions
        if isinstance(q, dict) and q.get("search_type") in VALID_SEARCH_TYPES
    }


_QUESTION_STOPWORDS = {
    "what", "which", "how", "why", "when", "where", "who", "is", "are",
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "do",
    "does", "did", "with", "about", "definition", "overview",
    "introduction", "explanation", "explain", "examples", "example",
}


def _question_signature(question: str) -> Set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", (question or "").lower())
        if token not in _QUESTION_STOPWORDS
    }


def _question_similarity(a: str, b: str) -> float:
    sa, sb = _question_signature(a), _question_signature(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def deduplicate_semantic(
    items: List[Dict[str, Any]], threshold: float = 0.75
) -> List[Dict[str, Any]]:
    """Drop sub-questions that would search for the same thing.

    Uses content-token overlap (coefficient, not Jaccard, so a short question
    subsumed by a longer one is caught) instead of exact string equality after
    deleting three words. Two questions on the same axis are held to a stricter
    bar than two on different axes: "cost of X" as evidence and "cost of X" as
    criticism are genuinely different research jobs.
    """
    result: List[Dict[str, Any]] = []
    for item in items:
        question = str(item.get("question", ""))
        axis = str(item.get("axis", ""))
        duplicate = False
        for kept in result:
            same_axis = str(kept.get("axis", "")) == axis
            bar = threshold if same_axis else threshold + 0.15
            if _question_similarity(question, str(kept.get("question", ""))) >= bar:
                duplicate = True
                break
        if not duplicate:
            result.append(item)
    return result
