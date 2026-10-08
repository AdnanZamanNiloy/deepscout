from __future__ import annotations

import re



_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|"
    "november|december"
)


_NUMERIC_HINT_RE = re.compile(r"\d|%|\bper cent\b|\bpercent\b", re.I)


_DATE_WORD_RE = re.compile(rf"\b(?:{_MONTHS})\b|\b(?:19|20)\d{{2}}\b", re.I)


_QUOTE_RE = re.compile(r"[\"“][^\"”]{8,}[\"”]")


_PROPER_NOUN_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z]{2,}\b")


_COMMON_CAPS = {
    "The", "This", "That", "These", "Those", "There", "Their", "They", "It",
    "However", "Although", "While", "Because", "Where", "When", "What", "Which",
    "Both", "Each", "Most", "Some", "Many", "Several", "One", "Two", "Three",
    "First", "Second", "Third", "Overall", "Taken", "In", "By", "As", "For",
    "But", "And", "Its", "Not", "No", "Yes", "If", "So", "At", "On", "To",
}


_ATTRIBUTION_RE = re.compile(
    r"\b(according to|reported by|announced|stated|published|filed|said)\b", re.I
)


_EVENT_VERB_RE = re.compile(
    r"\b(acquired|merged|launched|opened|closed|filed|approved|rejected|banned|"
    r"ruled|resigned|appointed|raised|cut|fell|rose|grew|declined|shrank|"
    r"agreed|settled|sued|fined|recalled|withdrew|halted|resumed|found|"
    r"reported|awarded|signed|issued)\b",
    re.I,
)


def is_factual_sentence(sentence: str) -> bool:
    """Does this sentence assert something checkable?

    The previous digits-only test let every non-numeric assertion through
    uncited — "Acme acquired Beta", "the regulator opened an investigation",
    "the study found no effect" all have no digits. Named entities, quoted
    text, dates in words and attribution verbs are added, which is where most
    untraceable claims actually live. Analysis and transition prose carries
    none of these.
    """
    text = (sentence or "").strip()
    if not text:
        return False
    if _NUMERIC_HINT_RE.search(text) or _DATE_WORD_RE.search(text):
        return True
    if _QUOTE_RE.search(text) or _ATTRIBUTION_RE.search(text):
        return True
    if _EVENT_VERB_RE.search(text):
        return True
    for match in _PROPER_NOUN_RE.finditer(text):
        if match.group(0) not in _COMMON_CAPS:
            return True
    return False
