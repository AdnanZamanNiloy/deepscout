from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Set

from app.agents.evidence.text import (
    _tokenize,
)


DATE_STAMP_RE = re.compile(
    r"^(?:[A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}|\d+\s+(?:day|hour|minute|second)s?\s+ago)"
    r"(?:\s*[·\-–|]\s*|\s+\d+\s+min(?:ute)?s?\s+read\b\s*|\s+)",
    re.IGNORECASE,
)


LEADING_HASH_RE = re.compile(r"^#+\s*")


LINK_TEXT_RE = re.compile(
    r"\b(?:Learn|Read|Show|See|Click|Continue)\s+mo(?:r(?:e)?)?\b\.?", re.IGNORECASE
)


WIKI_NAV_RE = re.compile(
    r"^(Main article|See also|Further information|References|External links|Notes)\s*:",
    re.IGNORECASE,
)


EXCERPT_SEAM_RE = re.compile(r"\[\s*\.\.\.|\(\s*\.\.\.")


LINK_TITLE_PAREN_RE = re.compile(r'\s*"[^"]{2,80}"\)')


HEADING_MARK_RE = re.compile(r"#{2,}\s*")


BLOCKQUOTE_RE = re.compile(r"^(?:>\s*)+")


LATEX_SOUP_RE = re.compile(r"\\[a-zA-Z]{3,}")


BOILERPLATE_LEAD_RE = re.compile(
    r"^(Use this page to\b|This (article|post|guide|page|blog) will\b)", re.IGNORECASE
)


DANGLING_END_RE = re.compile(
    r"\b(including|such\s+as|as\s+well\s+as|with|from|through|using|by|and|or|"
    r"to|of|in|on|for|as|like|via|per|within|without|between|among)\s*\.?\s*$",
    re.IGNORECASE,
)


TITLE_PREFIX_RE = re.compile(r"^([^.!?]{2,80}):\s*[^.!?]{2,80}\.?$")


WIKI_EDIT_MARK_RE = re.compile(r"\[\s*edit(?:\s+source)?\s*\]", re.IGNORECASE)


CONSENT_NOISE_RE = re.compile(
    r"\b(accept all cookies|manage (your )?(cookie|consent) preferences|"
    r"subscribe to (continue|read)|sign in to (continue|read)|"
    r"you have \d+ free articles?|enable cookies|privacy policy and terms)\b",
    re.IGNORECASE,
)


NAV_RUN_RE = re.compile(
    r"^(?:(?:Home|About|Contact|Careers|Privacy|Terms|Login|Sign\s?in|Menu|"
    r"Search|Newsletter|Subscribe|Share|Follow|Advertisement)\b[\s|·,-]*){3,}$",
    re.IGNORECASE,
)


MIN_CLEAN_CLAIM_CHARS = 50


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _looks_like_title_prefix(head: str) -> bool:
    words = re.findall(r"[A-Za-z][a-z]*", head or "")
    if len(words) < 2:
        return False
    capped = sum(1 for w in words if w[0].isupper())
    return capped / len(words) > 0.5


def split_into_sentences(text: str, max_sentences: int = 12) -> List[str]:
    """Split page/snippet text into candidate claim sentences."""
    chunks = [chunk.strip() for chunk in _SENTENCE_SPLIT_RE.split(text or "")]
    return [chunk for chunk in chunks if chunk][:max_sentences]


def looks_truncated(text: str) -> bool:
    """True when a claim ends in a probable mid-word cut ("...a large dat")."""
    stripped = re.sub(r"\s+", " ", (text or "")).strip()
    if not stripped:
        return True
    if re.search(r"[.!?](?=\s|$)", stripped):
        return False
    return len(stripped.rsplit(None, 1)[-1]) <= 3


def clean_snippet_text(
    snippet: str, max_chars: int = 300, min_chars: int = MIN_CLEAN_CLAIM_CHARS
) -> str:
    """Turn a raw snippet or model claim into a presentable claim sentence.

    Returns "" when nothing salvageable remains. Every rejection rule here
    corresponds to a specific garbage shape seen in production; two are new in
    this version (consent/paywall furniture, flattened nav runs).
    """
    text = re.sub(r"\s+", " ", (snippet or "")).strip()
    text = html.unescape(text).strip()
    text = WIKI_EDIT_MARK_RE.sub(" ", text).strip()
    text = DATE_STAMP_RE.sub("", text).strip()
    text = LEADING_HASH_RE.sub("", text).strip()
    text = BLOCKQUOTE_RE.sub("", text).strip()
    text = LINK_TEXT_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if WIKI_NAV_RE.match(text):
        return ""
    if EXCERPT_SEAM_RE.search(text):
        return ""
    if LINK_TITLE_PAREN_RE.search(text):
        return ""
    if LATEX_SOUP_RE.search(text):
        return ""
    if BOILERPLATE_LEAD_RE.match(text):
        return ""
    if CONSENT_NOISE_RE.search(text):
        return ""
    if NAV_RUN_RE.match(text):
        return ""
    if TITLE_PREFIX_RE.match(text):
        head = text.split(":", 1)[0]
        if _looks_like_title_prefix(head):
            return ""
    if text.endswith("?"):
        return ""
    text = HEADING_MARK_RE.sub("", text).strip()
    if len(text) < min_chars:
        return ""

    working = text[:max_chars]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", working)]
    if ends:
        text = working[: ends[-1]].strip()
    else:
        alt = [m.end() for m in re.finditer(r"[,;:](?=\s)", working)]
        if alt:
            text = (working[: alt[-1]].rstrip(",;:") + ".").strip()
        elif len(working.rsplit(None, 1)[-1]) <= 3:
            return ""
    text = text.strip()
    if len(text) < min_chars:
        return ""
    if text.count("(") != text.count(")"):
        return ""
    if DANGLING_END_RE.search(text):
        return ""
    return text


def claim_query_overlap(query: str, claim: str) -> float:
    """Word overlap between the research query and a claim (0-1)."""
    q_words = _tokenize(query or "")
    c_words = _tokenize(claim or "")
    if not q_words:
        return 0.0
    hits = 0
    for qw in q_words:
        if qw in c_words:
            hits += 1
            continue
        if len(qw) >= 5 and any(
            len(cw) >= 5 and (cw.startswith(qw) or qw.startswith(cw)) for cw in c_words
        ):
            hits += 1
    return hits / len(q_words)


MIN_QUERY_OVERLAP = 0.15


def select_diverse(
    claims: List[Dict[str, Any]], k: int = 3, max_similarity: float = 0.40
) -> List[Dict[str, Any]]:
    """Greedy maximal-marginal-relevance pick over the overlap coefficient."""
    ranked = sorted(
        claims or [], key=lambda f: float(f.get("confidence", 0.0) or 0.0), reverse=True
    )
    selected: List[Dict[str, Any]] = []
    selected_tokens: List[Set[str]] = []
    for candidate in ranked:
        text = str(candidate.get("claim", "") or "")
        if not text:
            continue
        candidate_tokens = _tokenize(text)
        if not candidate_tokens:
            continue
        novel = True
        for kept_tokens in selected_tokens:
            if not kept_tokens:
                continue
            overlap = len(candidate_tokens & kept_tokens) / min(
                len(candidate_tokens), len(kept_tokens)
            )
            if overlap >= max_similarity:
                novel = False
                break
        if novel:
            selected.append(candidate)
            selected_tokens.append(candidate_tokens)
        if len(selected) >= k:
            break
    return selected
