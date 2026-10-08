from __future__ import annotations

import re
from typing import List, Set

from app.core.synthesis_intel.constants import (
    _CITATION_RE,
    _DIMENSIONS,
    _GENERIC_ANCHORS,
    _MIN_CONTENT_TOKENS,
    _NEGATION_RE,
    _STOPWORDS,
    _SUFFIXES,
)


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def canonical_tokens(text: str, *, keep_negation: bool = True) -> List[str]:
    """Content tokens of `text`: citations stripped, stopwords removed, stemmed.

    Negation words are preserved by default (AGENTS.md: negation is never a
    stopword in a similarity/verification component) so "X" and "not X" keep
    different token sets.
    """
    body = _CITATION_RE.sub(" ", text or "")
    tokens: List[str] = []
    for raw in re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", body.lower()):
        for part in re.split(r"[-']", raw):
            part = part.strip()
            if not part or part in _STOPWORDS:
                continue
            tokens.append(_stem(part))
    if not keep_negation:
        tokens = [t for t in tokens if not _NEGATION_RE.fullmatch(t)]
    return tokens


def claim_key(text: str) -> str:
    """A canonical, order-insensitive key for a claim sentence.

    Two sentences that assert the same fact with different word order or a
    light inflection share a key; a sentence with an extra negation does not
    (negation tokens survive). Returns "" for units too short to be claims.
    """
    tokens = canonical_tokens(text)
    if len(tokens) < _MIN_CONTENT_TOKENS:
        return ""
    return " ".join(sorted(tokens))


def claim_polarity(text: str) -> int:
    """-1 when the sentence is negated, +1 otherwise.

    Part of the polarity guard: identical tokens with opposite polarity are
    different claims, and this layer must never remove one as a "repeat" of
    the other.
    """
    return -1 if _NEGATION_RE.search(text or "") else 1


def analytical_dimensions(text: str) -> Set[str]:
    """Which analytical dimensions a sentence adds: mechanism / implication /
    comparison / uncertainty. Empty means it is a bare fact statement."""
    found: Set[str] = set()
    for name, pattern in _DIMENSIONS:
        if pattern.search(text or ""):
            found.add(name)
    return found


def anchors(text: str) -> Set[str]:
    """Distinctive content tokens: the recurring subject names and figures.

    Generic report vocabulary ("cost", "energy", "project" — frequent in any
    query's evidence) is excluded, so two sentences only share anchors when
    they name the same specific entity or quantity.
    """
    return {
        token
        for token in canonical_tokens(text)
        if token not in _GENERIC_ANCHORS and (len(token) >= 5 or token.isdigit())
    }
