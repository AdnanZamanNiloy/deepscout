from __future__ import annotations

import re
from typing import Set

from app.core.semantic import pair_similarity


def normalize_claim_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.strip("-:;,. ")
    if not cleaned:
        return ""
    if len(cleaned) > 260:
        cleaned = cleaned[:257].rstrip() + "..."
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _tokenize(text: str) -> Set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _semantic_similarity(a: str, b: str) -> float:
    """Hybrid char-ratio + token-Jaccard + TF-IDF similarity in [0, 1].

    Delegates to the shared semantic engine (app/core/semantic.py): the
    TF-IDF term catches paraphrase-level overlap the pure char-diff missed,
    while cheap gates keep the fast path fast. Blend weights were calibrated
    so the 0.86 dedup threshold and the 0.50-0.86 contradiction band keep
    their historical meaning for near-duplicates.
    """
    return pair_similarity(a, b)


def semantic_similarity(a: str, b: str) -> float:
    """Public alias — the contradiction and confidence engines need this."""
    return _semantic_similarity(a, b)
