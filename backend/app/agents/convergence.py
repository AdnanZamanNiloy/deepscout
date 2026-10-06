"""A persistent fundamental gap converges the loop. And no manufactured winners.

TWO FAILURES THIS PREVENTS
--------------------------
1. REOPENING THE LOOP FOR AN UNSOURCED ANGLE. A "most demanding job" run learns
   in round 1 that no source ranks occupations by strain, searches again, learns
   the same thing in round 2, and searches again — because a planned angle is
   still unsourced. The angle is unsourced because the EVIDENCE DOES NOT EXIST,
   not because the search was wrong. Once that is established, more searching
   cannot change the answer, and the loop must converge.

2. MANUFACTURING A WINNER. Asked for a #1, the run has no ranking evidence, so it
   reaches for an analogy, a proxy or an unrelated finding and presents one as
   the answer. A proxy may explain context; it may never substitute for the
   requested ranking. If the evidence supports no defensible #1, the answer says
   exactly that.

WHAT A FUNDAMENTAL GAP IS
-------------------------
Not "an angle is unsourced" — that is a coverage gap and searching may fix it.
A fundamental gap is a conclusion about the QUESTION: the kind of evidence the
question requires (a ranking, a comparison, a specific measure) is absent from
the reachable sources, repeatedly, across rounds. It is identified by the
review's own repeated conclusion, not by a dimension count.

DOMAIN AGNOSTICISM
------------------
Nothing here names a subject, a metric or a candidate. It reads the query's
SHAPE (does it ask for a #1?) and the review's own wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

# Converge after this many consecutive reviews reporting the same fundamental
# gap. Three: two independent rounds agreeing is strong, and the third confirms
# it is stable rather than a slow start. The user's requirement is "2-3 rounds".
FUNDAMENTAL_GAP_ROUNDS = 2

# Requests for a single winner. A query containing one of these demands a
# RANKING, which is the thing a proxy must never substitute for.
_SINGLE_WINNER_RE = re.compile(
    r"\b(most|best|highest|worst|largest|biggest|smallest|least|greatest|"
    r"top|leading|number\s+one|#1|single\s+best|the\s+one)\b",
    re.IGNORECASE,
)

# The reviewer's own vocabulary for "the evidence needed to answer this does not
# exist". Structural phrases about the EVIDENCE, never a subject.
_FUNDAMENTAL_MARKERS: Tuple[str, ...] = (
    "no source provides",
    "no source ranks",
    "no source compares",
    "no published",
    "no dataset",
    "no study",
    "does not rank",
    "do not rank",
    "does not compare",
    "do not compare",
    "no occupation is ranked",
    "no candidate is ranked",
    "cannot rank",
    "cannot be ranked",
    "no direct evidence",
    "indirect evidence",
    "only indirect",
    "contextual evidence",
    "off-target",
    "off target",
    "not comparable",
    "no common measure",
    "no shared metric",
    "cannot establish",
    "no defensible",
    "unable to rank",
)

# Markers that the gap is COVERAGE, not fundamental: material is missing but the
# kind of evidence is the right kind, so searching may still fix it.
_COVERAGE_MARKERS: Tuple[str, ...] = (
    "uncovered angle",
    "uncovered_angles",
    "thin dimension",
    "under-sourced",
    "single source",
    "needs corroboration",
    "one publisher",
    "more sources",
)


@dataclass
class FundamentalGap:
    """Whether the loop should converge because the evidence cannot answer."""

    identified: bool = False
    rounds: int = 0
    reason: str = ""
    signature: str = ""
    defensible_number_one: bool = False
    missing_evidence: str = ""

    @property
    def should_converge(self) -> bool:
        return self.identified

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identified": self.identified,
            "rounds": self.rounds,
            "reason": self.reason,
            "signature": self.signature,
            "defensible_number_one": self.defensible_number_one,
            "missing_evidence": self.missing_evidence,
        }


def is_single_winner_query(query: str) -> bool:
    """Does this question ask for one winner, i.e. demand a ranking?"""
    return bool(_SINGLE_WINNER_RE.search(str(query or "")))


def fundamental_gap_language(text: str) -> bool:
    """Does this text report that the required KIND of evidence is absent?

    Coverage language is excluded: "an uncovered angle" is material that might
    still be found, whereas "no source ranks these" is a property of the
    question. Conflating them is what made the loop reopen forever.
    """
    low = str(text or "").lower()
    if not any(marker in low for marker in _FUNDAMENTAL_MARKERS):
        return False
    # If the text is ONLY about coverage, it is not fundamental.
    fundamental_hits = sum(1 for m in _FUNDAMENTAL_MARKERS if m in low)
    coverage_hits = sum(1 for m in _COVERAGE_MARKERS if m in low)
    return fundamental_hits > 0 and (coverage_hits == 0 or fundamental_hits >= 1)


def _gap_signature(text: str) -> str:
    """A stable signature for a fundamental gap, so repeats are countable.

    Built from the MATCHED markers rather than the whole sentence, so two rounds
    phrased differently but reporting the same missing kind of evidence collapse
    to one signature.
    """
    low = str(text or "").lower()
    hits = sorted({m for m in _FUNDAMENTAL_MARKERS if m in low})
    # A coarse signature: the first marker is enough to identify the KIND.
    return hits[0] if hits else ""


def assess_fundamental_gap(
    query: str,
    reviews: Sequence[str],
    *,
    history: Sequence[str] = (),
    required_rounds: int = FUNDAMENTAL_GAP_ROUNDS,
) -> FundamentalGap:
    """Has the review concluded, repeatedly, that the evidence cannot answer?

    `reviews` are the review reasons for the current pass (the critic's reason
    and gaps). `history` is the run's record of prior fundamental-gap signatures,
    so repetition is measured across rounds from the run's own state — no
    module-level counter (AGENTS.md 4.3).

    Returns `identified` only when the SAME kind of gap has been reported for
    `required_rounds` consecutive rounds. A single round is a slow start; a
    repeat is a property of the question.
    """
    combined = " ".join(str(r or "") for r in reviews if str(r or "").strip())
    signature = _gap_signature(combined)
    if not signature:
        return FundamentalGap()

    prior = [str(h or "") for h in history if str(h or "").strip()]
    consecutive = 1
    for previous in reversed(prior):
        if previous == signature:
            consecutive += 1
        else:
            break

    return FundamentalGap(
        identified=consecutive >= required_rounds,
        rounds=consecutive,
        signature=signature,
        reason=(
            f"the reviewer reported the same fundamental evidence gap for "
            f"{consecutive} consecutive round(s): {signature}"
        ),
        # A #1 question cannot be answered by this evidence if no ranking exists.
        defensible_number_one=not is_single_winner_query(query) or False,
        missing_evidence=_missing_evidence_note(signature),
    )


def _missing_evidence_note(signature: str) -> str:
    """A plain statement of what kind of evidence is absent."""
    if not signature:
        return ""
    return (
        "the evidence needed to answer this is a source that ranks or compares "
        f"the candidates ({signature}); none was found"
    )


# ---------------------------------------------------------------------------
# The writer contract
# ---------------------------------------------------------------------------


def render_convergence_contract(
    query: str,
    gap: FundamentalGap,
    *,
    cluster: Sequence[str] = (),
) -> str:
    """What the writer must produce once the loop has converged on a gap.

    For a #1 question with no ranking evidence the shape is fixed: say no single
    winner can be established, give the strongest supported CLUSTER if there is
    one, name the missing evidence, and drop irrelevant material. Never
    manufacture a winner.
    """
    if not gap.identified:
        return ""
    parts: List[str] = []
    if is_single_winner_query(query):
        parts.append(
            "NO DEFENSIBLE #1 — the evidence does not establish a single best "
            "option, and the research concluded so repeatedly. State that "
            "plainly and do NOT name a winner.",
        )
        parts.append(
            "Do NOT manufacture a winner from an analogy, a proxy measure, an "
            "unrelated finding or creative inference. A proxy may explain "
            "context; it may never substitute for the requested ranking.",
        )
        if cluster:
            listed = "; ".join(str(c).strip() for c in cluster[:5] if str(c).strip())
            parts.append(
                f"A supported CLUSTER or consensus may be given instead: {listed}. "
                "Present it as a group of candidates with comparable supporting "
                "evidence, explicitly NOT as a ranking."
            )
        else:
            parts.append(
                "If no supported cluster exists either, say the evidence supports "
                "no answer to this question rather than inventing one."
            )
        parts.append(
            f"Name the specific evidence that is missing: {gap.missing_evidence}. "
            "Say what would settle the ranking."
        )
    else:
        parts.append(
            "CONVERGED ON A FUNDAMENTAL GAP — the evidence cannot answer the "
            "question as asked. State that directly rather than forcing the "
            "question into an answerable shape."
        )
    parts.append(
        "REMOVE IRRELEVANT EVIDENCE — material that is indirect, contextual or "
        "off-target must not appear in the answer merely because it was "
        "retrieved. Keep what bears on the question and drop the rest."
    )
    return "\n".join(parts)


def _winner_phrases() -> Tuple[str, ...]:
    return (
        "strongest candidate",
        "likely winner",
        "the clear winner",
        "takes the top spot",
        "ranks first",
        "comes out on top",
        "is the single best",
        "the number one",
        "no other option comes close",
    )


# A superlative used as a PREDICATE — "X is the most demanding job". Structural:
# the superlative word followed by a noun and a linking verb, or the superlative
# in an "is the ..." position. Built from the query's own superlative word, so
# nothing is hardcoded: the pattern is generic and the vocabulary comes from the
# question.
def _asserted_superlative(text: str) -> List[str]:
    """Superlative assertions in the text, read structurally.

    An answer names a winner in one of two ways, and both put the linking verb
    adjacent to the superlative:
      * "<superlative> ... is"  — "the most demanding job is X"
      * "is the <superlative>"  — "X is the most demanding job"
    So the check reads a window on BOTH sides of the superlative. A superlative
    used descriptively ("the most demanding roles often involve...") has no
    linking verb beside it and is not returned — description is not a claim.
    """
    low = str(text or "").lower()
    found: List[str] = []
    for match in _SINGLE_WINNER_RE.finditer(low):
        before = low[max(0, match.start() - 40): match.start()]
        after = low[match.start(): match.start() + 90]
        if re.search(r"\b(is|are|was|were|remains|stands)\b", before + " " + after):
            found.append(after.split(".", 1)[0].strip())
    return found


def manufactured_winner(
    answer: str, query: str, gap: FundamentalGap, *, window: int = 1600
) -> List[str]:
    """Winner language used where a #1 was concluded indefensible.

    Two shapes count: a fixed winner idiom ("strongest candidate"), and a
    superlative ASSERTED as fact ("the most demanding job is X"). The second is
    built from the query's own superlative, so it holds for any subject — the
    earlier version hardcoded one example and the domain-agnosticism test caught
    it.

    Only meaningful once the gap is identified; before that the ranking gate
    (app/agents/ranking_basis.py) governs.
    """
    if not (gap.identified and is_single_winner_query(query)):
        return []
    opening = str(answer or "")[:window]
    low = opening.lower()
    hits = [phrase for phrase in _winner_phrases() if phrase in low]
    hits.extend(_asserted_superlative(opening))
    return hits


def assess_convergence(
    answer: str,
    query: str,
    reviews: Sequence[str],
    *,
    history: Sequence[str] = (),
    cluster: Sequence[str] = (),
) -> Dict[str, Any]:
    """Full check for the audit and tests. Total; never raises."""
    try:
        gap = assess_fundamental_gap(query, reviews, history=history)
    except Exception:
        gap = FundamentalGap()
    return {
        "single_winner_question": is_single_winner_query(query),
        "gap": gap.to_dict(),
        "manufactured_winner": manufactured_winner(answer, query, gap),
    }
