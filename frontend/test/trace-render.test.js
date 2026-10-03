/* Render tests for AgentPipelineTrace.
 *
 * These exist because a scope bug shipped to the browser as a blank screen:
 * `traceStatus` read `run`, which is only destructured inside a later branch,
 * so React threw during render and the error boundary replaced the whole
 * message list. `npm run build` passed the entire time — undefined variables
 * are a RUNTIME fault, and nothing in the suite ever rendered the component.
 *
 * The reducer tests cover the data model. This file covers the thing that
 * actually broke: does the component mount, and does it emit the narrative
 * structure the reference design calls for.
 *
 * Node's runner strips types but not JSX, so the component is bundled with
 * esbuild first, then server-rendered with react-dom/server.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { build } from "esbuild";

const ENTRY = new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url).pathname;

/** Bundle the TSX to a temp ESM module and import it. Cached across tests. */
let modPromise = null;
function loadComponent() {
  if (modPromise) return modPromise;
  modPromise = (async () => {
    const out = await build({
      entryPoints: [ENTRY],
      bundle: true,
      write: false,
      format: "esm",
      jsx: "automatic",
      target: "node20",
      external: ["react", "react-dom", "react/jsx-runtime", "framer-motion"],
      logLevel: "silent",
    });
    // Written INSIDE the project so Node resolves the externalized react /
    // framer-motion by walking up to ./node_modules. A temp dir under the OS
    // tmp path cannot resolve them, and bundling a second React copy would
    // break hooks against react-dom/server.
    const dir = join(dirname(ENTRY), "..", "..", "node_modules", ".apt-test");
    await mkdir(dir, { recursive: true });
    const file = join(dir, "component.mjs");
    await writeFile(file, out.outputFiles[0].text);
    return import(file);
  })();
  return modPromise;
}

/** A realistic slice of the live NDJSON stream. */
function wireEvents() {
  return [
    { type: "progress", message: "Research run started.", __ts: 1 },
    {
      type: "intent",
      query_type: "analytical",
      domain: "energy",
      explanation_level: "deep",
      __ts: 2,
    },
    {
      type: "route",
      path: "deep_research",
      reason: "needs multiple sources",
      __ts: 3,
    },
    {
      type: "plan",
      items: ["battery storage additions 2026", "grid scale deployment"],
      __ts: 4,
    },
    {
      type: "search_query",
      query: "global battery storage capacity additions 2026",
      results: [
        { title: "Global Energy Review", url: "https://www.iea.org/reports/ger2026", source: "tavily" },
        { title: "Grid storage", url: "https://example.org/storage", source: "tavily" },
      ],
      __ts: 5,
    },
    {
      type: "findings",
      items: [
        { claim: "Additions reached 90 GW.", source: "IEA", verified: true },
        { claim: "Grid scale led growth.", source: "IEA", verified: false },
      ],
      __ts: 6,
    },
    { type: "critic", iteration: 1, reason: "evidence is thin", __ts: 7 },
    { type: "final_report", report: "# Answer\n\nStorage grew.", __ts: 8 },
  ];
}

async function render(props) {
  const { default: Trace } = await loadComponent();
  return renderToStaticMarkup(createElement(Trace, props));
}

test("mounts without throwing — regression guard for the blank-screen crash", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /<section class="apt\s*"/);
});

test("renders the narrative structure: rail, rows, prose bullets", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-flow"/, "expected the single-rail flow list");
  assert.match(html, /class="apt-row"/);
  // Prose reasoning renders as paragraphs, not a status table.
  assert.match(html, /apt-prose/);
  // The reference design carries no uppercase kind labels or timestamps.
  assert.doesNotMatch(html, />EVIDENCE</);
  assert.doesNotMatch(html, /class="apt-time"/);
});

test("tool rows carry a monochrome icon, not a coloured status dot", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-gutter"/);
  assert.match(html, /<svg/);
  assert.doesNotMatch(html, /class="apt-dot"/);
});

test("row icons carry an intrinsic size so they can never fill the card", async () => {
  // Regression: the SVGs shipped with a viewBox but no className and no
  // width/height, so the CSS size rule never applied and a viewBox-only SVG
  // stretched to its container — a full-page magnifying glass over the trace.
  // Both the class and the attributes are asserted, because either alone
  // leaves a path back to that bug (CSS not loaded, or rule renamed).
  const html = await render({ events: wireEvents(), status: "done" });
  const svgs = html.match(/<svg[^>]*>/g) ?? [];
  assert.ok(svgs.length > 0, "expected inline icons");
  for (const tag of svgs) {
    assert.match(tag, /class="apt-icon"/, `icon missing its class: ${tag}`);
    assert.match(tag, /width="15"/, `icon has no intrinsic width: ${tag}`);
    assert.match(tag, /height="15"/, `icon has no intrinsic height: ${tag}`);
    assert.doesNotMatch(tag, /width="100%"/);
  }
  // The gutter must not contribute layout of its own.
  assert.doesNotMatch(html, /class="apt-gutter"[^>]*>\s*<svg(?![^>]*class="apt-icon")/);
});

test("search results render as monospace path chips linking to the source", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-code"/);
  assert.match(html, /href="https:\/\/www\.iea\.org\/reports\/ger2026"/);
  // Accent bar is the signature detail of the reference chip.
  assert.match(html, /\.apt-code::before|apt-code/);
});

test("header reads 'Less steps' and reports the step count", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /Less steps/);
  assert.match(html, /\d+ steps?/);
});

test("shows a live pulse only while the run is streaming", async () => {
  const running = await render({ events: wireEvents(), status: "running" });
  assert.match(running, /class="apt-live"/);
  const done = await render({ events: wireEvents(), status: "done" });
  assert.doesNotMatch(done, /class="apt-live"/);
});

test("stays collapsed when defaultOpen is false", async () => {
  const html = await render({ events: wireEvents(), status: "done", defaultOpen: false });
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /class="apt-flow"/);
});

test("an empty stream renders an honest empty state, not invented steps", async () => {
  const html = await render({ events: [], status: "done" });
  assert.match(html, /No pipeline steps were recorded/);
  assert.doesNotMatch(html, /class="apt-row"/);
});

test("unknown event types are ignored rather than rendered as noise", async () => {
  const html = await render({
    events: [{ type: "totally_new_agent", payload: "x" }, ...wireEvents()],
    status: "done",
  });
  assert.doesNotMatch(html, /totally_new_agent/);
  assert.match(html, /class="apt-row"/);
});

test("a malformed frame does not crash the trace", async () => {
  const html = await render({
    events: [null, undefined, 42, { type: "search_query", results: null }, ...wireEvents()],
    status: "done",
  });
  assert.match(html, /<section class="apt\s*"/);
});