/* Static-demo fixtures + fetch interceptor.
 *
 * Contract: the demo bundle runs the REAL console against scripted fixtures.
 * These tests lock the invariants the UI depends on:
 *   - every conversation streams in a valid order (progress → stages → plan →
 *     critic → final_report) and only uses known pipeline node names, so the
 *     stage rail can render every rung;
 *   - a conversation's declared source count matches its final frame;
 *   - the sidebar sessions, their details and their traces all exist and agree
 *     on ids, so a replay card never fetches a trace that isn't there;
 *   - the interceptor answers the client's API calls and streams abortable
 *     NDJSON without touching the network.
 *
 * The demo modules use extensionless relative imports (Vite resolves them);
 * plain Node does not, so this test loads them through a tiny shim that adds
 * the `.js` extension and stubs `import.meta.env`. The SHIPPED files are what
 * is under test — the shim only rewrites the import specifier.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const srcDemo = join(here, "..", "src", "demo");
/* Per-file output dir: `node --test` runs test FILES concurrently, so two
 * files sharing one shim dir race on the writes. Distinct dirs per file. */
const outDir = join(here, ".demo-build", "fixtures");mkdirSync(outDir, { recursive: true });

for (const name of ["conversations", "sessions", "index"]) {
  let src = readFileSync(join(srcDemo, `${name}.js`), "utf8");
  src = src.replace(/from "\.\/([a-z]+)"/g, 'from "./$1.js"');
  src = src.replace(/import\.meta\.env/g, '({ VITE_DEMO: "1" })');
  writeFileSync(join(outDir, `${name}.js`), src);
}

const VALID_NODES = new Set([
  "pathfinder", "architect", "review_gate", "scout_swarm",
  "narrative_writer", "evidence_auditor", "diagram_designer", "report_assembler",
]);

/* The ONLY frame types the current pipeline emits. Anything else (intent,
 * route, direct_answer, search_progress, search_query, findings) is from the
 * removed pre-consolidation engine and must not appear. */
const VALID_FRAME_TYPES = new Set([
  "progress", "stage", "plan", "plan_review", "critic", "final_report", "error",
]);

/* The real flat signals a final_report.confidence_breakdown carries. */
const CONFIDENCE_SIGNALS = [
  "source_quality", "source_diversity", "citation_coverage",
  "claim_verification_strength", "cross_source_agreement", "freshness",
  "critic_survival", "claim_evidence_quality",
];

const { CONVERSATIONS, DEFAULT_QUERY, DEMO_STARTERS, findConversation } =
  await import(join(outDir, "conversations.js"));
const { DEMO_SESSIONS, DEMO_RUN_IDS, demoSessionDetail, demoTrace } =
  await import(join(outDir, "sessions.js"));

test("there is a meaningful set of demo conversations", () => {
  assert.ok(CONVERSATIONS.length >= 3, "at least three scripted runs");
  for (const c of CONVERSATIONS) {
    assert.ok(typeof c.query === "string" && c.query.trim(), "has a query");
    assert.ok(Array.isArray(c.frames) && c.frames.length, "has frames");
  }
});

test("only current-contract frame types are emitted", () => {
  for (const c of CONVERSATIONS) {
    for (const f of c.frames) {
      assert.ok(VALID_FRAME_TYPES.has(f.type),
        `"${c.query}" emits a forbidden frame type "${f.type}"`);
    }
  }
});

test("every conversation streams in a valid order with known nodes", () => {
  for (const c of CONVERSATIONS) {
    const types = c.frames.map((f) => f.type);
    assert.equal(types[0], "progress", `"${c.query}" starts with progress`);
    assert.equal(c.frames[0].message, "Query received", "first progress is Query received");
    assert.ok(types.includes("plan"), `"${c.query}" emits a plan`);
    assert.ok(types.includes("final_report"), `"${c.query}" emits a final report`);
    assert.equal(types[types.length - 1], "final_report", `"${c.query}" ends on the report`);
    assert.ok(types.indexOf("plan") < types.indexOf("final_report"));
    assert.equal(c.frames.find((f) => f.type === "plan").items.length > 0, true);

    for (const f of c.frames) {
      if (f.type === "stage") {
        assert.ok(VALID_NODES.has(f.node), `unknown stage node "${f.node}"`);
        assert.ok(["running", "done"].includes(f.status));
      }
    }
  }
});

test("every node that starts also finishes (the rail cannot be left spinning)", () => {
  for (const c of CONVERSATIONS) {
    const byNode = {};
    for (const f of c.frames) {
      if (f.type !== "stage") continue;
      byNode[f.node] = byNode[f.node] || { running: 0, done: 0 };
      byNode[f.node][f.status] += 1;
    }
    for (const [node, counts] of Object.entries(byNode)) {
      assert.equal(counts.running, counts.done,
        `"${c.query}" node "${node}" has ${counts.running} running / ${counts.done} done`);
    }
    // And a clean run walks all eight nodes at least once.
    for (const n of VALID_NODES) {
      assert.ok(byNode[n], `"${c.query}" never ran node "${n}"`);
    }
  }
});

test("declared source count matches the final frame", () => {
  for (const c of CONVERSATIONS) {
    const final = c.frames.find((f) => f.type === "final_report");
    assert.equal(final.source_count, c.sourceCount, `"${c.query}" source_count`);
    assert.equal(typeof final.confidence, "number");
  }
});

test("final_report carries the real wire shape", () => {
  for (const c of CONVERSATIONS) {
    const f = c.frames.find((x) => x.type === "final_report");
    // confidence_breakdown is the FLAT signals dict, not {overall, signals}.
    assert.ok(f.confidence_breakdown && typeof f.confidence_breakdown === "object");
    assert.ok(!("signals" in f.confidence_breakdown), "breakdown must be flat, not nested");
    for (const sig of CONFIDENCE_SIGNALS) {
      assert.equal(typeof f.confidence_breakdown[sig], "number",
        `"${c.query}" missing signal ${sig}`);
    }
    // quality is the real metrics shape, not {grade, score}.
    assert.deepEqual(Object.keys(f.quality).sort(),
      ["findings", "independence", "is_clean", "section_coverage", "serious_count", "temporal"].sort());
    assert.ok(Array.isArray(f.quality.findings));
    assert.equal(typeof f.quality.is_clean, "boolean");
    // citation_health is {} in practice.
    assert.deepEqual(f.citation_health, {});
    assert.ok(Array.isArray(f.degraded));
    assert.ok(Array.isArray(f.contradictions));
  }
});

test("reports follow the publisher template and use inline links, never [n]", () => {
  for (const c of CONVERSATIONS) {
    const report = c.frames.find((f) => f.type === "final_report").report;
    assert.match(report, /^# .+/m, "has an H1 title");
    assert.match(report, /^## Introduction$/m, "has Introduction");
    assert.match(report, /^## Table of Contents$/m, "has Table of Contents");
    assert.match(report, /^## Conclusion$/m, "has Conclusion");
    assert.match(report, /^## References$/m, "has References");
    // Real delivered reports strip numeric markers; citations are inline links.
    assert.ok(!/\[\d+\]/.test(report), `"${c.query}" has a numeric [n] citation`);
  }
});

test("sidebar sessions, details and traces agree on identity", () => {
  assert.equal(DEMO_SESSIONS.length, CONVERSATIONS.length);
  DEMO_SESSIONS.forEach((s, i) => {
    const detail = demoSessionDetail(s.id);
    assert.ok(detail, `detail for ${s.id} exists`);
    assert.ok(detail.messages.length >= 2, "a user turn and a run turn");
    const trace = demoTrace(DEMO_RUN_IDS[i]);
    assert.ok(trace, `trace for ${DEMO_RUN_IDS[i]} exists`);
    assert.ok(trace.claims.length > 0, "trace carries claims for the evidence panel");
    assert.ok(trace.frames.length > 0, "trace carries replay frames for the rail");
    assert.ok(trace.final_report.report_markdown.length > 100);
  });
});

test("trace critic_reviews carry the real confidence breakdown the panel reads", () => {
  for (const runId of DEMO_RUN_IDS) {
    const trace = demoTrace(runId);
    assert.ok(trace.critic_reviews.length > 0);
    for (const rev of trace.critic_reviews) {
      const b = rev.breakdown;
      assert.equal(typeof b.overall, "number", "breakdown.overall is what the panel shows");
      assert.ok(b.signals && typeof b.signals === "object");
      assert.equal(typeof b.signals.source_quality, "number");
      assert.ok(b.weights && typeof b.weights === "object");
    }
  }
});

test("the replay frames exclude the terminal report (it is rendered separately)", () => {
  for (const runId of DEMO_RUN_IDS) {
    const trace = demoTrace(runId);
    assert.ok(!trace.frames.some((f) => f.type === "final_report"),
      "replay must not re-stream the report on top of the rendered one");
    assert.ok(!trace.frames.some((f) => "delay" in f),
      "authored delays are stripped from replay frames");
  }
});

test("findConversation matches exactly and returns null otherwise", () => {
  const first = CONVERSATIONS[0];
  assert.equal(findConversation(first.query)?.query, first.query);
  assert.equal(findConversation(first.query.toUpperCase()), findConversation(first.query));
  assert.equal(findConversation("a question with no script"), null);
});

test("starters and default query are real conversations", () => {
  assert.equal(findConversation(DEFAULT_QUERY)?.query, DEFAULT_QUERY);
  for (const q of DEMO_STARTERS) assert.ok(findConversation(q), `starter "${q}" resolves`);
});
