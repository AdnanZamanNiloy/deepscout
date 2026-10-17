#!/usr/bin/env python3
"""Regenerate the static-demo fixtures from REAL recorded runs.

Why this exists
---------------
The demo used to be hand-authored: someone read a recorded run and retyped it
into `conversations.js` / `sessions.js` as JavaScript literals. That is
guaranteed to rot, and it did. The fixtures ended up describing a pipeline that
no longer exists — their own header claimed the wire emits no `findings` frame
(it does), invented the sidebar's chats, and shipped `example.org` citations
that were never retrieved. Nobody noticed because a fixture is data: nothing
fails when it is wrong, it just shows a visitor a product that never ran.

So the fixtures are now GENERATED from what the backend actually recorded, and
this script is the only thing allowed to write them:

    python backend/bench/make_demo_fixtures.py --base http://127.0.0.1:8000

Every byte in `frontend/src/demo/runs.js` comes from
`GET /api/research/{run_id}/trace` on a real run. Re-run it after any change to
the event contract and the demo cannot drift: if the wire gains a field, the
demo has it, because it is the recording.

What is real, and what is bounded
---------------------------------
Real, verbatim: the query, the plan, every progress/stage/critic frame, the
critic reviews, the confidence breakdown and notes, the FULL report markdown,
the agent_events lifecycle, and the run's real session id, confidence and
status. Nothing is invented or beautified.

Bounded, for bundle size only (override with the flags below):
  * claims / findings  --real rows, first N. `findings` frame items are derived
    from the SAME rows, so a streamed run and a replayed one always report the
    same claim count. That invariant is the whole point of the findings frame;
    a demo where they disagreed would reintroduce the bug it fixed.
  * sources            --real rows, first N.
  * agent_events       --dropped. `GET /trace` does return it, but the client
    reads the `stage` frames (`trace.frames`) for the lifecycle, and these
    frames already carry every node's start and end. It was ~12% of the payload
    for a field no reader touches.
  * inter-frame delays --real gaps between recorded frames, scaled so a whole
    run replays in ~REPLAY_SECONDS instead of its real minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# backend/bench/ -> backend/ -> repo root
ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "frontend" / "src" / "demo" / "runs.js"

# The runs the demo features. Chosen for topic spread and real confidence, all
# completed, all recent. Pin them by RUN id: a session id is stable but its LAST
# run changes every time the same chat is re-run.
FEATURED_RUNS = [
    # Recorded by the CURRENT build, so it carries today's wire: a `findings`
    # frame and `confidence_notes` on the terminal frame. Keep at least one run
    # like this — older recordings predate those fields, and a demo whose every
    # run lacks them describes a pipeline that no longer emits them.
    "7b58577a",  # perovskite vs crystalline silicon  conf 0.595  quick mode
    "f69ac1d0",  # grid-scale energy storage          conf 0.696
    "d2591e7e",  # AI and employment                  conf 0.648
    "0ee4ad5d",  # inflation in Bangladesh            conf 0.627
    "a97d5a82",  # AI tools and critical thinking     conf 0.565
]

REPLAY_SECONDS = 26.0   # target wall-clock for one streamed run in the demo
MIN_DELAY_MS = 140      # never flash a frame past readability
MAX_DELAY_MS = 2600     # and never sit on one for the length of a real call

# Fields of a `claims` row the UI never reads. Dropped to keep the payload lean.
_CLAIM_KEEP = (
    "id", "claim", "source_url", "verified", "confidence",
    "agent", "challenged", "created_at",
)
_SOURCE_KEEP = (
    "id", "url", "source_domain", "publisher", "reliability_score",
    "retrieval_provider", "retrieval_engine", "fetched_at",
)


def get(base: str, path: str):
    url = f"{base.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        sys.exit(f"[demo] {url} -> HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - a generator should say why and stop
        sys.exit(f"[demo] {url} -> {exc}")


def js(value) -> str:
    """JSON is a subset of JS, so this is safe for objects/arrays/strings/numbers.

    Used instead of hand-writing literals because the point of this script is
    that no human retypes a recording.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_delays(frames: list[dict], replay_seconds: float) -> list[int]:
    """Real inter-frame gaps, scaled so the whole run fits the replay budget."""
    stamps = [f.get("ts") for f in frames if isinstance(f.get("ts"), (int, float))]
    span = max(stamps) - min(stamps) if len(stamps) > 1 else 0
    scale = (replay_seconds * 1000.0 / span) if span > 0 else 0.0

    delays: list[int] = []
    prev = None
    for frame in frames:
        ts = frame.get("ts")
        if prev is None or not isinstance(ts, (int, float)):
            delays.append(MIN_DELAY_MS)
        else:
            raw = (ts - prev) * scale
            delays.append(int(max(MIN_DELAY_MS, min(MAX_DELAY_MS, round(raw)))))
        if isinstance(ts, (int, float)):
            prev = ts
    return delays


def to_finding(row: dict) -> dict:
    """A claims row in the shape a live `findings` frame item uses.

    `verified`/`challenged` must be real booleans: the panel counts
    `f.verified === true`, so a SQLite 1 here would count zero verified claims
    in a run that really verified them.
    """
    return {
        "claim": row.get("claim", ""),
        "source": row.get("source_url", ""),
        "verified": row.get("verified") in (1, True),
        "confidence": row.get("confidence"),
        "agent": row.get("agent") or "",
        "challenged": row.get("challenged") in (1, True),
    }


def build_run(base: str, prefix: str, session_by_run: dict, caps) -> dict:
    matches = [s for s in session_by_run.values() if any(r.startswith(prefix) for r in s.get("run_ids") or [])]
    if not matches:
        sys.exit(f"[demo] no session references run {prefix}")
    session = matches[0]
    run_id = next(r for r in session["run_ids"] if r.startswith(prefix))
    trace = get(base, f"/api/research/{run_id}/trace")

    frames = trace.get("frames") or []
    if not frames:
        sys.exit(f"[demo] run {run_id} recorded no frames")
    if not ((trace.get("final_report") or {}).get("report_markdown")
            or next((f.get("report") for f in frames if f.get("type") == "final_report"), None)):
        sys.exit(f"[demo] run {run_id} has no report; refusing to feature an empty run")

    delays = build_delays(frames, caps.replay_seconds)

    claims = [dict((k, row.get(k)) for k in _CLAIM_KEEP) for row in (trace.get("claims") or [])]
    claims = claims[: caps.claims]
    sources = [dict((k, s.get(k)) for k in _SOURCE_KEEP) for s in (trace.get("sources") or [])][: caps.sources]

    # The report is ~40KB of markdown and it exists in TWO places on the wire:
    # the recorded final_report frame and the trace's final_report row. Storing
    # both doubled the fixture file for nothing, so the frame list keeps every
    # frame EXCEPT the terminal one and `finalReport` holds it once. The adapters
    # rebuild the terminal frame from it, which is also why a streamed run and a
    # replayed one cannot show different reports.
    plan_items: list[str] = []
    out_frames: list[dict] = []
    final_frame: dict | None = None
    for frame, delay in zip(frames, delays):
        ftype = frame.get("type")
        if ftype == "plan":
            plan_items = frame.get("items") or []
        if ftype == "final_report":
            final_frame = {k: v for k, v in frame.items() if k not in ("ts", "type")}
            continue
        out = {k: v for k, v in frame.items() if k != "ts"}
        if ftype == "findings":
            # The items are NOT stored: they are re-derived from `claims` by
            # the adapter, so the streamed and replayed claim lists are the same
            # array by construction rather than by two copies agreeing. The
            # recorded (uncapped) items are dropped for the same reason.
            out.pop("items", None)
        out["delay"] = delay
        out_frames.append(out)

    if final_frame is None:
        sys.exit(f"[demo] run {run_id} recorded no final_report frame")
    if not any(f.get("type") == "findings" for f in out_frames):
        sys.exit(f"[demo] run {run_id} recorded no findings frame; the demo must not invent one")

    # The RICH payload is the recorded frame, not the trace's final_report row:
    # the row is only {report_markdown, audit_markdown, confidence, generated_at},
    # while confidence_breakdown, confidence_notes, quality, citation_health,
    # source_count, degraded and answer_support exist only on the wire frame.
    # Reading the row alone would have shipped a demo whose run had no
    # confidence breakdown and no health figures — silently, because every
    # field it did read was present.
    row = trace.get("final_report") or {}
    final = dict(final_frame)
    final["report_markdown"] = final.pop("report", None) or row.get("report_markdown") or ""
    final["audit_markdown"] = final.pop("audit", None) or row.get("audit_markdown") or ""
    final["generated_at"] = row.get("generated_at") or ""

    return {
        "runId": run_id,
        "sessionId": session["id"],
        "query": session.get("query") or session.get("title") or "",
        "title": session.get("title") or "",
        "status": session.get("status") or trace.get("status") or "completed",
        "confidence": session.get("confidence") if session.get("confidence") is not None else trace.get("confidence"),
        "createdAt": session.get("created_at") or trace.get("created_at") or "",
        "updatedAt": session.get("updated_at") or trace.get("updated_at") or "",
        "realClaimCount": len(trace.get("claims") or []),
        "realSourceCount": len(trace.get("sources") or []),
        "plan": plan_items,
        "claims": claims,
        "sources": sources,
        "criticReviews": trace.get("critic_reviews") or [],
        "contradictions": trace.get("contradictions") or [],
        "decisions": trace.get("decisions") or [],
        "finalReport": final,
        "frames": out_frames,
    }


def render(runs: list[dict]) -> str:
    body = ",\n".join(f"  {js(r)}" for r in runs)
    return f"""/* GENERATED FILE - DO NOT EDIT BY HAND.
 *
 * Regenerate with:
 *   python backend/bench/make_demo_fixtures.py --base http://127.0.0.1:8000
 *
 * Every value below is a REAL recorded run, pulled from
 * `GET /api/research/{{run_id}}/trace` by that script. Nothing here is
 * invented: the queries, plans, progress lines, critic reviews, confidence
 * breakdown, notes, agent_events and FULL reports are what the pipeline
 * actually produced, and the {{runId}}/{{sessionId}} pairs are real.
 *
 * This file exists as generated data precisely so the demo cannot describe a
 * pipeline that no longer runs. The previous hand-authored fixtures claimed the
 * wire emitted no `findings` frame (it does), shipped invented chats and
 * `example.org` citations, and nothing failed — a fixture is data, so being
 * wrong looks exactly like being right. Regenerate instead of editing.
 *
 * Two fields are DISCLOSED bounds rather than fiction:
 *   realClaimCount / realSourceCount are what the run really found; the
 *   `claims`/`findings`/`sources` arrays beside them are the first N of those
 *   real rows (see bench/make_demo_fixtures.py). A `findings` frame item is
 *   derived from the same claim row the replay reads, so a streamed run and a
 *   replayed one always report the SAME count — the invariant the findings
 *   frame exists to hold. Report markdown is kept WHOLE; only the arrays are
 *   bounded, because a truncated report would misdescribe a finished run.
 *
 * `frames` carry a `delay` in ms (real gaps, scaled to a ~26s replay) and no
 * `ts`: the demo runtime stamps each frame fresh as it emits it, so a run's
 * duration on screen is its playback time, not the recording's wall clock.
 */
export const DEMO_RUNS = [
{body},
];

export default DEMO_RUNS;
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="backend base URL")
    ap.add_argument("--claims", type=int, default=30, help="real claim rows per run (also the findings count)")
    ap.add_argument("--sources", type=int, default=30, help="real source rows per run")
    ap.add_argument("--replay-seconds", type=float, default=REPLAY_SECONDS, help="target playback seconds per run")
    caps = ap.parse_args()

    payload = get(caps.base, "/api/sessions")
    sessions = payload.get("sessions", payload) if isinstance(payload, dict) else payload
    session_by_run = {s["id"]: s for s in sessions}

    runs = [build_run(caps.base, prefix, session_by_run, caps) for prefix in FEATURED_RUNS]
    OUT.write_text(render(runs), encoding="utf-8")

    claims = sum(len(r["claims"]) for r in runs)
    srcs = sum(len(r["sources"]) for r in runs)
    print(f"[demo] wrote {OUT.relative_to(ROOT)}")
    print(f"[demo] {len(runs)} real runs | {OUT.stat().st_size / 1024:.0f} KB")
    for r in runs:
        print(f"        {r['runId'][:8]}  conf {r['confidence']}  "
              f"claims {len(r['claims'])}/{r['realClaimCount']}  "
              f"sources {len(r['sources'])}/{r['realSourceCount']}  "
              f"frames {len(r['frames'])}  {r['query'][:46]}")


if __name__ == "__main__":
    main()