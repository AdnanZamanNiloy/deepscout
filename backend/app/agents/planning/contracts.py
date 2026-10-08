"""Contract construction, plan repair and axis-coverage enforcement.

Extracted verbatim from `app/agents/planner.py` (refactor; no behaviour change).
The single contract builder (`_contract`), the gap-to-contract bridge
(`gap_contracts`), deterministic required-axis and frontier-track injection, and
the query/date helpers they share. These are the pieces that turn a dimension
list into executable delegation contracts.

`planner.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.logging import get_logger
from app.agents.sources import (
    build_dimension_primary_query,
    grounded_site_targets,
    primary_source_hints,
)

from app.agents.planning.normalize import (
    axis_search_type,
    dimension_to_axis,
    normalize_domain,
    normalize_text,
)
from app.agents.planning.types import (
    COUNTER_EVIDENCE_AXIS,
    DEFAULT_MINIMUM_SOURCES,
    DEFAULT_STOP_CONDITION,
    DOMAIN_TO_SPECIALIST,
    FRONTIER_AXES,
    FRONTIER_AXIS_QUESTIONS,
    FRONTIER_AXIS_SET,
    VALID_SEARCH_TYPES,
    _HARD_NARROW_TOKENS,
    _SOFT_NARROW_TOKENS,
    _STRONG_TRENDS_RE,
    _TRENDS_RE,
)

logger = get_logger(__name__)


# =========================
# Contract construction
# =========================

def _contract(
    *,
    index: int,
    question: str,
    axis: str,
    search_type: str,
    priority: int,
    domain: str,
    coverage_goal: str = "",
    minimum_sources: int = DEFAULT_MINIMUM_SOURCES,
    stop_condition: str = DEFAULT_STOP_CONDITION,
    variants: Optional[Sequence[str]] = None,
    depends_on: Optional[Sequence[int]] = None,
    agent: str = "",
    tools: Optional[Sequence[str]] = None,
    scope: Optional[Sequence[str]] = None,
    sense: str = "",
) -> Dict[str, Any]:
    """Build one fully-populated delegation contract.

    Single construction point so every field (including the new source
    preferences) is present on contracts from the LLM path, the axis-repair
    path and the fallback path alike. Downstream code can rely on the shape
    instead of defaulting per call site.
    Dynamic-planning note: `axis` may be a model-chosen dimension label, not a
    canonical enum value. Canonical labels and common aliases are mapped via
    `dimension_to_axis`; a genuinely query-specific label is kept as a slug so
    it still gets its own report section (downstream keyed by axis string).
    """
    # Frontier tracks are first-class axes: never fold them onto the legacy
    # canonical alias ("safety" must not become "risk", "counter_evidence"
    # must not become "criticism"). Those aliases exist for free-form model
    # labels; the mandatory tracks are their own report sections and their
    # coverage is checked by exact name.
    requested = str(axis).strip()
    axis = requested if requested in FRONTIER_AXIS_SET else dimension_to_axis(axis)
    search_type = (
        search_type if search_type in VALID_SEARCH_TYPES
        else axis_search_type(axis, default="encyclopedia")
    )
    domain = normalize_domain(domain)
    specialist = DOMAIN_TO_SPECIALIST.get(domain, "general")
    # Publisher preferences are GROUNDED in the question, not looked up from the
    # (search_type, domain) bucket alone: a contract about Bangladesh gets
    # Bangladesh's own official suffix family alongside whichever registered
    # hints are not bound to a different country. Same resolver the primary
    # query uses, so the two cannot drift apart.
    hints = list(
        grounded_site_targets(question.strip(), search_type, domain, max_sites=3)
        or primary_source_hints(search_type, domain)
    )
    return {
        "id": index,
        "question": question.strip(),
        "axis": axis,
        "search_type": search_type,
        "priority": max(1, min(3, int(priority or 2))),
        "depends_on": list(depends_on or []),
        "coverage_goal": coverage_goal,
        "domain": domain,
        "minimum_sources": max(1, int(minimum_sources)),
        "stop_condition": stop_condition or DEFAULT_STOP_CONDITION,
        "variants": list(variants or [])[:2],
        "agent": (agent or f"{specialist}_researcher")[:60],
        "tools": list(tools or ["web_search"]),
        "scope": list(scope or [])[:5],
        "output_format": "structured_findings",
        "specialist": specialist,
        "preferred_domains": hints,
        "primary_source_query": build_dimension_primary_query(question, search_type, domain),
        "wave": 0,
        "sense": str(sense or "").strip(),
    }


def synthesize_dimension_contract(
    *,
    index: int,
    dimension: str,
    query: str,
    domain: str = "general",
    minimum_sources: int = DEFAULT_MINIMUM_SOURCES,
    today: str = "",
    coverage_goal: str = "",
) -> Dict[str, Any]:
    """Build a search-ready contract for a model-chosen dimension the plan omitted.

    The dynamic-planning replacement for the old fixed axis templates: the
    dimension label is the model's own, so the question is derived from that
    label + the query's concept rather than from a stock sentence. Keyword-
    shaped (not prose) because its whole job is to be a search string.
    """
    axis = dimension_to_axis(dimension)
    search_type = axis_search_type(axis)
    concept = _query_concept(query)
    year = _year_from(today)
    label = dimension.replace("_", " ").strip()
    question = f"{concept} {label}{year}".strip()
    return _contract(
        index=index,
        question=question,
        axis=axis,
        search_type=search_type,
        priority=1,
        domain=domain,
        coverage_goal=coverage_goal or f"required dimension: {label}",
        minimum_sources=minimum_sources,
    )


def _query_concept(query: str) -> str:
    text = re.sub(
        r"^\s*(what\s+is|what\s+are|who\s+is|define|explain|how\s+do(?:es)?|should\s+\w+)\s+",
        "",
        (query or "").strip(),
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip(" ?.!") or (query or "").strip()


def _year_from(today: str) -> str:
    match = re.search(r"(20\d{2})", today or "")
    return f" {match.group(1)}" if match else ""


def _intent_research_senses(intent: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(sense_label, domain) pairs the intent stage says research should target.

    Empty when the query is unambiguous (or intent is absent): nothing in the
    plan is sense-tagged and behaviour is exactly the pre-intent one.

    Reads `senses` (the homonym path) AND the ambiguity policy's readings (the
    under-specification path). The two are different vocabularies for the same
    idea — "which meanings of the term are in play" — and only checking `senses`
    left the under-specified case with NO sense tags on any contract, so facts
    were never labelled with the reading they were gathered for and the evidence
    could not be attributed to a reading downstream.
    """
    if not intent:
        return []
    senses = [
        s for s in (intent.get("senses") or [])
        if isinstance(s, dict) and str(s.get("label", "")).strip()
    ]
    if senses and intent.get("ambiguity"):
        chosen = senses[:2] if intent.get("recommended_action") == "research_both" else senses[:1]
        return [
            (str(s["label"]).strip(), normalize_domain(str(s.get("domain", "")) or "general"))
            for s in chosen
        ]

    # Under-specification path: the ambiguity policy chose the reading(s). The
    # CHOSEN reading leads, so the reading the user's question is answered under
    # is the one the majority of contracts research.
    policy = intent.get("ambiguity_policy")
    if isinstance(policy, dict):
        chosen_label = str(policy.get("assumption", "") or "").strip()
        action = str(policy.get("action", "") or "")
        labels: List[str] = []
        if chosen_label:
            labels.append(chosen_label)
        if action == "separate":
            labels.extend(
                str(x).strip()
                for x in (policy.get("interpretations") or ())
                if str(x).strip() and str(x).strip() != chosen_label
            )
        return [(label, "general") for label in labels if label]
    return []


def _sense_concept(label: str) -> str:
    """Lowercased, parenthetical-stripped sense label — a search-ready phrase."""
    return re.sub(r"\s*\([^)]*\)", "", (label or "")).strip().lower()


def _assign_intent_senses(
    plan: List[Dict[str, Any]],
    intent: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Stamp the intent's sense labels onto contracts (deterministic).

    The model's own sense tags win when they name a researched sense; the
    rest are assigned by round-robin (research_both) or the single dominant
    sense. A sense's domain upgrades a `general` contract so the right
    specialist prompt loads downstream.
    """
    research = _intent_research_senses(intent)
    if not research:
        return plan
    domains = dict(research)
    valid = {label for label, _ in research}

    unassigned: List[Dict[str, Any]] = []
    for item in plan:
        sense = str(item.get("sense", "") or "").strip()
        if sense in valid:
            if item.get("domain", "general") == "general" and domains[sense] != "general":
                item["domain"] = domains[sense]
                item["specialist"] = DOMAIN_TO_SPECIALIST.get(item["domain"], "general")
        else:
            item["sense"] = ""
            unassigned.append(item)

    if len(valid) == 1:
        only = next(iter(valid))
        for item in unassigned:
            item["sense"] = only
            if item.get("domain", "general") == "general" and domains[only] != "general":
                item["domain"] = domains[only]
                item["specialist"] = DOMAIN_TO_SPECIALIST.get(item["domain"], "general")
    else:
        labels = [label for label, _ in research]
        for i, item in enumerate(unassigned):
            sense = labels[i % len(labels)]
            item["sense"] = sense
            if item.get("domain", "general") == "general" and domains[sense] != "general":
                item["domain"] = domains[sense]
                item["specialist"] = DOMAIN_TO_SPECIALIST.get(item["domain"], "general")
    return plan


def gap_contracts(
    *,
    query: str,
    missing: Sequence[str] = (),
    thin: Sequence[str] = (),
    existing: Sequence[Dict[str, Any]] = (),
    start_index: int = 0,
    domain: str = "general",
    minimum_sources: int = DEFAULT_MINIMUM_SOURCES,
    today: str = "",
    limit: int = 4,
) -> List[Dict[str, Any]]:
    """Turn measured coverage gaps into search-ready contracts.

    THIS IS THE STEP THE LOOP WAS MISSING. The reviewer reported missing
    dimensions every pass, but nothing converted them into research tasks: on an
    expansion pass `planner_node` seeded `required_axes` from the axes ALREADY
    researched and appended only what the model volunteered, so a plan could be
    drawn entirely from the direction that had already been explored. The
    reported gaps stayed textual — a sentence in `critique_feedback` for the
    next LLM call to ignore — and the run re-discovered the same holes each
    round while search kept returning the same technical material.

    Taking `missing`/`thin` from the focus report (app/agents/focus.py) and
    emitting a real contract per dimension makes the gap EXECUTABLE: the
    contract carries its own axis, so it becomes a sub-question, gets searched
    by `search_node`, and its facts land against that axis in the next coverage
    measurement. That is the loop closing.

    Domain-agnostic by construction: the dimension labels come from the plan,
    and each question is built from the label plus the query's own concept.
    Nothing here knows what the subject is.
    """
    wanted: List[str] = []
    for dimension in (*missing, *thin):
        name = str(dimension or "").strip()
        if name and name not in wanted:
            wanted.append(name)
    if not wanted:
        return []

    # Never duplicate a dimension the plan already carries a contract for.
    present = {
        dimension_to_axis(str(item.get("axis", "") or ""))
        for item in existing or ()
        if isinstance(item, dict)
    }
    planned_questions = {
        normalize_text(str(item.get("question", "") or ""))
        for item in existing or ()
        if isinstance(item, dict)
    }

    out: List[Dict[str, Any]] = []
    index = int(start_index)
    for dimension in wanted[: max(1, limit)]:
        axis = dimension_to_axis(dimension)

        if axis in present:
            # The dimension HAS a contract but produced no verified evidence.
            # Skipping it would leave the gap forever (the convergence failure);
            # re-issuing the identical question returns the identical pages (the
            # non-convergence failure). So widen the ask: same dimension, angled
            # at a DIFFERENT source class, which is what surfaces evidence the
            # first angle missed.
            #
            # This is checked BEFORE the question-dedup below, because the
            # re-angle is precisely the case where the base synthesized question
            # collides with the existing contract's text — deduping first would
            # discard the re-angle and leave the dimension uncovered.
            widened = _widen_dimension_query(
                dimension=dimension, query=query, axis=axis, today=today
            )
            if not widened:
                continue
            contract = _contract(
                index=index,
                question=widened,
                axis=axis,
                search_type=_alternate_search_type(axis),
                priority=1,
                domain=domain,
                coverage_goal=f"re-angle uncovered dimension: {dimension}",
                minimum_sources=minimum_sources,
            )
        else:
            contract = synthesize_dimension_contract(
                index=index,
                dimension=dimension,
                query=query,
                domain=domain,
                minimum_sources=minimum_sources,
                today=today,
                coverage_goal=f"close measured coverage gap: {dimension}",
            )

        question_key = normalize_text(contract["question"])
        if question_key in planned_questions:
            continue
        out.append(contract)
        present.add(axis)
        planned_questions.add(question_key)
        index += 1
    return out


# =========================
# Plan repair and shaping
# =========================

# Source classes to try in order when a dimension's first angle found nothing.
# Ordered from most authoritative to most general so the re-angle widens the
# net rather than repeating it. DOMAIN-AGNOSTIC: these are search TYPES, not
# subjects — the dimension label and the user's query supply the subject.
_REANGLE_SEARCH_TYPES: Tuple[str, ...] = (
    "statistical", "news", "encyclopedia", "academic", "general",
)


def _alternate_search_type(axis: str) -> str:
    """A search_type different from the axis default, to widen a retry."""
    default = axis_search_type(axis)
    for candidate in _REANGLE_SEARCH_TYPES:
        if candidate != default:
            return candidate
    return default


def _widen_dimension_query(
    *, dimension: str, query: str, axis: str, today: str = ""
) -> str:
    """Re-phrase an uncovered dimension at a different angle.

    Built from the dimension label, the query's concept and a source-class word
    ("official data", "reporting", "analysis"), so the retry targets material the
    first angle would not have returned. No subject is hardcoded: every word
    here is generic and the subject comes from the caller.
    """
    concept = _query_concept(query)
    label = str(dimension or "").replace("_", " ").strip()
    if not label or not concept:
        return ""
    angle = {
        "statistical": "official statistics and measured data",
        "news": "recent reporting and developments",
        "encyclopedia": "background and overview",
        "academic": "peer-reviewed research and analysis",
        "general": "documented evidence and case studies",
    }.get(_alternate_search_type(axis), "documented evidence")
    year = _year_from(today)
    return f"{concept} {label} {angle}{year}".strip()


# Legacy axis->keyword template. Retained ONLY for the no-dimension fallback
# (a caller that requires canonical axes but supplied no dynamic dimension
# labels). New planning flows should never reach it — required axes now come
# from `plan_dimensions` and are synthesized by `synthesize_dimension_contract`.
_AXIS_FALLBACK_TEMPLATES: Dict[str, Tuple[str, str, int]] = {
    "evidence": ("statistics official data figures", "statistical", 1),
    "criticism": ("limitations criticism counter-evidence risks", "academic", 2),
    "comparison": ("compared alternatives side by side", "comparison", 2),
    "definition": ("definition explanation overview", "encyclopedia", 1),
    "outlook": ("outlook forecast recent developments", "news", 2),
    "mechanism": ("how it works mechanism why causes drivers", "academic", 1),
    "risk": ("risks failure modes downsides", "academic", 2),
    "cost": ("cost price economics figures", "statistical", 2),
    "history": ("history origin development timeline", "encyclopedia", 3),
    "regulation": ("regulation policy law governance", "news", 3),
}


def enforce_axis_coverage(
    plan: List[Dict[str, Any]],
    query: str,
    required_axes: Sequence[str],
    *,
    domain: str = "general",
    minimum_sources: int = DEFAULT_MINIMUM_SOURCES,
    today: str = "",
    required_questions: Optional[Mapping[str, str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Add a contract for every required dimension the model omitted.

    `required_axes` are the dynamic dimensions from `plan_dimensions`; when a
    `required_questions` mapping is supplied (as `axis -> literal question`),
    the injected contract uses that question verbatim instead of synthesizing
    one. Returns (plan, injected_axes).
    """
    present = {str(item.get("axis", "")) for item in plan}
    concept = _query_concept(query)
    year = _year_from(today)
    injected: List[str] = []
    next_id = max((int(item.get("id", 0)) for item in plan), default=0) + 1
    overrides = {
        dimension_to_axis(k): str(v).strip()
        for k, v in (required_questions or {}).items()
        if str(v).strip()
    }

    for raw_axis in required_axes or ():
        # `required_axes` may carry dynamic dimension labels ("cost and
        # financing"): map each to its canonical axis first so an existing
        # contract that already serves it counts as covering it.
        axis = dimension_to_axis(raw_axis)
        if axis in present or not str(raw_axis).strip():
            continue
        label = str(raw_axis).replace("_", " ").strip()
        if axis in overrides:
            question = overrides[axis]
            search_type = axis_search_type(axis)
            priority = 1
        elif axis in _AXIS_FALLBACK_TEMPLATES:
            suffix, search_type, priority = _AXIS_FALLBACK_TEMPLATES[axis]
            question = f"{concept} {suffix}{year}".strip()
        else:
            # A dynamic, non-canonical dimension with no supplied question.
            plan.append(
                synthesize_dimension_contract(
                    index=next_id,
                    dimension=label,
                    query=query,
                    domain=domain,
                    minimum_sources=minimum_sources,
                    today=today,
                )
            )
            present.add(axis)
            injected.append(axis)
            next_id += 1
            continue
        plan.append(
            _contract(
                index=next_id,
                question=question,
                axis=axis,
                search_type=search_type,
                priority=priority,
                domain=domain,
                coverage_goal=f"required dimension: {label}",
                minimum_sources=minimum_sources,
            )
        )
        present.add(axis)
        injected.append(axis)
        next_id += 1

    if injected:
        logger.info("[Planner] injected required dimensions: %s", ", ".join(injected))
    return plan, injected


def _subject_phrase(query: str, limit: int = 7) -> str:
    """The query's own subject, for filling scaffold placeholders.

    Content words only. It must never invent a subject, so it cannot fall back
    to a default topic — an empty result leaves the scaffold unsatisfiable and
    the caller skips injection rather than searching for the wrong thing.
    """
    words = [
        w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'&.-]*", query or "")
        if w.lower() not in _QUESTION_FILLER_WORDS and len(w) > 1
    ]
    return " ".join(words[:limit])


# Function/meta words stripped from a query when building a search scaffold, so
# "What are the current trends in AI?" searches "current trends AI" rather than
# "What are the current trends in AI?".
_QUESTION_FILLER_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "do", "does", "did", "what",
    "which", "who", "whom", "whose", "when", "where", "why", "how", "that",
    "this", "these", "those", "in", "on", "at", "of", "for", "to", "from",
    "with", "and", "or", "but", "it", "its", "be", "been", "about", "as",
    "should", "would", "could", "can", "will", "me", "i", "we", "us", "you",
    "please", "tell", "explain", "describe", "give", "list", "any", "some",
})


def _trends_scope_wanted(query: str) -> bool:
    """Does this question ask about a subject's current state and direction?

    This replaces the unconditional frontier injection that made the planner
    AI-shaped. A trends question ("current state of X", "future of X") genuinely
    benefits from capability/infrastructure/economics/adoption/regulation/safety
    spread, in ANY field. A narrow question ("what is TCP congestion control",
    "population of Malawi 2024") does not, and giving it six extra contracts is
    precisely how a research loop drifts away from what was asked.

    Narrow question-shape tokens veto the trends tokens, because "what is the
    current state of TCP congestion control" is still a definition question.
    """
    low = (query or "").lower()
    if any(tok in low for tok in _HARD_NARROW_TOKENS):
        return False
    # A survey noun ("trends in AI", "outlook for solar") is what the user is
    # ASKING FOR, so a leading "what are" is only the wh-word and must not veto.
    if _STRONG_TRENDS_RE.search(low):
        return True
    # Weaker phrases like "current state of X" also occur inside the noun
    # phrase being defined, so they require a following word AND no leading
    # interrogative: "what is the current state of TCP congestion control" is a
    # definition question wearing a trends phrase.
    if _TRENDS_RE.search(low):
        return not any(tok in low for tok in _SOFT_NARROW_TOKENS)
    return False


def enforce_frontier_axes(
    plan: List[Dict[str, Any]],
    query: str,
    *,
    domain: str = "general",
    minimum_sources: int = DEFAULT_MINIMUM_SOURCES,
    today: str = "",
    axes: Sequence[str] = FRONTIER_AXES,
    include_counter_evidence: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Add frontier-track contracts, but only when the question warrants them.

    The six frontier tracks are equal weight WHEN THEY APPLY. Previously this
    ran unconditionally on every query, which made every plan an AI-trends plan.
    It now runs only for a trends-style question about the user's own subject
    (`_trends_scope_wanted`), and each injected contract is built from a scaffold
    filled with the query's own words.

    Counter-evidence still gets its own track where the subject is contested:
    `criticism` asks for limitations of the subject, whereas the counter-evidence
    track asks for arguments that the prevailing narrative is wrong, which a
    hype-heavy corpus will never surface on its own. It is no longer forced onto
    questions that have no prevailing narrative to argue against.
    """
    subject = _subject_phrase(query)
    if not subject:
        return plan, []

    present = {str(item.get("axis", "")) for item in plan}
    # Also honour contracts the model labelled with a frontier synonym:
    # dimension_to_axis maps "ai safety" -> risk and "counter argument" ->
    # criticism, so check each contract's raw label AND its canonical mapping
    # against the frontier axis. Exact frontier axis names always count.
    normalized_present = set(present)
    for item in plan:
        raw = str(item.get("axis", ""))
        normalized_present.add(dimension_to_axis(raw))
        normalized_present.add(raw.replace(" ", "_"))
    wanted = list(axes or ())
    if include_counter_evidence:
        wanted.append(COUNTER_EVIDENCE_AXIS)

    injected: List[str] = []
    next_id = max((int(item.get("id", 0)) for item in plan), default=0) + 1
    for axis in wanted:
        if axis in normalized_present:
            continue
        scaffold = FRONTIER_AXIS_QUESTIONS.get(axis, "").strip()
        if not scaffold:
            continue
        question = scaffold.format(subject=subject)
        plan.append(
            _contract(
                index=next_id,
                question=question,
                axis=axis,
                search_type=axis_search_type(axis),
                priority=1,
                domain=domain,
                coverage_goal=f"frontier track: {axis}",
                minimum_sources=minimum_sources,
            )
        )
        normalized_present.add(axis)
        injected.append(axis)
        next_id += 1

    if injected:
        logger.info(
            "[Planner] injected frontier tracks for trends-scope question: %s",
            ", ".join(injected),
        )
    return plan, injected


