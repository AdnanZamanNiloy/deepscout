/* The demo fixtures must be REAL runs, not plausible-looking fiction.
 *
 * The fixtures used to be hand-authored: real-shaped literals with invented
 * chats, invented claims and `example.org` citations that were never retrieved.
 * Nothing failed, because a fixture is data: being wrong looks exactly like
 * being right, and a visitor has no way to tell. That is the failure mode this
 * file exists to prevent.
 *
 * These assertions therefore check provenance and honesty, not just shape:
 *   - the payload is generated, and says so;
 *   - ids are real UUIDs and queries are real questions;
 *   - every cited source is a real, fetchable-looking http(s) URL on a real
 *     domain, never a placeholder;
 *   - claims are real sentences with real verification verdicts, and the
 *     `findings` frame and the replay `claims` are THE SAME rows, so a
 *     streamed run and a replayed run report one claim count;
 *   - the bounded arrays are disclosed, and the real totals beside them are
 *     never quietly lowered to match the subset.
 *
 * If the demo ever needs re-recording, run the generator. Do not
 * hand-edit runs.js.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const srcDemo = join(here, "..", "src", "demo");
const outDir = join(here, ".demo-build", "realdata");
mkdirSync(outDir, { recursive: true });

/* The shipped modules use extensionless imports (Vite resolves them); plain
 * Node does not, so add the extension. The SHIPPED file is what is tested. */
for (const name of ["conversations", "sessions", "runs"]) {
  const src = readFileSync(join(srcDemo, `${name}.js`), "utf8")
    .replace(/from "\.\/([a-z]+)"/g, 'from "./$1.js"');

  writeFileSync(join(outDir, `${name}.js`), src);
}

const runsSource = readFileSync(join(srcDemo, "runs.js"), "utf8");
const { DEMO_RUNS } = await import(join(outDir, "runs.js"));
const { CONVERSATIONS } = await import(join(outDir, "conversations.js"));
const { DEMO_SESSIONS, demoTrace } = await import(join(outDir, "sessions.js"));

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/* Hosts that only ever appear in invented data. */
const PLACEHOLDER_HOSTS = [
  "example.org", "example.com", "example.net", "localhost",
  "test.com", "foo.com", "placeholder", "your-domain",
];

test("the payload is generated and says how to regenerate it", () => {
  assert.match(runsSource, /GENERATED FILE - DO NOT EDIT BY HAND/,
    "runs.js must declare itself generated");
  assert.match(runsSource, /bench\/make_demo_fixtures\.py/,
    "runs.js must name the script that produced it");
});

test("every featured run and session carries a real id", () => {
  for (const r of DEMO_RUNS) {
    assert.match(r.runId, UUID, `run id ${r.runId} is not a uuid`);
    assert.match(r.sessionId, UUID, `session id ${r.sessionId} is not a uuid`);
  }
});

test("the demo's chats are real questions, not labels", () => {
  assert.ok(DEMO_RUNS.length >= 3, "enough real runs to browse");
  for (const r of DEMO_RUNS) {
    assert.ok(r.query.trim().length > 15, `"${r.query}" is not a real question`);
    assert.ok(r.status === "completed", `run ${r.runId} is ${r.status}; feature finished work`);
    assert.equal(typeof r.confidence, "number");
  }
});

test("no source is a placeholder: every citation is a real URL", () => {
  let checked = 0;
  for (const r of DEMO_RUNS) {
    for (const s of r.sources) {
      let url;
      try {
        url = new URL(s.url);
      } catch {
        assert.fail(`run ${r.runId} cites an unparseable url: ${s.url}`);
      }
      assert.ok(url.protocol === "https:" || url.protocol === "http:",
        `run ${r.runId} cites a non-http url: ${s.url}`);
      for (const host of PLACEHOLDER_HOSTS) {
        assert.ok(!url.hostname.includes(host),
          `run ${r.runId} cites the placeholder host ${url.hostname}`);
      }
      checked += 1;
    }
  }
  assert.ok(checked > 20, `expected many real citations, checked ${checked}`);
});

test("every kept claim is a real sentence with a real verdict", () => {
  for (const r of DEMO_RUNS) {
    assert.ok(r.claims.length > 0, `run ${r.runId} has no claims`);
    for (const c of r.claims) {
      assert.ok(c.claim.trim().length > 20, `run ${r.runId} has a stub claim: ${c.claim}`);
      assert.ok(c.source_url.startsWith("http"), `claim has no source url: ${c.claim.slice(0, 40)}`);
      assert.ok([0, 1, true, false].includes(c.verified), "verified is a real flag");
      assert.equal(typeof c.confidence, "number");
    }
    /* A run where every claim is verified is a fiction; real pools contain
     * failures, and the panel's verified count is meaningless without them. */
    const verified = r.claims.filter((c) => c.verified === 1 || c.verified === true).length;
    assert.ok(verified < r.claims.length,
      `run ${r.runId} marks every kept claim verified, which no real run does`);
  }
});

test("a streamed run and a replayed run report the SAME claims", () => {
  /* The findings frame exists so a live run's panel shows what its replay
   * shows. Deriving both from one array is what guarantees it, and the old
   * fixtures emitted no findings frame at all, so the demo's live run showed an
   * empty evidence panel. */
  for (const c of CONVERSATIONS) {
    const findings = c.frames.find((f) => f.type === "findings");
    assert.ok(findings, `"${c.query}" streams no findings frame`);
    const trace = demoTrace(c.runId);
    assert.equal(findings.items.length, trace.claims.length,
      `"${c.query}" streams ${findings.items.length} claims but replays ${trace.claims.length}`);
    findings.items.forEach((item, i) => {
      const row = trace.claims[i];
      assert.equal(item.claim, row.claim, "same claim text");
      assert.equal(item.verified, row.verified === 1 || row.verified === true,
        "verified must be a BOOLEAN: the panel counts `f.verified === true`");
    });
  }
});

test("the bounded arrays are disclosed, and the real totals are not lowered", () => {
  for (const r of DEMO_RUNS) {
    assert.ok(r.realClaimCount >= r.claims.length,
      "the disclosed real claim total cannot be below what was kept");
    assert.ok(r.realSourceCount >= r.sources.length);
    const final = r.finalReport;
    assert.equal(final.source_count, r.realSourceCount,
      "source_count is what the run retrieved and must stay the real figure");
  }
});

test("every run's report is a finished report, not a stub", () => {
  for (const r of DEMO_RUNS) {
    const md = r.finalReport.report_markdown || "";
    assert.ok(md.length > 4000, `run ${r.runId} report is only ${md.length} chars`);
    assert.match(md, /^# .+/m, "has an H1 title");
    assert.match(md, /^## Introduction$/m, "has Introduction");
    assert.match(md, /^## Conclusion$/m, "has Conclusion");
    assert.match(md, /^## References$/m, "has References");
    assert.doesNotMatch(md, /\[\d+\]/, "citations are inline links, never [n]");
  }
});

test("the terminal frame uses the wire field names the client reads", () => {
  /* The client reads `evt.report` / `evt.audit`. An earlier adapter emitted the
   * stored `report_markdown` names instead, and the demo rendered runs with no
   * report and no audit, silently, because every other field was present. */
  for (const c of CONVERSATIONS) {
    const final = c.frames.at(-1);
    assert.equal(final.type, "final_report");
    assert.ok(typeof final.report === "string" && final.report.length > 1000,
      `"${c.query}" terminal frame has no report`);
    assert.ok("audit" in final, "terminal frame must carry an audit field");
    assert.ok("report_markdown" in final === false,
      "the stored name must not leak onto the wire frame");
    assert.equal(typeof final.confidence, "number");
    assert.ok(final.confidence_breakdown && typeof final.confidence_breakdown === "object");
    /* Runs recorded before the field existed simply lack it. That is history,
     * not an error, and the generator must not invent one. But the set must
     * include at least one run recorded by the CURRENT build, or the demo never
     * shows today's wire and drifts back to describing an older pipeline. */
    if ("confidence_notes" in final) {
      assert.ok(Array.isArray(final.confidence_notes),
        "confidence_notes, when present, must be an array");
    }
  }
  assert.ok(
    CONVERSATIONS.some((c) => Array.isArray(c.frames.at(-1).confidence_notes)),
    "no featured run was recorded by the current build; re-record one",
  );
});

test("the sidebar lists exactly the runs the demo can replay", () => {
  assert.equal(DEMO_SESSIONS.length, DEMO_RUNS.length);
  DEMO_SESSIONS.forEach((s, i) => {
    assert.equal(s.id, DEMO_RUNS[i].sessionId);
    assert.deepEqual(s.run_ids, [DEMO_RUNS[i].runId]);
    assert.ok(demoTrace(s.run_ids[0]), `no trace for ${s.run_ids[0]}`);
  });
});