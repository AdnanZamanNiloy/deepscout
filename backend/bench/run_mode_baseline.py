#!/usr/bin/env python3
"""Step 0 baseline: per-stage latency and spend for the three research modes.

This is the MEASUREMENT harness the mode-profile work is calibrated from. It
runs a real query end-to-end through a real mode — real retrieval, real LLM
calls — and records:

  * per-stage wall-clock (from the ``pipeline_stage`` start/end signals the
    team already emits for the live rail), so the critical path is visible
    rather than inferred;
  * LLM calls, search calls, pages fetched and tokens, read from the run's own
    ``RunUsage`` ledger (the same object the budget governor will charge);
  * the section count actually researched and the auditor's pass count, since
    those are the two things a mode is supposed to control.

It is deliberately separate from ``run_offline.py``: that suite is deterministic
and network-free, this one is neither. It needs a real provider key and network
access, and it is expected to be slow.

Usage (from backend/):

    # one query, one mode
    python bench/run_mode_baseline.py --mode standard --query "state of nuclear energy in 2026"

    # every mode over the default query set, write JSON + a markdown table
    python bench/run_mode_baseline.py --modes quick,standard,deep

    # a specific set and output path
    python bench/run_mode_baseline.py --modes standard --queries-file q.txt --out bench/results

The default query set is intentionally varied (definitional, comparative,
temporal, guidance) because stage cost depends on question shape, not just mode.

Output: ``mode_baseline.json`` and ``MODE_BASELINE.md`` in the results dir.
Exit code is 0 unless every run failed — a slow or degraded run is data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]

# Varied by shape so the baseline is not one question's timing.
DEFAULT_QUERIES = [
    "What is retrieval augmented generation?",
    "Compare the economics of solar versus nuclear power for baseload electricity.",
    "What can be the most demanding jobs in 2027?",
    "What are the latest advances in AI agents?",
    "Should cities adopt congestion pricing?",
]

MODES = ["quick", "standard", "deep"]


async def _run_one(mode: str, query: str, timeout_s: float) -> Dict[str, Any]:
    """Run one query in one mode; return a measurement record."""
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env")

    import app.retrieval.client as search_mod
    # A warm cache would make a repeat query look free and hide retrieval time.
    search_mod.get_cache = lambda _settings: (_ for _ in ()).throw(
        RuntimeError("baseline: search cache disabled")
    )

    from app.common.config import Settings
    from app.core.llm import LLMClient
    from app.core.usage import start_run_usage, clear_run_usage, get_run_usage
    from app.retrieval.client import SearchClient
    # The route's own entry point, so the harness measures what production runs.
    from app.runtime.orchestrator import PipelineRunner

    settings = Settings(_env_file=str(BACKEND / ".env"))
    llm = LLMClient(settings)
    search_client = SearchClient(settings)

    run_id = f"baseline-{mode}-{int(time.time() * 1000)}"
    start_run_usage(run_id, settings, mode=mode)

    runner = PipelineRunner(
        llm=llm,
        search_client=search_client,
        query=query,
        mode=mode,
        run_id=run_id,
        hitl=False,  # never block on a human in a baseline run
    )

    # Stage timing, keyed on the team's own pipeline_stage signals.
    stage_start: Dict[str, float] = {}
    stage_ms: Dict[str, float] = {}
    auditor_passes = 0
    sections_researched: Optional[int] = None

    t0 = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_s):
            async for ev in runner.run():
                node = str(ev.get("node") or "")
                activity = str(ev.get("activity") or "")

                # pipeline_stage start/end is the authoritative stage boundary.
                if activity == "pipeline_stage":
                    phase = str(ev.get("action") or "")
                    if phase == "start":
                        stage_start[node] = time.perf_counter()
                    elif phase == "end" and node in stage_start:
                        stage_ms[node] = round(
                            (time.perf_counter() - stage_start.pop(node)) * 1000, 1
                        )
                    if node == "evidence_auditor" and phase == "start":
                        auditor_passes += 1

                # The runner already derives the section count from the scout
                # swarm's own output; read it off the node event rather than
                # re-deriving it (the final state drops research_data).
                if isinstance(ev.get("research_sections"), int):
                    sections_researched = ev["research_sections"]
    except TimeoutError:
        elapsed = time.perf_counter() - t0
        return {
            "mode": mode,
            "query": query,
            "ok": False,
            "error": f"timeout after {timeout_s}s",
            "wall_s": round(elapsed, 2),
            "stages_ms": stage_ms,
        }
    except Exception as exc:  # a failed run is data, not a crash
        elapsed = time.perf_counter() - t0
        return {
            "mode": mode,
            "query": query,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "wall_s": round(elapsed, 2),
            "stages_ms": stage_ms,
        }

    wall = time.perf_counter() - t0
    state = runner.final_state() or {}
    usage = get_run_usage()
    snap = usage.budget.snapshot() if usage is not None else {}
    clear_run_usage()

    # First token of the report is enough to prove an answer was produced.
    report = str(state.get("final_report") or "")

    return {
        "mode": mode,
        "query": query,
        "ok": bool(report),
        "wall_s": round(wall, 2),
        "stages_ms": stage_ms,
        "llm_calls": snap.get("llm_calls"),
        "search_calls": snap.get("search_calls"),
        "tokens": snap.get("spent_tokens"),
        "budget_utilization": snap.get("utilization"),
        "sections_researched": sections_researched,
        "auditor_passes": auditor_passes,
        "sources": len(state.get("source_index") or state.get("sources") or []),
        "report_chars": len(report),
        "confidence": state.get("confidence"),
        "critical_path_ms": round(sum(stage_ms.values()), 1),
    }


def _pct(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return round(vals[0], 1)
    # Nearest-rank, which is honest about small samples (no interpolation
    # pretending to a smooth distribution we do not have).
    k = max(0, min(len(vals) - 1, int(round((p / 100.0) * len(vals))) - 1))
    return round(vals[k], 1)


def _summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate one mode's records into the table the plan is calibrated from."""
    ok = [r for r in records if r.get("ok")]
    if not ok:
        return {"runs": len(records), "ok_runs": 0}
    walls = [r["wall_s"] for r in ok]

    # Per-stage p50/p90 across the runs that reported that stage.
    stages: Dict[str, List[float]] = {}
    for r in ok:
        for node, ms in (r.get("stages_ms") or {}).items():
            stages.setdefault(node, []).append(ms / 1000.0)

    stage_table = {
        node: {"p50_s": _pct(v, 50), "p90_s": _pct(v, 90), "mean_s": round(statistics.mean(v), 1)}
        for node, v in sorted(stages.items())
    }
    # The stage that dominates is the one worth optimising first.
    dominant = max(stage_table.items(), key=lambda kv: kv[1]["mean_s"], default=(None, {}))[0]

    def _mean(key):
        vals = [r[key] for r in ok if isinstance(r.get(key), int)]
        return round(statistics.mean(vals), 1) if vals else None

    return {
        "runs": len(records),
        "ok_runs": len(ok),
        "wall_p50_s": _pct(walls, 50),
        "wall_p90_s": _pct(walls, 90),
        "wall_min_s": round(min(walls), 1),
        "wall_max_s": round(max(walls), 1),
        "mean_llm_calls": _mean("llm_calls"),
        "mean_search_calls": _mean("search_calls"),
        "mean_tokens": _mean("tokens"),
        "mean_sections": _mean("sections_researched"),
        "mean_auditor_passes": _mean("auditor_passes"),
        "dominant_stage": dominant,
        "stages": stage_table,
    }


def _markdown(summary: Dict[str, Dict[str, Any]], records: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# Mode baseline (Step 0)\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    lines.append(
        "Measured with `bench/run_mode_baseline.py` against live providers and "
        "live retrieval. These are observations, not targets.\n"
    )

    lines.append("\n## Headline\n")
    lines.append("| mode | ok/total | wall p50 | wall p90 | wall max | LLM calls | search | sections | auditor passes | dominant stage |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for mode in MODES:
        s = summary.get(mode) or {}
        if not s.get("ok_runs"):
            lines.append(f"| {mode} | 0/{s.get('runs', 0)} | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {mode} | {s['ok_runs']}/{s['runs']} | {s['wall_p50_s']}s | {s['wall_p90_s']}s | "
            f"{s['wall_max_s']}s | {s['mean_llm_calls']} | {s['mean_search_calls']} | "
            f"{s['mean_sections']} | {s['mean_auditor_passes']} | {s['dominant_stage']} |"
        )

    lines.append("\n## Per-stage mean (seconds)\n")
    all_stages = sorted({n for s in summary.values() for n in (s.get("stages") or {})})
    if all_stages:
        lines.append("| stage | " + " | ".join(MODES) + " |")
        lines.append("|---" * (len(MODES) + 1) + "|")
        for node in all_stages:
            cells = []
            for mode in MODES:
                st = (summary.get(mode) or {}).get("stages", {}).get(node)
                cells.append(f"{st['mean_s']}" if st else "—")
            lines.append(f"| {node} | " + " | ".join(cells) + " |")

    lines.append("\n## Per-run detail\n")
    lines.append("| mode | query | ok | wall | LLM | search | sections | auditor | conf | error |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in records:
        q = r["query"][:38] + ("…" if len(r["query"]) > 38 else "")
        lines.append(
            f"| {r['mode']} | {q} | {'yes' if r.get('ok') else 'no'} | {r.get('wall_s')}s | "
            f"{r.get('llm_calls','—')} | {r.get('search_calls','—')} | {r.get('sections_researched','—')} | "
            f"{r.get('auditor_passes','—')} | {r.get('confidence','—')} | {r.get('error','')} |"
        )
    lines.append("")
    return "\n".join(lines)


async def _main_async(args: argparse.Namespace) -> int:
    queries = DEFAULT_QUERIES
    if args.queries_file:
        queries = [
            line.strip()
            for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if args.query:
        queries = [args.query]

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    bad = [m for m in modes if m not in MODES]
    if bad:
        print(f"unknown mode(s): {bad}; expected one of {MODES}", file=sys.stderr)
        return 2

    records: List[Dict[str, Any]] = []
    for mode in modes:
        for query in queries:
            print(f"[{mode}] {query[:60]}…", flush=True)
            rec = await _run_one(mode, query, args.timeout)
            print(
                f"    -> ok={rec.get('ok')} wall={rec.get('wall_s')}s "
                f"llm={rec.get('llm_calls')} search={rec.get('search_calls')} "
                f"sections={rec.get('sections_researched')} "
                f"auditor={rec.get('auditor_passes')} {rec.get('error','')}",
                flush=True,
            )
            records.append(rec)
            # A short pause between runs so a provider rate limit does not make
            # the second run look slower than it is.
            await asyncio.sleep(args.pause)

    summary = {mode: _summarize([r for r in records if r["mode"] == mode]) for mode in modes}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "queries": queries,
        "modes": modes,
        "summary": summary,
        "runs": records,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "mode_baseline.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "MODE_BASELINE.md").write_text(_markdown(summary, records), encoding="utf-8")
    print(f"\nwrote {out_dir/'mode_baseline.json'} and {out_dir/'MODE_BASELINE.md'}")

    # Non-zero only if nothing at all succeeded — a slow run is data.
    return 0 if any(r.get("ok") for r in records) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", default="quick,standard,deep")
    parser.add_argument("--query", default=None, help="a single query (overrides the set)")
    parser.add_argument("--queries-file", default=None, help="one query per line")
    parser.add_argument("--timeout", type=float, default=1100.0, help="per-run hard timeout (s)")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between runs")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
