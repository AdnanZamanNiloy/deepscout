"""The final answer must preserve the definition research was locked to.

THE FAILURE THIS PREVENTS
-------------------------
The pipeline locks a meaning before research (app/agents/ambiguity.py). The final
answer is then written from whatever evidence came back — and evidence reaches
for the nearest measurable proxy. Asked for the "most demanding job" with
"demanding" locked to overall difficulty/strain, the run finds O*NET Job Zone —
which measures preparation requirements — and the report quietly becomes a
ranking by preparation. The proxy has replaced the requested concept, and the
reader is told the answer to a question nobody asked.

Research evidence may NARROW or REJECT a hypothesis. It must not REDEFINE the
question. The pipeline is:

    QUERY -> INTERPRETATION LOCK -> RESEARCH -> EVIDENCE CHECK -> ANSWER

and never:

    QUERY -> RESEARCH -> redefine term from available evidence -> ANSWER

WHAT THIS MODULE DOES
---------------------
Two things, both pure and domain-agnostic:

1. A CONTRACT for the writer: keep the locked definition, and when the evidence
   cannot answer the locked question, use the required shape —
     * say the evidence is insufficient for the requested ranking;
     * give the strongest supported PARTIAL answer, labelled as partial;
     * name the metric or data that is MISSING;
     * never promote a proxy metric into the requested concept.

2. A DETECTOR the report can be checked with: does the answer's opening statement
   still define the term as locked, and does it claim a ranking the evidence
   cannot support? Used for measurement and tests; the writer instruction is the
   primary mechanism (same shape as the rest of the pipeline's guidance).

Nothing here names a subject or a metric. "O*NET Job Zone" appears only in these
docstrings as the worked example; every rule is about DEFINITIONS and EVIDENCE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'&.-]*", re.UNICODE)

# Function words carry no meaning. Negation is deliberately NOT here (AGENTS.md:
# negation must never be a stopword in a similarity component).
_STOPWORDS = frozenset({
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "has", "have",
    "how", "in", "into", "is", "it", "its", "may", "might", "must", "of", "on",
    "or", "our", "over", "shall", "should", "so", "some", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "to",
    "under", "up", "was", "we", "were", "what", "when", "where", "which", "who",
    "whom", "whose", "why", "will", "with", "within", "would", "you", "your",
})


def _tokens(text: str) -> frozenset:
    return frozenset(
        w for w in _TOKEN_RE.findall((text or "").lower())
        if len(w) > 1 and w not in _STOPWORDS
    )


# Phrases that PROMOTE a measure into the requested concept. Each says "this
# thing IS the thing asked about", which is the promotion being forbidden. Read
# as a ranking verb applied to the requested noun.
_PROMOTION_PATTERNS: tuple = (
    r"\bmeasured\s+by\b",
    r"\bdefined\s+(?:as|by)\b",
    r"\bproxy\s+for\b",
    r"\bequivalent\s+to\b",
    r"\bstands\s+in\s+for\b",
    r"\bserves\s+as\s+the\b",
    r"\bis\s+the\s+best\s+measure\b",
)

# Phrases that honestly mark an insufficiency. Their presence means the writer
# did the required thing rather than papering over the gap.
_INSUFFICIENCY_MARKERS: tuple = (
    "insufficient",
    "not enough evidence",
    "no direct evidence",
    "cannot be ranked",
    "cannot rank",
    "unable to rank",
    "does not measure",
    "not a measure of",
    "partial",
    "what is missing",
    "data gap",
    "no published",
    "not available",
    "remains unknown",
)


@dataclass
class DefinitionLock:
    """The meaning fixed before research, and what the writer must preserve."""

    term: str = ""
    definition: str = ""
    readings: List[str] = field(default_factory=list)

    @property
    def locked(self) -> bool:
        return bool(self.definition or self.term)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "definition": self.definition,
            "readings": list(self.readings),
            "locked": self.locked,
        }


def _extract_term(query: str, readings: Sequence[str]) -> str:
    """The ambiguous term: the query's content word the readings are readings OF.

    Found by elimination — a content word of the query that is NOT part of any
    reading's vocabulary is the term being disambiguated ("demanding" survives
    while "job"/"2027" appear in the readings). No subject knowledge.
    """
    query_tokens = _tokens(query)
    reading_tokens: set = set()
    for reading in readings:
        reading_tokens |= _tokens(reading)
    candidates = sorted(query_tokens - reading_tokens)
    # The longest survivor is the most likely to be the disputed term.
    return max(candidates, key=len) if candidates else ""


def definition_lock(
    query: str,
    policy: Mapping[str, Any] | None,
) -> DefinitionLock:
    """Build the lock from the ambiguity policy that research was shaped by.

    An empty lock when the query was not ambiguous: with no readings there is no
    disputed term and nothing to preserve. Without this guard the term was
    "extracted" from any query at all (every content word survives when there
    are no readings to account for them), so an unambiguous question reported
    itself as locked.
    """
    policy = policy if isinstance(policy, Mapping) else {}
    readings = [
        str(x).strip()
        for x in (policy.get("interpretations") or ())
        if str(x).strip()
    ]
    definition = str(policy.get("assumption", "") or "").strip()
    if not definition and readings:
        definition = readings[0]
    if not definition or not readings:
        return DefinitionLock()
    return DefinitionLock(
        term=_extract_term(query, readings),
        definition=definition,
        readings=readings,
    )


def render_lock_contract(lock: DefinitionLock) -> str:
    """The writer contract: preserve the definition, and handle insufficiency.

    Deliberately states the PROHIBITION (do not promote a proxy) as well as the
    required shape, because the failure mode is not a missing sentence — it is a
    confident answer to a different question.
    """
    if not lock.locked:
        return ""
    term = lock.term or "the ambiguous term"
    parts = [
        "LOCKED DEFINITION — the meaning of this question was fixed BEFORE "
        "research and must NOT change in this report.",
        f"  '{term}' is taken to mean: **{lock.definition}**",
        "Open by stating that definition. Do not restate it differently, do not "
        "quietly widen it, and do not let the available evidence decide what the "
        "term means.",
        "PROXY RULE — a measure that correlates with the definition is NOT the "
        "definition. If the best available data measures something adjacent "
        "(a requirement, a count, an input) rather than the locked concept, it "
        "may be used only as supporting/partial evidence. Never write that it "
        "'defines', 'measures', 'is a proxy for' or 'is equivalent to' the locked "
        "concept, and never let it become the basis of the requested ranking.",
        "WHEN THE EVIDENCE CANNOT ANSWER THE LOCKED QUESTION, use this shape: "
        "(1) state plainly that the evidence is insufficient to make the "
        "requested ranking; (2) give the strongest PARTIAL answer the evidence "
        "does support, explicitly labelled as partial; (3) name the specific "
        "metric or dataset that is missing and what would settle it. A partial "
        "answer with an honest gap is the correct output. Re-defining the "
        "question to fit the evidence is not.",
    ]
    return "\n".join(parts)


def definition_drifted(
    answer: str,
    lock: DefinitionLock,
    *,
    window: int = 1200,
) -> List[str]:
    """Does the answer's opening redefine the locked term?

    Reads only the first `window` characters, where the definition is stated. A
    report may discuss a proxy later; the defect is DEFINING the term as the
    proxy up front, which then governs the whole answer.

    Returns the matched definitional phrases, empty when the lock is preserved.
    """
    if not lock.locked:
        return []
    opening = str(answer or "")[:window].lower()
    if not opening.strip():
        return []

    hits: List[str] = []
    # The locked definition's own words should still be present near the open.
    locked_tokens = _tokens(lock.definition)
    opening_tokens = _tokens(opening)
    preserved = bool(locked_tokens & opening_tokens) if locked_tokens else True
    if preserved:
        return []

    # Not preserved: is a proxy being promoted, or is the definition simply
    # absent from a long opening (which the contract also forbids)?
    for pattern in _PROMOTION_PATTERNS:
        match = re.search(pattern, opening)
        if match:
            hits.append(match.group(0))
    if not hits:
        hits.append("locked definition not stated in the opening")
    return hits


def proxy_promoted(answer: str, lock: DefinitionLock) -> List[str]:
    """Definitional phrases that promote a measure into the locked concept.

    Checked over the WHOLE answer: the prohibition is on the claim, wherever it
    appears, not only in the opening.
    """
    if not lock.locked:
        return []
    low = str(answer or "").lower()
    return [p for p in _PROMOTION_PATTERNS if re.search(p, low)]


def insufficiency_handled(answer: str) -> bool:
    """Does the answer honestly mark an evidence gap where one exists?"""
    low = str(answer or "").lower()
    return any(marker in low for marker in _INSUFFICIENCY_MARKERS)


def assess_answer_definition(
    answer: str,
    query: str,
    policy: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Full check, for the audit and for tests. Total; never raises."""
    lock = definition_lock(query, policy)
    if not lock.locked:
        return {"locked": False, "drifted": [], "promoted": [], "honest_gap": False}
    return {
        "locked": True,
        "term": lock.term,
        "definition": lock.definition,
        "drifted": definition_drifted(answer, lock),
        "promoted": proxy_promoted(answer, lock),
        "honest_gap": insufficiency_handled(answer),
    }
