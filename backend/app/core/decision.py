"""Decision Intelligence Layer (Phase 3.5, Feature 18).

Derives named strategic options from the synthesized findings and
contradictions, marks exactly one recommended via an explicit code-level
rule, and attaches evidence-backed rationale + risk per option.

Code-level recommendation rule (manual 3.5: don't leave it to unstructured
LLM judgment): each option scores on (verified-support, contradiction
penalty, confidence alignment); the highest score wins, ties broken by
lower downside risk. Factual/definitional queries produce no options —
the report has no Decision Layer section at all.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.core.config import Settings

MAX_OPTIONS = 4
MIN_OPTIONS_COMPARATIVE = 2


def _url_to_axis(state: Dict[str, Any]) -> Dict[str, str]:
    """source URL -> axis via search result sub_question + planner contracts."""
    contract_axis: Dict[str, str] = {}
    for q in state.get("sub_questions", []):
        if isinstance(q, dict):
            question = str(q.get("question", "")).strip()
            axis = str(q.get("axis", "")).strip()
            if question and axis:
                contract_axis[question] = axis
    url_axis: Dict[str, str] = {}
    for result in state.get("search_results", []) or []:
        url = str(result.get("url", "")).strip()
        sub_q = str(result.get("sub_question", "")).strip()
        if url and sub_q in contract_axis:
            url_axis[url] = contract_axis[sub_q]
    return url_axis


def _verified_supporting_claims(
    option_axis: str,
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Verified facts attributed to the option's axis (URL-based attribution,
    falling back to keyword overlap)."""
    facts = state.get("facts", [])
    url_axis = _url_to_axis(state)
    keywords = set(option_axis.lower().split())
    supporting = []
    for f in facts:
        if not f.get("verified"):
            continue
        attributed_axis = url_axis.get(str(f.get("source", "")).strip(), "")
        claim_words = set(str(f.get("claim", "")).lower().split())
        if attributed_axis == option_axis or keywords & claim_words:
            supporting.append(f)
    return supporting


def _contradiction_penalty(contradictions: List[Dict[str, Any]]) -> int:
    # MVP: each UNRESOLVED contradiction raises the risk of every substantive
    # option equally (we can't yet attribute a contradiction to one option).
    # Fix C: resolved conflicts (period/scope/metric) are explained spreads,
    # not sources of decision uncertainty.
    return sum(
        1 for c in (contradictions or [])
        if isinstance(c, dict) and not c.get("resolved")
    )


def _primary_support_count(supporting: List[Dict[str, Any]]) -> int:
    """How many of an option's supporting claims rest on a primary source.

    Primary evidence (a dataset, official series, statute, peer-reviewed
    paper) is what makes a recommendation FEASIBLE to defend; commentary alone
    is not. Deterministic, from the same facts the option already carries.
    """
    n = 0
    for f in supporting or ():
        if f.get("is_primary"):
            n += 1
            continue
        try:
            from app.agents.sources import is_primary_source

            if is_primary_source(str(f.get("source", "") or "")):
                n += 1
        except Exception:
            continue
    return n


def _uncertainty_level(unresolved_conflicts: int, support: int, primary: int) -> str:
    """Coarse, honest uncertainty band for an option.

    Derived from the two things a reader can act on: how contested the
    evidence is (unresolved cross-source conflicts) and how thin the support
    is. Never claims more certainty than the pool supports — a
    single-source option is "high" uncertainty however confident the prose.
    """
    if unresolved_conflicts >= 2:
        return "high"
    if support == 0:
        return "high"
    if unresolved_conflicts >= 1 or support < 2 or primary == 0:
        return "medium"
    return "low"


def _feasibility_note(axis: str, support: int, primary: int, support_score: int) -> str:
    """A feasibility assessment grounded in the evidence behind the option.

    Feasibility here means "how defensible is acting on this framing, given
    what was actually found" — evidenced, not asserted. More independent,
    primary-backed claims means a more feasible recommendation to stand
    behind.
    """
    if support_score == 0:
        return (
            f"No verified claim supports the '{axis}' framing yet; acting on it "
            "would rest on no evidence gathered in this run."
        )
    if primary == 0:
        return (
            f"Defensible but secondary: {support_score} verified claim(s) back "
            f"'{axis}', none from a primary source, so the recommendation rests "
            "on commentary rather than measured data."
        )
    return (
        f"Feasible: {support_score} verified claim(s) back '{axis}', "
        f"including {primary} from primary source(s)."
    )


def build_decision_layer(
    state: Dict[str, Any],
    settings: Settings | None = None,
) -> List[Dict[str, Any]]:
    """Return the DecisionOption list for the report + persistence.

    Factual/definitional queries produce NO options: the report simply has
    no Decision Layer section. The old single "no material decision"
    placeholder read as manufactured filler in the UI.
    """
    orchestration = state.get("orchestration", {})
    query_type = str(orchestration.get("query_type", "factual"))

    if query_type not in ("comparative", "analytical"):
        return []

    contradictions = state.get("contradictions", [])
    axes: List[str] = []
    for q in state.get("sub_questions", []):
        if isinstance(q, dict):
            axis = str(q.get("axis", "")).strip()
            if axis and axis not in axes:
                axes.append(axis)

    if len(axes) < MIN_OPTIONS_COMPARATIVE:
        return []

    # Derive one option per leading axis (up to MAX_OPTIONS): the axis IS the
    # strategic framing — e.g. a cost axis yields "prioritize cost evidence".
    options: List[Dict[str, Any]] = []
    unresolved_conflicts = _contradiction_penalty(contradictions)
    for idx, axis in enumerate(axes[:MAX_OPTIONS]):
        label = chr(ord("A") + idx)
        supporting = _verified_supporting_claims(axis, state)
        support_score = len(supporting)
        primary = _primary_support_count(supporting)
        risk = unresolved_conflicts
        top_claim = supporting[0]["claim"] if supporting else ""
        # Explicit citation refs so the rationale is traceable to evidence, not
        # just a count. Bounded and de-duplicated.
        citations: List[str] = []
        for f in supporting:
            src = str(f.get("source", "") or "").strip()
            if src and src not in citations:
                citations.append(src)
            if len(citations) >= 3:
                break
        options.append({
            "option_label": label,
            "description": (
                f"Frame the decision primarily around the '{axis}' dimension "
                f"of the findings."
            ),
            "is_recommended": False,
            "rationale": (
                f"Supported by {support_score} verified claim(s)"
                + (f', e.g. "{top_claim[:120]}"' if top_claim else "")
                + "."
            ),
            "risk_note": (
                f"Narrows the decision to '{axis}'; "
                f"{risk} unresolved source contradiction(s) raise uncertainty."
                if risk
                else f"Narrows the decision to '{axis}' alone."
            ),
            # New, additive fields (Feature 18 completeness): a recommendation
            # must carry its reasoning (rationale), evidence (supporting_sources),
            # a feasibility assessment, and an honest uncertainty band.
            "supporting_sources": citations,
            "feasibility": _feasibility_note(axis, support_score, primary, support_score),
            "uncertainty": _uncertainty_level(unresolved_conflicts, support_score, primary),
            "_support": support_score,
            "_risk": risk,
        })

    # Code-level recommendation: most verified support; ties broken by fewer
    # contradictions-weighted risk, then lowest label for determinism.
    options.sort(key=lambda o: (-o["_support"], o["_risk"], o["option_label"]))
    options[0]["is_recommended"] = True

    for o in options:
        o.pop("_support", None)
        o.pop("_risk", None)
    return options
