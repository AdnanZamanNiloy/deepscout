"""Evidence-based answer construction: the middle case between "answered" and "cannot determine".

The pipeline had two reachable outcomes. Either a source answered the question
and the writer reported it, or the convergence diagnosis established that the
required evidence does not exist and the writer refused. In between sat the case
that actually happens most often: **no single source answers the question, but
several independent pieces of evidence cover its dimensions well enough to
construct a defensible answer.**

That case was being routed to a refusal. `convergence.py` detects "no source
ranks or compares these candidates" — which is true, and is NOT the same claim
as "no useful answer can be built". This module separates them:

  DIRECT       a source (or a directly comparable set of sources) answers the
               question, so report it and cite it.

  SYNTHESIZED  nothing answers it directly, but the evidence covers the
               relevant dimensions independently and consistently enough to
               build an answer — which must then be LABELLED as a synthesis and
               must never be dressed up as a published ranking.

  INSUFFICIENT the evidence is too fragmented, narrow, contradictory or
               off-topic to build anything. Say what cannot be determined and
               name the specific missing evidence.

Design constraints, all of them load-bearing:

* **Pure and total.** No LLM, no I/O, never raises. Same contract as
  `convergence`, `ranking_basis` and `report_consistency`, and for the same
  reason: this decision must be reproducible from measured state, not from a
  model's opinion of its own evidence.
* **It cannot upgrade what the existing gates refused.** Degradation, the
  convergence diagnosis, the ranking basis and the budget walls all stay
  authoritative. A degraded run in particular can never enter SYNTHESIZED —
  when the extraction itself was degenerate, the pool cannot attest that the
  evidence was READ correctly, which is precisely what a synthesis claims.
* **It never manufactures a ordering.** `allowed_ranking` is true only when the
  ranking basis found a real comparison. SYNTHESIZED always carries
  `allowed_ranking=False`; several candidates that cannot be distinguished come
  back as a supported cluster, never as a ranking.
* **Every threshold is a named constant with its rationale**, and the ones that
  already exist elsewhere (`MIN_FACTS_REQUIRED`, `MIN_DISTINCT_DOMAINS`) are
  imported rather than re-invented, so this layer cannot drift from the critic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from app.agents.critic import DEFINITIONAL_QUERY_RE, MIN_DISTINCT_DOMAINS, MIN_FACTS_REQUIRED
from app.agents.planner import dimension_to_axis

# ---------------------------------------------------------------------------
# Answer states
# ---------------------------------------------------------------------------

ANSWER_DIRECT = "direct"
ANSWER_SYNTHESIZED = "synthesized"
# PARTIAL: a material part of the question IS supported, but not enough to carry
# a full answer. The prompt names this case ("lead with that part and name the
# gap in one clause"), so the layer needs it too — otherwise a genuinely partial
# result is forced into SYNTHESIZED (claiming more coverage than exists) or into
# INSUFFICIENT (throwing away support the evidence does provide).
ANSWER_PARTIAL = "partial"
ANSWER_INSUFFICIENT = "insufficient"

# Inference levels. How far the delivered answer stands from a source that
# states it outright. Surfaced so the writer (and the audit) can calibrate
# language instead of treating every supported answer as equally direct.
INFERENCE_DIRECT = "direct"              # a source states the answer
INFERENCE_CROSS_DIMENSION = "cross_dimension"  # assembled from independent dimensions
INFERENCE_PARTIAL = "partial"            # a supported part, and a named gap
INFERENCE_NONE = "none"                  # nothing to stand on

# --- thresholds ------------------------------------------------------------
#
# A synthesis must rest on INDEPENDENT dimensions, not on volume. Every number
# here exists to stop one specific failure:
#
#   MIN_SYNTHESIS_DIMENSIONS: one well-evidenced dimension is a partial answer,
#     not a synthesis. Two independent dimensions is the same floor the critic
#     uses for source diversity, applied to the question's own structure.
#   MIN_SYNTHESIS_CORROBORATED: at least one claim corroborated across
#     publishers, so the synthesis is not a chain of single-source assertions.
#   MAX_SINGLE_DOCUMENT_SHARE: no single document may carry the synthesis. A
#     long report on one case is one study however many claims were extracted
#     from it.
#
# Independent-source count reuses the critic's MIN_DISTINCT_DOMAINS rather than
# declaring a second constant for the same number: two answers to "how many
# distinct publishers are enough" is how the two gates would drift apart.
MIN_SYNTHESIS_DIMENSIONS = 2
MIN_SYNTHESIS_CORROBORATED = 1

# The PARTIAL floor. Below the synthesis bar but above a refusal: at least one
# planned dimension the evidence actually speaks to. Deliberately low, because
# this tier's whole job is to report support that EXISTS rather than discard it
# — while still requiring a covered dimension, so a pool of off-topic claims
# cannot be dressed up as a partial answer.
MIN_PARTIAL_DIMENSIONS = 1
MAX_SINGLE_DOCUMENT_SHARE = 0.6

# `dimension_to_axis` maps empty or unmappable text to this, so an UNTAGGED fact
# would otherwise be credited with covering a dimension called "general" — and
# could satisfy MIN_SYNTHESIS_DIMENSIONS on its own. It is the absence of a
# dimension, not one.
_GENERIC_AXIS = "general"

# How many items the contract carries. Bounded because this text is prepended to
# every section prompt (AGENTS.md 5: the 8GB host pays for every token, and the
# prompt is duplicated per section).
MAX_CONTRACT_DIMENSIONS = 8
MAX_CONTRACT_EVIDENCE = 6
MAX_CONTRACT_CLAIMS = 6
MAX_CONTRACT_CONTRADICTIONS = 4

# Query shapes the answer must match. Same regex the critic uses for its
# definitional gate, so the two cannot disagree about what "definitional" means.
_RANKING_RE = re.compile(
    r"\b(most|best|highest|worst|largest|biggest|smallest|least|greatest|top|"
    r"leading|strongest|weakest|number one|#1)\b",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _facts(pool: Sequence[Mapping[str, Any]] | None) -> List[Dict[str, Any]]:
    return [f for f in (pool or ()) if isinstance(f, Mapping)]


def _verified(pool: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    verified = [f for f in pool if f.get("verified") is True]
    # `verified` is a tri-state: None means "not checked", which is not the same
    # as checked-and-failed. An unchecked claim may support a synthesis; a
    # checked-and-failed one must not.
    return verified if verified else [f for f in pool if f.get("verified") is not False]


def _document(url: str) -> str:
    return re.sub(r"[#?].*$", "", _text(url)).rstrip("/")


def asks_for_ranking(query: str) -> bool:
    """Does the question ask for a single winner / an ordering?"""
    return bool(_RANKING_RE.search(query or ""))


def asks_for_definition(query: str) -> bool:
    return bool(DEFINITIONAL_QUERY_RE.match(query or ""))


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass
class AnswerConstruction:
    """What the evidence can support, and how the writer must present it."""

    mode: str = ANSWER_INSUFFICIENT
    reason: str = ""

    # Dimensions the evidence actually covers, and the planned ones it misses.
    # Canonical axis names (planner.dimension_to_axis), so these line up with
    # the critic's coverage terms and the synthesizer's section titles.
    supported_dimensions: List[str] = field(default_factory=list)
    missing_dimensions: List[str] = field(default_factory=list)

    # Who/what the covered dimensions converge on. Never a ranking.
    candidate_claims: List[str] = field(default_factory=list)
    # Bounded per-dimension evidence the writer combines. Machine-owned.
    supporting_evidence: List[Dict[str, Any]] = field(default_factory=list)
    contradictions: List[str] = field(default_factory=list)

    inference_level: str = INFERENCE_NONE
    # True ONLY when a real comparison exists (ranking basis = RANKED).
    allowed_ranking: bool = False
    # A shortlist/cluster is legitimate where a winner is not.
    allows_cluster: bool = False
    # The user's locked reading, carried verbatim so construction cannot drift.
    locked_interpretation: str = ""
    # Set when the layer refused to synthesise because the run was degraded.
    blocked_by_degradation: bool = False

    @property
    def is_synthesis(self) -> bool:
        return self.mode == ANSWER_SYNTHESIZED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "supported_dimensions": list(self.supported_dimensions),
            "missing_dimensions": list(self.missing_dimensions),
            "candidate_claims": list(self.candidate_claims),
            "supporting_evidence": list(self.supporting_evidence),
            "contradictions": list(self.contradictions),
            "inference_level": self.inference_level,
            "allowed_ranking": self.allowed_ranking,
            "allows_cluster": self.allows_cluster,
            "locked_interpretation": self.locked_interpretation,
            "blocked_by_degradation": self.blocked_by_degradation,
            "dimensions_covered": len(self.supported_dimensions),
        }


def _locked_interpretation(definition_lock: Mapping[str, Any] | None) -> str:
    """The user's fixed reading, as a single line the writer must preserve."""
    if not isinstance(definition_lock, Mapping):
        return ""
    definition = _text(definition_lock.get("definition"))
    term = _text(definition_lock.get("term"))
    if definition and term:
        return f"{term} = {definition}"
    return definition or term


def _dimensions(pool: Sequence[Mapping[str, Any]], plan: Sequence[Mapping[str, Any]] | None) -> tuple[List[str], List[str]]:
    """(covered, missing) canonical axes.

    `supported` is read from the evidence's own axis stamp — an axis counts as
    covered only when a fact was actually attributed to it, which is the same
    rule the critic's coverage gate applies. `missing` is the planned axes with
    no such fact, so the writer can name the gap instead of padding it.
    """
    covered: List[str] = []
    for fact in pool:
        axis = dimension_to_axis(
            _text(fact.get("sub_question")) or _text(fact.get("axis")),
            _text(fact.get("search_type")),
        )
        axis = (axis or "").strip()
        # An untagged fact normalises to _GENERIC_AXIS, which is the ABSENCE of a
        # dimension, not one — otherwise it would count as coverage on its own.
        if axis and axis != _GENERIC_AXIS and axis not in covered:
            covered.append(axis)

    planned: List[str] = []
    for item in plan or ():
        if not isinstance(item, Mapping):
            continue
        axis = dimension_to_axis(
            _text(item.get("axis")) or _text(item.get("question")),
            _text(item.get("search_type")),
        )
        axis = (axis or "").strip()
        if axis and axis != _GENERIC_AXIS and axis not in planned:
            planned.append(axis)

    missing = [a for a in planned if a not in covered]
    return covered, missing


def _single_document_share(pool: Sequence[Mapping[str, Any]]) -> float:
    if not pool:
        return 1.0
    counts: Dict[str, int] = {}
    for fact in pool:
        doc = _document(_text(fact.get("source")))
        if doc:
            counts[doc] = counts.get(doc, 0) + 1
    if not counts:
        return 1.0
    return max(counts.values()) / float(len(pool))


def _top_evidence(
    pool: Sequence[Mapping[str, Any]], *, limit: int
) -> List[Dict[str, Any]]:
    """The most defensible facts, best first, one per source document.

    Ordered by verification then confidence, and deduped by document so the
    contract cannot hand the writer six claims from the same page and imply six
    independent findings.
    """
    ranked = sorted(
        pool,
        key=lambda f: (
            1 if f.get("verified") is True else 0,
            int(f.get("corroboration_count", 1) or 1),
            float(f.get("confidence", 0.0) or 0.0),
        ),
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    seen_docs: set[str] = set()
    for fact in ranked:
        doc = _document(_text(fact.get("source")))
        if doc and doc in seen_docs:
            continue
        if doc:
            seen_docs.add(doc)
        out.append({
            "claim": _text(fact.get("claim"))[:400],
            "source": _text(fact.get("source")),
            "axis": dimension_to_axis(_text(fact.get("sub_question"))),
            "verified": fact.get("verified") is True,
            "confidence": round(float(fact.get("confidence", 0.0) or 0.0), 3),
        })
        if len(out) >= limit:
            break
    return out


def _contradiction_lines(contradictions: Sequence[Mapping[str, Any]] | None) -> List[str]:
    lines: List[str] = []
    for item in contradictions or ():
        if not isinstance(item, Mapping) or item.get("resolved"):
            continue
        a = _text(item.get("claim_a") or item.get("a"))[:160]
        b = _text(item.get("claim_b") or item.get("b"))[:160]
        if a and b:
            lines.append(f"{a} ⟷ {b}")
    return lines[:MAX_CONTRACT_CONTRADICTIONS]


def _basis_verdict(ranking_basis: Mapping[str, Any] | None) -> str:
    if not isinstance(ranking_basis, Mapping):
        return ""
    nested = ranking_basis.get("basis")
    if isinstance(nested, Mapping) and not ranking_basis.get("verdict"):
        return _text(nested.get("verdict"))
    return _text(ranking_basis.get("verdict"))


def _basis_candidates(ranking_basis: Mapping[str, Any] | None) -> List[str]:
    """Named options the evidence treats as examples, from the ranking basis."""
    if not isinstance(ranking_basis, Mapping):
        return []
    source: Any = ranking_basis
    nested = ranking_basis.get("basis")
    if isinstance(nested, Mapping) and not ranking_basis.get("candidates"):
        source = nested
    raw = source.get("candidates") or ()
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: List[str] = []
    for item in raw:
        text = _text(item)
        if text and len(text) <= 80 and text.lower() not in {s.lower() for s in out}:
            out.append(text)
    return out[:MAX_CONTRACT_CLAIMS]


def _plausible_candidate(text: str) -> bool:
    """Is this string a named OPTION, or just a capitalised word?

    Candidates are the highest-stakes output here: naming the wrong one is the
    "manufactured #1" failure the whole layer exists to prevent. So the bar is
    structural rather than a blocklist.

    Accepted: anything the ranking-basis assessment supplied (it knows the
    question's own named options) — but that path does not come through this
    function. Here, where candidates are INFERRED from claim text, a candidate
    must look like a name: a multi-word phrase, or a token with internal
    capitalisation ("PostgreSQL", "MongoDB"). A bare capitalised common noun
    fails — claim text is full of them ("Claim", "Study", "Report"), each one
    recurring across the pool, so recurrence alone cannot separate a candidate
    from the sentence-initial word of a claim.
    """
    text = _text(text)
    if not (3 <= len(text) <= 80):
        return False
    words = [w for w in re.split(r"\s+", text) if w.strip()]
    if len(words) >= 2:
        return True
    # One token: a real single-word option carries internal capitalisation or a
    # digit ("3M", "GPT-4"). A sentence-initial capital does not qualify.
    return bool(re.search(r"[A-Z0-9]", text[1:]))


def _candidate_claims(
    pool: Sequence[Mapping[str, Any]],
    query: str,
    ranking_basis: Mapping[str, Any] | None,
) -> List[str]:
    """What the covered dimensions potentially converge on.

    Prefers names the ranking-basis assessment already extracted (it knows the
    question's own named options). Otherwise reads candidates from the claim
    text via `ranking_basis._named_candidates`, whose recurrence bar is what
    keeps a single passing mention from becoming a candidate — then filters
    those through `_plausible_candidate`, because recurrence alone still admits
    a repeated sentence-initial noun.
    """
    named = _basis_candidates(ranking_basis)
    if named:
        return named
    try:
        from app.agents.ranking_basis import _named_candidates

        inferred = [_text(c) for c in _named_candidates(pool, query)]
    except Exception:
        # Candidate extraction is best-effort: a synthesis with no named
        # candidates is still describable, it simply cannot point at one.
        return []
    out: List[str] = []
    for candidate in inferred:
        if _plausible_candidate(candidate) and candidate.lower() not in {c.lower() for c in out}:
            out.append(candidate)
    return out[:MAX_CONTRACT_CLAIMS]


def classify(
    query: str,
    *,
    facts: Sequence[Mapping[str, Any]] | None = None,
    plan: Sequence[Mapping[str, Any]] | None = None,
    ranking_basis: Mapping[str, Any] | None = None,
    convergence: Mapping[str, Any] | None = None,
    contradictions: Sequence[Mapping[str, Any]] | None = None,
    definition_lock: Mapping[str, Any] | None = None,
    degraded: bool = False,
) -> AnswerConstruction:
    """Classify the answer state. Pure, total, never raises.

    Order of authority. The existing gates stay authoritative, and this layer is
    built to be unable to contradict them:

    1. A CONVERGENCE diagnosis ("the required kind of evidence is absent")
       forbids DIRECT. It does not forbid SYNTHESIZED — that is the entire point
       of this layer: "no source states the answer" and "no answer can be built"
       are different findings, and convergence only establishes the first. It
       does match `report_consistency.report_status`, which lets convergence
       outrank the ranking basis, so the two cannot disagree about whether a
       published answer exists.
    2. Degradation blocks SYNTHESIZED outright (see the module docstring).
    3. Otherwise the evidence shape decides between SYNTHESIZED and
       INSUFFICIENT.
    """
    query = _text(query)
    pool = _facts(facts)
    verified = _verified(pool)
    covered, missing = _dimensions(verified, plan)
    contradiction_lines = _contradiction_lines(contradictions)
    locked = _locked_interpretation(definition_lock)
    verdict = _basis_verdict(ranking_basis)
    candidates = _candidate_claims(verified, query, ranking_basis)
    # Convergence wins over the ranking basis, matching report_consistency: a
    # loop that concluded the evidence cannot answer must not be overridden by a
    # basis assessment that saw a comparison. DIRECT is therefore unavailable,
    # and the run is either a labelled synthesis or a refusal.
    converged_on_gap = bool(
        isinstance(convergence, Mapping) and convergence.get("identified")
    )

    base = dict(
        supported_dimensions=covered[:MAX_CONTRACT_DIMENSIONS],
        missing_dimensions=missing[:MAX_CONTRACT_DIMENSIONS],
        candidate_claims=candidates,
        supporting_evidence=_top_evidence(verified, limit=MAX_CONTRACT_EVIDENCE),
        contradictions=contradiction_lines,
        locked_interpretation=locked,
    )

    # --- 1. A source answered it ------------------------------------------
    if verdict == "ranked" and not converged_on_gap:
        return AnswerConstruction(
            mode=ANSWER_DIRECT,
            reason=(
                "the evidence contains an explicit comparison of the candidates, "
                "so the ordering is reported rather than inferred"
            ),
            inference_level=INFERENCE_DIRECT,
            allowed_ranking=True,
            **base,
        )

    has_definition = any(
        " is " in _text(f.get("claim")).lower() for f in verified[:6]
    )
    if not converged_on_gap and asks_for_definition(query) and has_definition and covered:
        return AnswerConstruction(
            mode=ANSWER_DIRECT,
            reason="a verified definitional claim answers this question directly",
            inference_level=INFERENCE_DIRECT,
            **base,
        )

    # A non-ranking question whose dimensions are all covered *is* answered by
    # the sources; nothing needs assembling.
    if (
        not converged_on_gap
        and not asks_for_ranking(query)
        and not asks_for_definition(query)
        and covered
        and not missing
        and len(verified) >= MIN_FACTS_REQUIRED
    ):
        return AnswerConstruction(
            mode=ANSWER_DIRECT,
            reason=(
                "every planned dimension is covered by verified evidence, so the "
                "answer is reported from the sources rather than inferred"
            ),
            inference_level=INFERENCE_DIRECT,
            **base,
        )

    # --- 2. At least one door the layer must not open ---------------------
    if degraded:
        return AnswerConstruction(
            mode=ANSWER_INSUFFICIENT,
            reason=(
                "the run was degraded: the evidence was produced by deterministic "
                "extraction, so an evidence-based synthesis cannot be attested."
            ),
            inference_level=INFERENCE_NONE,
            blocked_by_degradation=True,
            **base,
        )

    # --- 3. Can the evidence carry a synthesis? ---------------------------
    distinct_domains = len({_document(_text(f.get("source"))) for f in verified} - {""})
    corroborated = sum(
        1 for f in verified if int(f.get("corroboration_count", 1) or 1) > 1
    )
    doc_share = _single_document_share(verified)

    blockers: List[str] = []
    if len(covered) < MIN_SYNTHESIS_DIMENSIONS:
        blockers.append(
            f"only {len(covered)} dimension(s) covered (needs {MIN_SYNTHESIS_DIMENSIONS})"
        )
    if len(verified) < MIN_FACTS_REQUIRED:
        blockers.append(f"{len(verified)} usable claim(s) (needs {MIN_FACTS_REQUIRED})")
    if distinct_domains < MIN_DISTINCT_DOMAINS:
        blockers.append(
            f"{distinct_domains} independent source(s) (needs {MIN_DISTINCT_DOMAINS})"
        )
    if corroborated < MIN_SYNTHESIS_CORROBORATED:
        blockers.append("no claim is corroborated across publishers")
    if doc_share > MAX_SINGLE_DOCUMENT_SHARE:
        blockers.append(
            f"one document carries {doc_share:.0%} of the evidence "
            f"(max {MAX_SINGLE_DOCUMENT_SHARE:.0%})"
        )
    # A question asking for a winner needs something to point at. Without a
    # candidate the "synthesis" would be a general essay, not an answer.
    if asks_for_ranking(query) and not candidates:
        blockers.append("no candidates are named anywhere in the evidence")

    if blockers:
        # Below the synthesis bar is not automatically a refusal. If any planned
        # dimension is covered by a verified claim, the evidence DOES support
        # part of the question, and PARTIAL reports that part with its gap named
        # instead of discarding it. Without this tier the only alternatives were
        # to over-claim a synthesis or to answer nothing.
        if len(covered) >= MIN_PARTIAL_DIMENSIONS and verified:
            return AnswerConstruction(
                mode=ANSWER_PARTIAL,
                reason=(
                    "part of the question is supported but a full answer is not: "
                    + "; ".join(blockers)
                ),
                inference_level=INFERENCE_PARTIAL,
                allowed_ranking=False,
                allows_cluster=len(candidates) >= 2,
                **base,
            )
        return AnswerConstruction(
            mode=ANSWER_INSUFFICIENT,
            reason=(
                "the evidence cannot support a defensible answer: "
                + "; ".join(blockers)
            ),
            inference_level=INFERENCE_NONE,
            **base,
        )

    return AnswerConstruction(
        mode=ANSWER_SYNTHESIZED,
        reason=(
            f"{len(covered)} independent dimension(s) are each covered by verified "
            f"evidence across {distinct_domains} source(s); no source states the "
            "answer directly, so it is constructed and must be labelled as such"
        ),
        inference_level=INFERENCE_CROSS_DIMENSION,
        # Never a ranking: only the RANKED branch above sets this true.
        allowed_ranking=False,
        # Several candidates that cannot be distinguished are still a legitimate
        # supported cluster.
        allows_cluster=len(candidates) >= 2,
        **base,
    )


# ---------------------------------------------------------------------------
# The writer contract
# ---------------------------------------------------------------------------


def render_construction_contract(construction: AnswerConstruction) -> str:
    """The rule the final answer must obey for its answer mode."""
    if construction.mode == ANSWER_DIRECT:
        # A direct answer needs no construction instruction; every existing
        # contract (definition lock, consistency, convergence) already applies.
        return ""

    if construction.mode == ANSWER_PARTIAL:
        parts = [
            "ANSWER MODE: PARTIAL — part of the question is supported and the "
            "rest is not. Lead with the supported part, stated as an answer, "
            "then name the gap in ONE clause. Never present the supported part "
            "as the whole answer.",
            "Do not pad the gap, do not speculate into it, and do not restate "
            "the limitation more than once.",
        ]
        if construction.supported_dimensions:
            parts.append(
                "What IS supported: "
                + ", ".join(construction.supported_dimensions[:MAX_CONTRACT_DIMENSIONS])
                + "."
            )
        if construction.missing_dimensions:
            parts.append(
                "What is NOT supported (name it once): "
                + ", ".join(construction.missing_dimensions[:MAX_CONTRACT_DIMENSIONS])
                + "."
            )
        # Why this is partial rather than a full synthesis. The reader deciding
        # how far to trust the supported part needs the reason named.
        parts.append(
            f"Why it is partial and not a complete answer: {construction.reason}."
        )
        if construction.candidate_claims:
            listed = "; ".join(construction.candidate_claims[:MAX_CONTRACT_CLAIMS])
            parts.append(
                f"Strongest supported candidate(s): {listed}. State them as the "
                "best the evidence supports on the covered part, NOT as the "
                "answer a source gives to the whole question."
            )
        if construction.contradictions:
            parts.append(
                "Unresolved conflicts to report rather than resolve: "
                + " | ".join(construction.contradictions[:2])
            )
        if construction.locked_interpretation:
            parts.append(
                "PRESERVE THE LOCKED INTERPRETATION — the partial answer must "
                f"address the question as fixed before research: {construction.locked_interpretation}. "
                "Do not let the available evidence redefine it."
            )
        return "\n".join(parts)

    if construction.mode == ANSWER_INSUFFICIENT:
        parts = [
            "ANSWER MODE: INSUFFICIENT — say what cannot be determined. The "
            "evidence does not support a defensible answer to this question, and "
            "constructing one would be a guess.",
            "State plainly which part of the question is unanswerable, then name "
            "the SPECIFIC missing evidence that would settle it. Do not pad the gap "
            "with adjacent material.",
        ]
        blocked = " ".join(construction.contradictions[:2])
        if blocked:
            parts.append(f"Unresolved evidence conflicts: {blocked}")
        if construction.missing_dimensions:
            parts.append(
                "Evidence is absent for: "
                + ", ".join(construction.missing_dimensions[:MAX_CONTRACT_DIMENSIONS])
                + "."
            )
        if construction.supported_dimensions:
            parts.append(
                "What the evidence DOES support may be reported as an explicitly "
                "partial answer: "
                + ", ".join(construction.supported_dimensions[:MAX_CONTRACT_DIMENSIONS])
                + "."
            )
        if construction.blocked_by_degradation:
            parts.append(
                "This run was degraded, so an evidence-based synthesis is not "
                "available. Do not present extracted source text as a constructed "
                "answer."
            )
        return "\n".join(parts)

    # --- SYNTHESIZED -------------------------------------------------------
    dims = ", ".join(construction.supported_dimensions[:MAX_CONTRACT_DIMENSIONS])
    parts = [
        "ANSWER MODE: SYNTHESIZED — no source directly answers this question. "
        "The answer below is an EVIDENCE-BASED SYNTHESIS, and must be labelled as "
        "one in plain language the reader cannot miss.",
        f"Build it ONLY from these independently evidenced dimensions: {dims}. "
        "Every major inference must trace to one of them; an inference with no "
        "dimension behind it must be dropped, not softened.",
        "Say explicitly that no published source states this answer, and that it "
        "is assembled from the dimensions above.",
        "NEVER present the synthesis as a published ranking, a consensus, or an "
        "authoritative finding. It is the strongest reading the available evidence "
        "supports, and it must read that way.",
        "Do not turn one narrow source into a broad conclusion: each inference "
        "carries the weight of the dimension it came from and no more.",
        "A measure that correlates with what the question asked is NOT the thing "
        "asked for. An adjacent metric may support an inference; it may never be "
        "reported as the requested quantity.",
    ]
    if construction.allows_cluster:
        listed = "; ".join(construction.candidate_claims[:MAX_CONTRACT_CLAIMS])
        parts.append(
            f"Several supported candidates are present ({listed}) and the evidence "
            "does not distinguish them. Present them as a SUPPORTED CLUSTER, in no "
            "order, unless the evidence itself orders them — never imply a winner."
        )
    elif construction.candidate_claims:
        listed = "; ".join(construction.candidate_claims[:MAX_CONTRACT_CLAIMS])
        parts.append(
            f"Strongest supported candidate(s): {listed}. State the candidate as the "
            "strongest the evidence supports, NOT as the answer a source gives."
        )
    if construction.contradictions:
        parts.append(
            "Unresolved conflicts to report rather than resolve: "
            + " | ".join(construction.contradictions[:2])
        )
    if construction.locked_interpretation:
        parts.append(
            "PRESERVE THE LOCKED INTERPRETATION — the synthesis must answer the "
            f"question as fixed before research: {construction.locked_interpretation}. "
            "Do not let the available evidence redefine the question."
        )
    if construction.missing_dimensions:
        parts.append(
            "Name what is NOT covered so the reader can weigh the synthesis: "
            + ", ".join(construction.missing_dimensions[:MAX_CONTRACT_DIMENSIONS])
            + "."
        )
    return "\n".join(parts)


def _asserts_unbacked_ranking(text: str) -> bool:
    """Does `text` assert an ordering the evidence does not contain?

    Delegates to `report_consistency.ranking_language_without_basis` rather
    than re-testing superlatives here. That detector is the established one and
    uses WINDOW-based negation, so "the evidence establishes no ranking" and
    "no single #1" are correctly not violations, while "X is the strongest
    candidate" is. A second, cruder superlative check in this module flagged a
    merely descriptive "the highest measured burnout rate" as a ranking.
    """
    try:
        from app.agents.report_consistency import (
            STATUS_NO_NUMBER_ONE,
            ReportStatus,
            ranking_language_without_basis,
        )

        # A synthesis/partial mode is exactly the state in which the evidence
        # does not support an ordering, so it forbids one.
        status = ReportStatus(status=STATUS_NO_NUMBER_ONE, allowed_ranking=False)
        hits = ranking_language_without_basis(text, status)
    except Exception:
        return False
    if not hits:
        return False
    # A superlative carrying the contract's OWN hedging vocabulary is the
    # wording this layer asks for ("the strongest evidence-based candidate"),
    # not a ranking claim. report_consistency does not know that vocabulary, so
    # without this filter the audit would flag a report for obeying its
    # instructions. Only a superlative with no such hedge anywhere near it is a
    # violation.
    low = str(text or "").lower()
    # Deliberately NOT the bare word "candidate": "X is the strongest candidate
    # for 2027" is precisely the ranking claim this check exists to catch. Only
    # wording that frames the claim as an assembled reading qualifies.
    hedges = ("synthesis", "synthesised", "synthesized", "assembled",
              "evidence-based", "evidence based", "supported cluster",
              "not a ranking", "not a published ranking", "no single source")
    for hit in hits:
        start = low.find(hit)
        if start == -1:
            continue
        window = low[max(0, start - 60): start + len(hit) + 60]
        if any(hedge in window for hedge in hedges):
            continue
        return True
    return False


def assess_answer_construction(
    query: str,
    answer: str,
    construction: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Audit the delivered prose against the contract it was given.

    Observational, like the other audits: it never rewrites. It answers one
    question per mode — did the writer label a synthesis as a synthesis, and did
    it avoid asserting a ranking the evidence does not contain?
    """
    text = (answer or "")
    low = text.lower()
    mode = _text((construction or {}).get("mode"))
    out: Dict[str, Any] = {"mode": mode, "violations": []}
    if not mode or not text:
        return out

    if mode == ANSWER_SYNTHESIZED:
        labelled = any(
            marker in low
            for marker in (
                "synthesis", "synthesised", "synthesized", "no source directly",
                "no published source", "evidence-based", "evidence based",
                "assembled from", "combined from", "inference",
            )
        )
        out["labelled_as_synthesis"] = labelled
        if not labelled:
            out["violations"].append(
                "mode is SYNTHESIZED but the answer never labels itself as a "
                "synthesis or an inference"
            )
        ranked = _asserts_unbacked_ranking(text)
        out["asserts_ranking"] = ranked
        if ranked:
            out["violations"].append(
                "mode is SYNTHESIZED but the answer asserts a ranking the "
                "evidence does not contain"
            )

    elif mode == ANSWER_PARTIAL:
        # The two things a partial answer must do: say what IS supported, and
        # name that it is incomplete. Either alone is a failure — leading with
        # the gap hides the answer, and leading with the part without the gap
        # lets a partial answer pass as the whole.
        out["names_the_gap"] = any(
            marker in low
            for marker in ("does not", "not established", "no evidence", "cannot",
                           "insufficient", "not supported", "unanswered",
                           "remains unclear", "partial", "limited to")
        )
        if not out["names_the_gap"]:
            out["violations"].append(
                "mode is PARTIAL but the answer never says which part is "
                "unsupported"
            )
        out["asserts_ranking"] = _asserts_unbacked_ranking(text)
        if out["asserts_ranking"]:
            out["violations"].append(
                "mode is PARTIAL but the answer asserts a ranking the evidence "
                "does not contain"
            )

    elif mode == ANSWER_INSUFFICIENT:
        named_gap = any(
            marker in low
            for marker in ("cannot determine", "cannot be determined", "insufficient",
                           "not enough evidence", "no evidence", "cannot establish",
                           "unable to determine", "no source")
        )
        out["names_the_gap"] = named_gap
        if not named_gap:
            out["violations"].append(
                "mode is INSUFFICIENT but the answer does not state that the "
                "question cannot be determined"
            )
    return out
