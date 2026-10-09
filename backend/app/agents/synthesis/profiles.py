"""Report profiles and query-type inference.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). `ReportProfile` decides which sections a report must carry and how
verbose the findings bullets are; `select_profile` picks one from the mode,
query type and evidence shape; `infer_query_type` classifies the question.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

@dataclass(frozen=True)
class ReportProfile:
    """Which sections a report must carry, and how loudly it accounts for itself.

    `required` sections are always present (added from measured state when the
    writer omits them). `conditional` sections are added ONLY when they would
    carry real content — a "Key Figures" section that says "no figures were
    extracted" is noise, and an epistemology note about counter-evidence on a
    definitional query is worse than noise. A conditional section whose signal
    IS present is never suppressed, so nothing adverse is hidden to look tidy.
    """

    name: str
    required: Tuple[str, ...]
    conditional: Tuple[str, ...] = ()
    verbose_findings: bool = False
    include_reasoning: bool = True
    # Machine-owned section injection. Almost everything is adaptive: the answer
    # is the writer's prose, and process-shaped accounting is emitted separately
    # rather than pasted into it. A profile that needs guaranteed headings sets
    # this; nothing does today, and the default keeps reports adaptive.
    enforce_sections: bool = False
    max_findings: int = 6
    writer_sections: Tuple[str, ...] = ()


_WRITER_OWNED_ALWAYS = ("Executive Summary", "Key Findings")


PROFILE_DIRECT = ReportProfile(
    name="direct",
    required=("Executive Summary",),
    conditional=(
        "Key Findings",
        "Key Figures",
        "Limitations & Unknowns",
        "Counterarguments & Disputed Points",
    ),
    verbose_findings=False,
    include_reasoning=False,
    max_findings=5,
    writer_sections=_WRITER_OWNED_ALWAYS,
)


PROFILE_BRIEF = ReportProfile(
    name="brief",
    required=("Executive Summary", "Key Findings", "Evidence & Confidence"),
    conditional=(
        "Key Figures",
        "Limitations & Unknowns",
        "Counterarguments & Disputed Points",
        "Open Questions & Missing Angles",
    ),
    verbose_findings=False,
    include_reasoning=True,
    max_findings=6,
    writer_sections=_WRITER_OWNED_ALWAYS,
)


PROFILE_ANALYTICAL = ReportProfile(
    name="analytical",
    required=(
        "Executive Summary",
        "Key Findings",
        "Evidence & Confidence",
        "Limitations & Unknowns",
        "Counterarguments & Disputed Points",
    ),
    conditional=("Key Figures", "Open Questions & Missing Angles"),
    verbose_findings=False,
    include_reasoning=True,
    max_findings=8,
    writer_sections=_WRITER_OWNED_ALWAYS
    + ("Limitations & Unknowns", "Counterarguments & Disputed Points"),
)


PROFILES: Dict[str, ReportProfile] = {
    p.name: p for p in (PROFILE_DIRECT, PROFILE_BRIEF, PROFILE_ANALYTICAL)
}


_LIGHTWEIGHT_QUERY_TYPES = {
    "definition", "factual", "lookup", "status", "howto", "how_to", "procedural",
}


_CONTESTED_QUERY_TYPES = {
    "decision", "forecast", "evaluative", "strategic", "causal", "comparison",
}


def select_profile(
    ctx: Dict[str, Any] | None,
    *,
    fact_count: int = 0,
) -> ReportProfile:
    """Pick the report shape from mode, query type and what the evidence holds.

    Explicit `ctx["report_profile"]` always wins — a caller that knows what it
    needs is not overruled. Otherwise: a deep run is analytical (depth is the
    product); a small evidence pool answering a lightweight question is
    `direct`; everything else is `brief`. A contested question is never demoted
    below `brief`, and detected contradictions never get a profile that could
    hide them.
    """
    ctx = ctx or {}
    explicit = str(ctx.get("report_profile", "") or "").strip().lower()
    if explicit in PROFILES:
        return PROFILES[explicit]

    mode = str(ctx.get("mode", "standard") or "standard").lower()
    if mode.startswith("deep"):
        return PROFILE_ANALYTICAL

    intent = ctx.get("intent") if isinstance(ctx.get("intent"), dict) else {}
    query_type = str(intent.get("query_type", "") or ctx.get("query_type", "") or "").lower()
    level = str(intent.get("explanation_level", "") or "").lower()
    contested = bool(ctx.get("contradictions"))

    if query_type in _CONTESTED_QUERY_TYPES or contested:
        return PROFILE_BRIEF
    if mode == "quick":
        return PROFILE_DIRECT
    if query_type in _LIGHTWEIGHT_QUERY_TYPES or level == "basic":
        # A small, uncontested pool answering a simple question: an answer,
        # its findings, its sources. Limitations still appear if measured.
        return PROFILE_DIRECT if fact_count <= 12 else PROFILE_BRIEF
    return PROFILE_BRIEF


_FORMAT_GUIDANCE: Dict[str, str] = {
    "definition": (
        "SHAPE — this is a definitional question. Open with a one-sentence "
        "plain-language definition a non-expert would understand, then how it "
        "works, then where it matters and what it is commonly confused with. "
        "Include at most one analogy, and mark it as an analogy."
    ),
    "comparison": (
        "SHAPE — this is a comparison. Organise by CRITERION, not by item: each "
        "section compares both options on one dimension with their numbers side "
        "by side. Close with a short verdict naming which option wins for which "
        "use case. 'It depends' is only acceptable when you say what it depends "
        "ON and give the threshold."
    ),
    "howto": (
        "SHAPE — this is a procedural question. Give prerequisites first, then "
        "numbered steps in execution order, one action per step. For any step "
        "that can fail, name the failure mode and how to tell it happened. No "
        "step may depend on information the reader has not been given yet."
    ),
    "causal": (
        "SHAPE — this is a 'why' question. Lead with the mechanism the evidence "
        "supports, stated as a chain (X drives Y because Z). Give the competing "
        "explanation where one exists, and say what evidence would separate "
        "them. Where the evidence is only correlational, say so explicitly."
    ),
    "decision": (
        "SHAPE — this is a decision question. Lay out the realistic options with "
        "their trade-offs, then give a recommendation conditioned on the "
        "reader's situation ('if you value X over Y, then...'), then state what "
        "would change that recommendation. Never recommend without naming the "
        "cost of the recommendation."
    ),
    "forecast": (
        "SHAPE — this is a forward-looking question. State the current measured "
        "level and its as-of date FIRST, label every projection as a projection "
        "with its source's assumptions, and give a range rather than a point "
        "estimate. Never present a projection in the same voice as an "
        "observation. Keep four kinds of statement strictly separate: OFFICIAL "
        "PROJECTIONS (a named body's forecast, with its assumptions), CURRENT "
        "INDICATORS (the measured today that the forecast extends), CROSS-SOURCE "
        "INFERENCE (your own reasoning across sources, clearly marked as "
        "inference), and UNKNOWNS (say so; do not guess). Never invent a "
        "probability, a rank, a percentage or a date the evidence does not "
        "contain."
    ),
    "timeline": (
        "SHAPE — this is a chronological question. Order the report by date, "
        "attach a date to every event, and mark any date the evidence gives "
        "only approximately. End with the current state and its as-of date."
    ),
    "status": (
        "SHAPE — this is a current-state question. The first sentence gives the "
        "current state and the date the evidence was published or retrieved. "
        "Flag explicitly anything that may have changed since."
    ),
    "list": (
        "SHAPE — this is an enumerative question. Give the list as the primary "
        "content, one item per bullet, each self-contained and cited, ordered "
        "by whatever the reader would rank them by (size, date, importance) and "
        "say which ordering you used."
    ),
    "entity": (
        "SHAPE — this question is about a specific person or organisation. "
        "Establish identity first (who exactly, distinguished from similarly "
        "named others), then the facts asked for. If the evidence may conflate "
        "two entities, say so before anything else."
    ),
}


_QUERY_TYPE_PATTERNS: Sequence[Tuple[str, "re.Pattern[str]"]] = (
    ("comparison", re.compile(r"\b(vs\.?|versus|compare[ds]?|comparison|better than|"
                              r"difference between|which (?:is|one))\b", re.I)),
    ("howto", re.compile(r"\b(how (?:do|to|can) |steps? to|guide to|set ?up|install|"
                         r"configure|tutorial)\b", re.I)),
    ("causal", re.compile(r"\b(why|what caused|cause[ds]? of|reason[s]? (?:for|why)|"
                          r"leads? to|because of)\b", re.I)),
    ("decision", re.compile(r"\b(should (?:i|we|they)|worth it|is it worth|"
                            r"recommend|choose between|invest in)\b", re.I)),
    ("forecast", re.compile(r"\b(will |forecast|projection|predicted|by 20\d\d|"
                            r"outlook|future of|expected to)\b", re.I)),
    ("timeline", re.compile(r"\b(timeline|history of|when did|chronolog|"
                            r"over time|evolution of)\b", re.I)),
    ("status", re.compile(r"\b(current(?:ly)?|right now|as of|latest|today|"
                          r"still |up to date)\b", re.I)),
    ("list", re.compile(r"\b(list of|top \d+|examples? of|what are the|"
                        r"types of|kinds of)\b", re.I)),
    ("definition", re.compile(r"^\s*(what (?:is|are|does)|define|meaning of|"
                              r"explain )\b", re.I)),
)


def infer_query_type(query: str) -> str:
    """Best-effort question shape when the intent classifier gave none.

    Deterministic and cheap. Order matters: "what are the differences between
    X and Y" is a comparison, not a definition, so comparison is tested first.
    Returns "" when nothing matches, and the caller falls back to the generic
    angle-per-section structure.
    """
    text = (query or "").strip()
    if not text:
        return ""
    for name, pattern in _QUERY_TYPE_PATTERNS:
        if pattern.search(text):
            return name
    # A forward year with no explicit forecast verb is still a forecast
    # question ("the most demanding jobs in 2027"). Reuses the temporal module's
    # clock-relative detector, so a historical year never triggers the shape.
    from app.core.temporal import query_targets_future

    if query_targets_future(text):
        return "forecast"
    return ""


def _format_guidance(query: str, intent: Dict[str, Any] | None) -> str:
    """The expected document shape for this question, or "" if unrecognised."""
    intent = intent if isinstance(intent, dict) else {}
    declared = str(intent.get("query_type", "") or "").strip().lower()
    key = declared if declared in _FORMAT_GUIDANCE else ""
    if not key:
        alias = {"how_to": "howto", "procedural": "howto", "chronology": "timeline",
                 "evaluative": "decision", "strategic": "decision",
                 "factual": "status", "person": "entity", "organisation": "entity",
                 "organization": "entity"}.get(declared, "")
        key = alias if alias in _FORMAT_GUIDANCE else ""
    if not key:
        key = infer_query_type(query)
    return _FORMAT_GUIDANCE.get(key, "")
