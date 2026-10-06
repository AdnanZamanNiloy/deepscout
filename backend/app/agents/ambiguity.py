"""Ambiguity policy: resolve it, state an assumption, ask, or separate.

THE PROBLEM THIS SOLVES
-----------------------
An underspecified question was acknowledged and then silently researched under
every reading at once. "What can be the most demanding job in 2027" produced
plans, searches and evidence for job growth, openings, burnout, cognitive load,
physical strain and automation risk — six research programmes for one question
whose ambiguity was never resolved. The reviewer noticed that none of them
defined the ask and asked for more research, which cannot help: the missing thing
was a definition, not evidence.

More searching is never a solution to a semantic problem. This module decides,
before research starts, which of four things to do:

  PROCEED   the query has one clear reading — plan normally (the common case).
  ASSUME    one reading clearly dominates, or the query's own wording already
            picks one. State the assumption and research that reading only.
  ASK       the readings would produce substantially different answers and
            nothing in the query chooses between them. Return a clarifying
            question and STOP. Guessing here is worse than asking.
  SEPARATE  the readings genuinely differ but a single answer can cover both
            usefully. Research them as distinct strands and keep their evidence
            apart in the answer, rather than blending them into one muddle.

DOMAIN AGNOSTICISM
------------------
Nothing here names a subject. Interpretations come from the intent layer
(LLM-generated senses, or the curated homonym table when the model is down), and
every decision below is made from the query's STRUCTURE: does it carry a
disambiguating qualifier, how many readings are there, how far apart are they,
and would they change the answer. A question about turbine blades and a question
about poetry are decided by identical code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence, Tuple

# Actions.
PROCEED = "proceed"
ASSUME = "assume"
ASK = "ask"
SEPARATE = "separate"

# A reading at or above this probability, with the runner-up far below, is
# dominant enough to research alone. Mirrors intent's own confidence vocabulary
# so the two layers cannot disagree about what "clearly dominant" means.
DOMINANT_PROBABILITY = 0.70
# Top-two gap at or above this: one reading safely wins.
DOMINANT_GAP = 0.40
# Top-two gap BELOW this, with both readings plausible: they diverge enough that
# answering one would miss the question.
DIVERGENT_GAP = 0.25

# How many readings to show the user. More than a few is a questionnaire, not a
# clarification; the intent layer caps senses at a small number anyway.
MAX_INTERPRETATIONS = 4
MIN_INTERPRETATIONS = 2

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'&.-]*", re.UNICODE)

# Qualifiers that CHOOSE between readings. A query containing one of these is not
# ambiguous any more — the user already said which reading they meant, so asking
# would be obtuse. Structural markers, not topics: "by cost", "in terms of X",
# "as measured by Y" narrow a question in any field.
_DISAMBIGUATING_MARKERS: Tuple[str, ...] = (
    "in terms of", "as measured by", "measured by", "according to", "based on",
    "by contrast", "with respect to", "in respect of", "when it comes to",
    "specifically", "in particular", "for example", "such as", "i mean",
    "referring to", "you know", "of the two", "out of these", "specifically the",
    "interms of", "defin", "namely",
)
# "X or Y?" / "X vs Y" — the user names two things to compare. NOTE this is a
# SEPARATE signal, never a RESOLUTION signal: "nursing or teaching" compares two
# ENTITIES and leaves the term under comparison ("demanding") exactly as
# ambiguous as before. Treating it as resolution made every comparison query
# silently pick one reading of its own key term.
_ENUMERATED_CHOICE_RE = re.compile(
    r"\b(or|versus|vs\.?)\b", re.IGNORECASE
)

# Words that signal the query is ALREADY a request for a choice among readings
# ("which of", "either"). Their presence means the readings are the question, so
# separating them in the answer is exactly right.
_COMPARATIVE_MARKERS: Tuple[str, ...] = (
    "which", "either", "whether", "compare", "contrast", "difference between",
    "better", "worse", "vs", "versus", "trade-off", "tradeoff",
)


@dataclass
class AmbiguityPolicy:
    """What to do about a query's ambiguity, decided before planning."""

    action: str = PROCEED
    query: str = ""
    interpretations: List[str] = field(default_factory=list)
    question: str = ""
    assumption: str = ""
    reason: str = ""

    @property
    def should_stop(self) -> bool:
        """ASK means the run stops and returns the question instead of answering."""
        return self.action == ASK

    @property
    def needs_separation(self) -> bool:
        return self.action == SEPARATE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "interpretations": list(self.interpretations),
            "question": self.question,
            "assumption": self.assumption,
            "reason": self.reason,
            "should_stop": self.should_stop,
            "needs_separation": self.needs_separation,
        }


# ---------------------------------------------------------------------------
# Reading extraction
# ---------------------------------------------------------------------------


def _reading_labels(intent: Mapping[str, Any], query: str = "") -> List[str]:
    """The plausible readings of the query, from the intent layer.

    Both channels are accepted so this works on the LLM path (senses) and on the
    deterministic path (interpretations), without a second taxonomy.

    A "reading" that merely restates the query is not an alternative reading —
    including it produced a clarification question whose first option was the
    user's own question back at them ("Did you mean Most demanding jobs in 2027,
    Hard to fill...?"). Filtered by containment against the query, which needs no
    subject knowledge.
    """
    query_tokens = _subject_tokens(query)
    labels: List[str] = []

    def _add(label: str) -> None:
        label = str(label or "").strip()
        if not label or label in labels:
            return
        # A restatement of the query adds no choice.
        if _is_restatement(label, query_tokens):
            return
        labels.append(label)

    for item in intent.get("senses") or ():
        if isinstance(item, Mapping):
            _add(item.get("label", ""))
    for item in intent.get("interpretations") or ():
        if isinstance(item, Mapping):
            _add(item.get("label", ""))
    return labels[:MAX_INTERPRETATIONS]


def _probabilities(intent: Mapping[str, Any]) -> List[float]:
    out: List[float] = []
    for item in intent.get("senses") or ():
        if isinstance(item, Mapping):
            try:
                out.append(float(item.get("probability", 0.0) or 0.0))
            except (TypeError, ValueError):
                out.append(0.0)
    return out


def _has_disambiguating_context(query: str) -> bool:
    """Did the query itself already choose a reading?

    Checked BEFORE anything else: a query that says "in terms of physical
    effort" has no open ambiguity, and asking the user to clarify a question they
    already clarified is its own failure.

    Only QUALIFIER phrases count. A bare "or"/"versus" does NOT: it names two
    entities to compare and says nothing about which reading of the shared key
    term is meant, so it is a separation signal, not a resolution one.
    """
    low = f" {(query or '').lower().strip()} "
    return any(marker in low for marker in _DISAMBIGUATING_MARKERS)


def _is_comparative(query: str) -> bool:
    low = f" {(query or '').lower()} "
    return any(marker in low for marker in _COMPARATIVE_MARKERS)


def _subject_tokens(text: str) -> frozenset:
    return frozenset(
        w for w in _TOKEN_RE.findall((text or "").lower()) if len(w) > 1
    )


def _is_restatement(label: str, query_tokens: frozenset) -> bool:
    """Is this label just the query said back, rather than an alternative reading?

    Restatement, not similarity: every content word of the label already appears
    in the query. "Most demanding jobs in 2027" against "what can be the most
    demanding job in 2027" is a restatement even though the Jaccard is only 0.4,
    because the query carries function words the label does not. A genuine
    reading ("Hard to fill", "Stressful or difficult") introduces vocabulary the
    query never had.

    Plural/tense is folded with a light stem so "job"/"jobs" compare equal.
    """
    if not query_tokens:
        return False
    tokens = _subject_tokens(label)
    if not tokens:
        return False
    stems = {_stem(t) for t in query_tokens}
    return all(_stem(t) in stems for t in tokens)


def _stem(token: str) -> str:
    """Very light stemming: enough for plural/tense, no dictionary needed."""
    for suffix in ("ies", "es", "s", "ing", "ed"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def readings_would_diverge(labels: Sequence[str]) -> bool:
    """Would these readings produce substantially different answers?

    Judged structurally: readings that share almost no vocabulary describe
    different subjects and therefore different answers. Readings that overlap
    heavily ("highest quality" vs "best fit for a case") can be covered by one
    answer. This is the discriminator between ASK and SEPARATE, and it reads only
    the labels the intent layer produced.
    """
    if len(labels) < 2:
        return False
    token_sets = [_subject_tokens(label) for label in labels]
    # Averaged pairwise Jaccard over the distinguishable readings.
    scores: List[float] = []
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            a, b = token_sets[i], token_sets[j]
            if not a or not b:
                continue
            scores.append(len(a & b) / len(a | b))
    if not scores:
        return True
    overlap = sum(scores) / len(scores)
    return overlap < 0.20


def clarification_question(query: str, labels: Sequence[str]) -> str:
    """A neutral question naming the readings, with no subject invented.

    Phrased from the user's own alternatives so it cannot smuggle in a topic the
    question did not contain.
    """
    options = [str(label).strip() for label in labels if str(label).strip()]
    if not options:
        return ""
    if len(options) == 2:
        joined = f"{options[0]}, or {options[1]}"
    else:
        joined = ", ".join(options[:-1]) + f", or {options[-1]}"
    return (
        f"Your question could be read more than one way. Did you mean {joined}? "
        f"Telling me which one will let me answer it properly instead of "
        f"covering all of them."
    )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def decide_ambiguity(
    query: str,
    intent: Mapping[str, Any] | None = None,
    *,
    broad_question: bool = False,
) -> AmbiguityPolicy:
    """Decide how to handle this query's ambiguity, before any research.

    `broad_question` is the focus layer's breadth judgement (a question asking
    for several things at once). A broad question that can be answered by covering
    its parts is SEPARATE rather than ASK — the user asked for breadth, so
    covering it is responsive, not evasive.

    Total and deterministic: any input yields a policy, never raises.
    """
    intent = intent if isinstance(intent, Mapping) else {}
    labels = _reading_labels(intent, query)
    probs = _probabilities(intent)

    # 1. No plausible readings at all: nothing to decide.
    if len(labels) < MIN_INTERPRETATIONS:
        return AmbiguityPolicy(
            action=PROCEED, query=query, reason="the question has one clear reading"
        )

    # 2. The query's OWN wording already picks a reading. Asking would be obtuse.
    if _has_disambiguating_context(query):
        return AmbiguityPolicy(
            action=PROCEED,
            query=query,
            interpretations=labels,
            reason=(
                "the question already specifies which reading is meant, so no "
                "clarification is needed"
            ),
        )

    # 3. One reading clearly dominates: state the assumption and proceed.
    #    Uses probabilities when the LLM supplied them; when it did not (the
    #    deterministic homonym path) dominance is not claimed and we fall through
    #    to the structural decision rather than inventing a winner.
    if len(probs) >= 2:
        top, second = probs[0], probs[1]
        if top >= DOMINANT_PROBABILITY and (top - second) >= DOMINANT_GAP:
            return AmbiguityPolicy(
                action=ASSUME,
                query=query,
                interpretations=labels,
                assumption=labels[0],
                reason=(
                    f"one reading is clearly dominant ({labels[0]}), so the answer "
                    f"states that assumption and proceeds"
                ),
            )

    # 4. The readings diverge. Either the user asked for a comparison (then the
    #    readings ARE the question and separating them is the answer), or the
    #    question is broad enough that covering them is responsive, or the
    #    readings would produce substantially different answers and nothing in
    #    the query chooses — which is the only case that warrants asking.
    divergent = readings_would_diverge(labels)
    if not divergent:
        return AmbiguityPolicy(
            action=ASSUME,
            query=query,
            interpretations=labels,
            assumption=labels[0],
            reason=(
                "the readings overlap enough that one answer covers them; "
                "stating the primary reading and proceeding"
            ),
        )

    enumerates_choices = _is_comparative(query)
    if enumerates_choices or broad_question:
        return AmbiguityPolicy(
            action=SEPARATE,
            query=query,
            interpretations=labels,
            reason=(
                "the readings are genuinely different and the question asks for "
                "them, so each is researched and answered separately rather than "
                "blended"
            ),
        )

    return AmbiguityPolicy(
        action=ASK,
        query=query,
        interpretations=labels,
        question=clarification_question(query, labels),
        reason=(
            "the readings would produce substantially different answers and "
            "nothing in the question chooses between them; more research cannot "
            "resolve a definition"
        ),
    )


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------

# Gate-failure prefixes that describe a SEMANTIC problem rather than a missing
# source. The critic emits these when the evidence cannot settle what the
# question means; more searching cannot fix them.
_SEMANTIC_GAP_MARKERS: Tuple[str, ...] = (
    "no definitional claim",
    "definition",
    "ambiguous",
    "ambiguity",
    "underspecified",
    "interpretation",
    "means different",
    "unclear what",
)


def is_semantic_gap(gate_failures: Sequence[str]) -> bool:
    """Is the remaining problem a definition, not a lack of evidence?

    The stopping rule: when the critic's blockers describe what the question
    MEANS, another search pass is waste. This reads the critic's own failure
    vocabulary, so it stays in step with what the critic actually reports.
    """
    for failure in gate_failures or ():
        text = str(failure or "").lower()
        if any(marker in text for marker in _SEMANTIC_GAP_MARKERS):
            return True
    return False
