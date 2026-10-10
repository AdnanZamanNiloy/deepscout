"""Evidence recovery: turn a validation failure into targeted replacement queries.

Why this module exists
----------------------
The critic already DIAGNOSES evidence-quality failures per pass — `domains=1<2`,
`drift=0.62`, `concentration_on=<dim>`, `missing_primary_source=<axis>`,
`severe_conflicts=<n>` — and those gates correctly stop a run from finalizing on
thin evidence. What was missing is the second half of the recovery loop: when
validation fails, the queries issued next were generic
("`{query} official statistics data`") and did not correspond to the defect that
was actually found. So a run whose evidence was concentrated on ONE domain went
searching for the same area in the same place, re-found the same publisher, and
failed the same gate again.

This module is the missing half. It maps each diagnosed defect to a replacement
query aimed at the SPECIFIC weakness, reusing the existing source-targeting
registry (`build_dimension_primary_query`, `build_substitution_query`) rather
than a second query-building system:

  single-domain concentration  -> substitution query that EXCLUDES the dominant
                                  domain, so an independent publisher is sought
  irrelevant evidence / drift  -> re-scope onto the planned axis that produced
                                  nothing, using that axis's own question text
  no primary source for an axis-> a `site:`-scoped primary-source query for that
                                  exact dimension
  severe contradiction         -> a counter-evidence query naming the contested
                                  quantity, so the conflict gets adjudicated
  insufficient quantity        -> widen the axis with its own text

Every replacement carries its reason, so the trace shows WHY each query was
issued, and every query is deduplicated against what the run already searched
— a recovery that re-issues executed searches is not a recovery, it is spend.

Design constraints (AGENTS.md):
  * Deterministic, total, and domain-agnostic: nothing here names a subject
    string; every target comes from the question's own plan and pool.
  * No new evidence logic, no new LLM calls, no new search system.
  * Additive: an empty diagnosis yields no queries, and behaviour with no
    recovery needed is exactly as before.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.core.logging import get_logger

logger = get_logger(__name__)


# When a pool is concentrated on one domain, how many independent publishers
# the substitution query should aim at. Two is the point of the exercise: the
# gate that fired demanded a SECOND independent domain, so the replacement must
# actually reach somewhere else.
DIVERSITY_TARGET_SITES = 2

# A query shorter than this carries no targeting signal worth issuing.
MIN_QUERY_CHARS = 8


def diagnose(
    critique: Dict[str, Any],
    facts: Sequence[Dict[str, Any]],
    *,
    plan: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return the evidence-quality defects this pass found, most severe first.

    Reads ONLY what the critic already computed (`gate_failures`, `gaps`) plus
    the measured pool, so a diagnosis can never disagree with the gate that
    fired. Returns replacement-ready defect records; an empty list means the
    pool validated and no recovery is needed.
    """
    critique = critique if isinstance(critique, dict) else {}
    failures = [str(f) for f in (critique.get("gate_failures") or ()) if str(f or "").strip()]
    gaps = [str(g) for g in (critique.get("gaps") or ()) if str(g or "").strip()]
    stats = critique.get("stats") if isinstance(critique.get("stats"), dict) else {}

    defects: List[Dict[str, Any]] = []

    # --- concentration on ONE domain -------------------------------------
    # `concentration_on=` (evidence piled onto one dimension) and `domains=1<2`
    # (every fact from one publisher) describe the SAME defect for recovery
    # purposes: the pool reflects one source's framing. They are merged into a
    # single defect so the recovery does not emit the same substitution query
    # twice under two labels.
    dominant_dimension = ""
    single_domain = ""
    single_domain_detail = ""
    for failure in failures:
        if failure.startswith("concentration_on="):
            dominant_dimension = _dominant_dimension(failure)
        elif failure.startswith("domains=") and _int_after("domains=", failure) <= 1:
            single_domain = _dominant_domain(facts)
            single_domain_detail = failure
            break

    if dominant_dimension or single_domain:
        # Prefer the measured publisher over the focus layer's `_unassigned`
        # placeholder: a placeholder is not a domain that can be excluded.
        target = single_domain or (
            dominant_dimension if _is_domain_like(dominant_dimension) else _dominant_domain(facts)
        )
        defects.append({
            "kind": "concentration",
            "target": target,
            "detail": single_domain_detail or f"concentration_on={dominant_dimension}",
            "severity": 3,
        })

    # --- drift: the evidence is off the question --------------------------
    for failure in failures:
        if failure.startswith("drift="):
            defects.append({
                "kind": "drift",
                "target": "",
                "detail": failure,
                "severity": 3,
            })
            break

    # --- an axis with no primary source ------------------------------------
    for failure in failures:
        if failure.startswith("missing_primary_source="):
            axis = failure.split("primary_source_for:", 1)[-1].strip()
            defects.append({
                "kind": "missing_primary",
                "target": axis,
                "detail": failure,
                "severity": 2,
            })

    # --- an unresolved severe contradiction --------------------------------
    for failure in failures:
        if failure.startswith("severe_conflicts="):
            defects.append({
                "kind": "severe_conflict",
                "target": "",
                "detail": failure,
                "severity": 2,
            })
            break

    # --- a planned angle that produced nothing -----------------------------
    for gap in gaps:
        axis = _gap_target(gap)
        if axis:
            defects.append({
                "kind": "unsourced_angle",
                "target": axis,
                "detail": gap,
                "severity": 2,
            })

    # --- the pool is simply too small --------------------------------------
    for failure in failures:
        if failure.startswith("facts="):
            total = _int_after("facts=<", failure)
            if total:
                defects.append({
                    "kind": "insufficient_quantity",
                    "target": "",
                    "detail": failure,
                    "severity": 1,
                })
            break

    defects.sort(key=lambda d: (-int(d["severity"]), str(d["kind"])))
    return defects


def replacement_queries(
    critique: Dict[str, Any],
    facts: Sequence[Dict[str, Any]],
    *,
    plan: Optional[Sequence[Dict[str, Any]]] = None,
    query: str = "",
    executed: Optional[Sequence[str]] = None,
    limit: int = 4,
) -> List[Dict[str, Any]]:
    """Build targeted replacement queries for the diagnosed defects.

    Returns dicts `{"query": ..., "reason": ..., "kind": ...}` — the reason is
    what makes the recovery auditable in the trace, so a reader can see which
    defect each search was aimed at. Queries already executed (and near
    duplicates of them) are dropped: a recovery that re-runs a search is spend,
    not research.
    """
    defects = diagnose(critique, facts, plan=plan)
    if not defects:
        return []

    already = _normalize_set(executed or [])
    out: List[Dict[str, Any]] = []
    seen: set = set()

    for defect in defects:
        for q in _queries_for(defect, critique=critique, facts=facts, plan=plan, query=query):
            key = _normalize(q)
            if not key or key in already or key in seen:
                continue
            if len(q) < MIN_QUERY_CHARS:
                continue
            seen.add(key)
            out.append({
                "query": q,
                "reason": _reason_for(defect),
                "kind": str(defect["kind"]),
            })
            if len(out) >= max(1, int(limit)):
                return out
    return out


# --------------------------------------------------------------------------
# per-defect query construction
# --------------------------------------------------------------------------


def _queries_for(
    defect: Dict[str, Any],
    *,
    critique: Dict[str, Any],
    facts: Sequence[Dict[str, Any]],
    plan: Optional[Sequence[Dict[str, Any]]] = None,
    query: str = "",
) -> List[str]:
    kind = str(defect.get("kind", ""))
    target = str(defect.get("target", "") or "").strip()

    if kind == "concentration":
        # The strongest signal that a pool is one publisher's framing: seek an
        # EQUIVALENT publisher, explicitly excluding the dominant domain.
        out = _diversity_queries(target, facts, plan, query)
        if not out:
            out = _primary_queries_for_axes(plan, limit=DIVERSITY_TARGET_SITES)
        return out

    if kind == "drift":
        # Research wandered off the question. Recover by aiming at the planned
        # axes that produced NOTHING, in their own words — not by rewording the
        # original query, which is how the run drifted in the first place.
        return _unsourced_axis_queries(plan, facts)

    if kind == "missing_primary":
        axis = target or " ".join((str(c.get("axis", "")) for c in (plan or []) if isinstance(c, dict))[:1])
        q = _primary_query_for(axis, plan)
        return [q] if q else []

    if kind == "severe_conflict":
        # Adjudicate the conflict with a query that names the contested axis.
        return _counter_evidence_queries(plan, query)

    if kind == "unsourced_angle":
        q = _question_for_axis(target, plan)
        if q:
            return [q, f"{q} primary source official data"]
        return []

    if kind == "insufficient_quantity":
        # More of the SAME area, not a new one: widen every planned axis.
        out = _question_for_each_axis(plan, limit=2)
        if not out and query:
            out = [f"{query} evidence statistics data"]
        return out

    return []


def _diversity_queries(
    dominant: str,
    facts: Sequence[Dict[str, Any]],
    plan: Optional[Sequence[Dict[str, Any]]],
    query: str,
) -> List[str]:
    """Substitution queries that EXCLUDE the dominant domain."""
    from app.agents.sources import build_substitution_query

    # `dominant` may be the focus layer's `_unassigned` placeholder (facts
    # attributed to no planned dimension) rather than a real publisher. That is
    # not a domain to exclude — fall back to the measured dominant publisher.
    blocked = dominant if _is_domain_like(dominant) else _dominant_domain(facts)
    axes = _questions_for_axes(plan, limit=2)
    if not axes and query:
        axes = [query]

    out: List[str] = []
    for question in axes:
        q = build_substitution_query(
            question, "general", blocked or "", max_sites=DIVERSITY_TARGET_SITES
        )
        q = (q or "").strip()
        if q and q not in out:
            out.append(q)
    return out


def _is_domain_like(value: str) -> bool:
    """True for a real publisher host, false for the `_unassigned` placeholder
    and other non-domain markers the focus layer emits."""
    token = (value or "").strip().lower()
    if not token or token.startswith("_"):
        return False
    return "." in token


def _primary_query_for(axis: str, plan: Optional[Sequence[Dict[str, Any]]]) -> str:
    """A `site:`-scoped primary-source query for one axis (deterministic)."""
    from app.agents.sources import build_dimension_primary_query

    question = _question_for_axis(axis, plan) or axis
    if not question:
        return ""
    return (build_dimension_primary_query(question, "academic") or "").strip()


def _primary_queries_for_axes(
    plan: Optional[Sequence[Dict[str, Any]]], *, limit: int
) -> List[str]:
    from app.agents.sources import build_dimension_primary_query

    out: List[str] = []
    for question in _questions_for_axes(plan, limit=limit):
        q = (build_dimension_primary_query(question, "academic") or "").strip()
        if q and q not in out:
            out.append(q)
    return out


def _unsourced_axis_queries(
    plan: Optional[Sequence[Dict[str, Any]]], facts: Sequence[Dict[str, Any]]
) -> List[str]:
    """Queries for planned axes that have no facts — the drift correction."""
    covered = {
        str(f.get("sub_question", "") or "").strip().lower()
        for f in (facts or []) if isinstance(f, dict)
    }
    out: List[str] = []
    for question in _questions_for_axes(plan, limit=3):
        if question.strip().lower() in covered:
            continue
        out.append(question)
        primary = _primary_query_for(question, plan)
        if primary and primary not in out:
            out.append(primary)
    return out[:3]


def _counter_evidence_queries(
    plan: Optional[Sequence[Dict[str, Any]]], query: str
) -> List[str]:
    """Conflict-adjudicating queries, aimed at independent disagreement."""
    out: List[str] = []
    for question in _questions_for_axes(plan, limit=2):
        out.append(f"{question} conflicting evidence disagreement")
    if not out and query:
        out.append(f"{query} criticism counter-evidence")
    return out


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _questions_for_axes(
    plan: Optional[Sequence[Dict[str, Any]]], *, limit: int = 3
) -> List[str]:
    """The plan's own question texts, de-duplicated and bounded."""
    out: List[str] = []
    for contract in plan or []:
        if not isinstance(contract, dict):
            continue
        text = str(contract.get("question", "") or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _question_for_each_axis(
    plan: Optional[Sequence[Dict[str, Any]]], *, limit: int = 3
) -> List[str]:
    return _questions_for_axes(plan, limit=limit)


def _question_for_axis(axis: str, plan: Optional[Sequence[Dict[str, Any]]]) -> str:
    """The question text of the contract whose axis matches `axis`."""
    axis = (axis or "").strip().lower().replace("_", " ")
    if not axis:
        return ""
    for contract in plan or []:
        if not isinstance(contract, dict):
            continue
        contract_axis = str(contract.get("axis", "") or "").strip().lower().replace("_", " ")
        if contract_axis and contract_axis == axis:
            return str(contract.get("question", "") or "").strip()
    return ""


def _gap_target(gap: str) -> str:
    text = str(gap or "").strip()
    if ":" in text:
        return text.rsplit(":", 1)[-1].strip()
    return text


def _dominant_domain(facts: Sequence[Dict[str, Any]]) -> str:
    """The domain holding the most facts — the concentration the gate named."""
    from app.agents.evidence_utils import extract_domain

    counts: Dict[str, int] = {}
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        domain = extract_domain(str(fact.get("source", "") or ""))
        if not domain:
            continue
        counts[domain] = counts.get(domain, 0) + 1
    if not counts:
        return ""
    return max(sorted(counts), key=lambda d: counts[d])


def _dominant_dimension(failure: str) -> str:
    """`concentration_on=<dim>=0.62` -> `<dim>`."""
    body = failure.split("concentration_on=", 1)[-1]
    return body.split("=", 1)[0].strip()


def _int_after(prefix: str, text: str) -> int:
    tail = text.split(prefix, 1)[-1]
    digits = ""
    for ch in tail:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0


def _reason_for(defect: Dict[str, Any]) -> str:
    kind = str(defect.get("kind", ""))
    target = str(defect.get("target", "") or "").strip()
    if kind == "concentration":
        return f"evidence concentrated on one publisher ('{target}'); substituting an equivalent independent source"
    if kind == "drift":
        return "evidence drifted off the question; re-scoping to uncovered plan axes"
    if kind == "missing_primary":
        return f"no primary source for '{target}'; targeting official/peer-reviewed publishers"
    if kind == "severe_conflict":
        return "unresolved cross-source conflict; seeking adjudicating evidence"
    if kind == "unsourced_angle":
        return f"planned angle '{target}' produced no evidence"
    if kind == "insufficient_quantity":
        return "evidence pool too small to support a conclusion"
    return "evidence validation failed"


def _normalize(query: str) -> str:
    """Stable key for dedup: lowercase, whitespace-collapsed."""
    from app.agents.planner import normalize_text

    return normalize_text(str(query or ""))


def _normalize_set(queries: Sequence[str]) -> set:
    return {_normalize(q) for q in (queries or []) if str(q or "").strip()}
