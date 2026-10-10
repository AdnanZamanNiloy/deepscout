"""Query-anchored research focus: scope, coverage, concentration and drift.

THE PROBLEM THIS SOLVES
-----------------------
A research loop that only asks "is the evidence pile big enough?" drifts. It
keeps finding material that is genuinely interesting and quietly stops being
material the user asked for. The loop has no memory of the ORIGINAL question
once the planner has turned it into sub-questions, so nothing detects that
eight of the last ten searches were about one fascinating corner while the
question's other halves went unresearched.

This module is that memory, and it is the smallest place to put it: pure,
deterministic, no LLM, no I/O, so it is cheap enough to run on every pass.

FOUR QUESTIONS, ONE PASS
------------------------
  scope         What did the user actually ask for? Which dimensions are
                admissible, and which are merely adjacent?
  coverage      Which declared dimensions have real evidence behind them?
  concentration Is the evidence piled onto one dimension?
  drift         How far has the research travelled from the original question?

DESIGN RULE: NO DOMAIN TAXONOMY
--------------------------------
There is deliberately no list of AI / cybersecurity / finance / medical topics
anywhere in this module. A previous design carried one: six "frontier" axes
(capability, infrastructure, economics, adoption, regulation, safety) forced
onto EVERY query with literal AI search strings. Asked "population of Malawi in
2024" it produced six research contracts about model capability and GPU
supply chains, and the critic then blocked finalization until every one of them
had primary sources. The report was structurally incapable of answering the
question.

Dimensions here are whatever the plan declared for THIS query. Concentration and
drift are measured against the query's own terms, so a plan about medieval
poetry and a plan about datacenter load factors are described by identical code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.primitives import jaccard as _jaccard

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# A dimension needs this many verified facts to count as covered. Matches the
# planner's per-contract `minimum_sources` default and depth_controller's
# AXIS_MIN_FACTS, so the three views of "covered" agree.
DEFAULT_MIN_FACTS_PER_DIMENSION = 2

# Above this share of verified evidence on one dimension the run is
# "concentrated" and follow-ups should be redirected to what is missing.
# 0.60 because a focused answer to a narrow question legitimately has one
# dimension; a broad question answered from a single dimension is a defect.
DEFAULT_CONCENTRATION_LIMIT = 0.60

# Above this drift score the run is judged to have left the question. Drift is
# the fraction of evidence whose text shares almost nothing with the query, so
# this is deliberately high: a good answer legitimately includes material that
# does not restate the question verbatim (definitions, mechanism, caveats).
DEFAULT_DRIFT_LIMIT = 0.35

# Two follow-ups are near-duplicates above this Jaccard overlap on content
# tokens. Mirrors depth_controller's _similar_query (0.8) so "have we already
# searched this?" means the same thing in both places.
FOLLOWUP_DUPLICATE_LIMIT = 0.80

# A dimension is "authoritative" when this share of its evidence comes from a
# primary source. Below it, the dimension is researched but not grounded, which
# is a different problem from being uncovered.
DEFAULT_PRIMARY_SHARE = 0.34

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'&.-]*", re.UNICODE)

# Function words and interrogatives carry no subject. Excluded from the drift
# denominator so "what is the population of Malawi" is not judged on
# what/is/the/of.
_QUESTION_WORDS = frozenset({
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "compare", "comparison", "could", "did", "do", "does", "for",
    "from", "give", "has", "have", "how", "i", "if", "in", "into", "is", "it",
    "its", "list", "many", "much", "of", "on", "or", "our", "over", "please",
    "should", "so", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "those", "to", "us", "was", "were", "what",
    "when", "where", "which", "who", "whom", "whose", "why", "will", "with",
    "within", "would", "you", "your",
})

# Dimensions whose whole purpose is to argue against the rest of the report.
# Concentration on one of these is not drift, so they are exempt from the
# concentration rule. Expressed as AXIS NAMES (the planner's vocabulary), not
# topics, so it stays domain-agnostic.
ADVERSARIAL_AXES = frozenset({"counter_evidence", "criticism", "risk"})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _subject_tokens(text: str) -> frozenset[str]:
    """Content words of a question, for measuring how far evidence has strayed.

    Negatives are NOT stopwords (AGENTS.md: negation must never be a stopword
    in a similarity component). "countries without electricity access" must not
    read the same as "countries with electricity access".
    """
    return frozenset(
        w for w in _TOKEN_RE.findall((text or "").lower())
        if w not in _QUESTION_WORDS and len(w) > 1
    )


# Coordinators that join two SUBJECTS. "outcomes and costs" asks for two things;
# "sodium chloride and its role in nerve signalling" is still one subject, which
# is why the count is a signal rather than a verdict.
_CONJUNCTIONS = (
    " and ", " or ", " versus ", " vs ", " plus ", " along with ", " as well as ",
    " compared to ", " compared with ", " versus ", " relative to ", " against ",
)
# A bare "and" inside a compound noun is not a conjunction of subjects: these
# are the shapes where the word joins parts of ONE term.
_NON_CONJUNCTION_PREFIXES = (
    "research and development", "science and technology", "health and safety",
    "supply and demand", "research and innovation", "safety and security",
)


def _conjunction_parts(query: str) -> int:
    """How many separate subjects the question appears to ask about."""
    low = f" {(query or '').lower().strip()} "
    for phrase in _NON_CONJUNCTION_PREFIXES:
        low = low.replace(f" {phrase} ", " ")
    count = 1
    for token in _CONJUNCTIONS:
        count += low.count(token)
    return count


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass
class ResearchScope:
    """What the user's question admits, resolved before research starts.

    `dimensions` is the plan's OWN axis vocabulary for this query. It is the
    only authority on what counts as in-scope; there is no external taxonomy to
    satisfy. `terms` are the question's content words, used as the drift anchor
    and as the seed for generated follow-ups.
    """

    query: str
    dimensions: Tuple[str, ...] = ()
    must_cover: Tuple[str, ...] = ()
    terms: frozenset[str] = field(default_factory=frozenset)
    query_type: str = ""
    domain: str = ""
    is_broad: bool = False
    is_narrow: bool = False

    @classmethod
    def from_plan(
        cls,
        query: str,
        plan: Sequence[Mapping[str, Any]] | None = None,
        *,
        must_cover: Sequence[str] = (),
        query_type: str = "",
        domain: str = "",
    ) -> "ResearchScope":
        axes: List[str] = []
        for item in plan or ():
            if not isinstance(item, Mapping):
                continue
            axis = _normalize(str(item.get("axis", "") or "")).replace(" ", "_")
            if axis and axis not in axes:
                axes.append(axis)
        terms = _subject_tokens(query)
        # Breadth is a property of the QUESTION plus how many dimensions it
        # earned, not a topic judgement. A question that names one subject and
        # one relation is narrow however technical its vocabulary.
        #
        # A CONJUNCTION is the reliable narrow/broad signal: "outcomes AND costs"
        # asks for two things and is broad, while "What is X?" is one thing even
        # though the planner may have split it across two contracts. Counting
        # contract axes alone is not enough — a single definitional question
        # often earns a definition and a mechanism contract.
        parts = _conjunction_parts(query)
        if parts >= 2:
            is_broad, is_narrow = True, False
        elif len(axes) >= 4 or len(terms) >= 7:
            is_broad, is_narrow = True, False
        elif len(axes) <= 2 and len(terms) <= 4:
            is_broad, is_narrow = False, True
        else:
            is_broad, is_narrow = False, False
        return cls(
            query=query,
            dimensions=tuple(axes),
            must_cover=tuple(
                _normalize(m).replace(" ", "_") for m in must_cover if str(m or "").strip()
            ),
            terms=terms,
            query_type=query_type,
            domain=domain,
            is_broad=is_broad,
            is_narrow=is_narrow,
        )

    @property
    def required(self) -> Tuple[str, ...]:
        """Dimensions that MUST be evidenced before the answer is complete."""
        return tuple(dict.fromkeys((*self.must_cover, *self.dimensions)))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class DimensionCoverage:
    dimension: str
    facts: int = 0
    verified: int = 0
    primary_share: float = 0.0
    hosts: Tuple[str, ...] = ()

    @property
    def covered(self) -> bool:
        return self.verified > 0


@dataclass
class FocusReport:
    """Where the research actually is, versus where the question asked to go."""

    query: str
    dimensions: Dict[str, DimensionCoverage] = field(default_factory=dict)
    coverage: float = 0.0
    concentration: float = 0.0
    dominant_dimension: str = ""
    drift: float = 0.0
    off_query_share: float = 0.0
    source_diversity: int = 0
    missing: Tuple[str, ...] = ()
    thin: Tuple[str, ...] = ()
    ungrounded: Tuple[str, ...] = ()
    host_concentration: float = 0.0
    is_broad: bool = False
    is_narrow: bool = False

    @property
    def concentrated(self) -> bool:
        """Evidence piled on one dimension in a way that hides the rest.

        Requires MORE THAN ONE dimension to be at stake. A question with a single
        declared dimension is concentrated at 100% by construction, and calling
        that a defect is what pushes a narrow question to expand.

        The `ungrounded` set is not in this condition. It is a property of the
        evidence ledger (are these primary sources?), not of where the run chose
        to look, and the loop answers it by fetching better sources for a
        dimension it has already covered — not by looking somewhere else.
        """
        if self.is_narrow or len(self.dimensions) < 2:
            return False
        return (
            self.concentration > DEFAULT_CONCENTRATION_LIMIT
            and self.dominant_dimension not in ADVERSARIAL_AXES
        )

    @property
    def drifted(self) -> bool:
        return self.drift > DEFAULT_DRIFT_LIMIT

    @property
    def complete(self) -> bool:
        """Answered proportionately: covered, spread out, still on the question."""
        return (
            not self.missing
            and not self.thin
            and not self.concentrated
            and not self.drifted
        )

    @property
    def needs_more_research(self) -> bool:
        return not self.complete

    def summary(self) -> str:
        bits: List[str] = [f"coverage={self.coverage:.2f}"]
        if self.concentrated:
            bits.append(f"concentrated_on={self.dominant_dimension}")
        if self.drifted:
            bits.append(f"drift={self.drift:.2f}")
        if self.missing:
            bits.append("missing=" + ",".join(self.missing[:3]))
        if self.thin:
            bits.append("thin=" + ",".join(self.thin[:3]))
        bits.append(f"sources={self.source_diversity}")
        return "focus[" + " ".join(bits) + "]"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "coverage": round(self.coverage, 4),
            "concentration": round(self.concentration, 4),
            "dominant_dimension": self.dominant_dimension,
            "drift": round(self.drift, 4),
            "off_query_share": round(self.off_query_share, 4),
            "source_diversity": self.source_diversity,
            "host_concentration": round(self.host_concentration, 4),
            "missing": list(self.missing),
            "thin": list(self.thin),
            "ungrounded": list(self.ungrounded),
            "concentrated": self.concentrated,
            "drifted": self.drifted,
            "is_broad": self.is_broad,
            "is_narrow": self.is_narrow,
            "dimensions": {
                name: {
                    "facts": cov.facts,
                    "verified": cov.verified,
                    "primary_share": round(cov.primary_share, 4),
                    "hosts": list(cov.hosts),
                }
                for name, cov in self.dimensions.items()
            },
        }


def verified_fact(fact: Mapping[str, Any]) -> bool:
    """Is this fact verified, by the pipeline's own definition?

    `verifier.verify_facts` writes `verified` as the outcome of an overlap,
    polarity and quote check, and that flag is the authority. Anything else —
    the mere presence of a score, a quote, or a confident claim — is NOT
    verification: inferring it from presence is the self-verifying-evidence
    trap AGENTS.md records for the confidence engine, and it would let a run
    that gathered nothing report full coverage.
    """
    if not isinstance(fact, Mapping):
        return False
    if "verified" in fact:
        return fact.get("verified") is True
    # Pre-verifier records: fall back to the verifier's own verdict field when
    # present, and to False otherwise.
    if "verification_score" in fact and "verification_reason" in fact:
        return str(fact.get("verification_reason", "")).startswith("verified:")
    return False


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def _fact_dimension(
    fact: Mapping[str, Any],
    plan_by_question: Mapping[str, str],
    fallback: str = "",
) -> str:
    """Which planned dimension a fact belongs to.

    Facts are labelled by `sub_question`, which is the contract's question text,
    so the plan is the index. Unlabelled facts fall back to their own axis
    field, then to a single synthetic bucket: an unlabelled fact is evidence for
    the question, just not attributable to one declared dimension.

    Matching is TOLERANT, not exact. The summarizer's `sub_question` is taken
    from the source record and is often a shortened or re-worded variant of the
    contract's question ("population" for "population statistics"), plus a
    re-angled gap contract carries different text for the same axis. Exact
    equality silently attributed those facts to nothing, so a dimension with
    real evidence still measured as uncovered — which is what made the reviewer
    report the same gaps every round while coverage stayed at zero.
    """
    for key in ("sub_question", "question"):
        raw = _normalize(str(fact.get(key, "") or ""))
        if not raw:
            continue
        if raw in plan_by_question:
            return plan_by_question[raw]
        # Containment, either direction: the contract question usually contains
        # the shorter label, and vice versa.
        for question, axis in plan_by_question.items():
            if raw in question or question in raw:
                return axis
        # Paraphrase: strongest remaining overlap with a contract question.
        raw_tokens = _subject_tokens(raw)
        if raw_tokens:
            best_axis, best_score = "", 0.0
            for question, axis in plan_by_question.items():
                score = _jaccard(raw_tokens, _subject_tokens(question))
                if score > best_score:
                    best_axis, best_score = axis, score
            if best_score >= 0.34:
                return best_axis
    axis = _normalize(str(fact.get("axis", "") or "")).replace(" ", "_")
    return axis or fallback


def _host_of(url: str) -> str:
    try:
        from app.agents.sources import extract_domain

        return _normalize(extract_domain(str(url or "")))
    except Exception:  # pragma: no cover - defensive only
        return ""


def assess_focus(
    query: str,
    plan: Sequence[Mapping[str, Any]] | None = None,
    facts: Sequence[Mapping[str, Any]] | None = None,
    *,
    scope: Optional[ResearchScope] = None,
    min_facts: int = DEFAULT_MIN_FACTS_PER_DIMENSION,
    must_cover: Sequence[str] = (),
    query_type: str = "",
    domain: str = "",
) -> FocusReport:
    """Measure coverage, concentration and drift against the original question.

    Pure and total: any input yields a FocusReport. `scope` is derived from
    `plan` when not supplied. Dimensions come from the plan, never from a
    built-in list, so the same call describes any domain.
    """
    scope = scope or ResearchScope.from_plan(
        query, plan, must_cover=must_cover, query_type=query_type, domain=domain
    )
    fact_list = [f for f in (facts or ()) if isinstance(f, Mapping)]

    plan_by_question: Dict[str, str] = {}
    # The plan's own per-dimension source requirement. This is the standard the
    # answer is held to; nothing here overrides it with a constant.
    per_dimension_min: Dict[str, int] = {}
    for item in plan or ():
        if not isinstance(item, Mapping):
            continue
        axis = _normalize(str(item.get("axis", "") or "")).replace(" ", "_")
        q = _normalize(str(item.get("question", "") or ""))
        if axis and q:
            plan_by_question[q] = axis
        if axis:
            try:
                declared_min = int(item.get("minimum_sources", 0) or 0)
            except (TypeError, ValueError):
                declared_min = 0
            if declared_min > 0:
                per_dimension_min[axis] = max(
                    per_dimension_min.get(axis, 0), declared_min
                )

    declared = list(scope.dimensions) or ["_unassigned"]
    # The plan's own declared set, captured before the tally loop can append to
    # `declared` (unlabelled evidence), so "off-plan" means off-PLAN and not
    # merely "newly seen".
    declared_set = set(declared)

    # --- per-dimension tally -------------------------------------------------
    buckets: Dict[str, DimensionCoverage] = {
        name: DimensionCoverage(dimension=name) for name in declared
    }
    hosts_by_dimension: Dict[str, set[str]] = {name: set() for name in declared}
    all_hosts: set[str] = set()
    verified_total = 0
    primary_by_dimension: Dict[str, int] = {name: 0 for name in declared}
    verified_by_dimension: Dict[str, int] = {name: 0 for name in declared}
    off_query = 0
    scored = 0
    off_dimension = 0

    from app.agents.sources import is_primary_source

    for fact in fact_list:
        # DRIFT MEASURES RESEARCH BEHAVIOUR, NOT WORDING.
        #
        # An earlier version compared the CLAIM's vocabulary to the query's and
        # called a fact drifted when they shared fewer than 5% of terms. That is
        # both insensitive and misleading. Insensitive because a report whose
        # facts all restate the query's own words scores 0 drift no matter which
        # dimension it actually researched. Misleading because a legitimately
        # on-question fact ("Global AI investment reached 200bn") shares no
        # wording with "What are the current trends in AI?" and was scored as
        # drift, while the query's own words were reintroduced through
        # `sub_question` (a contract question derived FROM the query), producing
        # a number that barely moved as the run's behaviour changed.
        #
        # Drift is therefore distributional: it is the share of verified evidence
        # sitting on dimensions the plan did NOT declare. The plan IS the
        # question, decomposed, so evidence outside it is the operational
        # definition of "the research wandered off".
        if scope.terms:
            text = f"{fact.get('claim', '')} {fact.get('sub_question', '')}"
            if _jaccard(scope.terms, _subject_tokens(text)) < 0.05:
                off_query += 1
            scored += 1

        dim = _fact_dimension(fact, plan_by_question, "_unassigned")
        if dim not in buckets:
            buckets[dim] = DimensionCoverage(dimension=dim)
            hosts_by_dimension[dim] = set()
            declared.append(dim)
            primary_by_dimension[dim] = 0
            verified_by_dimension[dim] = 0
        cov = buckets[dim]
        cov.facts += 1
        host = _host_of(str(fact.get("source", "") or ""))
        if host:
            all_hosts.add(host)
            hosts_by_dimension[dim].add(host)
        if verified_fact(fact):
            cov.verified += 1
            verified_total += 1
            verified_by_dimension[dim] += 1
            # Evidence on no declared dimension is research that left the plan.
            if dim not in declared_set:
                off_dimension += 1
            if is_primary_source(str(fact.get("source", "") or "")):
                primary_by_dimension[dim] += 1

    for name in declared:
        cov = buckets[name]
        cov.hosts = tuple(sorted(hosts_by_dimension.get(name, ())))
        verified = verified_by_dimension.get(name, 0)
        cov.primary_share = (primary_by_dimension.get(name, 0) / verified) if verified else 0.0

    # --- coverage, concentration, host spread -------------------------------
    # A dimension counts as covered on VERIFIED evidence: unverified claims are
    # not evidence of coverage, and counting them is how a run that retrieved
    # 40 snippets of nothing reports full coverage.
    required = scope.required or tuple(declared)
    covered = [n for n in required if buckets.get(n) and buckets[n].verified > 0]
    # "Thin" is judged against the QUESTION's own demand for that dimension, not
    # a flat constant. The plan states `minimum_sources` per contract — that IS
    # the requirement, and overriding it with a fixed 2 is the same class of bug
    # this module exists to remove (a constant dictating terms the question never
    # set). A dimension the planner asked one source for is not thin at one
    # source, so it does not generate follow-up work forever.
    #
    # `min_facts` is the fallback for a dimension with no declared requirement.
    def _dimension_min(dimension: str) -> int:
        declared_min = per_dimension_min.get(dimension)
        if declared_min is not None and declared_min > 0:
            return declared_min
        if len(required) <= 1:
            return 1
        return max(1, min_facts)

    thin = [
        n for n in required
        if buckets.get(n) and 0 < buckets[n].verified < _dimension_min(n)
    ]
    missing = [n for n in required if not (buckets.get(n) and buckets[n].verified > 0)]
    ungrounded = [
        n for n in required
        if (buckets.get(n) and buckets[n].verified > 0
            and buckets[n].primary_share < DEFAULT_PRIMARY_SHARE)
    ]
    coverage = (len(covered) / len(required)) if required else 0.0

    concentration = 0.0
    dominant = ""
    if verified_total > 0 and declared:
        shares = {
            n: verified_by_dimension.get(n, 0) / verified_total for n in declared
        }
        dominant = max(shares, key=lambda n: shares[n])
        concentration = shares[dominant]

    host_concentration = 0.0
    host_total = sum(
        1 for f in fact_list if verified_fact(f) and _host_of(str(f.get("source", "") or ""))
    )
    if host_total > 0 and all_hosts:
        per_host: Dict[str, int] = {}
        for fact in fact_list:
            if not verified_fact(fact):
                continue
            host = _host_of(str(fact.get("source", "") or ""))
            if host:
                per_host[host] = per_host.get(host, 0) + 1
        host_concentration = max(per_host.values()) / host_total

    # Drift is the share of verified evidence that left the plan. Falls back to
    # the lexical reading ONLY when every fact is attributable, so a plan whose
    # dimensions were never labelled still gets a usable signal instead of a
    # silent zero.
    if verified_total > 0:
        drift = off_dimension / verified_total
    else:
        drift = (off_query / scored) if scored else 0.0

    return FocusReport(
        query=query,
        dimensions={n: buckets[n] for n in declared},
        coverage=round(coverage, 4),
        concentration=round(concentration, 4),
        dominant_dimension=dominant,
        drift=round(drift, 4),
        off_query_share=round(off_query / scored, 4) if scored else 0.0,
        source_diversity=len(all_hosts),
        missing=tuple(missing),
        thin=tuple(thin),
        ungrounded=tuple(ungrounded),
        host_concentration=round(host_concentration, 4),
        is_broad=scope.is_broad,
        is_narrow=scope.is_narrow,
    )


# ---------------------------------------------------------------------------
# Verifier compatibility
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Targeted follow-ups
# ---------------------------------------------------------------------------


def _dimension_query(query: str, dimension: str, plan: Sequence[Mapping[str, Any]]) -> str:
    """A search string for a dimension that has no evidence.

    Built from the QUESTION plus the plan's own contract for that dimension when
    one exists, so the follow-up inherits the planner's phrasing and jurisdiction
    rather than a canned template. This is what keeps the mechanism domain
    agnostic: there is no template to hardcode an AI topic into.
    """
    for item in plan or ():
        if not isinstance(item, Mapping):
            continue
        axis = _normalize(str(item.get("axis", "") or "")).replace(" ", "_")
        if axis == dimension:
            question = str(item.get("question", "") or "").strip()
            if question:
                return question
    terms = " ".join(sorted(_subject_tokens(query))[:6])
    return f"{query} {terms} {dimension.replace('_', ' ')}".strip()


def targeted_followups(
    report: FocusReport,
    plan: Sequence[Mapping[str, Any]] | None = None,
    *,
    limit: int = 4,
    already_asked: Sequence[str] = (),
) -> List[str]:
    """Search strings aimed at what is MISSING, not at what is already known.

    Priority is inverted on purpose: research that has become concentrated needs
    the dimensions it skipped, so missing and thin dimensions come first, then
    ungrounded ones (researched but with no primary source), and only then does
    drift contribute. Nothing is proposed for a dimension that already has solid
    evidence, which is what stops a focused run from expanding for its own sake.
    """
    asked = [_normalize(q) for q in already_asked]
    candidates: List[str] = []

    # Grounding is a separate concern from coverage, so it must not propose
    # follow-ups for a dimension that is already answered. Otherwise a complete
    # run whose evidence happens to be all secondary would keep proposing new
    # searches for dimensions it has already covered — which is precisely the
    # "find more" behaviour this module exists to stop. The grounding gap is
    # still REPORTED (`ungrounded`) for the critic to gate on.
    actionable = (*report.missing, *report.thin)
    for dimension in actionable:
        query = _dimension_query(report.query, dimension, plan or ())
        if query:
            candidates.append(query)

    # Drift contributes a restatement of the ORIGINAL question, because the
    # failure it indicates is that the run stopped answering it. Only when there
    # is nothing missing, so a broad run does not get told to re-search what it
    # already covered.
    if not candidates and report.drifted:
        candidates.append(report.query.strip())

    out: List[str] = []
    for query in candidates:
        norm = _normalize(query)
        if not norm:
            continue
        if any(_jaccard(_subject_tokens(norm), _subject_tokens(q)) >= FOLLOWUP_DUPLICATE_LIMIT
               for q in asked):
            continue
        if any(_jaccard(_subject_tokens(norm), _subject_tokens(q)) >= FOLLOWUP_DUPLICATE_LIMIT
               for q in out):
            continue
        out.append(query)
        if len(out) >= limit:
            break
    return out


def needs_redirect(
    report: FocusReport,
    *,
    iteration: int = 0,
    max_iterations: int = 3,
) -> bool:
    """Should the next pass search somewhere other than where it just looked?

    False for a narrow, complete question: "how does TCP congestion control
    work" is answered well by one good dimension, and expanding it would be the
    exact drift this module exists to prevent. True as soon as a broad question
    has a gap, evidence is piled up, or the run has wandered off-question — and
    always before the iteration ceiling, so a redirect gets a chance to land.
    """
    if report.is_narrow and not report.missing:
        return False
    if not report.needs_more_research:
        return False
    return iteration < max_iterations


def should_go_deeper(
    report: FocusReport,
    *,
    iteration: int = 0,
    max_iterations: int = 3,
) -> bool:
    """Distinguish "broaden" from "deepen".

    Broad questions that have missed a declared dimension must explore it before
    going deeper anywhere: the whole point of a broad question is breadth, and a
    second pass over the dimension that already has evidence makes the report
    MORE lopsided, not more complete.
    """
    if report.is_narrow and not report.missing:
        return False
    if iteration >= max_iterations:
        return False
    if report.missing or report.thin:
        return True
    return False