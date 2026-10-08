from __future__ import annotations

"""Node factory `make_search_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from typing import Any, Dict, List
from app.agents.planner import normalize_text
from app.graph.nodes.helpers import _extract_question_text, _unanswered_questions
from app.graph.state import ResearchState, SearchUpdate

from app.core.logging import get_logger

logger = get_logger(__name__)


def make_search_node(search_client):
    async def _node(state: ResearchState) -> SearchUpdate:
        previous = [r for r in state.get("search_results", []) or [] if isinstance(r, dict)]
        # Per-axis expansion: search only questions with no results yet.
        # Each unanswered parent fans out to its alternate phrasings
        # (variants ride the parent contract, so nothing orphans).
        # Accumulation is explicitly capped (SEARCH_MAX_RESULTS_RETAINED) and
        # the verifier blanks raw content after each pass.
        settings = getattr(search_client, "settings", None)
        fresh: List[Any] = []
        answered = {
            normalize_text(str(r.get("sub_question", ""))) for r in previous
        } - {""}
        by_text = {}
        for item in state.get("sub_questions", []) or []:
            text = _extract_question_text(item)
            if text and text not in by_text:
                by_text[text] = item

        def _question_search_type(text: str) -> str:
            """The contract's search_type steers retrieval (news topic vs
            general). Variants inherit their parent's type — they are
            rephrasings, not new contracts."""
            item = by_text.get(text)
            if isinstance(item, dict):
                return str(item.get("search_type", "") or "")
            return ""

        for text in _unanswered_questions(state.get("sub_questions", []), previous):
            fresh.append((text, _question_search_type(text)))
            item = by_text.get(text)
            if isinstance(item, dict):
                parent_type = _question_search_type(text)
                for v in item.get("variants", []) or []:
                    vs = str(v or "").strip()
                    if vs and normalize_text(vs) not in answered:
                        fresh.append((vs, parent_type))

        # Fix A.3 — corroboration procurement must actually be EXECUTED, not
        # merely stored on the critique. These claim-specific, publisher-
        # excluding queries are injected straight into this pass's search set on
        # expansion passes, independent of whether the planner model chose to
        # turn critique feedback into a contract. Deduped against everything
        # already searched.
        cap = max(1, int(getattr(settings, "search_max_queries_per_pass", 8) or 8))
        corroboration_to_run: List[str] = []
        if int(state.get("iteration", 0)) > 0:
            # Central investigation allocator (app/core/investigation_planner.py):
            # instead of a fixed-order concatenation of the corroboration /
            # counter-evidence / primary-source channels, rank every candidate
            # investigation by expected value and take the top-K within the
            # per-pass budget. Deterministic fallback: when the allocator yields
            # nothing (empty/garbage state, all targets exhausted, every query
            # already executed), revert to the historical channel list exactly
            # as before — existing behavior is preserved, never weakened.
            allocation: Dict[str, Any] = {"selected": []}
            try:
                from app.core.investigation_planner import select_investigations

                allocation = select_investigations(state, budget_cap=cap)
            except Exception as exc:  # allocator failure must not break search
                logger.warning("investigation_allocator_failed", error=str(exc), exc_info=exc)
                allocation = {"selected": []}
            alloc_queries = [
                str(c.get("query", "") or "").strip()
                for c in (allocation.get("selected") or [])
                if isinstance(c, dict) and str(c.get("query", "") or "").strip()
            ]
            if alloc_queries:
                for text in alloc_queries:
                    key = normalize_text(text)
                    if not key or key in answered:
                        continue
                    corroboration_to_run.append(text)
                    answered.add(key)
            else:
                for q in state.get("corroboration_queries", []) or []:
                    text = str(q or "").strip()
                    key = normalize_text(text)
                    if not text or not key or key in answered:
                        continue
                    corroboration_to_run.append(text)
                    answered.add(key)

        # GAP-FIRST ALLOCATION of the per-pass query cap.
        #
        # The plan's own questions used to be truncated to `cap - len(
        # corroboration_to_run)` BEFORE corroboration was appended, so a pass
        # with many corroboration queries left almost no room for the plan.
        # Measured in a live run: 3 dimensions were reported missing, 3
        # gap contracts were planned, and only 2 reached search_node — the third
        # was crowded out by `(attempt 2)` / `site:arxiv.org` queries aimed at
        # the dimension that was ALREADY covered. That is the reported symptom
        # of searches continuing to concentrate on the covered direction.
        #
        # So contracts for dimensions the focus report named as missing get first
        # claim on the cap. Domain-agnostic: the protected set is read from the
        # focus report, which derives it from the plan.
        gap_axes = {
            str(a)
            for a in ((state.get("focus") or {}).get("report") or {}).get("missing", ())
        } | {
            str(a)
            for a in ((state.get("focus") or {}).get("report") or {}).get("thin", ())
        }

        def _axis_of(text: str) -> str:
            item = by_text.get(text)
            return str(item.get("axis", "") or "") if isinstance(item, dict) else ""

        if gap_axes:
            gap_fresh = [f for f in fresh if _axis_of(f[0]) in gap_axes]
            other_fresh = [f for f in fresh if _axis_of(f[0]) not in gap_axes]
        else:
            gap_fresh, other_fresh = [], list(fresh)

        # Gap-closing queries are never truncated by the cap: they are the
        # reason this pass exists, and dropping them re-creates the
        # non-convergence being fixed. Everything else competes for what is left,
        # corroboration included.
        gap_fresh = gap_fresh[:cap]
        remaining = max(0, cap - len(gap_fresh))
        room = max(0, remaining - len(corroboration_to_run))
        fresh = [*gap_fresh, *other_fresh[:room]]
        fresh.extend((q, "general") for q in corroboration_to_run)
        if not fresh:
            if previous:
                return {"search_results": previous}
            fallback = state.get("query", "").strip()
            if not fallback:
                return {"search_results": previous}
            fresh = [(fallback, "")]

        # Fix B.3 — hard per-run expansion wall. Count the expansion passes and
        # the extra searches they issue; once either cap is hit, stop issuing
        # NEW expansion searches (the evidence already gathered still flows on).
        prior_passes = int(state.get("expansion_passes", 0) or 0)
        is_expansion = int(state.get("iteration", 0)) > 0

        # Executed-query memory (perf): an identical query text returns the
        # same results, so re-issuing it across passes spends a full retrieval
        # round-trip for zero new evidence. The depth controller already builds
        # this memory (`coverage_searched` -> _searched_queries) but nothing
        # ever WROTE it, so primary-source/corroboration follow-ups regenerated
        # the same query every pass and were executed each time. Enforce the
        # memory here and record what actually runs. EXACT normalized-text
        # matching only: a differently-worded query is never dropped, so no
        # research path or source is lost.
        prior_executed = {
            normalize_text(str(q))
            for q in (state.get("executed_queries") or [])
            if str(q).strip()
        }
        prior_executed |= {
            normalize_text(str(q))
            for q in (state.get("coverage_searched") or [])
            if str(q).strip()
        }
        seen_executed = set(prior_executed)
        deduped: List[Any] = []
        for item in fresh:
            text = item[0] if isinstance(item, tuple) else str(item)
            key = normalize_text(str(text))
            if not key or key in seen_executed:
                continue
            seen_executed.add(key)
            deduped.append(item)
        dropped_repeats = len(fresh) - len(deduped)
        if dropped_repeats:
            logger.info("search_queries_deduped", dropped=dropped_repeats)
        fresh = deduped
        # Nothing novel left to search: keep the already-gathered evidence
        # flowing rather than re-running a query this run already issued.
        if not fresh and previous:
            return {"search_results": previous, "expansion_passes": prior_passes}

        max_passes = max(1, int(getattr(settings, "max_expansion_passes", 12) or 12))
        max_searches = max(1, int(getattr(settings, "max_expansion_searches", 48) or 48))
        expansion_passes = prior_passes + (1 if is_expansion else 0)
        budget_stop = is_expansion and (
            expansion_passes > max_passes
            or prior_passes * cap + len(fresh) > max_searches
        )
        if budget_stop:
            logger.info(
                "expansion_wall_reached",
                expansion_passes=expansion_passes,
                max_expansion_passes=max_passes,
                iteration=int(state.get("iteration", 0)),
            )
            return {
                "search_results": previous,
                "expansion_passes": prior_passes,
            }
        results = await search_client.run_search(fresh)
        # Track whether a disagreement-seeking/corroboration query was actually
        # issued — the stopping redesign requires counter-evidence to have been
        # attempted, and this is the only point that knows what really ran.
        counter_markers = (
            "conflicting evidence", "disagreement", "independent corroboration",
            "counter-evidence", "counter evidence", "verification",
            "independent source", "official report", "-site:",
        )
        issued_counter = any(
            any(marker in str(text).lower() for marker in counter_markers)
            for text, _ in fresh
        )
        seen_urls = {r.get("url") for r in previous if r.get("url")}
        merged = [*previous, *(r for r in results if r.get("url") not in seen_urls)]
        # Hard memory bound: expansion passes append results forever, so a
        # long deep run could otherwise accumulate hundreds of result dicts
        # in state (raw content is blanked after verification, but the list
        # and its metadata still grow). Keep the NEWEST results — the ones
        # the current pass's summarizer needs — and drop the oldest beyond
        # the cap. Verification already ran on older passes, so nothing
        # downstream loses content it still needs.
        cap_results = max(10, int(getattr(settings, "search_max_results_retained", 80) or 80))
        if len(merged) > cap_results:
            dropped = len(merged) - cap_results
            merged = merged[dropped:]
            logger.info("search_results_capped", dropped=dropped, retained=len(merged))
        logger.info("search_done", results=len(merged), fresh=len(fresh))
        # Corroboration LINKING: fresh expansion results are matched back to
        # pending needs_corroboration facts HERE, at the moment the new pages
        # exist. Previously the results were merely merged into state (and the
        # measurement primitives existed) but nothing ever matched a fresh
        # result to the claim that needed it, so corroborating_sources stayed
        # at one publisher and corroborated_ge2 was always 0. Annotate copies
        # of the facts so no fact is dropped and no same-publisher URL can
        # raise the count (find_corroborating_sources/apply_corroboration
        # enforce registrable-domain independence).
        updated_facts: List[Dict[str, Any]] = [
            f for f in (state.get("facts", []) or []) if isinstance(f, dict)
        ]
        matched_corroboration = 0
        try:
            from app.core.evidence_grade import (
                apply_corroboration,
                find_corroborating_sources,
                grade_claim,
                registrable_domain,
            )

            existing_domains = {
                registrable_domain(str(f.get("source", "") or ""))
                for f in updated_facts
            }
            new_publishers = sum(
                1
                for r in results
                if isinstance(r, dict)
                and registrable_domain(str(r.get("url", "") or ""))
                and registrable_domain(str(r.get("url", "") or ""))
                not in existing_domains
            )
            settings = getattr(search_client, "settings", None)
            threshold = float(getattr(settings, "corroboration_similarity", 0.55) or 0.55)
            annotated: List[Dict[str, Any]] = []
            for fact in updated_facts:
                claim = str(fact.get("claim", "") or "").strip()
                source = str(fact.get("source", "") or "").strip()
                if not claim or not source:
                    annotated.append(fact)
                    continue
                record = grade_claim(fact)
                if not (record.needs_corroboration or record.corroboration_count < 2):
                    annotated.append(fact)
                    continue
                existing_urls = [
                    str(u) for u in (fact.get("corroborating_sources") or [])
                    if str(u).strip()
                ]
                existing_urls.append(source)
                matches = find_corroborating_sources(
                    claim, results, existing_urls, threshold=threshold
                )
                gained = 0
                copy = dict(fact)
                for url in matches:
                    if apply_corroboration(copy, url):
                        gained += 1
                if gained:
                    matched_corroboration += 1
                    annotated.append(copy)
                else:
                    annotated.append(fact)
            updated_facts = annotated
            logger.info(
                "corroboration_funnel",
                queries_issued=len(fresh),
                results_returned=len(results),
                new_publishers=new_publishers,
                matched_corroboration=matched_corroboration,
            )
        except Exception as exc:
            logger.warning(
                "corroboration_linking_failed", error=str(exc), exc_info=exc
            )
            updated_facts = [
                f for f in (state.get("facts", []) or []) if isinstance(f, dict)
            ]

        update: SearchUpdate = {
            "search_results": merged,
            "counter_evidence_attempted": bool(
                state.get("counter_evidence_attempted") or issued_counter
            ),
            "expansion_passes": expansion_passes,
            # Persist the executed-query memory: everything prior plus the
            # queries this pass actually issued. Mirrored under
            # `coverage_searched`, the key the depth controller's
            # `_searched_queries()` reader already consumes (it was read but
            # never written before this).
            "executed_queries": sorted(seen_executed),
            "coverage_searched": sorted(seen_executed),
        }
        if matched_corroboration:
            update["facts"] = updated_facts
        return update
    return _node
