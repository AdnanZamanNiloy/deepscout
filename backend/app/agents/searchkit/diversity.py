"""Retrieval diversity vs publisher diversity — two different questions.

The bug this module exists to make visible: one metasearch engine returning
fifty results from fifty different domains LOOKS like superb diversity and is
not. The other failure is the mirror image — twenty results from one publisher
are one source however many pages they fill.

    engine diversity     = how many independent indexes answered
    publisher diversity  = how many distinct organisations were actually cited

They are reported side by side and never added together. `results` is the only
thing they have in common, and it is exactly the number that hides both.

Everything here is pure and offline: it operates on already-fetched results and
the `unresponsive_engines` list SearXNG already reports, so an audit can be run
against a stored run or a recorded fixture without re-querying anything.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from app.core.logging import get_logger

log = get_logger(__name__)


# A single engine carrying more than this share of results is "concentrated".
# Judged on ENGINE share only: the point is to catch one index answering
# everything, which is what an upstream block or an engine outage looks like.
CONCENTRATION_SHARE = 0.60

# Below this many distinct engines, retrieval is not meaningfully diversified
# even if every result came from a different domain.
MIN_HEALTHY_ENGINES = 3


def _engine_of(result: Any) -> str:
    """The index that returned this result, "" when unknown.

    Falls back to the legacy `provider` string ONLY to parse the engine out of
    the historical "searxng:<engine>" form. It never returns that whole string:
    "searxng" is an aggregator, and reporting it as an engine would inflate the
    engine count with a name that retrieved nothing on its own.
    """
    for attr in ("retrieval_engine",):
        value = str(getattr(result, attr, "") or "").strip()
        if value:
            return value.lower()
    # Dicts (cached/serialized rows) go through the same lookup.
    if isinstance(result, dict):
        value = str(result.get("retrieval_engine") or "").strip()
        if value:
            return value.lower()
        provider = str(result.get("provider") or "")
        if provider.startswith("searxng:") and len(provider) > len("searxng:"):
            return provider.split(":", 1)[1].strip().lower()
        return ""
    provider = str(getattr(result, "provider", "") or "")
    if provider.startswith("searxng:") and len(provider) > len("searxng:"):
        return provider.split(":", 1)[1].strip().lower()
    # A BARE "searxng" is the aggregator, not an index: it retrieved nothing on
    # its own behalf. Counting it would inflate the engine count with a name
    # that never answered anything.
    if provider and provider != "cache" and provider != "searxng":
        return provider.lower()
    return ""


def _engines_of(result: Any) -> List[str]:
    """Every engine that returned this hit (a metasearch may list several)."""
    raw: Iterable[Any]
    if isinstance(result, dict):
        raw = result.get("retrieval_engines") or []
    else:
        raw = getattr(result, "retrieval_engines", None) or []
    out = [str(e).strip().lower() for e in raw if str(e).strip()]
    if out:
        return out
    single = _engine_of(result)
    return [single] if single else []


def _domain_of(result: Any) -> str:
    """Canonical source domain, falling back to the URL.

    The stored `source_domain` is re-canonicalized rather than trusted: it is
    normally already normalized by the provider mapper, but a caller that set it
    directly (or a row written by another build) could have left a `www.` on it,
    and an audit that counts "www.mdpi.com" and "mdpi.com" as two publishers is
    worse than no audit at all.

    The URL fallback matters for rows persisted before the column existed: an
    audit over an old run must still be able to name its publishers.
    """
    if isinstance(result, dict):
        domain = str(result.get("source_domain") or "").strip().lower()
        url = str(result.get("url") or "")
    else:
        domain = str(getattr(result, "source_domain", "") or "").strip().lower()
        url = str(getattr(result, "url", "") or "")
    try:
        from app.agents.searchkit.identity import canonical_source_domain

        if domain:
            return canonical_source_domain(f"https://{domain}") or domain
        return canonical_source_domain(url)
    except Exception as exc:  # defensive: an audit must never break a run
        log.warning("diversity_domain_failed", error=str(exc))
        return domain


def _publisher_of(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("publisher") or "").strip().lower()
    return str(getattr(result, "publisher", "") or "").strip().lower()


def engine_diversity(results: Sequence[Any]) -> Dict[str, Any]:
    """Counts by retrieval engine, and whether one index dominates."""
    counts: Dict[str, int] = {}
    for result in results or []:
        for engine in _engines_of(result):
            counts[engine] = counts.get(engine, 0) + 1
    total = sum(counts.values())
    top_engine, top_count = ("", 0)
    if counts:
        top_engine, top_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    share = round(top_count / total, 3) if total else 0.0
    return {
        "counts_by_engine": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unique_engines": len(counts),
        "total_attributed_results": total,
        "top_engine": top_engine,
        "top_engine_share": share,
        # True when retrieval is effectively single-sourced. Computed from
        # engine share and engine count ONLY — never from domain spread.
        "concentrated": bool(
            total and (share >= CONCENTRATION_SHARE or len(counts) < MIN_HEALTHY_ENGINES)
        ),
    }


def publisher_diversity(results: Sequence[Any]) -> Dict[str, Any]:
    """Distinct source domains and distinct named publishers."""
    domains: Dict[str, int] = {}
    publishers: Dict[str, int] = {}
    undomained = 0
    for result in results or []:
        domain = _domain_of(result)
        if domain:
            domains[domain] = domains.get(domain, 0) + 1
        else:
            # Counted per RESULT, not as `len(results) - len(domains)`: several
            # results routinely share one domain, and that subtraction would go
            # negative and report phantom blind spots.
            undomained += 1
        publisher = _publisher_of(result)
        if publisher:
            publishers[publisher] = publishers.get(publisher, 0) + 1
    return {
        "counts_by_domain": dict(sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unique_source_domains": len(domains),
        "counts_by_publisher": dict(
            sorted(publishers.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "unique_publishers": len(publishers),
        # Results whose domain could not be determined. A non-zero value means
        # the audit is blind to that many sources and should not be read as
        # "those results had no publisher".
        "undomained_results": undomained,
    }


def unresponsive_engines(payload_unresponsive: Any) -> List[Dict[str, str]]:
    """Normalise SearXNG's `unresponsive_engines` into [{engine, reason}].

    SearXNG reports these as [[name, reason], ...] but the shape has varied
    across versions, and one upstream returns a bare string. Anything
    unrecognised is preserved by position rather than dropped: an engine the
    audit cannot explain is exactly what you want to see.
    """
    out: List[Dict[str, str]] = []
    for item in payload_unresponsive or []:
        if isinstance(item, (list, tuple)):
            name = str(item[0]).strip() if len(item) > 0 else ""
            reason = str(item[1]).strip() if len(item) > 1 else ""
        else:
            name, reason = str(item).strip(), ""
        if name:
            out.append({"engine": name.lower(), "reason": reason})
    return out


def cross_engine_collisions(results: Sequence[Any]) -> Dict[str, Any]:
    """Were results from DIFFERENT engines deduplicated against each other?

    The ranker collapses hits that share a canonical URL and keeps one. That is
    correct behaviour, but it means one engine's hit can silently disappear
    because another engine surfaced the same page first — which is a real loss of
    a source, and invisible unless it is counted. `overwritten_across_engines`
    is the number of canonical URLs where that happened.

    Note this is distinct from SearXNG's own `engines[]` array, which lists the
    engines that returned one page together. Here a collision means two SEPARATE
    result rows for one URL.
    """
    by_canonical: Dict[str, List[str]] = {}
    total = 0
    for result in results or []:
        if isinstance(result, dict):
            url = str(result.get("url") or "")
        else:
            url = str(getattr(result, "url", "") or "")
        if not url:
            continue
        total += 1
        try:
            from app.agents.sources.urls import canonical_url

            key = canonical_url(url)
        except Exception as exc:
            log.warning("diversity_canonical_failed", error=str(exc))
            key = url.strip().lower()
        for engine in _engines_of(result) or [""]:
            by_canonical.setdefault(key, [])
            if engine not in by_canonical[key]:
                by_canonical[key].append(engine)
    collisions = {k: v for k, v in by_canonical.items() if len(v) > 1}
    return {
        "unique_canonical_urls": len(by_canonical),
        "result_rows": total,
        "duplicate_url_rows": total - len(by_canonical),
        "urls_seen_from_multiple_engines": len(collisions),
        "overwritten_across_engines": len(collisions),
    }


def diversity_report(
    results: Sequence[Any],
    unresponsive: Any = None,
) -> Dict[str, Any]:
    """Engine and publisher diversity side by side, plus upstream health.

    The two sections are deliberately separate objects: a caller can assert on
    `engines["unique_engines"]` and `publishers["unique_source_domains"]`
    without either being able to contaminate the other.
    """
    results = list(results or [])
    failed = unresponsive_engines(unresponsive)
    engines = engine_diversity(results)
    publishers = publisher_diversity(results)
    seen = set(engines["counts_by_engine"])
    failed_names = {f["engine"] for f in failed}
    responsive = sorted(seen - failed_names)
    # Success rate is over ENGINES, not results: one engine returning 20 hits
    # is not twenty successes, and weighting by result count would hide an
    # outage behind the engine that happened to survive.
    total_engines = len(seen | failed_names)
    return {
        "results": len(results),
        "engines": engines,
        "publishers": publishers,
        "dedup": cross_engine_collisions(results),
        # SearXNG's own view of the upstream pool, kept separate from the
        # direct providers (wikipedia/arxiv/crossref) that are not part of it.
        "searxng": {
            "responsive_engines": responsive,
            "unresponsive_engines": [f["engine"] for f in failed],
            "responsive_count": len(responsive),
            "unresponsive_count": len(failed_names),
            "engine_success_rate": (
                round(len(responsive) / total_engines, 3) if total_engines else None
            ),
            "unresponsive_detail": failed,
        },
        "unresponsive_engines": failed,
        "responsive_engines": responsive,
        # The headline diagnostic: if many engines failed upstream, whatever
        # concentration remains is caused by the outage, not by configuration.
        "upstream_failure_count": len(failed),
        "concentration_cause": (
            "upstream_outage" if failed and engines["concentrated"]
            else "single_index" if engines["concentrated"]
            else "none"
        ),
    }


def aggregate_reports(reports: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll per-query reports up into one run-level view.

    Engine and publisher counts are summed independently and the two unique
    counts are NOT derived from each other: a run that touched 5 engines and 40
    domains has 5 engines and 40 domains, and averaging the per-query ratios
    would invent a number that describes no query in the run.
    """
    engine_counts: Dict[str, int] = {}
    domain_count = 0
    publisher_count = 0
    queries = 0
    results = 0
    concentrated_queries = 0
    failed_engines: Dict[str, str] = {}
    for report in reports or []:
        queries += 1
        results += int(report.get("results", 0) or 0)
        for engine, count in (report.get("counts_by_engine") or {}).items():
            engine_counts[engine] = engine_counts.get(engine, 0) + int(count or 0)
        domain_count = max(domain_count, int(report.get("unique_source_domains", 0) or 0))
        publisher_count = max(
            publisher_count, int(report.get("unique_publishers", 0) or 0)
        )
        if report.get("concentrated"):
            concentrated_queries += 1
        for item in report.get("unresponsive_engines") or []:
            if isinstance(item, dict) and item.get("engine"):
                failed_engines[str(item["engine"]).lower()] = str(item.get("reason", ""))

    total = sum(engine_counts.values())
    top_engine, top_count = ("", 0)
    if engine_counts:
        top_engine, top_count = max(engine_counts.items(), key=lambda kv: (kv[1], kv[0]))
    share = round(top_count / total, 3) if total else 0.0
    return {
        "queries_audited": queries,
        "results_audited": results,
        "counts_by_engine": dict(sorted(engine_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unique_engines": len(engine_counts),
        "top_engine": top_engine,
        "top_engine_share": share,
        "max_unique_source_domains_in_one_query": domain_count,
        "max_unique_publishers_in_one_query": publisher_count,
        "concentrated_queries": concentrated_queries,
        "concentrated": bool(queries and concentrated_queries == queries),
        "unresponsive_engines": [
            {"engine": name, "reason": reason}
            for name, reason in sorted(failed_engines.items())
        ],
        "upstream_failure_count": len(failed_engines),
        "concentration_cause": (
            "upstream_outage" if failed_engines and concentrated_queries
            else "single_index" if concentrated_queries
            else "none"
        ),
    }