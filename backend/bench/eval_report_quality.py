"""Per-type report quality scoring — the 0-10 axis the eval suite was missing.

The existing `eval_answer_quality.py` scores STRUCTURE (does the shape match the
category), PROCESS NOISE, CITATION PRESENCE and lexical RELEVANCE, with a
relevance floor of 0.25. Those are real properties, but none of them is "is this
answer good": an answer can be well-shaped, clean, cited and lexically on-topic
and still be wrong, thin, or unreadable.

This module grades what "9 out of 10" actually means, on a 0-10 scale, per query
type:

    groundedness  do the answer's factual sentences trace to the evidence given?
    coverage      were the question's dimensions addressed?
    accuracy      are the numbers in the answer present in the evidence?
    readability   is it prose a person can read, not a clip dump?

Why per-type: the failure modes differ. A comparison answer fails by not
comparing; an ambiguous answer fails by answering the wrong reading; a factual
answer fails by going unsupported. A single blended number hides all three, which
is exactly how the codebase carried a known-broken ambiguous path while its
suite reported all-green.

Deterministic and offline on purpose: an LLM judge would be a second unmeasured
system. `score_report` takes the answer plus the evidence pool the run actually
had, so groundedness is measured against real sources rather than a rubric.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence

# Target the product promises. A report at or above this per type is "good".
QUALITY_TARGET = 9.0


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|percent|million|billion|trillion|thousand|[kmb]n?\b)?", re.I)
_CITATION_RE = re.compile(r"\[(\d+)\]")
_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "as", "at", "by", "from",
    "that", "this", "these", "those", "it", "its", "if", "then", "than", "so",
    "what", "which", "who", "how", "why", "when", "where", "does", "do", "did",
}


def _tokens(text: str) -> set:
    return {
        w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(w) > 2 and w not in _STOP
    }


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text or "") if s.strip()]


# --- individual dimensions, each 0..1 -------------------------------------

def groundedness(answer: str, evidence_texts: Sequence[str]) -> float:
    """Fraction of the answer's factual sentences that trace to the evidence.

    A sentence is grounded when a good share of its content words appear in some
    single evidence text. Measured per sentence against the BEST source, because
    a synthesised sentence legitimately draws on several — but it must draw on
    at least one, or it was invented.

    ANALYTICAL SENTENCES ARE EXEMPT, and this is the subtlety that matters.
    "Taken together, these findings indicate…" and "The evidence does not settle
    which effect dominates" are the most valuable sentences in a report and share
    almost no vocabulary with any source by construction. Counting them as
    ungrounded penalises exactly the reasoning the product exists to produce —
    the same failure AGENTS.md section 2 records for `verify_answer_support`,
    which this scorer reproduced before the guard was added.

    The discriminator is whether the sentence asserts a CHECKABLE FACT. A
    sentence with no numbers and no named entity is doing analysis, not making a
    claim, so it is excluded from the denominator rather than being debited.
    """
    sents = [s for s in _sentences(answer) if len(_tokens(s)) >= 4]
    if not sents:
        return 0.0
    pool = [_tokens(e) for e in evidence_texts if e]
    if not pool:
        return 0.0

    checkable = [s for s in sents if _asserts_a_fact(s)]
    if not checkable:
        # A purely analytical answer makes no checkable claims: judge it on
        # whether it engaged the evidence at all, via one blend over the whole.
        whole = _tokens(answer)
        return max((len(whole & p) / max(1, len(whole)) for p in pool), default=0.0)

    grounded = 0
    for sent in checkable:
        st = _tokens(sent)
        if not st:
            continue
        best = max((len(st & p) / len(st) for p in pool), default=0.0)
        if best >= 0.34:
            grounded += 1
    return grounded / len(checkable)


_FACT_CUE_RE = re.compile(
    r"\d", re.I
)


def _asserts_a_fact(sentence: str) -> bool:
    """Does this sentence make a checkable claim?

    A number, a percentage, or a capitalised multi-word entity. A sentence that
    merely reasons over the evidence ("these findings indicate…", "the evidence
    does not settle…") asserts nothing checkable and must not be judged as a
    sourcing failure.
    """
    if _FACT_CUE_RE.search(sentence):
        return True
    # A named entity: a capitalised word that is not merely sentence-initial and
    # not a common discourse word.
    body = sentence.strip()
    words = body.split()
    for i, w in enumerate(words):
        stripped = re.sub(r"[^\w]", "", w)
        if not stripped or not stripped[0].isupper():
            continue
        if i == 0:
            continue  # sentence-initial capital is not entity evidence
        if stripped.lower() in {"the", "this", "that", "these", "those", "it"}:
            continue
        return True
    return False


def numeric_accuracy(answer: str, evidence_texts: Sequence[str]) -> float:
    """Fraction of numbers stated in the answer that also appear in the
    evidence.

    Fabricated figures are the single most damaging quality failure, and they
    are invisible to every lexical score. Units are stripped before comparison
    so "2,400 MW" matches "2400".

    An answer that states NOTHING returns 0.0, not 1.0: "no numbers" is not
    "every number correct", and returning 1.0 handed a free 25% of the score to
    an empty or purely qualitative answer — including the empty answer itself.

    NOTE FOR CALLERS: this 0.0 is an "unmeasured" signal, not a "wrong" one.
    `score_report` distinguishes the two: a number-free answer has the accuracy
    dimension REMOVED and its weight redistributed, while an answer with wrong
    numbers keeps this 0.0 and is debited in full. Use `numeric_accuracy_applicable`
    to tell the two apart.
    """
    stated = _stated_numbers(answer)
    if not stated:
        return 0.0
    known: set = set()
    for e in evidence_texts:
        known |= _stated_numbers(e)
    return len(stated & known) / len(stated)


def numeric_accuracy_applicable(answer: str) -> bool:
    """Does the answer state any checkable figure at all?

    False means the accuracy dimension cannot be measured — there is nothing to
    be right or wrong about — so `score_report` must drop it rather than score
    it zero. A qualitative answer ("what does this mean?") is not inaccurate for
    declining to invent statistics.
    """
    return bool(_stated_numbers(answer))


def _stated_numbers(text: str) -> set:
    out: set = set()
    # Citation markers are not claims. A report is REQUIRED to carry [n]
    # references, so counting them as numbers made every well-cited answer
    # look like it fabricated figures — the exact inverse of the check's
    # purpose.
    text = _CITATION_RE.sub(" ", text or "")
    for raw in _NUMBER_RE.findall(text):
        digits = re.sub(r"[^\d.]", "", raw)
        if not digits or digits == ".":
            continue
        digits = digits.rstrip(".")
        # A bare 4-digit year is a DATE, not a measured quantity. Counting
        # it penalised a correct report for saying "2027" when the evidence
        # phrased the same year differently, and rewarded a fragment dump
        # for containing it. Fabrication is about figures, so years are out.
        if re.fullmatch(r"(?:19|20)\d\d", digits):
            continue
        out.add(digits)
    return out


def coverage(query: str, answer: str) -> float:
    """Fraction of the question's content terms the answer engages with.

    A lower bound on answering the question asked: it cannot detect a wrong
    answer, only an unaddressed one, which is why it is one signal among four.
    """
    qt = _tokens(query)
    if not qt:
        return 1.0
    at = _tokens(answer)
    return len(qt & at) / len(qt)


def readability(answer: str) -> float:
    """Is this prose a person can read?

    Penalises the two live failure shapes: raw source-text dumps and wall-of-text.

    The dump case is the subtle one. A fragment run has NO terminal punctuation
    at all, so it parses as a single "sentence" — and a single long sentence looks
    like normal prose to a sentence-length check. It scored 0.72 without this
    guard. The signal that distinguishes it is punctuation DENSITY: real prose
    ends sentences, a source clip does not. Fewer than one sentence terminator
    per ~40 words means the text was never written as prose.
    """
    if not answer or not answer.strip():
        return 0.0
    sents = _sentences(answer)
    if not sents:
        return 0.0

    words = len(answer.split())
    terminators = len(re.findall(r"[.!?]", answer))
    # ~1 terminator per 15-20 words is normal prose; the dump had one (or zero)
    # across 40+ words. Scale linearly up to a full rate at 1 per 25 words.
    terminator_rate = terminators / max(1, words)
    punctuation_ok = min(1.0, terminator_rate / (1 / 25))

    fragments = sum(1 for s in sents if len(_tokens(s)) < 4)
    fragment_rate = fragments / len(sents)
    avg_words = words / len(sents)
    length_ok = 1.0 if 8 <= avg_words <= 35 else max(0.0, 1.0 - abs(avg_words - 21) / 40)
    return max(0.0, min(1.0, punctuation_ok * 0.45 + (1.0 - fragment_rate) * 0.3 + length_ok * 0.25))


# --- per-type weighting ----------------------------------------------------

# Which dimension actually determines quality for each question shape. A
# comparison answer is judged mainly on coverage (did it compare?), a factual
# one mainly on groundedness (is it supportable?), etc. Weights sum to 1.
_TYPE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "factual":       {"groundedness": 0.45, "accuracy": 0.25, "coverage": 0.15, "readability": 0.15},
    "definition":    {"groundedness": 0.40, "accuracy": 0.10, "coverage": 0.20, "readability": 0.30},
    "comparison":    {"groundedness": 0.30, "accuracy": 0.15, "coverage": 0.35, "readability": 0.20},
    "causal":        {"groundedness": 0.40, "accuracy": 0.15, "coverage": 0.25, "readability": 0.20},
    "how-to":        {"groundedness": 0.30, "accuracy": 0.10, "coverage": 0.35, "readability": 0.25},
    "decision-support": {"groundedness": 0.30, "accuracy": 0.20, "coverage": 0.30, "readability": 0.20},
    "forecasting":   {"groundedness": 0.35, "accuracy": 0.25, "coverage": 0.20, "readability": 0.20},
    "data-analysis": {"groundedness": 0.30, "accuracy": 0.35, "coverage": 0.20, "readability": 0.15},
    "ambiguous":     {"groundedness": 0.35, "accuracy": 0.10, "coverage": 0.25, "readability": 0.30},
}
_DEFAULT_WEIGHTS = {"groundedness": 0.35, "accuracy": 0.20, "coverage": 0.25, "readability": 0.20}


def weights_for(query_type: str) -> Dict[str, float]:
    return _TYPE_WEIGHTS.get((query_type or "").strip().lower(), _DEFAULT_WEIGHTS)


@dataclass
class QualityScore:
    query_type: str
    groundedness: float
    accuracy: float
    coverage: float
    readability: float
    score: float                     # 0-10
    weights: Dict[str, float] = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        return self.score >= QUALITY_TARGET

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_type": self.query_type,
            "groundedness": round(self.groundedness, 3),
            "accuracy": round(self.accuracy, 3),
            "coverage": round(self.coverage, 3),
            "readability": round(self.readability, 3),
            "score": round(self.score, 2),
            "target": QUALITY_TARGET,
            "passes": self.passes,
            "weights": self.weights,
        }


def score_report(
    query: str,
    answer: str,
    evidence_texts: Iterable[str],
    query_type: str = "",
) -> QualityScore:
    """Grade one delivered report 0-10 for its query type.

    `evidence_texts` is the pool the run actually had (claim text or source
    content). Groundedness and accuracy are measured against THAT, not a rubric,
    so an answer cannot score well by being plausible.

    ACCURACY IS CONDITIONAL. When the answer states no checkable figures there
    is nothing to be accurate about, so the accuracy dimension is REMOVED and
    its weight redistributed across the others. Scoring it zero instead capped a
    perfect qualitative factual answer at 7.5-8.5 and a data-analysis answer at
    6.5 — an unreachable target for reasons unrelated to quality. An answer with
    figures that are absent from the evidence still takes the full accuracy
    debit; `numeric_accuracy_applicable` is what tells the two cases apart.
    """
    ev = [str(e) for e in (evidence_texts or []) if str(e).strip()]
    # An empty answer is not "unmeasured accuracy", it is a total failure. Short-
    # circuit before renormalization can hand it free coverage points (an empty
    # or stopword-only query makes `coverage` return 1.0).
    if not (answer or "").strip():
        return QualityScore(
            query_type=query_type or "default",
            groundedness=0.0, accuracy=0.0, coverage=0.0, readability=0.0,
            score=0.0, weights=weights_for(query_type),
        )
    g = groundedness(answer, ev)
    a = numeric_accuracy(answer, ev)
    c = coverage(query, answer)
    r = readability(answer)
    w = weights_for(query_type)
    if not numeric_accuracy_applicable(answer):
        # Renormalize the remaining weights so the score is still on a 0-10
        # scale and accuracy contributes nothing rather than zero.
        remaining = {k: v for k, v in w.items() if k != "accuracy"}
        total = sum(remaining.values()) or 1.0
        w = {k: v / total for k, v in remaining.items()}
    score = 10.0 * (
        w.get("groundedness", 0.0) * g
        + w.get("accuracy", 0.0) * a
        + w.get("coverage", 0.0) * c
        + w.get("readability", 0.0) * r
    )
    return QualityScore(
        query_type=query_type or "default",
        groundedness=g, accuracy=a, coverage=c, readability=r,
        score=score, weights=w,
    )
