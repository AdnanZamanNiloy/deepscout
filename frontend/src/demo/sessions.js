/* DeepScout static-demo runtime fixtures: the chats the demo lists.
 *
 * The DATA is generated, not authored — see ./runs.js and
 * bench/make_demo_fixtures.py. Every run is a real recorded run: its query,
 * plan, progress lines, critic reviews, confidence, findings and full report
 * came out of `GET /api/research/{run_id}/trace`. This module and
 * ./conversations.js only map that recording into the two shapes the demo
 * runtime and the UI expect.
 *
 * Because both read the same rows, a streamed run and a replayed run report the
 * same claim count, the same confidence and the same report. The demo is the
 * real UI pointed at recordings, so a divergence here would look like the bug it
 * stands in for.
 */

import { DEMO_RUNS } from "./runs";

/* The plan the run announced. Recorded on the wire as a frame of section
 * titles; the replay shape wants rows, so they are derived from that same
 * frame rather than invented. */
function planRows(run) {
  return run.plan.map((question) => ({ question }));
}

export const DEMO_SESSIONS = DEMO_RUNS.map((run) => ({
  id: run.sessionId,
  title: run.title || run.query,
  query: run.query,
  status: run.status,
  confidence: run.confidence,
  created_at: run.createdAt,
  updated_at: run.updatedAt,
  run_ids: [run.runId],
  run_count: 1,
}));

export const DEMO_SESSION_IDS = DEMO_RUNS.map((r) => r.sessionId);

/* The trace shape `GET /api/research/{run_id}/trace` returns. Real columns
 * throughout: `claims` rows carry claim/source_url/verified/confidence/agent/
 * challenged, and `events` is the agent_events stage lifecycle the restored
 * rail reads. `verification_results` and `evidence` are empty because the run
 * genuinely recorded none. */
function traceFor(run) {
  return {
    run_id: run.runId,
    session_id: run.sessionId,
    query: run.query,
    status: run.status,
    confidence: run.confidence,
    created_at: run.createdAt,
    completed_at: run.finalReport.generated_at || run.updatedAt,
    plan: planRows(run),
    claims: run.claims,
    sources: run.sources,
    evidence: [],
    critic_reviews: run.criticReviews,
    decisions: run.decisions,
    contradictions: run.contradictions,
    verification_results: [],
    citations: [],
    events: run.events,
    /* No `delay` (a replay detail) and no terminal frame (the card renders the
     * report from final_report below). */
    frames: run.frames.map(({ delay: _d, ...rest }) => rest),
    /* The real wire fields, under the row names the replay card reads. The
     * breakdown, notes, quality and answer-support figures all come from the
     * recorded terminal frame, so a replayed run shows the same health the live
     * run showed. */
    final_report: {
      report_markdown: run.finalReport.report_markdown || "",
      audit_markdown: run.finalReport.audit_markdown || "",
      confidence: run.finalReport.confidence ?? run.confidence,
      confidence_breakdown: run.finalReport.confidence_breakdown || {},
      confidence_notes: run.finalReport.confidence_notes || [],
      quality: run.finalReport.quality || null,
      degraded: run.finalReport.degraded || [],
      answer_support: run.finalReport.answer_support ?? null,
      citation_health: run.finalReport.citation_health || {},
      generated_at: run.finalReport.generated_at || "",
    },
  };
}

const TRACE_BY_RUN = new Map(DEMO_RUNS.map((r) => [r.runId, traceFor(r)]));

export function demoTrace(runId) {
  return TRACE_BY_RUN.get(runId) || null;
}

/* Session detail — the ordered user question + run message the chat replays. */
export function demoSessionDetail(sessionId) {
  const run = DEMO_RUNS.find((r) => r.sessionId === sessionId);
  if (!run) return null;
  const at = run.updatedAt || run.createdAt;
  return {
    id: run.sessionId,
    title: run.title || run.query,
    created_at: run.createdAt || at,
    updated_at: at,
    messages: [
      {
        id: `${run.runId}-user`,
        role: "user",
        text: run.query,
        created_at: run.createdAt || at,
        run_id: run.runId,
      },
      {
        id: `${run.runId}-run`,
        role: "run",
        run_id: run.runId,
        status: run.status,
        confidence: run.confidence,
        created_at: at,
        query: run.query,
        report: run.finalReport.report_markdown || "",
        audit: run.finalReport.audit_markdown || "",
      },
    ],
    runs: [{
      id: run.runId,
      query: run.query,
      status: run.status,
      confidence: run.confidence,
      created_at: at,
    }],
    run_ids: [run.runId],
    run_count: 1,
  };
}