/* Static-demo fetch interceptor.
 *
 * Contract: with demo mode active, every `/api/*` call the console makes is
 * answered locally — the visitor's browser never reaches the network — and the
 * research stream is abortable exactly like the real one, so "Stop" works.
 *
 * Loads the SHIPPED interceptor through a small shim (Node cannot resolve the
 * extensionless Vite imports), installs it against a mock window, and asserts
 * the responses the client depends on.
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
const outDir = join(here, ".demo-build", "interceptor");
mkdirSync(outDir, { recursive: true });

for (const name of ["conversations", "sessions", "index", "runs"]) {
  let src = readFileSync(join(srcDemo, `${name}.js`), "utf8");
  src = src.replace(/from "\.\/([a-z]+)"/g, 'from "./$1.js"');
  src = src.replace(/import\.meta\.env/g, '({ VITE_DEMO: "1" })');
  writeFileSync(join(outDir, `${name}.js`), src);
}

// A minimal browser surface. `fetch` is on `window` in a browser; in Node the
// real fetch is global, so the mock window borrows it before interception.
globalThis.window = {
  location: { search: "?demo=1", hash: "", origin: "http://localhost" },
  __deepscoutDemoInstalled: false,
  fetch: globalThis.fetch,
};
globalThis.localStorage = {
  _d: {},
  getItem(k) { return this._d[k] ?? null; },
  setItem(k, v) { this._d[k] = String(v); },
};

const { installDemoFetch, IS_DEMO, setDemoSpeed } = await import(join(outDir, "index.js"));
const { DEMO_SESSIONS } = await import(join(outDir, "sessions.js"));
// Replay instantly: the authored ~10s of pauses are a viewing detail, not a
// behaviour under test. The abort test re-enables a small real delay below.
setDemoSpeed(0.001);
installDemoFetch();
const demoFetch = window.fetch;

test("demo mode is recognised from ?demo=1", () => {
  assert.equal(IS_DEMO, true);
});

test("GET /api/sessions returns the demo sidebar", async () => {
  const res = await demoFetch("/api/sessions");
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.ok(Array.isArray(body.sessions) && body.sessions.length >= 3);
  assert.ok(body.sessions[0].id && body.sessions[0].title);
});

test("GET /api/sessions/{id} returns an ordered chat history", async () => {
  // The id is the run's REAL session id, read from the fixture — a hardcoded id
  // here would silently stop exercising the handler the moment the recordings
  // are re-recorded.
  const res = await demoFetch(`/api/sessions/${DEMO_SESSIONS[0].id}`);
  const body = await res.json();
  assert.equal(body.messages[0].role, "user");
  assert.equal(body.messages[1].role, "run");
});

test("an unknown session id returns 404, not a fabricated success", async () => {
  const res = await demoFetch("/api/sessions/does-not-exist");
  assert.equal(res.status, 404);
});

test("GET /api/research/{id}/trace returns a readable trace", async () => {
  const res = await demoFetch(`/api/research/${DEMO_SESSIONS[0].run_ids[0]}/trace`);
  const body = await res.json();
  assert.ok(Array.isArray(body.claims) && body.claims.length);
  assert.ok(body.final_report.report_markdown.length > 100);
  // The trace's findings must survive the round trip, or a replayed run opens
  // with an empty evidence panel.
  assert.ok(Array.isArray(body.frames) && body.frames.some((f) => f.type === "findings"));
});

test("POST /api/research/stream replays NDJSON frames in order", async () => {
  const ctrl = new AbortController();
  const res = await demoFetch("/api/research/stream", {
    method: "POST",
    body: JSON.stringify({ query: "What is retrieval augmented generation?", session_id: "s" }),
    signal: ctrl.signal,
  });
  assert.equal(res.status, 200);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const types = [];
  // Read the whole stream (fixtures are short); collect frame types.
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const line of decoder.decode(value, { stream: true }).split("\n")) {
      if (line.trim()) types.push(JSON.parse(line).type);
    }
  }
  assert.equal(types[0], "progress");
  assert.ok(types.includes("plan"));
  assert.equal(types[types.length - 1], "final_report");
});

test("the stream honours an abort mid-flight (Stop works)", async () => {
  setDemoSpeed(1); // a real pause is needed so the abort lands mid-flight
  const ctrl = new AbortController();
  const res = await demoFetch("/api/research/stream", {
    method: "POST",
    body: JSON.stringify({ query: "What is retrieval augmented generation?" }),
    signal: ctrl.signal,
  });
  const reader = res.body.getReader();
  const first = await reader.read();
  assert.ok(!first.done, "at least one frame arrives");
  ctrl.abort();
  // Draining after abort must settle, not hang.
  let done = false;
  for (let i = 0; i < 200 && !done; i++) {
    try {
      ({ done } = await reader.read());
    } catch {
      done = true; // AbortError is an acceptable, expected termination
    }
  }
  assert.equal(done, true);
  setDemoSpeed(0.001);
});

test("unknown API paths 404 rather than fabricate a success", async () => {
  const res = await demoFetch("/api/not-a-real-endpoint");
  assert.equal(res.status, 404);
});

test("non-/api requests pass through to the real fetch", async () => {
  // A same-origin asset path must not be intercepted. We assert the mock
  // window.fetch was not consulted by checking the interceptor returns the
  // real fetch's result for a non-API URL (which here rejects for a bad host).
  const res = await demoFetch("data:application/json,%7B%22ok%22%3Atrue%7D");
  assert.equal(res.status, 200);
});
