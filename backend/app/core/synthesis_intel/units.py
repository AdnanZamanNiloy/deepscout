from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from app.core.logging import get_logger

from app.core.synthesis_intel.constants import (
    ANCHOR_OVERLAP_MIN,
    ANCHOR_SHARED_MIN,
    RESTATEMENT_SIMILARITY,
    _AUXILIARY_VERBS,
    _FRAGMENT_RE,
    _HEADING_RE,
    _LABEL_DELIM_RE,
    _MIN_CONTENT_TOKENS,
    _SENTENCE_SPLIT_RE,
    _TRANSITION_MARKERS,
)
from app.core.synthesis_intel.tokens import (
    anchors,
    canonical_tokens,
)


logger = get_logger(__name__)


def _has_finite_verb(text: str) -> bool:
    """Conservative test for a finite (tensed) verb in a LABEL HEAD.

    Only copulas/auxiliaries/modals count. A label/value head is a bare noun
    phrase ("frontier performance", "risk scores", "survey base"); a real
    sentence's head carries a tensed form ("the choice is subtractive"). Being
    strict here is correct: a head with no auxiliary is a label, and refining a
    label is exactly the corruption this guard prevents. A genuine sentence
    without a colon never reaches this check.
    """
    return any(
        word.lower() in _AUXILIARY_VERBS
        for word in re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text or "")
    )


def _is_already_refined(sentence: str) -> bool:
    """True when the sentence already carries a refinement transition.

    Checks the first twelve words only: the transition opens the sentence, so a
    genuine prose sentence that happens to contain similar words later is not
    mistaken for an already-refined line. This is the no-double-append guard.
    """
    head = " ".join((sentence or "").split()[:12]).lower()
    if not head:
        return False
    return any(marker.lower() in head for marker in _TRANSITION_MARKERS)


def _is_label_bullet(sentence: str) -> bool:
    """True when the line is a label/value or title fragment, not prose.

    Rejects a colon- or em-dash-delimited label whose head carries no finite
    verb ("Frontier performance: 60% …", "Survey base: 7,000 firms") and a very
    short unit that cannot carry a full clause. A real sentence with a colon
    ("The choice is subtractive: …") has a finite verb in its head and passes.
    """
    stripped = (sentence or "").strip()
    if not stripped:
        return False
    # A bold-only label line, or a value line led by a number/bullet glyph.
    if _FRAGMENT_RE.match(stripped):
        return True
    # Too few content words to be a full prose sentence.
    if len(stripped.split()) < 6:
        return True
    delim = _LABEL_DELIM_RE.search(stripped)
    if delim is not None:
        head = stripped[: delim.start()].strip()
        # The label lives before the first delimiter; if there is no finite verb
        # there, the line is a label/value fragment.
        if head and not _has_finite_verb(head):
            return True
    return False


def _is_refinable_sentence(sentence: str, *, is_bullet: bool = False) -> bool:
    """True when `sentence` is a full prose sentence worth transforming.

    Only genuine PROSE restatements qualify. An enumerable bullet — even when
    its value happens to be a full sentence, as in Key Figures ("The first mRNA
    vaccines … [6].") — is a data item, not an argument restatement, so it is
    left intact. Title/value fragments, short units, and lines that already
    carry a move clause are likewise left intact: refining them splices an
    analytical clause onto a label and corrupts the report (the live
    Key-Figures boilerplate failure this guard exists for).
    """
    stripped = (sentence or "").strip()
    if not stripped:
        return False
    if is_bullet:
        return False
    if _FRAGMENT_RE.match(stripped):
        return False
    if not stripped[-1:] in ".!?":
        return False
    if len(stripped.split()) < 6:
        return False
    if _is_already_refined(stripped):
        return False
    if _is_label_bullet(stripped):
        return False
    return True


def _is_sentence_unit(line: str) -> bool:
    stripped = line.strip()
    if not stripped or _HEADING_RE.match(stripped):
        return False
    return bool(re.search(r"[A-Za-z]", stripped))


def split_report_sections(answer: str) -> List[Tuple[str, List[str]]]:
    """Split a markdown report into (heading, body-lines) in order.

    The preamble before the first heading is returned under the "" heading.
    Lines are preserved as-is (bullets included) so reassembly is lossless
    apart from the sentences this layer deliberately removes.
    """
    sections: List[Tuple[str, List[str]]] = []
    current_heading = ""
    current_lines: List[str] = []
    for line in (answer or "").replace("\r\n", "\n").split("\n"):
        if _HEADING_RE.match(line):
            if current_heading or current_lines:
                sections.append((current_heading, current_lines))
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections.append((current_heading, current_lines))
    return sections


def _iter_units(lines: Sequence[str]) -> Iterable[Tuple[int, str]]:
    """Yield (line_index, sentence) for claim-bearing sentences in `lines`."""
    for index, line in enumerate(lines):
        if not _is_sentence_unit(line):
            continue
        stripped = line.strip().lstrip("-* ").strip()
        for sentence in _SENTENCE_SPLIT_RE.split(stripped):
            sentence = sentence.strip()
            if sentence and re.search(r"[A-Za-z]", sentence):
                yield index, sentence


def _similar_to_any(candidate: str, prior_texts: Sequence[str]) -> bool:
    """Same claim expressed in different words (near-duplicate restatement)."""
    if not candidate or not prior_texts:
        return False
    try:
        from app.core.semantic import pair_similarity

        return any(pair_similarity(candidate, prior) >= RESTATEMENT_SIMILARITY for prior in prior_texts)
    except Exception as exc:  # noqa: BLE001 - never let similarity break assembly
        logger.warning("[SynthesisIntel] similarity check failed", exc_info=exc)
        return False


def _fuzzy_restatement(candidate: str, prior: str) -> bool:
    """True when `candidate` restates the same fact as `prior`.

    Two independent signals, both required:
      * anchors — at least `ANCHOR_SHARED_MIN` distinctive tokens in common
        (the shared subject/figure), and
      * containment — the SHORTER sentence's content tokens are at least
        `ANCHOR_OVERLAP_MIN` contained in the longer one's. An expansion
        (same fact + a new causal clause) is a superset, so containment is
        evaluated in whichever direction makes the shorter text the numerator;
        a sentence that merely borrows a name but asserts something else does
        not match.
    """
    cand_tokens = set(canonical_tokens(candidate))
    prior_tokens = set(canonical_tokens(prior))
    if len(cand_tokens) < _MIN_CONTENT_TOKENS or len(prior_tokens) < _MIN_CONTENT_TOKENS:
        return False
    shared_anchors = anchors(candidate) & anchors(prior)
    if len(shared_anchors) < ANCHOR_SHARED_MIN:
        return False
    smaller, larger = (cand_tokens, prior_tokens) if len(cand_tokens) <= len(prior_tokens) else (prior_tokens, cand_tokens)
    containment = len(smaller & larger) / max(1, len(smaller))
    return containment >= ANCHOR_OVERLAP_MIN
