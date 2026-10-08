"""Structured types and module-level constant tables for the planner.

Extracted verbatim from `app/agents/planner.py` (refactor; no behaviour change).
The typed shapes every sub-question must have, the valid-value sets the
validator speaks, the frontier-track taxonomy, the axis/search-type maps and the
scaffolds and vocabulary the dimension and contract builders share.

`planner.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple, TypedDict


class SubQuestion(TypedDict, total=False):
    """Delegation Contract — the typed shape every sub-question must have.
    Validated by app.core.schemas.PlannerOutputModel before agents see it."""

    id: int
    question: str
    axis: str
    search_type: str
    priority: int
    depends_on: List[int]
    coverage_goal: str
    domain: str
    minimum_sources: int
    stop_condition: str
    variants: List[str]
    agent: str
    tools: List[str]
    scope: List[str]
    output_format: str
    # --- additive contract fields ---
    specialist: str            # summarizer prompt overlay to load
    preferred_domains: List[str]
    primary_source_query: str  # site:-scoped variant aimed at publishers
    wave: int                  # execution wave from dependency order
    sense: str                 # intent sense label this contract researches ("" = unambiguous)


DEFAULT_MINIMUM_SOURCES = 2
DEFAULT_STOP_CONDITION = "sufficient evidence for this axis"


class PlannerOutput(TypedDict, total=False):
    query_type: str
    query_scope: str
    dominant_domain: str
    sub_questions: List[SubQuestion]
    coverage_note: str


# =========================
# Constants
# =========================

VALID_SEARCH_TYPES = {
    "encyclopedia",
    "academic",
    "statistical",
    "news",
    "comparison",
}

VALID_DOMAINS = {
    "machine_learning",
    "software",
    "philosophy",
    "economics",
    "science",
    "legal",
    "policy",
    "academic",
    "engineering",
    "general",
}

VALID_TOOLS = ["web_search", "fetch_content"]

VALID_AXES = {
    "definition", "mechanism", "application", "criticism", "comparison",
    "evidence", "history", "outlook", "risk", "cost", "regulation",
    # --- frontier six-axis research tracks (equal weight) ---
    "capability", "infrastructure", "economics", "adoption", "safety",
    "counter_evidence",
}

# The six research tracks that "current state and trends of <field>" queries
# benefit from. Equal weight, used ONLY when the planner's dynamic dimension
# directive asks for a trends-style scope (see _trends_scope_wanted).
#
# HISTORY, because this is a live bug and not a design choice: this tuple was
# previously forced onto EVERY query regardless of subject, with literal AI
# search strings below. Asked "population of Malawi in 2024" it produced six
# contracts about model capability and GPU supply chains, and the critic then
# refused to finalize until each had primary sources. The question was
# unanswerable by construction. Dimensions now come from the query.
FRONTIER_AXES: Tuple[str, ...] = (
    "capability",
    "infrastructure",
    "economics",
    "adoption",
    "regulation",
    "safety",
)
# Always-on adversarial sub-track. This is the axis that keeps a report from
# being a press release: it searches specifically for over-hype, plateau,
# ROI-negative and overstated-claim arguments. Domain-agnostic by wording; it
# is applied to any subject the planner has classified as contested.
COUNTER_EVIDENCE_AXIS = "counter_evidence"

# Membership set for the front-half of the frontier taxonomy; the counter-
# evidence track is added separately because it is always-on.
FRONTIER_AXIS_SET = frozenset((*FRONTIER_AXES, COUNTER_EVIDENCE_AXIS))


# Which search_type serves each axis when the planner has to synthesize a
# missing angle itself.
AXIS_SEARCH_TYPE: Dict[str, str] = {
    "definition": "encyclopedia",
    "mechanism": "academic",
    "application": "comparison",
    "criticism": "academic",
    "comparison": "comparison",
    "evidence": "statistical",
    "history": "encyclopedia",
    "outlook": "news",
    "risk": "academic",
    "cost": "statistical",
    "regulation": "news",
    # frontier tracks: capability/benchmarks are academic, infrastructure and
    # economics are statistical, adoption news, safety academic, and the
    # counter-evidence track is deliberately academic+news (skeptical essays,
    # replication failures, plateau analyses live in both).
    "capability": "academic",
    "infrastructure": "statistical",
    "economics": "statistical",
    "adoption": "news",
    "safety": "academic",
    "counter_evidence": "academic",
}

# Search-question SCAFFOLDS for the optional frontier tracks. Each names the
# AXIS and what kind of document answers it, and every one carries a {subject}
# placeholder that is filled from the user's own query.
#
# Previously these were fully-written AI strings ("frontier AI model capability
# trajectory and benchmark results..."), so asking about grid redesign searched
# for AI benchmarks. A scaffold is domain-agnostic by construction: it cannot
# assert a subject the user never mentioned.
FRONTIER_AXIS_QUESTIONS: Dict[str, str] = {
    "capability": (
        "{subject} capability and performance: measured results, benchmarks, "
        "demonstrated limits and primary technical reports"
    ),
    "infrastructure": (
        "{subject} infrastructure and physical constraints: facilities, "
        "supply chains, manufacturing capacity and bottlenecks official "
        "reports and statistics"
    ),
    "economics": (
        "{subject} economics and investment: capital cost, funding, "
        "valuation, measured return on investment, success and failure rates "
        "financial filings and investor reports"
    ),
    "adoption": (
        "{subject} adoption and diffusion: who uses it, at what rate, and "
        "which groups or regions are left uneven official surveys and "
        "statistics"
    ),
    "regulation": (
        "{subject} regulation, policy and governance as reactive context: "
        "rules, enforcement, standards and compliance official regulatory "
        "and institutional texts"
    ),
    "safety": (
        "{subject} safety, harm and risk: incidents, evaluations, failure "
        "modes and open debates primary research"
    ),
    "counter_evidence": (
        "critical and sceptical analysis of {subject}: overstatement, "
        "plateaus, negative or null results, replication failures and the "
        "strongest published counterarguments"
    ),
}


# Queries that ask about a field's current state warrant the full frontier
# spread. A narrow factual or comparison query does not, and forcing it on one
# is what made the taxonomy a bug. Keyed on the QUERY'S OWN words: there is no
# subject list, so this stays domain-agnostic.
# Multi-word phrases only. A bare year or "now" is NOT a trends signal: "Who is
# the CEO of Siemens and when did he start?" contains "when" and a year, and
# treating that as a trends question is how a narrow factual query acquired six
# research contracts.
_TRENDS_SCOPE_PHRASES = (
    "current state", "state of the", "state of play", "current trends",
    "latest trends", "trends in", "trend in", "future of", "outlook for",
    "landscape of", "state-of-the-art", "state of the art", "trajectory of",
    "roadmap for", "where the field", "emerging trends", "current landscape",
    "everything about", "overview of", "overview of the", "general overview",
    "current adoption", "current state of", "how is the field",
)
# The same phrases, anchored so that a following word is required. Guards the
# case where the phrase is the subject being defined rather than a request.
_TRENDS_RE = re.compile(
    "|".join(re.escape(p) for p in _TRENDS_SCOPE_PHRASES) + r"\b\s+\S"
)

# Survey-noun phrases. "What are the CURRENT TRENDS in AI?" asks for a survey of
# a field, so the interrogative is just the wh-word and must not veto. These
# therefore win over a soft narrow veto.
_STRONG_TRENDS_RE = re.compile(
    r"\b("
    r"trends?\s+(?:in|of)|(?:current|latest|emerging)\s+trends?|"
    r"future\s+of|outlook\s+for|landscape\s+of|state[- ]of[- ]the[- ]art|"
    r"trajectory\s+of|roadmap\s+for|overview\s+of|general\s+overview|"
    r"everything\s+about"
    r")\b"
)
# Shapes that mark the WHOLE question as single-dimension, whatever else it
# contains. "How do I renew a passport in Kenya?" and "malawi vs mozambique
# population" are one question with one answer, so they never get six research
# contracts. These veto the trends phrases.
_HARD_NARROW_TOKENS = (
    "how do i", "how to", "vs", "versus", "compared to", "compare",
    "difference between", "better than", "is it legal", "is it safe",
    "how much does", "how much is", "calculate", "formula for",
    "definition of", "define",
)
# Weaker question-forms: "who is the CEO and when did he start" is narrow, but
# "what are the current trends in AI" is NOT a definition question — the
# interrogative is asking FOR the trends. So these only veto when no trends
# phrase is present.
_SOFT_NARROW_TOKENS = ("what is", "what are", "who is", "when did", "when is")


# Domain -> specialist overlay in summarizer.SPECIALIST_PROMPT_ADDITIONS.
DOMAIN_TO_SPECIALIST: Dict[str, str] = {
    "economics": "financial",
    "machine_learning": "technical",
    "software": "technical",
    "engineering": "technical",
    "science": "scientific",
    "legal": "legal",
    "policy": "policy",
    "academic": "academic",
    "philosophy": "academic",
    "general": "general",
}


# Deterministic dimensions by coarse query shape. This is the fallback tuple
# for `_heuristic_dimensions` when the query text carries no usable signal at
# all — kept deliberately coarse so the LLM directive, not a template, is what
# normally drives the plan.
_HEURISTIC_DIMENSION_DEFAULTS: Tuple[str, ...] = ("background", "evidence", "criticism")


# A required angle is "covered" when any existing dimension or must_cover entry
# carries one of these vocabulary fragments. These are the SAME words the
# directive prompt (R3/R4) and the orchestrator classifiers already use, so the
# validator speaks the pipeline's existing vocabulary instead of inventing a
# parallel one. Reuse `dimension_to_axis` for the semantic half of matching.
_REQUIRED_ANGLE_TOKENS: Dict[str, Tuple[str, ...]] = {
    "quantitative": (
        "quantit", "statistic", "data", "metric", "number", "figure",
        "cost", "price", "growth", "projection", "forecast",
    ),
    "decision": (
        "decision", "tradeoff", "trade-off", "option", "policy",
        "risk", "comparison", "scenario",
    ),
    "contested": (
        "critic", "counter", "limitation", "risk", "controvers",
        "oppos", "alternative",
    ),
    "comparison": (
        "comparison", "compar", "head-to-head", "versus", "vs", "tradeoff",
    ),
}

# Injected requirement label -> the canonical axis its synthesized contract
# takes. Used to prove a newly-appended angle is NOT already served by an
# existing contract (`dimension_to_axis` maps the covered label the same way),
# so the validator never adds a dimension a plan already covers.
_REQUIRED_ANGLE_AXIS: Dict[str, str] = {
    "quantitative": "evidence",
    "decision": "comparison",
    "contested": "criticism",
    "comparison": "comparison",
}
