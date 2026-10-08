from __future__ import annotations

from typing import Dict, Optional, Sequence

from app.core.synthesis_intel.constants import (
    MAX_APPENDED_WORDS,
    _ANALYSIS_BY_MOVE,
    _CAUSAL_AXIS_RE,
    _CAUSAL_CLAIM_RE,
    _CAUSAL_QUERY_RE,
    _CITATION_RE,
    _COMPARISON_AXIS_RE,
    _COMPARISON_QUERY_RE,
    _COMPARISON_RE,
    _DECISION_QUERY_RE,
    _MECHANISM_AXIS_RE,
    _MECHANISM_CLAIM_RE,
    _MECHANISM_QUERY_RE,
    _MOVE_RULES,
    _QUANT_RE,
    _STRATEGIC_AXIS_RE,
    _TOPIC_ANALYSIS,
    _TRANSITION_BY_MOVE,
)


def _analysis_clause(sentence: str, move: str) -> str:
    """The analytical tail for a refined sentence, scoped to its topic and move.

    Picks the first topic-specific variant whose subject vocabulary appears in
    the sentence. There is deliberately NO generic fallback: a move clause that
    is not scoped to the claim's own topic reads as topic-agnostic boilerplate
    ("the underlying mechanism is that the earlier conditions compound…" stapled
    onto an mRNA-delivery sentence). When no topic-specific variant applies the
    empty string is returned, and the caller leaves the sentence unchanged.

    The tail's REASONING SHAPE (mechanism, trade-off, uncertainty, …) still
    matches the move the signals selected; only the topic scoping changed.
    """
    move = move if move in _ANALYSIS_BY_MOVE else "implication"
    for pattern, variants in _TOPIC_ANALYSIS:
        if pattern.search(sentence or ""):
            clause = variants.get(move)
            if clause:
                return clause
    return ""


def _stable_pick(options: Sequence[str], seed: str) -> str:
    """Deterministically choose one template for a claim.

    No RNG: the choice is derived from the claim key so the same repetition
    always refines the same way (stable tests, stable diffs) while different
    claims receive different transitions (a report does not read as one
    boilerplate line repeated). Falls back to the first option on empty input.
    """
    if not options:
        return ""
    for char in seed or "":
        if char.isalnum():
            return options[sum(ord(c) for c in seed) % len(options)]
    return options[0]


def _is_comparative(sentence: str, sig: Dict[str, object]) -> bool:
    return bool(_COMPARISON_RE.search(sentence or "")) or bool(
        _COMPARISON_RE.search(_axis_text(sig))
    ) or bool(_COMPARISON_QUERY_RE.search(_query_text(sig)))


def _axis_text(sig: Dict[str, object]) -> str:
    return str(sig.get("axis", "") or "").strip()


def _query_text(sig: Dict[str, object]) -> str:
    return str(sig.get("query", "") or sig.get("query_text", "") or "").strip()


def _is_authoritative(sig: Dict[str, object]) -> bool:
    return bool(
        sig.get("authoritative")
        or sig.get("corroborated")
        or sig.get("primary")
        or sig.get("primary_source")
    )


def _choose_move(sentence: str, signals: Optional[Dict[str, object]]) -> str:
    """Choose the adaptive reasoning move for a refined restatement.

    Reads only signals that are actually available on the evidence record /
    report context — contradiction count, source authority, query intent, and
    the section axis — plus the claim's own shape. The precedence in
    `_MOVE_RULES` is fixed and ordered, so the same claim in the same context
    always receives the same move (no RNG, no hash, no LLM).

    Signals consumed (all optional):
      contradicted / uncertain  -> the claim is disputed
      comparative / comparison  -> claim or axis or query is a comparison
      authoritative / primary / corroborated -> stronger provenance
      query / query_text, query_type          -> classified intent
      axis                                     -> section heading
    """
    sig = signals or {}
    sentence = sentence or ""
    contradicted = bool(sig.get("contradicted") or sig.get("uncertain"))
    axis = _axis_text(sig)
    query = _query_text(sig)
    query_type = str(sig.get("query_type", "") or "").strip().lower()
    comparative = _is_comparative(sentence, sig)
    # Strip citation markers before reading a quantity: "[1]" is a source
    # pointer, not a figure, and treating it as one made every cited sentence
    # look quantitative.
    quant = bool(_QUANT_RE.search(_CITATION_RE.sub(" ", sentence)))
    authoritative = _is_authoritative(sig)

    is_causal_query = bool(_CAUSAL_QUERY_RE.search(query)) or query_type in ("causal", "analytical")
    is_decision_query = bool(_DECISION_QUERY_RE.search(query)) or bool(sig.get("decision_query"))
    is_comparison_query = query_type == "comparative" or bool(_COMPARISON_QUERY_RE.search(query))
    is_mechanism_query = bool(_MECHANISM_QUERY_RE.search(query)) or query_type == "exploratory"

    for rule, move in _MOVE_RULES:
        if rule == "contradicted_comparative" and contradicted and comparative:
            return move
        if rule == "contradicted" and contradicted:
            return move
        if rule == "decision_query" and is_decision_query:
            return move
        if rule == "comparison_query" and is_comparison_query:
            return move
        if rule == "causal_query" and is_causal_query:
            return move
        if rule == "mechanism_query" and is_mechanism_query:
            return move
        if rule == "comparison_axis" and _COMPARISON_AXIS_RE.search(axis):
            return move
        if rule == "mechanism_axis" and _MECHANISM_AXIS_RE.search(axis):
            return move
        if rule == "causal_axis" and _CAUSAL_AXIS_RE.search(axis):
            return move
        if rule == "strategic_axis" and _STRATEGIC_AXIS_RE.search(axis):
            return move
        if rule == "causal_claim" and _CAUSAL_CLAIM_RE.search(sentence):
            return move
        if rule == "mechanism_claim" and _MECHANISM_CLAIM_RE.search(sentence):
            return move
        if rule == "comparative_claim" and comparative:
            return move
        if rule == "quantitative_authoritative" and quant and authoritative:
            return move
        if rule == "quantitative" and quant:
            return move
        if rule == "authoritative" and authoritative:
            return move
        if rule == "default":
            return move
    return "implication"


def refine_restatement(
    sentence: str,
    *,
    dimension: str = "implication",
    move: str = "",
    seed: str = "",
) -> str:
    """Rewrite a bare restatement into a contextual transition + analysis.

    The original claim is preserved verbatim — including its number and `[n]`
    marker — so nothing is lost and no citation is invented; the transition
    names the relationship to the earlier use and the move-specific analytical
    clause adds the reasoning the restatement lacked. `move` (preferred) or the
    legacy `dimension` names the reasoning relation; the phrase is chosen to
    fit it. Deterministic: no LLM is ever needed.
    """
    clean = (sentence or "").strip()
    if not clean:
        return clean
    chosen = move or dimension or "implication"
    chosen = chosen if chosen in _ANALYSIS_BY_MOVE else "implication"
    analysis = _analysis_clause(clean, chosen)
    if not analysis:
        # No topic-specific clause applies to this claim. The adaptive layer
        # only ever transforms a sentence when it has a concrete, topic-scoped
        # move for it; a generic topic-agnostic tail is exactly the boilerplate
        # corruption this module must not introduce (AGENTS.md: adaptive-moves
        # boilerplate corruption). Leave the sentence byte-for-byte unchanged.
        return clean
    transition = _stable_pick(_TRANSITION_BY_MOVE[chosen], seed or clean)
    appended = f"{transition} {analysis}"
    if len(appended.split()) > MAX_APPENDED_WORDS:
        # Defensive bound; current clauses are all well under it, but a future
        # template must not silently inflate every refined sentence. Trim the
        # analysis (never the transition, which carries the discourse link).
        keep = max(0, MAX_APPENDED_WORDS - len(transition.split()))
        analysis = " ".join(analysis.split()[:keep]).rstrip(",;:") if keep else ""
    # The original claim keeps its terminal punctuation before the appended
    # clause, so "… $13 billion [1]" becomes "… $13 billion [1], and the figure
    # is significant because …" rather than two spliced sentences.
    stripped = clean.rstrip()
    if stripped.endswith("."):
        stripped = stripped[:-1]
    if analysis:
        return f"{transition} {stripped}, and {analysis}."
    return f"{transition} {stripped}."
