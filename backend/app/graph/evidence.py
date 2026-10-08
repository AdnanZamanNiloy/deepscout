"""Evidence acquisition: corroboration, coverage gaps and claim scoping.

Split out of `workflow.py`. These decide what ELSE the pipeline goes and reads
to back a claim -- procurement queries, independent corroboration,
counter-evidence, in-scope filtering and coverage accounting. They are pure
functions over state plus a search client, and were the densest cluster in the
file.

`_safe_float`, `get_settings_safe` and `IN_SCOPE_SIMILARITY` moved here rather
than being imported back from `workflow`, because `reports.py` needs
`_prepare_supporting_evidence` from this module; a one-way dependency keeps both
free of a circular import.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from app.agents.evidence_utils import dedupe_semantic_facts, filter_facts_by_domain
from app.agents.planner import normalize_text
from app.agents.sources import build_corroboration_query
from app.core.logging import get_logger

from app.graph.state import ResearchState

logger = get_logger(__name__)


from app.core.primitives import safe_float as _safe_float  # noqa: F401


def _prepare_supporting_evidence(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    quality = filter_facts_by_domain(facts)
    deduped = dedupe_semantic_facts(quality)
    ranked = sorted(deduped, key=lambda x: _safe_float(x.get("confidence", 0.0)), reverse=True)

    # Keep evidence diverse by preferring distinct source domains.
    seen_domains = set()
    diverse: List[Dict[str, Any]] = []
    for item in ranked:
        source = str(item.get("source", ""))
        domain = source.split("/")[2].lower() if source.startswith("http") and "/" in source else source.lower()
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        diverse.append(item)
        if len(diverse) >= 10:
            break

    return diverse if diverse else ranked[:10]


def _verified_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Facts that passed verification; if verification never ran, treat all as usable."""
    if not facts:
        return []
    if not any("verified" in f for f in facts):
        return facts
    return [f for f in facts if f.get("verified")]


def _evidence_gaps_remain(state: ResearchState) -> bool:
    """Per-claim evidence deficiencies that outrank a critic's "enough".

    SCOPE, stated explicitly because there are two "gaps remain" predicates in
    the system and they used to look like duplicates:

      * THIS ONE (`graph/evidence.py`) is the ROUTER guard. It runs in
        `route_after_critic` before the depth decision and looks only at
        per-claim grading: a claim that is single-source where it matters, or
        sits inside an unresolved contradiction. It is a strict SUBSET of the
        controller's holistic terms below.
      * `evidence_sufficient` (`core/depth_controller.py`) is the CONTROLLER
        measure: confidence at target, no uncovered planned axis, no thin
        dimension, no active uncorroborated high-impact claim, no severe
        contradiction. It is broader and it is what `decision_reason` reports.

    They are not in conflict because this one can only ever ADD research, never
    block a finalize the controller already approved: it returns False for an
    empty or ungradeable pool, and on a grading failure. The controller applies
    its own, wider set afterwards, so nothing is skipped by having the narrow
    check run first.
    """
    facts = [f for f in state.get("facts", []) or [] if isinstance(f, dict)]
    if not facts:
        return False
    try:
        from app.core.evidence_grade import grade_facts

        graded = grade_facts(facts, contradictions=state.get("contradictions") or [])
    except Exception as exc:  # a grading bug must never change routing
        logger.warning("evidence_gate_grading_failed", error=str(exc), exc_info=exc)
        return False
    for g in graded:
        ev = g.get("evidence") if isinstance(g, dict) else None
        if not isinstance(ev, dict):
            continue
        if int(ev.get("contradiction_count", 0) or 0) > 0:
            return True
        if ev.get("needs_corroboration"):
            return True
    return False


def _claim_terms(claim: str, limit: int = 8) -> str:
    """Content tokens from a claim, most informative first — the subject the
    corroboration query should target. Deterministic and LLM-free."""
    import re

    stop = {
        "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "by", "at", "from", "that", "this",
        "it", "its", "as", "than", "about", "over", "per", "will", "has",
        "have", "had", "as", "which", "there", "their", "they", "been",
    }
    tokens = re.findall(r"[A-Za-z0-9%$][A-Za-z0-9%$.\-]*", claim or "")
    out: List[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in stop or len(low) < 3:
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= limit:
            break
    return " ".join(out)


def _summary_claim_texts(state: ResearchState) -> List[str]:
    """Claim texts the executive summary / key findings will draw on.

    Budget targeting prefers these claims because they are the report's
    headline: corroborating a fact the summary states changes the answer, while
    corroborating a peripheral remark does not. Sourced from the state's own
    synthesized answer / critique when present; empty when synthesis has not run
    yet (expansions before the first synthesis simply rank on the other
    signals). Deterministic and total.
    """
    texts: List[str] = []
    answer = str(state.get("synthesized_answer", "") or "")
    if not answer:
        return texts
    # Best-effort: the summary's own sentences are its claims. Kept bounded so
    # matching stays cheap; never parsed with an LLM.
    for raw in re.split(r"[\n.!?]+", answer):
        line = raw.strip().lstrip("-*# ").strip()
        if 20 <= len(line) <= 200:
            texts.append(line)
        if len(texts) >= 12:
            break
    return texts


def _corroboration_queries(
    state: ResearchState, limit: int = 3, settings: Any | None = None
) -> tuple[List[str], Dict[str, Dict[str, Any]]]:
    """CLAIM-SPECIFIC procurement queries that seek a DIFFERENT publisher.

    The live deep run measured corroboration perfectly and then never went
    looking for it: 0 claims reached two independent registrable domains while
    121 needed corroboration. Measurement without procurement is a dead end.

    For each important single-publisher claim this builds a query that
    (a) targets the claim's own terms, (b) explicitly EXCLUDES the current
    publisher with `-site:<registrable-domain>`, and (c) targets the
    authoritative publisher registry (site:gov/edu/int + named agencies,
    journals and datasets) where an independent corroborating source is most
    likely to live.

    Per-claim attempt state is threaded through `corroboration_registry`
    (keyed by normalized claim) so a claim is never re-queried identically
    beyond `max_corroboration_attempts` and so the caller can see which
    domains were already targeted. Deterministic fallback: if grading fails,
    returns ([], {}) — never invent queries.

    Per-claim INVESTIGATION state (`state["investigation_state"]`, see
    app/core/investigation_state.py) additionally EXCLUDES claims already
    marked exhausted (budget spent AND still single-source) so budget is never
    re-spent on a known dead end, and it orders un-attempted gaps ahead of
    already-attempted ones. Absent/invalid state is a no-op, so the historical
    behavior is preserved when the state has not been populated yet.
    """
    facts = [f for f in state.get("facts", []) or [] if isinstance(f, dict)]
    if not facts:
        return [], {}
    try:
        from app.core.evidence_grade import grade_facts, registrable_domain
    except Exception as exc:
        logger.warning("corroboration_grading_failed", error=str(exc), exc_info=exc)
        return [], {}

    try:
        graded = grade_facts(facts, contradictions=state.get("contradictions") or [])
    except Exception as exc:
        logger.warning("corroboration_grading_failed", error=str(exc), exc_info=exc)
        return [], {}

    # Budget allocation (workstream B): when there are more single-source
    # claims than the pass can query, IMPACT decides which ones get the scarce
    # queries — quantitative claims, claims the executive summary uses, and
    # high-corroboration-need claims first. `rank_completion_targets` is
    # deterministic and returns [] on any grading failure, so the loop below
    # keeps its historical order in that case.
    target_order: Dict[str, Dict[str, Any]] = {}
    try:
        from app.core.evidence_completion import rank_completion_targets

        summary_claims = _summary_claim_texts(state)
        for passed in rank_completion_targets(
            facts, state.get("contradictions") or [], summary_claims=summary_claims
        ):
            target_order[normalize_text(str(passed.get("claim", "")))] = passed
    except Exception as exc:
        logger.warning("completion_ranking_failed", error=str(exc), exc_info=exc)
        target_order = {}

    settings = settings if settings is not None else get_settings_safe()
    max_attempts = max(1, int(getattr(settings, "max_corroboration_attempts", 2) or 2))
    registry: Dict[str, Dict[str, Any]] = {
        str(k): dict(v)
        for k, v in (state.get("corroboration_registry") or {}).items()
        if isinstance(v, dict)
    }

    # Investigation memory: exhausted (budget spent, still single-source) and
    # corroborated claims are never re-queried; un-attempted gaps are funded
    # first. A missing/invalid state is neutral (nothing excluded).
    excluded_keys: set = set()
    try:
        from app.core.investigation_state import STATUS_CORROBORATED, STATUS_EXHAUSTED, sanitize_investigation_state

        inv = sanitize_investigation_state(
            state.get("investigation_state"), max_attempts=max_attempts
        )
        excluded_keys = {
            k
            for k, e in inv.items()
            if e.get("status") in (STATUS_EXHAUSTED, STATUS_CORROBORATED)
        }
    except Exception as exc:
        logger.warning("investigation_lookup_failed", error=str(exc), exc_info=exc)
        excluded_keys = set()

    # Highest-impact claims first; any graded claim not in the ranked set keeps
    # its original relative order after the ranked targets.
    ordered = sorted(
        graded,
        key=lambda g: (
            0
            if normalize_text(
                str(((g.get("evidence") or {}) if isinstance(g, dict) else {}).get("claim", ""))
            )
            in target_order
            else 1
        ),
    )

    queries: List[str] = []
    for g in ordered:
        ev = g.get("evidence") if isinstance(g, dict) else None
        if not isinstance(ev, dict) or not ev.get("needs_corroboration"):
            continue
        claim = str(ev.get("claim", "")).strip()
        if not claim:
            continue
        key = normalize_text(claim)
        if key in excluded_keys:
            # Exhausted/corroborated per investigation memory: stays in the gap
            # set (surfaced as a limitation) but is not re-queried.
            continue
        entry = registry.setdefault(
            key, {"claim": claim, "domains_queried": [], "query_keys": [], "attempts": 0}
        )
        # Hard wall: a claim whose attempt budget is spent stays in the gap set
        # (so it is recorded as a limitation) but is no longer re-queried.
        try:
            attempts = int(entry.get("attempts", 0) or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= max_attempts:
            continue
        domain = registrable_domain(str(ev.get("domain", "") or ev.get("source", "")))
        terms = _claim_terms(claim)
        if not terms:
            continue
        # Rotate targeted hosts per attempt so a second pass is genuinely new
        # procurement, not a repeat of the first scoped query.
        query = build_corroboration_query(
            terms,
            exclude_domain=domain,
            quantitative=bool(ev.get("has_numbers")),
            attempt=attempts,
        )
        if not query:
            continue
        query_key = normalize_text(query)
        if query_key in (entry.get("query_keys") or []):
            # Identical query text is never re-issued — even with a larger
            # attempt budget, a rotation collision must not double-spend.
            entry["attempts"] = max_attempts
            continue
        queries.append(query)
        entry["attempts"] = attempts + 1
        entry["query_keys"] = [*(entry.get("query_keys") or []), query_key]
        # The queries actually issued THIS pass, for the investigation tracker
        # to record as attempts (outcome memory).
        entry["issued"] = [*(entry.get("issued") or []), query]
        if domain and domain not in (entry.get("domains_queried") or []):
            entry["domains_queried"] = [*(entry.get("domains_queried") or []), domain]
        if len(queries) >= max(1, limit):
            break
    return queries, registry


def get_settings_safe():
    """Settings for deterministic-fallback helpers; never raises."""
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception as exc:  # settings failure must not break query generation
        logger.warning("settings_lookup_failed", error=str(exc), exc_info=exc)
        return None


def _acquire_corroboration(
    facts: List[Dict[str, Any]],
    search_results: List[Dict[str, Any]],
    contradictions: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Attach NEW-publisher supporting pages to single-source claims.

    For every fact whose graded record still needs corroboration, find search
    results from a registrable domain the fact does not already hold whose text
    supports the claim at/above the corroboration similarity band, and attach
    them through `apply_corroboration` (the only sanctioned write path, which
    rejects same-publisher URLs). Facts are mutated in place and the same list
    is returned; deterministic, LLM-free, and total — any grading failure
    leaves the pool untouched rather than breaking the run.
    """
    items = [f for f in facts or [] if isinstance(f, dict)]
    candidates = [r for r in search_results or [] if isinstance(r, dict)]
    if not items or not candidates:
        return list(facts or [])
    try:
        from app.core.evidence_grade import (
            apply_corroboration,
            find_corroborating_sources,
            grade_facts,
        )
    except Exception as exc:
        logger.warning("corroboration_acquisition_import_failed", error=str(exc), exc_info=exc)
        return list(facts or [])

    try:
        graded = grade_facts(items, contradictions=contradictions or [])
    except Exception as exc:
        logger.warning("corroboration_acquisition_grading_failed", error=str(exc), exc_info=exc)
        return list(facts or [])

    by_claim = {
        normalize_text(str(g.get("claim", ""))): g
        for g in graded
        if isinstance(g, dict)
    }
    attached = 0
    settings = get_settings_safe()
    threshold = float(getattr(settings, "corroboration_similarity", 0.55) or 0.55)
    for fact in items:
        claim = str(fact.get("claim", "") or "").strip()
        if not claim:
            continue
        record = by_claim.get(normalize_text(claim))
        ev = record.get("evidence") if isinstance(record, dict) else None
        if not isinstance(ev, dict) or not ev.get("needs_corroboration"):
            continue
        existing = [
            str(u) for u in (fact.get("corroborating_sources") or []) if str(u).strip()
        ]
        if fact.get("source"):
            existing.append(str(fact["source"]))
        matches = find_corroborating_sources(
            claim, candidates, existing, threshold=threshold
        )
        for url in matches:
            if apply_corroboration(fact, url):
                attached += 1
    if attached:
        logger.info("corroboration_acquired", attachments=attached)
    return list(facts or [])


def _counter_evidence_queries(state: ResearchState, limit: int = 2) -> List[str]:
    """Targeted disagreement searches for the weakest claims in the pool.

    The brief is explicit: do not only search for support. For every
    single-source or contradicted claim, propose a query that seeks
    counter-evidence, limitations, or credible opposing views. These ride the
    existing critic `improved_queries` channel, so the normal expansion loop
    researches them — no new pipeline stage.
    """
    facts = [f for f in state.get("facts", []) or [] if isinstance(f, dict)]
    if not facts:
        return []
    try:
        from app.core.evidence_grade import grade_facts

        graded = grade_facts(facts, contradictions=state.get("contradictions") or [])
    except Exception as exc:
        logger.warning("counter_evidence_grading_failed", error=str(exc), exc_info=exc)
        return []
    queries: List[str] = []
    for g in graded:
        ev = g.get("evidence") if isinstance(g, dict) else None
        if not isinstance(ev, dict):
            continue
        claim = str(ev.get("claim", "")).strip()
        if not claim:
            continue
        if int(ev.get("contradiction_count", 0) or 0) > 0:
            queries.append(f"{claim[:140]} conflicting evidence OR disagreement")
        elif ev.get("needs_corroboration"):
            # Independent corroboration, aimed at PRIMARY publishers rather
            # than more commentary: the whole deficiency is that only one
            # publisher stands behind an important claim.
            queries.append(
                f"{claim[:120]} independent corroboration official data "
                "OR government report OR peer-reviewed study"
            )
        if len(queries) >= max(1, limit):
            break
    return queries


def _attach_evidence(
    facts: List[Dict[str, Any]],
    contradictions: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Annotate state facts in place with the claim-level EvidenceRecord.

    Requirement 6: a graded fact reaching synthesis must carry
    claim→source→verification→independence→corroboration→contradiction→
    confidence. `grade_facts` produces those records; without this the records
    lived only inside the confidence/synthesizer computations and the final
    report could not rely on them. Additive: existing fact keys are preserved
    and the record rides under `evidence` / `evidence_grade`.
    """
    try:
        from app.core.evidence_grade import grade_facts

        graded = grade_facts(facts, contradictions=contradictions or [])
    except Exception as exc:  # a grading bug must never break a run
        logger.warning("attach_evidence_failed", error=str(exc), exc_info=exc)
        return list(facts or [])
    by_claim = {
        str(g.get("claim", "")): g.get("evidence")
        for g in graded
        if isinstance(g, dict)
    }
    out: List[Dict[str, Any]] = []
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        ev = by_claim.get(str(fact.get("claim", "")))
        if ev and "evidence" not in fact:
            enriched = dict(fact)
            enriched["evidence"] = ev
            enriched["evidence_grade"] = str(ev.get("grade", ""))
            out.append(enriched)
        else:
            out.append(dict(fact))
    return out





# Minimum semantic similarity for a fact to count as part of the query's own
# evidence pool. Calibrated on live off-topic bleed: on-topic claims for a
# short query scored 0.07-0.24, unrelated domains (Iran nuclear, NBER
# clientelism) scored 0.00-0.02. Well below the on-topic band and above the
# noise floor.
IN_SCOPE_SIMILARITY = 0.04


def _query_inscope_facts(
    facts: List[Dict[str, Any]], query: str, senses: Sequence[str] = ()
) -> List[Dict[str, Any]]:
    """The query's OWN evidence pool: facts topically about the query.

    Live reports leaked unrelated claims into the Limitations list (Iran's
    nuclear programme, an NBER paper on clientelism — on a "What is a
    transformer?" query) because this pool was the raw `state["facts"]`: every
    sub-question's evidence, including off-domain material a search pass
    happened to fetch. Limitations and the evidence scoring must describe THIS
    query's evidence, not every domain the run touched.

    Reuses the EXISTING relevance machinery rather than adding a new system:
    word overlap (`claim_query_overlap` + `MIN_QUERY_OVERLAP`, the summarizer's
    own filter) OR the shared TF-IDF hybrid engine's similarity
    (`app.core.semantic`, the same engine section ranking and corroboration
    trust). A fact is in scope when it clears either bar against the query or
    any of the query's disambiguation senses (an ambiguous term's second
    meaning is genuinely in scope). The filter is all-or-nothing-safe: if it
    would leave nothing, the original pool is returned unchanged so a
    limitations section is never starved (AGENTS.md 4.7).
    """
    if not facts:
        return []
    try:
        from app.agents.evidence_utils import MIN_QUERY_OVERLAP, claim_query_overlap

        scope_texts = [str(t) for t in (query, *senses) if str(t or "").strip()]
        if not scope_texts:
            return facts
        claims = [str(f.get("claim", "") or "") for f in facts]
        # Semantic similarity is the stronger signal for a SHORT query, where
        # word overlap is stopword noise ("What is a transformer?" -> "a").
        try:
            from app.core.semantic import rank_by_similarity

            semantic: List[float] = [0.0] * len(claims)
            for text in scope_texts:
                scores = rank_by_similarity(text, claims)
                semantic = [max(a, float(b)) for a, b in zip(semantic, scores)]
            inscope = [f for i, f in enumerate(facts) if semantic[i] >= IN_SCOPE_SIMILARITY]
        except Exception as exc:  # noqa: BLE001 - fall back to word overlap
            logger.warning("query_inscope_semantic_failed", error=str(exc), exc_info=exc)
            inscope = [
                f
                for i, f in enumerate(facts)
                if max(
                    (claim_query_overlap(t, claims[i]) for t in scope_texts),
                    default=0.0,
                )
                >= MIN_QUERY_OVERLAP
            ]
        return inscope or facts
    except Exception as exc:  # noqa: BLE001 - isolation must never break a run
        logger.warning("query_inscope_filter_failed", error=str(exc), exc_info=exc)
        return facts


def _measured_coverage_gaps(state: ResearchState) -> List[str]:
    """Human-readable evidence gaps for the mandatory Limitations section.

    Deterministic, from the graded pool (single-source/contradicted claims) and
    the corroboration registry (claims whose procurement budget is spent and
    which remain uncorroborated). Only the query's OWN evidence pool is graded,
    so an unrelated domain's facts cannot surface as this report's limitations.
    Empty on any failure — a limitations section with no measured gap is honest,
    a crash is not.
    """
    facts = [f for f in state.get("facts", []) or [] if isinstance(f, dict)]
    if facts:
        intent = state.get("intent") or {}
        senses = [
            str(s.get("label", "") or "")
            for s in (intent.get("senses") or [])
            if isinstance(s, dict)
        ]
        facts = _query_inscope_facts(facts, str(state.get("query", "") or ""), senses)
    gaps: List[str] = []
    if facts:
        try:
            from app.core.evidence_grade import coverage_gaps_from_records, grade_claim

            records = [grade_claim(f, contradictions=state.get("contradictions") or []) for f in facts]
            gaps.extend(coverage_gaps_from_records(records))
        except Exception as exc:
            logger.warning("coverage_gaps_failed", error=str(exc), exc_info=exc)
    # Per-claim investigation memory: a claim whose targeted corroboration
    # attempts are exhausted and which is STILL single-source is an acknowledged
    # limitation, named explicitly, rather than a gap the report silently
    # re-chases. Deterministic and total.
    try:
        from app.core.investigation_state import exhausted_limitations

        gaps.extend(exhausted_limitations(state.get("investigation_state")))
    except Exception as exc:
        logger.warning("investigation_limitations_failed", error=str(exc), exc_info=exc)
    # AMBIGUITY READING GAP. When the question was underspecified and the run
    # chose a reading, that reading's evidence shortfall is a limitation of THIS
    # answer — reported as such rather than quietly answered under a different
    # reading because it had more evidence. The meaning of the question is
    # decided before research; evidence availability cannot redefine it.
    try:
        from app.agents.ambiguity import assess_reading_evidence

        policy = state.get("ambiguity") or {}
        if isinstance(policy, dict) and str(policy.get("action", "") or "") in (
            "assume",
            "separate",
        ):
            balance = assess_reading_evidence(state.get("facts"), policy)
            if balance.chosen_is_thin and balance.chosen:
                gaps.append(
                    f"the reading answered ('{balance.chosen}') is thin on "
                    "evidence; the question is answered under that reading "
                    "regardless of how much evidence was found for other readings"
                )
            if balance.alternative_is_better_evidenced and balance.alternative:
                gaps.append(
                    f"'{balance.alternative}' is a different reading of the "
                    "question and was better evidenced; it is reported separately "
                    "as an alternative, not as the answer"
                )
    except Exception as exc:
        logger.warning("reading_gap_limitations_failed", error=str(exc), exc_info=exc)
    return gaps[:8]





# Minimum semantic similarity for a fact to count as part of the query's own
# evidence pool. Calibrated on live off-topic bleed: on-topic claims for a
# short query scored 0.07-0.24, unrelated domains (Iran nuclear, NBER
# clientelism) scored 0.00-0.02. Well below the on-topic band and above the
# noise floor.
