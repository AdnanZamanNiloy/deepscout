from __future__ import annotations

import re

from app.agents.sources import extract_domain as _host


def _normalize_text(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def _semantic_overlap(a: str, b: str) -> float:
    a_words = set(_normalize_text(a).split())
    b_words = set(_normalize_text(b).split())
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / len(a_words | b_words)


def _is_semantic_duplicate(a: str, b: str, threshold: float = 0.6) -> bool:
    return _semantic_overlap(a, b) >= threshold


def _domain(url: str) -> str:
    return _host(url)
