/* deriveAgents: the sidebar pipeline status must match a finished run.
 *
 * Regression: the ladder was built for the old 7-stage roster, where a finished
 * run set `reached = 8` and the last stage was marked done by `reached > 7`.
 * When the roster grew to 8 real stages, a completed run still set `reached = 8`
 * — so the LAST stage (Report Assembler) satisfied neither `reached > 8` (done)
 * nor `reached === 8 && live` (active, false once done), and rendered "Queued"
 * next to its own "report delivered" descriptor. A finished run must show every
 * stage done.
 *
 * The module is .jsx (plain Node cannot import it), so it is bundled with
 * esbuild first, then imported directly — real behaviour, not a source scan.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { build } from "esbuild";

const ENTRY = new URL("../src/components/IntelligencePanel.jsx", import.meta.url).pathname;

let modPromise = null;
function loadPanel() {
  if (modPromise) return modPromise;
  modPromise = (async () => {
    const out = await build({
      entryPoints: [ENTRY],
      bundle: true,
      write: false,
      format: "esm",
      jsx: "automatic",
      target: "node20",
      external: ["react", "react-dom", "react/jsx-runtime"],
      logLevel: "silent",
    });
    const dir = join(dirname(ENTRY), "..", "..", "node_modules", ".intel-test");
    await mkdir(dir, { recursive: true });
    const file = join(dir, "panel.mjs");
    await writeFile(file, out.outputFiles[0].text);
    return import(file);
  })();
  return modPromise;
}

function run(overrides = {}) {
  return {
    plan: [],
    snippets: 0,
    findings: [],
    verifiedCount: 0,
    critiques: [],
    done: false,
    error: "",
    modeLabel: "Standard",
    ...overrides,
  };
}

test("a fully delivered run marks every stage done (no stage left Queued)", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run({
    plan: ["A", "B", "C"],
    snippets: 88,
    findings: Array.from({ length: 120 }, (_, i) => ({ claim: `c${i}`, verified: true })),
    verifiedCount: 120,
    critiques: [{ iteration: 1 }],
    done: true,
  }));
  assert.equal(agents.length, 8);
  for (const a of agents) {
    assert.equal(a.status, "done", `${a.name} should be done, got ${a.status}`);
  }
  const assembler = agents[agents.length - 1];
  assert.equal(assembler.name, "Report Assembler");
  assert.equal(assembler.status, "done");
  assert.equal(assembler.desc, "report delivered");
});

test("a live run never marks the last stage done", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run({
    plan: ["A"],
    snippets: 10,
    findings: [{ claim: "c", verified: false }],
    done: false,
  }));
  assert.notEqual(agents[agents.length - 1].status, "done");
});

test("an empty run leaves every stage queued", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run());
  for (const a of agents) assert.equal(a.status, "waiting");
});
