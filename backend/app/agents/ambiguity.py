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

from app.core.semantic import is_meaning_bearing_form

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
# Looser bar for ASSUMING a reading rather than asking. The question is not
# "is this certain?" but "is this reasonable enough to research with a stated
# assumption?" — a moderately leading reading clears it. Asking is reserved for
# a genuine tie, because stopping to interrogate the user is the expensive
# failure here, not proceeding on a defensible reading.
ASSUME_PROBABILITY = 0.45
ASSUME_GAP = 0.10
# Top-two gap BELOW this, with both readings plausible: they diverge enough that
# answering one would miss the question.
DIVERGENT_GAP = 0.25
# How many readings a single report can cover before covering all of them stops
# being responsive and starts being needlessly broad.
#
# Deliberately generous, and the reason ASK is so rare. Researching several
# readings IS the job: "most demanding jobs" can be answered across strain,
# skill and hours in one report that ranks jobs on all three, which is more
# useful than any single answer and more useful still than a question. Blocking
# research on "there are several plausible readings" is the failure this guard
# exists to prevent, so the bar is set at the point where covering the readings
# would actually produce a worse document — not at the point where there are
# simply more than two.
#
# 3 rather than the cap of 4 on purpose: with these two numbers equal, the ASK
# branch below could never be reached and the whole clarification path would be
# dead code. At 3, two or three readings are researched and answered side by
# side, and only the maximum — four genuinely divergent readings of one
# question — earns a question instead of a report.
MAX_READINGS_FOR_COVERAGE = 3

# How many readings to show the user. More than a few is a questionnaire, not a
# clarification; the intent layer caps senses at a small number anyway.
MAX_INTERPRETATIONS = 4
MIN_INTERPRETATIONS = 2

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'&.-]*", re.UNICODE)

# Function words carry no meaning and must not create apparent agreement between
# a query and a reading. Without this, "what can be the most demanding job in
# 2027" shared the token "the" with a reading and scored 0.73 semantic fit for
# it — the right answer arrived through a stopword, which is fragile rather than
# correct, and would break on any phrasing with no shared stopword.
#
# NEGATION IS NOT A STOPWORD (AGENTS.md, confirmed bug class): "not demanding"
# and "demanding" must never compare equal.
_MEANING_STOPWORDS = frozenset({
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "had", "has",
    "have", "how", "in", "into", "is", "it", "its", "may", "might", "must",
    "of", "on", "or", "our", "over", "shall", "should", "so", "some", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "under", "up", "was", "we", "were", "what", "when", "where",
    "which", "who", "whom", "whose", "why", "will", "with", "within", "would",
    "you", "your",
})


def _subject_tokens(text: str) -> frozenset:
    """Content words of a text: function words removed, negation kept."""
    return frozenset(
        w
        for w in _TOKEN_RE.findall((text or "").lower())
        if len(w) > 1 and w not in _MEANING_STOPWORDS
    )

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
    off_meaning_labels: List[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        label = str(label or "").strip()
        # One dedup set across BOTH lists: a reading offered on both channels
        # (as `senses` and as `interpretations`) is still one reading.
        if not label or label in seen:
            return
        seen.add(label)
        # A restatement of the query adds no choice.
        if _is_restatement(label, query_tokens):
            return
        # NOTE: no meaning-preservation filter here. An off-meaning reading is
        # still SHOWN and still researched separately — the user is told which
        # word they wrote and which one they might have meant. What must never
        # happen is adopting it as the interpretation, which `select_reading`
        # prevents. Dropping it would hide the distinction instead of making it.
        #
        # But it must never be the FIRST reading either. Position 0 is what the
        # report announces it assumed and what the synthesis prompt treats as
        # primary, and the classifier commonly returns the off-meaning sense
        # first — which is how "most demanding skill" was answered as "most
        # in-demand skill" even after the guard existed.
        off_meaning = _violates_meaning_preservation(
            query, label, intent.get("meaning_boundaries") or ()
        )
        if off_meaning:
            off_meaning_labels.append(label)
        else:
            labels.append(label)

    on_meaning = labels

    for item in intent.get("senses") or ():
        if isinstance(item, Mapping):
            _add(item.get("label", ""))
    for item in intent.get("interpretations") or ():
        if isinstance(item, Mapping):
            _add(item.get("label", ""))
    # On-meaning readings lead; off-meaning ones are still offered, last.
    return (on_meaning + off_meaning_labels)[:MAX_INTERPRETATIONS]


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


def _is_restatement(label: str, query_tokens: frozenset) -> bool:
    """Is this label just the query said back, rather than an alternative reading?

    A restatement adds NO vocabulary of its own: every content word of the label
    already appears in the query ("demanding job" for "what is the most demanding
    job"). A genuine reading introduces a word the query never had — "Stressful
    or difficult", "Requiring high skill" — even when it also names the query's
    term ("Transformer neural network architecture" for "what is a transformer").

    Deliberately a STRICT SUBSET test. A looser "the label covers the query"
    rule was tried and rejected: it fired on every legitimate reading, because a
    reading normally does mention the term it is a reading of. The cost of the
    strict test is that an LLM echo which adds a synonym ("Most demanding
    jobs/careers in 2027" — `careers` for `job`) survives it; that is caught
    downstream instead, where a reading that only rephrases the question shares
    the query's vocabulary and so is never a clearly-winning DISTINCT meaning.

    Plural/tense folds via a light stem so "job"/"jobs" compare equal.
    """
    if not query_tokens:
        return False
    tokens = _subject_tokens(label)
    if not tokens:
        return False
    stems = {_stem(t) for t in query_tokens}
    label_stems = {_stem(t) for t in tokens}

    # Nothing in the label the query did not already say.
    if label_stems <= stems:
        return True

    # The label reproduces the WHOLE question and adds only a rename of the
    # query's own head noun ("Most demanding jobs/CAREERS in 2027" for "the most
    # demanding JOB in 2027"). An LLM asked for the readings of a question often
    # hands the question back, and an echo then wins selection on semantic fit
    # precisely because it repeats the user's words — answering the question
    # with itself.
    #
    # Only meaningful when the question HAS enough content to echo. A one-word
    # query ("what is a transformer") is "fully covered" by any label mentioning
    # it, so the rule would filter "Electrical power transformer" — a genuine
    # reading. Below three content words there is no query to reproduce.
    if len(stems) < 3:
        return False
    covered = len(stems & label_stems) / len(stems)
    added = label_stems - stems
    # A real reading introduces concepts the query lacked (at least three:
    # "Transformer neural network ARCHITECTURE", "... NETWORK", "... NEURAL"),
    # whereas an echo contributes a synonym or two.
    return covered >= 1.0 and len(added) <= 2


def _stem(token: str) -> str:
    """Very light stemming: enough for plural/tense, no dictionary needed."""
    # Meaning first. Some words are NOT inflected forms of another word, and
    # stripping their suffix merges two different concepts — which is how a
    # question about demanding jobs came to be answered as a question about
    # labour shortage.
    if is_meaning_bearing_form(token):
        return token
    for suffix in ("ies", "es", "s", "ing", "ed"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _query_tokens_with_splits(query: str) -> set:
    """Subject tokens, plus the pieces of hyphenated ones.

    A hyphenated compound is a single token under `_TOKEN_RE`, so without the
    split the system cannot see which of two similar words the user actually
    wrote — and would treat their query as if it had named neither.
    """
    tokens = set(_subject_tokens(query))
    for token in list(tokens):
        if "-" in token:
            tokens.update(p for p in token.split("-") if p)
    return tokens


def _violates_meaning_preservation(
    query: str, reading_text: str, boundaries: Sequence[Mapping[str, Any]]
) -> bool:
    """True when a reading answers a DIFFERENT question than the one asked.

    `boundaries` arrives from the intent layer as DATA — this module names no
    subject, and its tests enforce that by scanning the file. Each entry is
    ``{"word": <term the user typed>, "excludes": <vocabulary belonging to a
    different word>}``.

    The rule is deliberately narrow, and both halves are required: the reading
    carries excluded vocabulary AND the user's own query carries none of it. So
    the guard fires on a reading about a word the user did not use, and stays
    silent when they did use it.
    """
    if not boundaries:
        return False
    query_tokens = _query_tokens_with_splits(query)
    # The READING side needs the same hyphen splitting the query side gets.
    # "in-demand" is one token under the tokenizer, so without the split a
    # reading about the other word never matched the "demand" marker and the
    # guard stayed silent — which is exactly how the labour-market reading kept
    # winning for "most demanding skill".
    reading_tokens = _query_tokens_with_splits(reading_text)
    if not query_tokens or not reading_tokens:
        return False
    for entry in boundaries:
        if not isinstance(entry, Mapping):
            continue
        word = str(entry.get("word", "") or "").strip()
        excludes = {
            str(token).strip().lower()
            for token in (entry.get("excludes") or ())
            if str(token).strip()
        }
        if not word or not excludes:
            continue
        if word in query_tokens and not (excludes & query_tokens):
            if excludes & reading_tokens:
                return True
    return False


@dataclass
class ReadingCandidate:

    label: str = ""
    description: str = ""
    semantic_fit: float = 0.0      # how naturally the wording implies this reading
    contextual_fit: float = 0.0    # how well it fits the surrounding intent
    evidence: float = 0.0          # how much evidence the run could find (0-1)
    score: float = 0.0             # semantic+contextual dominate; evidence is a tiebreak
    # This reading belongs to a DIFFERENT word the user did not write, so it can
    # be reported and researched but must never be adopted as the reading of
    # their query. Set by the intent layer's meaning boundaries.
    off_meaning: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "semantic_fit": round(self.semantic_fit, 4),
            "contextual_fit": round(self.contextual_fit, 4),
            "evidence": round(self.evidence, 4),
            "score": round(self.score, 4),
        }


# Weights. SEMANTIC AND CONTEXTUAL FIT DOMINATE. Evidence availability is a
# tiebreak only, and is capped low enough that it can never outvote meaning: the
# whole point is that search results must not redefine the user's terminology.
# "most demanding job" means the hardest/most stressful work; it does not become
# "hardest to fill" merely because labour-market data are easier to find.
W_SEMANTIC_FIT = 1.0
W_CONTEXTUAL_FIT = 0.5
W_EVIDENCE = 0.2
# A reading must beat the runner-up by this margin on the MEANING dimensions
# alone before it is treated as dominant. Evidence cannot close this gap.
MEANING_DECISION_MARGIN = 0.15


def _reading_candidates(intent: Mapping[str, Any], query: str = "") -> List[ReadingCandidate]:
    """The plausible readings with their labels AND descriptions.

    Descriptions matter: they carry the semantic content a reading stands for
    ("roles employers struggle to staff" vs "roles with the heaviest workload"),
    which is what semantic fit is measured against. The labels alone are often
    just a short name.
    """
    query_tokens = _subject_tokens(query)
    boundaries = intent.get("meaning_boundaries") or ()
    out: List[ReadingCandidate] = []
    seen: set[str] = set()

    def _add(label: Any, description: Any) -> None:
        text = str(label or "").strip()
        if not text or text in seen:
            return
        if _is_restatement(text, query_tokens):
            return
        seen.add(text)
        out.append(
            ReadingCandidate(
                label=text,
                description=str(description or "").strip(),
                off_meaning=_violates_meaning_preservation(
                    query, f"{text} {description or ''}", boundaries
                ),
            )
        )

    for item in intent.get("senses") or ():
        if isinstance(item, Mapping):
            _add(item.get("label", ""), item.get("note", "") or item.get("description", ""))
    for item in intent.get("interpretations") or ():
        if isinstance(item, Mapping):
            _add(item.get("label", ""), item.get("description", ""))
    # Off-meaning readings sort LAST. They are still shown and still researched,
    # but the first reading is the one a reader (and the synthesis prompt) treats
    # as the leading interpretation, and an off-meaning one must never occupy
    # that slot: "most demanding skill" was answered as "most in-demand skill"
    # because the labour-market reading arrived first from the classifier.
    out.sort(key=lambda c: c.off_meaning)
    return out[:MAX_INTERPRETATIONS]


def _reading_evidence_counts(intent: Mapping[str, Any]) -> Dict[str, int]:
    """Evidence per reading, when it is known.

    Selection happens BEFORE research, so this is normally empty and every
    reading scores zero evidence — which is the correct input: if the meaning
    were decided by evidence, the search results would be choosing the question.
    It exists so a caller that DOES have measured evidence (a re-plan, where the
    run already gathered material) can supply it, and so the scoring is testable
    against the failure it guards.
    """
    raw = intent.get("reading_evidence")
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _meaning_fit(
    reading: ReadingCandidate, query: str, *, prior: float = 0.0
) -> Tuple[float, float]:
    """(semantic_fit, contextual_fit) for a reading, both 0-1.

    SEMANTIC FIT asks: does the query's wording naturally imply this reading?
    Measured by how much of the reading's own vocabulary appears in the query,
    and — the stronger signal — whether the query's ambiguous term appears
    LITERALLY in the reading's label or description. A reading whose wording
    contains the user's own word is the one the user's word most likely means.

    The literal-term test is what separates "demanding" (difficulty/workload,
    the word the user actually wrote) from "in demand" (labour-market demand, a
    different word): a reading about demand does not contain "demanding", so it
    cannot claim the user's wording.

    CONTEXTUAL FIT asks: does it fit the rest of the question? Two signals:
    vocabulary shared with the query's remaining content words, and `prior` —
    the intent layer's own probability for this reading. The prior IS context:
    the classifier saw the phrasing and the surrounding intent, and a research
    assistant knowing "transformer" usually means the AI sense is exactly the
    contextual knowledge to encode. Without it, raw vocabulary picked
    "an electrical device" for "what is a transformer" because the word
    "transformer" appears in that label — overriding a correct 0.85 prior.

    Both remain vocabulary/prior comparisons — no subject knowledge, no topic
    list. Domain-agnostic by construction.
    """
    if not reading.label and not reading.description:
        return 0.0, 0.0
    query_tokens = _subject_tokens(query)

    label_tokens = _subject_tokens(reading.label)
    desc_tokens = _subject_tokens(reading.description)
    reading_tokens = label_tokens | desc_tokens
    if not reading_tokens:
        return 0.0, min(1.0, float(prior or 0.0))

    overlap = (len(query_tokens & reading_tokens) / len(query_tokens)) if query_tokens else 0.0

    # The literal-term signal: does the user's own ambiguous word appear, as the
    # SAME WORD, in the reading's vocabulary?
    #
    # Deliberately NOT stemmed. Stemming conflates "demanding" with "demand",
    # and those are precisely the two different words this must keep apart:
    # "demanding" means difficult/heavy, "in demand" means sought-after. The
    # readings that would exploit that collapse are removed upstream by
    # `_violates_meaning_preservation`, before any of them is scored.
    literal = 0.0
    for token in query_tokens:
        if token in reading_tokens:
            literal = 1.0
            break

    semantic = min(1.0, 0.7 * literal + 0.3 * overlap)
    # Context = shared vocabulary blended with the classifier's prior.
    contextual = max(overlap, min(1.0, float(prior or 0.0)))
    return semantic, contextual


def select_reading(
    candidates: Sequence[ReadingCandidate],
    query: str,
    *,
    evidence_counts: Mapping[str, int] | None = None,
    priors: Mapping[str, float] | None = None,
) -> Tuple[ReadingCandidate | None, List[ReadingCandidate]]:
    """Score every reading and pick the one the wording most supports.

    Requirement: semantic and contextual fit DOMINATE evidence availability. A
    reading that means what the user wrote wins even if the other reading has
    more searchable material — otherwise the search results would be redefining
    the user's terminology, which is precisely the failure.

    Evidence availability is measured and reported, but its weight is small
    enough (W_EVIDENCE) that it can only break a near-tie on meaning, never
    overturn a clear one.

    `priors` are the intent layer's probabilities, folded into CONTEXTUAL fit
    (they encode the classifier's reading of the phrasing), never into evidence.

    Returns (chosen or None, scored candidates).
    """
    evidence_counts = evidence_counts or {}
    priors = priors or {}
    if not candidates:
        return None, []

    # A reading that belongs to a word the user did not write is reported and
    # researched, but it is never ADOPTED as the reading of their query. Ranking
    # is the wrong frame for it: a perfect score only means the two words reduce
    # to the same stem, which is precisely the confusion being prevented.
    choosable = [c for c in candidates if not c.off_meaning]
    if not choosable:
        return None, []
    if len(choosable) != len(candidates):
        candidates = choosable

    max_evidence = max((int(evidence_counts.get(c.label, 0) or 0) for c in candidates), default=0)
    scored: List[ReadingCandidate] = []
    for candidate in candidates:
        try:
            prior = float(priors.get(candidate.label, 0.0) or 0.0)
        except (TypeError, ValueError):
            prior = 0.0
        semantic, contextual = _meaning_fit(candidate, query, prior=prior)
        count = int(evidence_counts.get(candidate.label, 0) or 0)
        evidence = (count / max_evidence) if max_evidence > 0 else 0.0
        candidate.semantic_fit = semantic
        candidate.contextual_fit = contextual
        candidate.evidence = evidence
        candidate.score = round(
            W_SEMANTIC_FIT * semantic
            + W_CONTEXTUAL_FIT * contextual
            + W_EVIDENCE * evidence,
            4,
        )
        scored.append(candidate)

    # Ranked by MEANING first; evidence only orders readings that tie on meaning.
    scored.sort(
        key=lambda c: (
            -(W_SEMANTIC_FIT * c.semantic_fit + W_CONTEXTUAL_FIT * c.contextual_fit),
            -c.evidence,
            c.label,
        )
    )
    if not scored:
        return None, []

    top = scored[0]
    if len(scored) == 1:
        return top, scored
    runner_up = scored[1]
    top_meaning = W_SEMANTIC_FIT * top.semantic_fit + W_CONTEXTUAL_FIT * top.contextual_fit
    next_meaning = (
        W_SEMANTIC_FIT * runner_up.semantic_fit + W_CONTEXTUAL_FIT * runner_up.contextual_fit
    )
    if top_meaning - next_meaning >= MEANING_DECISION_MARGIN:
        return top, scored
    # No reading clearly wins on meaning. If they tie on meaning but one is
    # better evidenced, that is still not a reason to switch meaning — return
    # None so the caller separates or asks rather than letting evidence decide.
    return None, scored


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


def _reading_is_on_subject(query: str, label: str) -> bool:
    """Does this reading share any content word with what the user actually asked?

    The shared word must not be the ambiguous term itself — "demanding" appears
    in a reading precisely BECAUSE the reading is about that word, so counting
    it would mark every reading of a homonym as on-subject and make the check
    vacuous.
    """
    query_tokens = _query_tokens_with_splits(query)
    if not query_tokens:
        return False
    return bool(_subject_tokens(label) & query_tokens)


def _any_reading_on_subject(query: str, labels: Sequence[str]) -> bool:
    """True when at least one plausible reading is actually about the question.

    This is the honest test for "ambiguity prevents a useful answer". If a
    reading is recognisably about what was asked, the system can research it and
    state its assumption — asking instead would be refusing to do work it can
    obviously do. Only when NOT ONE reading is recognisably on-subject is the
    question genuinely undetermined, and a clarifying question is the honest
    response.
    """
    return any(_reading_is_on_subject(query, label) for label in labels or ())


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

    # 3. A COMPARATIVE question is never assumed. When the user is explicitly
    #    weighing readings against each other, or asking for breadth, the
    #    readings ARE the question — picking one would answer a question they did
    #    not ask. Checked BEFORE the dominance shortcut, which would otherwise
    #    choose a reading for "which is more demanding: A or B?".
    if _is_comparative(query) or broad_question:
        return AmbiguityPolicy(
            action=SEPARATE,
            query=query,
            interpretations=labels,
            assumption=str(labels[0]),
            reason=(
                "the question asks for the readings to be weighed or covered, "
                "so each is researched and answered separately rather than "
                "picking one"
            ),
        )

    # 3b. One reading is reasonably dominant: state the assumption and proceed.
    #     Probabilities are used when the LLM supplied them. When it did not (the
    #     deterministic homonym path supplies none) dominance is still claimed
    #     because the intent layer orders readings by likelihood. This is the
    #     DEFAULT outcome for an underspecified query: research the best answer
    #     with a stated assumption rather than interrogating the user.
    # 3b. Score every reading on SEMANTIC FIT, CONTEXTUAL FIT and EVIDENCE
    #     AVAILABILITY, and take the one the wording most supports. Semantic and
    #     contextual fit dominate; evidence is a small tiebreak only, so search
    #     results can never redefine the user's terminology.
    candidates = _reading_candidates(intent, query)
    evidence_counts = _reading_evidence_counts(intent)
    # Priors come from the intent layer's senses, keyed by label.
    priors: Dict[str, float] = {}
    for item in intent.get("senses") or ():
        if isinstance(item, Mapping):
            label = str(item.get("label", "") or "").strip()
            if label:
                try:
                    priors[label] = float(item.get("probability", 0.0) or 0.0)
                except (TypeError, ValueError):
                    priors[label] = 0.0
    chosen_reading, _ = select_reading(
        candidates, query, evidence_counts=evidence_counts, priors=priors
    )
    if chosen_reading is not None:
        return AmbiguityPolicy(
            action=ASSUME,
            query=query,
            interpretations=labels,
            assumption=chosen_reading.label,
            reason=(
                f"'{chosen_reading.label}' best fits the question's wording "
                f"(semantic {chosen_reading.semantic_fit:.2f}, contextual "
                f"{chosen_reading.contextual_fit:.2f}); evidence availability "
                f"({chosen_reading.evidence:.2f}) does not decide the meaning"
            ),
        )

    # 4. No single reading is preferable. The question is now whether covering
    #    the readings together answers it, or whether they imply substantially
    #    different research and covering them would be needlessly broad.
    #
    #    DEFAULT IS TO RESEARCH. Ambiguity guides the strategy; it does not block
    #    research. Asking is the last resort and requires BOTH that the readings
    #    would need genuinely different plans AND that answering them together is
    #    not a reasonable response. Anything else proceeds on a stated assumption.
    divergent = readings_would_diverge(labels)
    enumerates_choices = _is_comparative(query)

    if enumerates_choices or broad_question or not divergent:
        # The readings are the question, or the question wants breadth, or one
        # answer covers them: research each and keep them apart rather than
        # asking. SEPARATE answers the user; ASK would not.
        return AmbiguityPolicy(
            action=SEPARATE,
            query=query,
            interpretations=labels,
            assumption=str(labels[0]),
            reason=(
                "the readings are genuinely different and the question can be "
                "answered by covering them, so each is researched and answered "
                "separately rather than blended"
                if divergent
                else "the readings overlap enough that one answer covers them, "
                "stated separately"
            ),
        )

    # The readings diverge and nothing in the question asks for them together.
    # Even here the default is to RESEARCH the most reasonable reading. Asking
    # requires THREE independent things to hold, not one:
    #
    #   1. the readings genuinely diverge (no single answer covers them), AND
    #   2. there are more of them than one report should carry, AND
    #   3. not ONE of them is recognisably about what was asked.
    #
    # (3) is the condition that was missing, and it is the one that matters. The
    # count alone is not a reason to stop: a model routinely returns four senses
    # for one word, so a threshold tuned on "two or three" is crossed by ordinary
    # queries, and the system refused to answer questions whose readings plainly
    # named the same subject the user asked about. Condition (3) is what makes
    # asking honest rather than lazy — if a reading is on-subject, the work is
    # doable, so the system must do it and state its assumption.
    if len(labels) <= MAX_READINGS_FOR_COVERAGE or _any_reading_on_subject(query, labels):
        return AmbiguityPolicy(
            action=SEPARATE,
            query=query,
            interpretations=labels,
            assumption=str(labels[0]),
            reason=(
                f"{len(labels)} readings include ones recognisably about what was "
                "asked, so the answerable reading is researched with its "
                "assumption stated, and the others are kept distinct, rather "
                "than handing the choice back to the user"
            ),
        )

    return AmbiguityPolicy(
        action=ASK,
        query=query,
        interpretations=labels,
        question=clarification_question(query, labels),
        reason=(
            f"{len(labels)} divergent readings would need substantially "
            "different research plans, and covering all of them would make the "
            "answer unnecessarily broad; one question is cheaper than a padded "
            "report"
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


# ---------------------------------------------------------------------------
# Evidence balance across readings
# ---------------------------------------------------------------------------

# Below this many facts, a reading's evidence is too thin to answer it.
THIN_READING_EVIDENCE = 1
# A competing reading needs at least this much MORE evidence than the chosen one
# before it is worth surfacing as an alternative.
ALTERNATIVE_EVIDENCE_RATIO = 2.0


@dataclass
class ReadingEvidence:
    """How much evidence each reading actually gathered."""

    chosen: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    chosen_count: int = 0
    alternative: str = ""
    alternative_count: int = 0
    chosen_is_thin: bool = False
    alternative_is_better_evidenced: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chosen": self.chosen,
            "counts": dict(self.counts),
            "chosen_count": self.chosen_count,
            "alternative": self.alternative,
            "alternative_count": self.alternative_count,
            "chosen_is_thin": self.chosen_is_thin,
            "alternative_is_better_evidenced": self.alternative_is_better_evidenced,
        }


def _fact_reading(fact: Mapping[str, Any]) -> str:
    """Which reading a fact was gathered for, by its sense/interpretation tag."""
    for key in ("sense", "interpretation", "reading"):
        value = str(fact.get(key, "") or "").strip()
        if value:
            return value
    return ""


def assess_reading_evidence(
    facts: Sequence[Mapping[str, Any]] | None,
    policy: Mapping[str, Any] | None,
) -> ReadingEvidence:
    """Compare evidence between the chosen reading and the others.

    This exists to PROTECT the chosen reading. The failure it prevents is
    evidence availability quietly redefining the question: the run picks a
    reading, that reading turns out to be hard to evidence, a different reading
    is easier to find, and the answer silently becomes about the easier one. The
    meaning of a question is decided before research, not by the search results.

    So the assessment never changes the choice. It reports two facts the writer
    needs:
      * the chosen reading's evidence is thin — say so as an evidence gap;
      * a competing reading is substantially better evidenced — it may be
        offered, but ONLY as an explicitly labelled alternative, never as the
        answer to the question that was asked.
    """
    policy = policy if isinstance(policy, Mapping) else {}
    chosen = str(policy.get("assumption", "") or "").strip()
    labels = [
        str(x).strip()
        for x in (policy.get("interpretations") or ())
        if str(x).strip()
    ]
    counts: Dict[str, int] = {label: 0 for label in labels}
    untagged = 0
    for fact in facts or ():
        if not isinstance(fact, Mapping):
            continue
        reading = _fact_reading(fact)
        if reading and reading in counts:
            counts[reading] += 1
        elif reading:
            counts[reading] = counts.get(reading, 0) + 1
        else:
            untagged += 1

    chosen_count = counts.get(chosen, 0)
    # 2.0 means "the search ran and this run gathered facts"; verified facts are
    # preferred by the caller, so the count is a reasonable proxy for evidence.
    alternative, alternative_count = "", 0
    for label, count in counts.items():
        if label == chosen:
            continue
        if count > alternative_count:
            alternative, alternative_count = label, count

    better = bool(
        alternative
        and alternative_count > 0
        and alternative_count >= max(
            1, int(chosen_count * ALTERNATIVE_EVIDENCE_RATIO)
        )
        and alternative_count > chosen_count
    )
    # Tagged evidence is what makes this assessment meaningful. When NO fact
    # carries a reading tag the pool simply was not sense-labelled — that is a
    # limitation of the labelling, not evidence that the chosen reading is thin,
    # and reporting it as thin would tell the writer to hedge on good evidence.
    tagged_total = sum(counts.values())
    return ReadingEvidence(
        chosen=chosen,
        counts=counts,
        chosen_count=chosen_count,
        alternative=alternative,
        alternative_count=alternative_count,
        chosen_is_thin=(
            bool(labels)
            and chosen_count <= THIN_READING_EVIDENCE
            and (tagged_total > 0 or not facts)
        ),
        alternative_is_better_evidenced=better,
    )


def evidence_balance_guidance(evidence: ReadingEvidence) -> str:
    """Writer instruction when the chosen reading is short of evidence.

    Names no subject. It states the epistemic rule — report the gap, never
    silently switch — and describes how to offer a better-evidenced reading.
    """
    if not evidence.chosen:
        return ""
    parts: List[str] = []
    if evidence.chosen_is_thin:
        parts.append(
            f"The chosen reading ('{evidence.chosen}') has little or no evidence "
            "in this run. REPORT THAT AS AN EVIDENCE GAP for the reading that was "
            "asked about. Do NOT quietly answer under a different reading just "
            "because it has more evidence — changing the meaning of the question "
            "to match the search results is a defect, not a result."
        )
    if evidence.alternative_is_better_evidenced and evidence.alternative:
        parts.append(
            f"The reading '{evidence.alternative}' has substantially more "
            "evidence. You MAY present it, but ONLY in a clearly labelled "
            f"separate subsection as an alternative reading of the question, "
            "with one sentence saying it is not the reading being answered. "
            "Never merge its evidence into the main answer and never let it "
            "become the report's subject."
        )
    return "\n".join(parts)
