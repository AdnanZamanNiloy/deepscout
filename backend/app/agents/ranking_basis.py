"""A ranking needs a comparative basis. Without one, do not name a winner.

THE FAILURE THIS PREVENTS
-------------------------
The evidence holds high-strain findings about seafaring AND about obstetrics.
Each is well sourced. Nothing compares them. The report says they are the
"strongest candidates" — a superlative manufactured by inference from two
qualitative signals.

That is the failure: a superlative asserts a COMPARISON, and two unrelated
findings are not a comparison. "Both are examples of highly demanding work" is
what the evidence supports; "these are the strongest candidates" is not.

The rule, applied to every "most / best / highest / worst / largest" question:

  RANKED      only when comparable evidence supports ordering candidates —
              an explicit comparison, or the same metric measured across them.
  SHORTLIST   when the evidence supports candidates as examples but does not
              order them.
  UNDETERMINED when neither is justified.

Anecdotal or occupation-specific evidence may be presented as EXAMPLES. It may
never be promoted into a ranking.

DOMAIN AGNOSTICISM
------------------
Everything here reads the query's shape and the claims' own wording. No list of
candidates, metrics or subjects appears anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple

RANKED = "ranked"
SHORTLIST = "shortlist"
UNDETERMINED = "undetermined"

# Words that ask for an ordering. A query containing one of these is a
# superlative question: its answer is a comparison whether or not the user says
# so, so the comparative-basis rule applies.
_SUPERLATIVE_RE = re.compile(
    r"\b(most|best|highest|worst|largest|biggest|smallest|least|greatest|"
    r"top|leading|strongest|weakest|fastest|slowest|cheapest|most\s+\w+|"
    r"number\s+one|#1)\b",
    re.IGNORECASE,
)

# Explicit comparison between candidates in a claim. Structural, not topical.
_COMPARATIVE_RE = re.compile(
    r"\b(more|less|higher|lower|greater|fewer|better|worse|stronger|weaker|"
    r"faster|slower|cheaper|dearer)\b[^.]{0,40}?\bthan\b",
    re.IGNORECASE,
)
_SUPERLATIVE_CLAIM_RE = re.compile(
    r"\b(highest|lowest|greatest|largest|smallest|most|least|best|worst|"
    r"ranked\s+(?:first|highest|lowest)|outrank)",
    re.IGNORECASE,
)

# Phrases that assert a winner. The output the rule forbids on a shortlist.
_WINNER_PHRASES: Tuple[str, ...] = (
    "strongest candidate",
    "strongest candidates",
    "likely winner",
    "the winner",
    "leading candidate",
    "best candidate",
    "top candidate",
    "clearest contender",
    "takes the top spot",
    "ranks first",
    "ranks highest",
    "comes out on top",
    "the number one",
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'&.-]*", re.UNICODE)
# A capitalised word mid-sentence: how a comparison study names its subjects.
# Matched per-claim, so a name at the start of a sentence also counts.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{3,}\b")
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


def is_superlative_query(query: str) -> bool:
    """Does this question ask for an ordering?"""
    return bool(_SUPERLATIVE_RE.search(str(query or "")))


def _subject_of(query: str) -> str:
    """The noun the superlative applies to, lowercased.

    The word right after the superlative: "most DEMANDING job" -> the head noun
    is "job". Used only as a light candidate filter, never as a topic list.
    """
    match = _SUPERLATIVE_RE.search(str(query or ""))
    if not match:
        return ""
    tail = str(query or "")[match.end():].strip()
    tail_tokens = [t for t in _TOKEN_RE.findall(tail.lower()) if t not in _STOPWORDS]
    return tail_tokens[0] if tail_tokens else ""


@dataclass
class ComparativeBasis:
    """What the evidence can and cannot support for an ordering question."""

    verdict: str = UNDETERMINED
    has_comparison: bool = False
    comparative_claims: List[str] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)
    shared_metric: str = ""
    reason: str = ""

    @property
    def may_rank(self) -> bool:
        return self.verdict == RANKED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "has_comparison": self.has_comparison,
            "comparative_claims": list(self.comparative_claims[:5]),
            "candidates": list(self.candidates[:8]),
            "shared_metric": self.shared_metric,
            "reason": self.reason,
        }


def assess_comparative_basis(
    query: str,
    facts: Sequence[Mapping[str, Any]] | None,
    *,
    candidates: Sequence[str] | None = None,
) -> ComparativeBasis:
    """Can the evidence order candidates, or only name examples?

    Three routes to a comparative basis, in order of strength:

    1. An explicit comparison in a claim ("X has a higher rate than Y").
    2. The SAME metric measured across two or more candidates — the shape a
       real comparison study has ("X: 12 per 100k. Y: 34 per 100k.").
    3. Nothing: whichever names appear are separate examples, and the verdict is
       SHORTLIST (or UNDETERMINED with no candidates at all).

    `candidates` may be supplied when the caller already knows the named options
    (an explicit "A vs B" question). Otherwise candidates are read from the
    claims' own wording — no list is built anywhere.

    Total: any input yields a verdict, never raises.
    """
    if not is_superlative_query(query) and not candidates:
        return ComparativeBasis(
            verdict=UNDETERMINED,
            reason="the question does not ask for an ordering",
        )

    pool = [f for f in (facts or ()) if isinstance(f, Mapping)]
    if not pool:
        return ComparativeBasis(
            verdict=UNDETERMINED,
            reason="no evidence to compare",
        )

    comparative_claims: List[str] = []
    for fact in pool:
        claim = str(fact.get("claim", "") or "")
        if _COMPARATIVE_RE.search(claim) or _SUPERLATIVE_CLAIM_RE.search(claim):
            comparative_claims.append(claim.strip())

    # A shared metric across candidates: the same number/unit phrase attached to
    # two or more of the named options. Read from the claim text only.
    shared_metric = ""
    named = [str(c).strip() for c in (candidates or ()) if str(c).strip()]
    if not named:
        named = _named_candidates(pool, query)
    if len(named) >= 2:
        shared_metric = _shared_metric_across(pool, named)

    has_comparison = bool(comparative_claims) or bool(shared_metric)

    if has_comparison and len(named) >= 2:
        return ComparativeBasis(
            verdict=RANKED,
            has_comparison=True,
            comparative_claims=comparative_claims,
            candidates=named,
            shared_metric=shared_metric,
            reason="the evidence compares the candidates on a common basis",
        )
    if has_comparison and not named:
        # A comparison exists but no two candidates were identified to order.
        return ComparativeBasis(
            verdict=SHORTLIST,
            has_comparison=True,
            comparative_claims=comparative_claims,
            reason="a comparison is present but the candidates are not named",
        )
    if named:
        return ComparativeBasis(
            verdict=SHORTLIST,
            candidates=named,
            reason=(
                "the evidence gives examples but does not compare them; a "
                "ranking cannot be supported"
            ),
        )
    return ComparativeBasis(
        verdict=UNDETERMINED,
        reason="the evidence does not identify candidates or compare them",
    )


def _named_candidates(pool: Sequence[Mapping[str, Any]], query: str) -> List[str]:
    """Candidate names appearing in the claim text.

    Read from the claims themselves — deliberately NOT from a list. A candidate
    is a capitalised subject recurring across claims: comparison studies name
    their subjects ("Seafaring ...", "Obstetrics ..."), and such a name appears
    more than once across the pool. That is a structural signal, and it avoids
    the earlier version's failure of returning ordinary nouns ("high", "rates")
    that appear in any claim.

    A single mention is not enough: one passing reference is an anecdote, and an
    anecdote is exactly what must not become a ranking. Recurrence across the
    pool is the bar, because a candidate is by definition something the evidence
    discusses more than once. A caller that already knows the named options (an
    explicit "A vs B" question) passes them in instead and skips this inference.
    """
    subject = _subject_of(query)
    claim_count: Dict[str, int] = {}
    display: Dict[str, str] = {}
    for fact in pool:
        claim = str(fact.get("claim", "") or "")
        seen_here: set = set()
        for match in _PROPER_NOUN_RE.finditer(claim):
            key = match.group(0).lower()
            if key == subject or len(key) < 4 or key in seen_here:
                continue
            seen_here.add(key)
            display.setdefault(key, match.group(0))
        for key in seen_here:
            claim_count[key] = claim_count.get(key, 0) + 1

    recurring = [(key, n) for key, n in claim_count.items() if n >= 2]
    recurring.sort(key=lambda pair: (-pair[1], pair[0]))
    return [display[key] for key, _ in recurring[:6]]


_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%?\b")


def _shared_metric_across(pool: Sequence[Mapping[str, Any]], named: Sequence[str]) -> str:
    """A quantitative measure appearing alongside two or more named candidates.

    That is the shape of a genuine cross-candidate comparison: the same kind of
    quantity measured for each candidate, with DIFFERENT values. Identical
    figures in two claims are a coincidence, not a comparison, so they do not
    count.
    """
    per_candidate: Dict[str, set] = {}
    for candidate in named:
        values: set = set()
        for fact in pool:
            claim = str(fact.get("claim", "") or "").lower()
            if candidate.lower() not in claim:
                continue
            values |= {m.group(0).strip() for m in _NUMBER_RE.finditer(claim)}
        if values:
            per_candidate[candidate] = values

    if len(per_candidate) < 2:
        return ""
    distinct_values = {tuple(sorted(v)) for v in per_candidate.values()}
    if len(distinct_values) < 2:
        # Every candidate cited the same figure: not a comparison.
        return ""
    return "shared quantitative measure across candidates"


# ---------------------------------------------------------------------------
# The writer contract
# ---------------------------------------------------------------------------


def render_ranking_contract(basis: ComparativeBasis, query: str) -> str:
    """What the writer is allowed to produce, given the comparative basis."""
    if basis.verdict == RANKED:
        return (
            "RANKING PERMITTED — the evidence compares the candidates on a "
            "common basis, so a ranked answer is supported. State the basis "
            "(the metric or comparison) the ranking rests on, and note any "
            "candidate the evidence does not cover."
        )
    if basis.verdict == SHORTLIST:
        return (
            "SHORTLIST ONLY — the evidence supports these as EXAMPLES, not as an "
            "ordering. Do NOT name a 'strongest candidate', 'likely winner', "
            "'top' or 'leading' option, and do not rank them.\n"
            "  State plainly that no study compares them, then present each as an "
            "example of the question's property (e.g. 'both are examples of ...').\n"
            "  Anecdotal or occupation-specific evidence may be given as an "
            "example; it must never be promoted into a ranking. Do not convert a "
            "qualitative signal into a superlative by inference."
        )
    return (
        "CANNOT DETERMINE — the evidence neither ranks the candidates nor "
        "identifies a supported set. Say so directly: the available evidence "
        "does not determine the answer. Name what would settle it. Do not name a "
        "strongest candidate or manufacture a shortlist from unrelated findings."
    )


def winner_claims(answer: str) -> List[str]:
    """Winner-asserting phrases present in the text. For measurement/tests."""
    low = str(answer or "").lower()
    return [phrase for phrase in _WINNER_PHRASES if phrase in low]


def ranking_violation(
    answer: str, basis: ComparativeBasis, *, window: int = 1600
) -> List[str]:
    """Winner language used where no comparative basis supports it.

    Reads the opening, where the headline claim lives. Returns the offending
    phrases, empty when the answer respects the basis.
    """
    if basis.may_rank:
        return []
    if basis.verdict == UNDETERMINED and not basis.candidates:
        # Nothing was even shortlisted; winner language is still unsupported.
        pass
    return winner_claims(str(answer or "")[:window])


def assess_answer_ranking(
    answer: str,
    query: str,
    facts: Sequence[Mapping[str, Any]] | None,
) -> Dict[str, Any]:
    """Full check for the audit and tests. Total; never raises."""
    try:
        basis = assess_comparative_basis(query, facts)
    except Exception:
        basis = ComparativeBasis(verdict=UNDETERMINED, reason="assessment failed")
    return {
        "query_is_superlative": is_superlative_query(query),
        "basis": basis.to_dict(),
        "winner_claims": winner_claims(answer),
        "violation": ranking_violation(answer, basis),
    }
