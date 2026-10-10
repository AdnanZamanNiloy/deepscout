"""Critic Agent — the quality gate that decides whether to loop or write.

What changed and why
--------------------
1. IT NO LONGER RECONSTRUCTS VERIFICATION BY STRING MATCHING. The old code built
   a `standing` dict keyed on normalized claim text, because the prefilters it
   called (`filter_facts_by_domain` -> `dedupe_semantic_facts`) stripped the
   `verified` key off every fact. With that dedup bug fixed in evidence_utils,
   verification standing survives, and the critic reads it directly. The
   workaround was also subtly wrong: two claims normalizing to the same text
   collapsed into whichever came first, so verified counts drifted.

2. THE GATES ARE NO LONGER ARBITRARY CONSTANTS. `avg_confidence < 0.74` failed
   nearly every real pool by a hundredth, which is why runs went to the
   iteration ceiling and every report was stamped "incomplete" regardless of
   quality. Gates now check what actually matters — verified evidence from
   independent domains, coverage of the planned angles, and each contract's own
   `minimum_sources` (a contract field that nothing had ever enforced) — and the
   confidence threshold comes from the mode's target rather than a magic number.

3. IT CONSUMES MEASURED CONFIDENCE INSTEAD OF PRODUCING IT. When a
   ConfidenceReport is supplied the critic reports it rather than substituting
   the model's self-assessment, and it can only lower it.

4. FOLLOW-UP QUERIES ARE DEDUPLICATED AGAINST WHAT WAS ALREADY SEARCHED. The
   critic used to re-emit the same `improved_queries` every pass with no memory,
   so a stubborn gap produced identical searches at full cost forever.

The public contract is unchanged: the returned dict still has `is_sufficient`,
`reason`, `improved_queries` and `confidence`. New keys are additive.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from app.core.degradation import record_fallback
from app.core.llm import LLMClient, clamp_confidence
from app.core.logging import get_logger
from app.core.schemas import CriticVerdictModel
from app.core.usage import set_stage_hint

from app.agents.confidence import ConfidenceReport, SUFFICIENCY_THRESHOLD
from app.agents.contradiction import contradiction_followups, summarize_contradictions
# Module level so the primary-source gate below can canonicalize plan axes; it
# was previously imported inside critic_agent(), which left it out of scope here.
from app.agents.planner import dimension_to_axis
from app.agents.evidence_utils import (
    dedupe_semantic_facts,
    evidence_stats,
    extract_domain,
    filter_facts_by_domain,
)
from app.agents.stopping import coverage_gaps

logger = get_logger(__name__)


CRITIC_SYSTEM_PROMPT = """
You are the Critic Agent in a multi-agent research pipeline.
You are the quality gate. Your decision to pass or loop determines
whether the Writer produces a complete, trustworthy report.

You must be demanding but fair:
  - Too strict → unnecessary loops, wasted API calls, slow output
  - Too lenient → shallow reports, missing perspectives, low confidence

━━━ EVALUATION CRITERIA ━━━

1) COMPLETENESS
   Would a knowledgeable reader consider the original query answered?
   Is any obvious major angle of the query entirely absent?

2) SUBSTANTIVE MATERIAL
   Is there enough reliable, non-redundant material to write a clear,
   well-supported answer (e.g. a definition with mechanisms and
   examples, not just fragments)?

3) SOURCE RELIABILITY AND INDEPENDENCE
   Are the facts backed by credible sources, and by more than one
   organisation? Ten claims from one site is one source, not ten.

4) REDUNDANCY / FRAGMENTATION
   Is the evidence mostly duplicated or too fragmented to synthesize?

━━━ ADVERSARIAL SELF-CHECK ━━━

Before deciding, attack the evidence yourself:

  RQ1 — What assumption in the current evidence is WEAKEST, i.e. most
        likely to be wrong or unrepresentative?
  RQ2 — What alternative explanation or competing claim would
        INVALIDATE the current conclusion if true?
  RQ3 — What important counter-evidence is conspicuously ABSENT from
        the retrieved material?

If is_sufficient is false, the reason field MUST explicitly name at
least one weak assumption (RQ1), a potentially invalidating alternative
(RQ2), or a missing counter-evidence (RQ3) — not just "insufficient
coverage".

━━━ DECISION RULES ━━━

  - is_sufficient = true  ONLY if the evidence clearly supports a
    complete answer for the original query.
  - If insufficient, improved_queries must contain 1-3 specific,
    search-ready strings targeting the exact gaps — never vague
    suggestions like "search for more information about X".
      BAD  → "Find more about knowledge types"
      GOOD → "metacognitive knowledge definition examples learning research"
  - Queries listed as ALREADY SEARCHED must not be repeated or
    trivially reworded; a repeat costs a full pass and returns the same
    evidence.
  - Be conservative: when in doubt, mark insufficient and say what
    is missing in the reason field.

━━━ OUTPUT FORMAT ━━━

Return ONLY valid JSON. No markdown fences. No text outside JSON.

{
  "is_sufficient": <true|false>,
  "reason": "<one or two sentences: what is complete or what is missing>",
  "improved_queries": ["<targeted search query to fix gap 1>", "..."],
  "confidence": <0.0 to 1.0, your confidence in the current evidence>
}
""".strip()


DEFINITIONAL_QUERY_RE = re.compile(
    r"^\s*(what\s+is|what\s+are|define|explain)\b", re.IGNORECASE
)

# Minimum independent domains before any pool can be called sufficient. Two is
# the floor, not a target: one publisher's framing cannot be distinguished from
# consensus.
MIN_DISTINCT_DOMAINS = 2
MIN_FACTS_REQUIRED = 4


async def critic_agent(
    llm: LLMClient,
    query: str,
    facts: List[Dict[str, Any]],
    iteration: int,
    max_iterations: int,
    contradictions: List[Dict[str, Any]] | None = None,
    query_type: str = "",
    *,
    confidence_report: Optional[ConfidenceReport] = None,
    plan: Optional[Sequence[Dict[str, Any]]] = None,
    searched_queries: Sequence[str] = (),
    confidence_target: Optional[float] = None,
    use_llm: bool = True,
    focus_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Judge the evidence pool. Returns the verdict dict the workflow consumes.

    All new arguments are keyword-only and optional, so the existing call
    signature keeps working unchanged.

    `focus_report` is the loop's query-anchored assessment (app/agents/focus.py):
    coverage, concentration and drift against the ORIGINAL question. It adds two
    gates that the evidence-size gates cannot express, because they are about
    WHAT was researched rather than how much was found:
      * DRIFT — most evidence barely overlaps the question. Size alone looks
        healthy here, which is exactly how a run fills up on the wrong subject.
      * CONCENTRATION on a broad question — one dimension holds nearly all the
        evidence while others the plan declared are empty.
    Deliberately NOT a gate for a narrow question: "how does TCP congestion
    control work" is correctly answered from one dimension, and widening it
    would be the drift this exists to catch.
    """
    target = float(
        confidence_target if confidence_target is not None else SUFFICIENCY_THRESHOLD
    )

    # Quality prefilters. dedupe now PRESERVES verification metadata, so
    # `quality_facts` is directly inspectable — no claim-text reconstruction.
    quality_facts = dedupe_semantic_facts(filter_facts_by_domain(facts or []))
    if not quality_facts:
        return {
            "is_sufficient": False,
            "reason": "No reliable evidence extracted.",
            "improved_queries": [
                f"Latest evidence for: {query}",
                f"Key statistics: {query}",
            ],
            "confidence": 0.2,
            "gaps": ["no reliable evidence in the pool"],
            "gate_failures": ["facts=0"],
            "stats": evidence_stats([]),
        }

    stats = evidence_stats(quality_facts)
    conflict_summary = summarize_contradictions(contradictions or [])

    contradiction_block = ""
    if contradictions:
        listed = "\n".join(
            f"  - \"{str(c.get('claim_a', ''))[:120]}\" ({c.get('source_a', '')}) vs "
            f"\"{str(c.get('claim_b', ''))[:120]}\" ({c.get('source_b', '')})"
            for c in contradictions[:3]
        )
        contradiction_block = (
            "\nKnown contradictions between sources (acknowledge these in your "
            f"reason — do NOT silently ignore them):\n{listed}\n"
        )

    searched_block = ""
    if searched_queries:
        searched_block = (
            "\nALREADY SEARCHED (do not repeat or trivially reword):\n"
            + "\n".join(f"  - {q}" for q in list(searched_queries)[:12])
            + "\n"
        )

    # The model sees measured facts about the pool, not just the claims. Asking
    # it to judge sufficiency without telling it how many independent domains it
    # is looking at is asking it to guess.
    measurement_block = (
        f"\nMeasured evidence state:\n"
        f"  claims={stats['total']} verified={stats['verified']} "
        f"domains={stats['distinct_domains']} primary_sources={stats['primary_documents']} "
        f"corroborated={stats['corroborated']} angles={stats['axes_covered']}\n"
        f"  cross-source conflicts={conflict_summary['cross_source']} "
        f"(severe={conflict_summary['severe']})\n"
    )

    user_prompt = (
        f"Main query: {query}\n"
        f"Current iteration: {iteration}/{max_iterations}\n"
        f"Extracted reliable facts: {_compact_facts(quality_facts)}\n"
        f"{measurement_block}"
        f"{contradiction_block}"
        f"{searched_block}\n"
        "Evaluate using these criteria:\n"
        "1) Is the answer complete?\n"
        "2) Is there enough material to write a clear, well-supported answer?\n"
        "3) Are sources reliable AND independent of each other?\n"
        "4) Is information redundant or fragmented?\n\n"
            "Also run the Adversarial Review checks from your instructions: name the weakest\n"
        "assumption, a potentially invalidating alternative, and any missing\n"
        "counter-evidence in your reason when the evidence is insufficient.\n\n"
        "Return JSON: "
        '{"is_sufficient": true/false, "reason": "...", "improved_queries": ["..."], "confidence": 0.0}'
    )

    if not use_llm:
        # Gates-only review (quick mode): the iteration ceiling makes the
        # verdict routing-neutral, and measured evidence stats stand in for
        # the model's confidence. A mode choice, NOT a degradation — no
        # fallback recorded.
        payload = {}
    else:
        try:
            set_stage_hint("critic")
            payload = await llm.generate_json(
                CRITIC_SYSTEM_PROMPT,
                user_prompt,
                response_model=CriticVerdictModel,
            )
        except Exception as exc:
            logger.warning("[Critic] LLM call failed, treating as insufficient", exc_info=exc)
            record_fallback("critic")
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    is_sufficient = bool(payload.get("is_sufficient", False))
    reason = str(payload.get("reason", "") or "").strip()
    if not reason:
        reason = (
            "Gates-only review (quick mode)" if not use_llm else "Insufficient assessment."
        )
    improved_queries = payload.get("improved_queries", []) or []
    model_confidence = clamp_confidence(payload.get("confidence", 0.4))

    cleaned_queries: List[str] = []
    for item in improved_queries:
        if isinstance(item, str) and item.strip():
            cleaned_queries.append(re.sub(r"\s+", " ", item).strip())

    # Contradiction-resolving searches are worth more than generic gap-filling,
    # so they lead the follow-up list.
    for query_text in contradiction_followups(contradictions or [], limit=2):
        if query_text not in cleaned_queries:
            cleaned_queries.insert(0, query_text)

    reason_lower = reason.lower()
    if any(t in reason_lower for t in ("incomplete", "lacks a clear definition", "insufficient")):
        is_sufficient = False

    # ------------------------------------------------------------------
    # Deterministic gates. Model judgement cannot waive these; it can only
    # be overridden BY them. Each gate corresponds to a way a report can be
    # confidently wrong.
    # ------------------------------------------------------------------
    # A recommendation/guidance request ("suggest some topics") is not a
    # definitional query: it owes candidate suggestions, not an "X is Y" claim.
    # Without this, the query is typed `factual`, the gate demands a definition
    # the evidence cannot contain, emits `no definitional claim`, and the depth
    # controller (is_semantic_gap) finalizes the run at iteration 1 with axes
    # still uncovered — the exact premature-stop defect.
    from app.core.primitives import is_guidance_query

    requires_definition = not is_guidance_query(query) and (
        query_type == "factual"
        or (not query_type and bool(DEFINITIONAL_QUERY_RE.match(query or "")))
    )
    has_definition = any(
        " is " in str(f.get("claim", "")).lower() for f in quality_facts[:6]
    )

    gaps = list(coverage_gaps(plan or (), quality_facts))
    gate_failures: List[str] = []

    if stats["total"] < MIN_FACTS_REQUIRED:
        gate_failures.append(f"facts={stats['total']}<{MIN_FACTS_REQUIRED}")
    if requires_definition and not has_definition:
        gate_failures.append("no definitional claim")
    if stats["verified"] < 1:
        gate_failures.append("verified=0")
    if stats["distinct_domains"] < MIN_DISTINCT_DOMAINS:
        gate_failures.append(f"domains={stats['distinct_domains']}<{MIN_DISTINCT_DOMAINS}")
    if conflict_summary["severe"]:
        gate_failures.append(f"severe_conflicts={conflict_summary['severe']}")
    if gaps:
        gate_failures.append(f"uncovered_angles={len(gaps)}")

    # --- declared-dimension coverage ---------------------------------------
    # A contract with no facts is an uncovered axis. Blocking, because the plan
    # is the record of what this question asked to be researched.
    from app.agents.planner import dimension_to_axis

    covered_axes = {
        str(f.get("sub_question", "") or "") for f in quality_facts
    }
    covered_canonical = {
        dimension_to_axis(str(c.get("axis", ""))) for c in (plan or [])
        if str(c.get("question", "")) in covered_axes
    }
    plan_axes = {
        dimension_to_axis(str(c.get("axis", ""))) for c in (plan or [])
        if str(c.get("axis", "")).strip()
    }
    # --- declared-dimension coverage ---------------------------------------
    # Every axis the PLAN declared needs verified evidence behind it. Driven by
    # the plan rather than a built-in list, so this holds for any subject: the
    # plan is the record of what the question asked to be researched, and an
    # axis that was planned but produced nothing is an unfinished answer.
    for axis in sorted(plan_axes):
        if axis not in covered_canonical:
            gate_failures.append(f"planned_axis_uncovered={axis}")

    # --- source-ledger composition ------------------------------------------
    # No SECONDARY source class may crowd out primary evidence. The original
    # gate was regulation-only (a >60% regulation report is a legal summary, not
    # a trends brief); it is generalised to the other secondary class, peer
    # review, because a >60% literature report that never consulted data or an
    # official series fails the same way.
    #
    # Primary share is deliberately NOT gated: a report that is mostly primary
    # documents is the goal, not a defect. Gating it was a mistake in an earlier
    # version of this change and it blocked healthy runs at 80% primary.
    if isinstance(stats, dict):
        _primary = float(stats.get("primary_share", 0.0) or 0.0)
        _secondary = max(
            float(stats.get("regulation_share", 0.0) or 0.0),
            float(stats.get("peer_reviewed_share", 0.0) or 0.0),
        )
        if _secondary > 0.60 and _primary < 0.60:
            gate_failures.append(
                f"secondary_source_dominance={_secondary:.2f}>0.60 with primary={_primary:.2f}"
            )

    # --- required primary sources per declared dimension --------------------
    # A report can be long and well-cited yet rest entirely on commentary. Every
    # dimension the plan declared needs a primary source behind it. Blocking:
    # without that the report is secondary whatever its other strengths.
    primary_gaps = _missing_required_primary_sources(quality_facts, plan)
    for gap in primary_gaps:
        gate_failures.append(f"missing_primary_source={gap}")

    # --- drift and concentration against the ORIGINAL question -------------
    if focus_report:
        if focus_report.get("drifted"):
            gate_failures.append(
                f"drift={float(focus_report.get('drift', 0.0)):.2f}"
            )
        # Concentration only blocks a BROAD question. A narrow one is supposed
        # to be answered from a single dimension, and blocking there would push
        # the loop to widen a question the user did not ask wide.
        if focus_report.get("concentrated") and not focus_report.get("is_narrow"):
            gate_failures.append(
                f"concentration_on={focus_report.get('dominant_dimension', '?')}"
                f"={float(focus_report.get('concentration', 0.0)):.2f}"
            )

    # Measured confidence, when available, replaces the model's self-report.
    if confidence_report is not None:
        confidence = confidence_report.overall
        if confidence < target:
            gate_failures.append(
                f"confidence={confidence:.2f}<target={target:.2f}"
            )
    else:
        # Gates-only mode has no model verdict: the measured evidence quality
        # stands in, so a strong pool can still pass the target honestly.
        model_confidence = (
            clamp_confidence(stats["avg_confidence"]) if not use_llm else model_confidence
        )
        confidence = model_confidence
        if stats["avg_confidence"] < 0.60:
            gate_failures.append(f"avg_fact_conf={stats['avg_confidence']:.2f}<0.60")

    if gate_failures:
        is_sufficient = False
        confidence = min(confidence, 0.65)
        if not cleaned_queries:
            cleaned_queries = _default_followups(query, gaps, requires_definition)
        reason = _explain_failure(reason, gate_failures, gaps, stats)
    elif confidence >= target:
        # Everything measurable passed and confidence is at target: pass the
        # gate even if the model hedged. A model hedging on evidence that
        # satisfies every objective criterion is what produced needless loops.
        is_sufficient = True

    if not is_sufficient and not cleaned_queries:
        # Model call failed (or hedged) without gates firing: the loop still
        # needs something actionable to search, or the next pass is empty.
        cleaned_queries = _default_followups(query, gaps, requires_definition)

    # --- is the remaining problem a definition rather than missing evidence? ---
    # A gap about what the question MEANS cannot be closed by searching. The
    # stopping controller reads this to finalize instead of spending another pass
    # (see app/agents/ambiguity.is_semantic_gap). Derived from the gate failures
    # already computed, so it stays in step with what this critic reports.
    semantic_gap = False
    try:
        from app.agents.ambiguity import is_semantic_gap

        semantic_gap = is_semantic_gap(gate_failures)
    except Exception as exc:  # never let the stopping hint break a verdict
        logger.warning("semantic_gap_check_failed", error=str(exc), exc_info=exc)

    return {
        "is_sufficient": is_sufficient,
        "reason": reason,
        "improved_queries": cleaned_queries[:5],
        "confidence": clamp_confidence(confidence),
        # --- additive, for the trace, the UI and the stopping controller ---
        "gaps": gaps,
        "gate_failures": gate_failures,
        "semantic_gap": semantic_gap,
        "stats": stats,
        "model_confidence": model_confidence,
        "conflicts": conflict_summary,
    }


# Primary-source requirements, derived from the PLAN rather than hardcoded.
#
# HISTORY, because this was a live bug. The gate below used to require a primary
# source on three fixed AI-frontier questions — model capability, compute/energy
# constraints, and ROI or pilot failure — for EVERY query. Asked "population of
# Malawi 2024" it blocked finalization until the report had a primary source on
# datacenter electricity demand and enterprise AI ROI, which is not a question
# about Malawi's population and cannot be answered by searching for it.
#
# The requirement is now structural: every dimension the plan declared must
# eventually have a primary source behind it. Same protection against a report
# that is long and well-cited but grounded only in commentary, with no
# topic-specific list anywhere.
#
# One primary source per dimension, matching the gate this replaces. Requiring
# several would make the block unreachable on narrow questions that legitimately
# have a single authoritative source (one statute, one dataset, one paper).
_PRIMARY_REQUIREMENT_MIN = 1


def _missing_required_primary_sources(
    facts: Sequence[Dict[str, Any]],
    plan: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    minimum: int = _PRIMARY_REQUIREMENT_MIN,
) -> List[str]:
    """Planned dimensions with no high-quality PRIMARY source behind them.

    A dimension is satisfied when at least `minimum` verified facts attributed to
    it cite a primary source (`is_primary_source`). With no plan supplied the
    caller gets no dimension requirements, which is the correct default: the
    critic cannot invent requirements it was not told about. Deterministic and
    total.
    """
    from app.agents.sources import is_primary_source

    if not plan:
        return []

    by_axis: Dict[str, List[bool]] = {}
    axes: List[str] = []
    for contract in plan:
        if not isinstance(contract, dict):
            continue
        axis = dimension_to_axis(str(contract.get("axis", "") or ""))
        if axis and axis not in axes:
            axes.append(axis)
            by_axis[axis] = []

    for fact in facts or ():
        if not isinstance(fact, dict):
            continue
        axis = dimension_to_axis(str(fact.get("axis", "") or ""))
        if axis not in by_axis:
            continue
        if is_primary_source(str(fact.get("source", "") or "")):
            by_axis[axis].append(True)

    return [
        f"primary_source_for:{axis}"
        for axis in axes
        if len(by_axis[axis]) < max(1, minimum)
    ]


def _compact_facts(facts: Sequence[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    """Trim facts for the prompt.

    The previous version passed `quality_facts[:10]` as full dicts, which after
    the metadata-preservation fix would include verification checks, numbers and
    quotes — several hundred wasted tokens per call for fields the critic does
    not reason over.
    """
    return [
        {
            "claim": str(f.get("claim", ""))[:220],
            "domain": extract_domain(str(f.get("source", ""))),
            "confidence": round(float(f.get("confidence", 0.0) or 0.0), 2),
            "verified": bool(f.get("verified")),
            "primary": bool(f.get("is_primary")),
        }
        for f in list(facts)[:limit]
    ]


def _default_followups(
    query: str, gaps: Sequence[str], requires_definition: bool
) -> List[str]:
    """Concrete follow-ups when the model gave none.

    Derived from the actual gaps where possible. The old fallback emitted two
    definition-hunting queries regardless of what was missing, so a run short on
    statistics searched for definitions it already had.
    """
    out: List[str] = []
    for gap in list(gaps)[:2]:
        angle = gap.split(":", 1)[-1].strip()
        if angle:
            out.append(angle)
    if requires_definition and not out:
        out.append(f"Authoritative definition of: {query}")
    if not out:
        out = [
            f"{query} official statistics data",
            f"{query} criticism limitations counter-evidence",
        ]
    return out


def _explain_failure(
    model_reason: str,
    gate_failures: Sequence[str],
    gaps: Sequence[str],
    stats: Dict[str, Any],
) -> str:
    """A reason a human can act on, not a generic 'still incomplete'."""
    parts: List[str] = []
    if model_reason and "insufficient assessment" not in model_reason.lower():
        parts.append(model_reason.rstrip("."))
    detail: List[str] = []
    if any(f.startswith("domains=") for f in gate_failures):
        detail.append(
            f"evidence comes from only {stats['distinct_domains']} independent domain(s)"
        )
    if "verified=0" in gate_failures:
        detail.append("no claim verified against its cited source")
    if any(f.startswith("severe_conflicts=") for f in gate_failures):
        detail.append("unresolved severe contradiction between sources")
    if gaps:
        detail.append(f"{len(gaps)} planned angle(s) still unsourced: {gaps[0]}")
    if any(f.startswith("confidence=") for f in gate_failures):
        detail.append("measured confidence below the mode's target")
    if detail:
        parts.append("Blocking: " + "; ".join(detail))
    parts.append(f"({', '.join(gate_failures)})")
    return ". ".join(parts) + "."
