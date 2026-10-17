/* DeepScout static-demo runtime fixtures: what a typed question streams.
 *
 * The DATA is generated, not authored. See ./runs.js and
 * bench/make_demo_fixtures.py. Every run is a real recorded run: its query,
 * plan, progress lines, critic reviews, confidence, findings and full report
 * came out of `GET /api/research/{run_id}/trace`. This module and ./sessions.js
 * only map that recording into the two shapes the demo runtime and the UI
 * expect.
 *
 * Because both read the same rows, a streamed run and a replayed run report the
 * same claim count, the same confidence and the same report. The demo is the
 * real UI pointed at recordings, so a divergence here would look like the bug it
 * stands in for.
 */

import { DEMO_RUNS } from "./runs";

/* Rebuild the terminal frame the recording held.
 *
 * `finalReport` IS that frame's own content, so re-emitting it is a spread, not
 * a reconstruction: a field added to the wire frame reaches the demo without
 * anyone editing this file. The two exceptions are the report and audit bodies:
 * they are stored under `*_markdown` (the trace row's own names, so the replay
 * trace and the wire frame can share one value) and renamed back here. The
 * client reads `evt.report` / `evt.audit`, so emitting the stored names would
 * silently render a run with no report and no audit. */
function finalFrame(run) {
  const { report_markdown, audit_markdown, generated_at: _generatedAt, ...rest } = run.finalReport;
  return {
    type: "final_report",
    delay: 700,
    ...rest,
    report: report_markdown || "",
    audit: audit_markdown || "",
  };
}

/* A claims row in the shape a live `findings` frame item uses.
 *
 * `verified`/`challenged` must be real booleans: the panel counts
 * `f.verified === true`, so a SQLite 1 here would count zero verified claims in
 * a run that really verified them. */
function findingFrom(row) {
  return {
    claim: row.claim || "",
    source: row.source_url || "",
    verified: row.verified === 1 || row.verified === true,
    confidence: row.confidence ?? null,
    agent: row.agent || "",
    challenged: row.challenged === 1 || row.challenged === true,
  };
}

/* Re-attach the claims to the findings frame the recording held.
 *
 * The generator stores the claims once and leaves the findings items off, so
 * this is the only place they are built, which is what makes a streamed run
 * and a replayed one report the SAME count by construction. Two stored copies
 * that happened to agree would be a weaker guarantee, and this is the bug the
 * findings frame exists to prevent. */
function withFindings(frames, run) {
  const items = run.claims.map(findingFrom);
  return frames.map((f) => (f.type === "findings" ? { ...f, items } : f));
}

export const CONVERSATIONS = DEMO_RUNS.map((run) => ({
  query: run.query,
  runId: run.runId,
  sessionId: run.sessionId,
  confidence: run.confidence,
  frames: [...withFindings(run.frames, run), finalFrame(run)],
}));

export const DEMO_RUN_IDS = DEMO_RUNS.map((r) => r.runId);

/* The questions the demo can actually answer: the real queries behind the
 * recordings. A visitor who types something else is served the closest
 * recording, and ./index.js says so in the run's first progress frame. */
export const DEFAULT_QUERY = CONVERSATIONS[0].query;
export const DEMO_STARTERS = CONVERSATIONS.map((c) => c.query);

/* Exact match only. pickConversation() in ./index.js does the keyword fallback
 * and discloses the substitution, so an unknown question replays a real run
 * rather than a blank one. */
export function findConversation(query) {
  const q = String(query || "").trim().toLowerCase();
  return CONVERSATIONS.find((c) => c.query.trim().toLowerCase() === q) || null;
}