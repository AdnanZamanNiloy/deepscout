/* Component render tests for <AgentPipelineTrace>.
 *
 * These exist because of a shipped bug: the trace header read a variable that
 * was only bound inside a nested branch, and `vite build` was perfectly happy
 * with it — undefined identifiers are runtime errors, so the whole message
 * list unmounted behind an error boundary in the browser while every unit
 * test and the production build passed.
 *
 * A pure-function test suite structurally cannot catch that class of defect.
 * Rendering the component does: any unbound identifier throws here.
 *
 * The component is JSX/TSX, which Node's type stripping does not transform, so
 * esbuild (already present via Vite) compiles it first and the result is
 * rendered with react-dom/server.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const { build } = await import("esbuild");
const { renderToStaticMarkup } = await import("react-dom/server");
const React = (await import("react")).default;

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");

let AgentPipelineTrace;
let outDir;

before(async () => {
  // The compiled module must live INSIDE the project so its bare imports
  // (react, framer-motion) resolve against this project's node_modules — and
  // React must stay external so the component and react-dom/server share one
  // copy, or the hooks dispatcher mismatches.
  outDir = fs.mkdtempSync(path.join(root, ".render-tmp-"));
  const result = await build({
    entryPoints: [path.join(root, "src/trace/AgentPipelineTrace.tsx")],
    bundle: true,
    format: "esm",
    platform: "neutral",
    jsx: "automatic",
    write: false,
    // Keep React external so the compiled module resolves it from node_modules
    // exactly as the browser build does.
    external: ["react", "react/jsx-runtime", "framer-motion"],
    logLevel: "silent",
  });
  const file = path.join(outDir, "trace.mjs");
  fs.writeFileSync(file, result.outputFiles[0].text);
  AgentPipelineTrace = (await import(`file://${file}`)).default;
});

after(() => {
  if (outDir) fs.rmSync(outDir, { recursive: true, force: true });
});

const hit = (host, i = 1) => ({
  title: `Doc ${i}`,
  url: `https://www.${host}.com/a-${i}`,
  source: "tavily",
});

const fullRun = [
  { type: "intent", query_type: "analytical", domain: "energy", explanation_level: "practical" },
  { type: "route", path: "research", reason: "needs current data" },
  { type: "plan", items: ["angle one", "angle two"] },
  { type: "search_progress", snippets: 9 },
  { type: "search_query", query: "grid storage 2026", results: [hit("iea", 1), hit("reuters", 2)] },
  { type: "search_query", query: "", results: [], issued: 2 },
  {
    type: "findings",
    verified_update: true,
    items: [
      { claim: "Additions reached 108 GW.", source: "https://iea.org/a", verified: true },
      { claim: "LFP share near 90%.", source: "https://reuters.com/b" },
    ],
  },
  { type: "critic", iteration: 1, reason: "one angle unsourced" },
  { type: "final_report", report: "The answer body.", confidence: 0.82 },
];

const render = (props) =>
  renderToStaticMarkup(
    React.createElement(AgentPipelineTrace, { events: fullRun, status: "done", ...props }),
  );

test("renders a full run without throwing", () => {
  const html = render();
  assert.match(html, /Agent Flow|agent flow/i);
});

test("every phase from the wire is present", () => {
  const html = render();
  for (const needle of [
    "Understood the question",
    "Planned 2 investigation angles",
    "Searched the web",
    "Verified 1 of 2 claims",
    "Evidence review",
    "Wrote the answer",
  ]) {
    assert.ok(html.includes(needle), `missing phase: ${needle}`);
  }
});

test("the mode toggle advertises the real counts", () => {
  const html = render();
  // 6 phases from 9 frames; the raw view shows every frame.
  assert.match(html, /6\s*phases/);
  assert.match(html, /9\s*events/);
});

test("the raw evidence is not dumped into the default view", () => {
  const html = render();
  // Claim bodies and result URLs live behind the disclosure, not in the rail.
  assert.doesNotMatch(html, /Additions reached 108 GW\./);
  assert.doesNotMatch(html, /iea\.org\/a/);
});

test("an empty stream renders an empty state, not a fake story", () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentPipelineTrace, { events: [], status: "done" }),
  );
  assert.match(html, /No agent steps/i);
  assert.doesNotMatch(html, /Planned|Searched the web/);
});

test("a live run marks a phase as in progress", () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentPipelineTrace, {
      events: [{ type: "intent", query_type: "factual" }],
      status: "running",
    }),
  );
  assert.match(html, /data-status="running"/);
});

test("a partial stream never throws on malformed frames", () => {
  const messy = [null, undefined, {}, 42, { type: 7 }, { type: "findings", items: "nope" }];
  assert.doesNotThrow(() =>
    renderToStaticMarkup(
      React.createElement(AgentPipelineTrace, { events: messy, status: "running" }),
    ),
  );
});

test("a report with no confidence or degradation still renders", () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentPipelineTrace, {
      events: [{ type: "final_report", report: "Body only." }],
      status: "done",
    }),
  );
  assert.match(html, /Wrote the answer/);
});

test("defaultOpen=false hides the panel body entirely", () => {
  const html = render({ defaultOpen: false });
  assert.doesNotMatch(html, /Planned 2 investigation angles/);
  assert.match(html, /Show agent flow/i);
});
// --- replay path -----------------------------------------------------------

const trail = [
  { node: "intent", event_type: "end", payload: '{"domain":"economics"}', started_at: "2026-10-01T18:53:32Z", ended_at: "2026-10-01T18:53:32Z" },
  { node: "route", event_type: "end", payload: '{"path":"research"}', started_at: "2026-10-01T18:53:32Z", ended_at: "2026-10-01T18:53:33Z" },
  { node: "planner", event_type: "end", payload: '{"sub_questions":4}', started_at: "2026-10-01T18:53:33Z", ended_at: "2026-10-01T18:53:34Z" },
  { node: "search", event_type: "end", payload: '{"results":75}', started_at: "2026-10-01T18:53:34Z", ended_at: "2026-10-01T18:53:50Z" },
  { node: "summarizer", event_type: "end", payload: '{"facts":71}', started_at: "2026-10-01T18:53:50Z", ended_at: "2026-10-01T18:54:10Z" },
  { node: "verifier", event_type: "end", payload: '{"verified":55,"total":66}', started_at: "2026-10-01T18:54:10Z", ended_at: "2026-10-01T18:54:20Z" },
  { node: "critic", event_type: "end", payload: '{"iteration":1,"is_sufficient":true}', started_at: "2026-10-01T18:54:20Z", ended_at: "2026-10-01T18:54:22Z" },
  { node: "synthesizer", event_type: "end", payload: '{"cited":19,"supported":19}', started_at: "2026-10-01T18:54:22Z", ended_at: "2026-10-01T18:54:40Z" },
];

const renderTrail = () =>
  renderToStaticMarkup(
    React.createElement(AgentPipelineTrace, { events: [], trail, status: "done" }),
  );

test("a restored run renders phases rebuilt from its node trail", () => {
  const html = renderTrail();
  for (const needle of [
    "Understood the question",
    "Planned 4 investigation angles",
    "Searched the web · 75 sources",
    "Verified 55 of 66 claims",
    "Evidence review · 1 round",
    "Wrote the answer",
  ]) {
    assert.ok(html.includes(needle), `missing replay phase: ${needle}`);
  }
});

test("a restored run is labelled as reconstructed, with no dead mode toggle", () => {
  const html = renderTrail();
  assert.match(html, /reconstructed/i);
  assert.doesNotMatch(html, /Full trace/, "there are no raw frames to show");
});

test("replay never invents query text the trail did not store", () => {
  const html = renderTrail();
  assert.doesNotMatch(html, /grid storage|query \d|https?:\/\//i);
});

test("a live stream takes precedence over a stale trail", () => {
  const html = renderToStaticMarkup(
    React.createElement(AgentPipelineTrace, {
      events: [{ type: "intent", query_type: "factual" }],
      trail,
      status: "running",
    }),
  );
  assert.doesNotMatch(html, /reconstructed/i);
  assert.match(html, /Full trace/);
});
