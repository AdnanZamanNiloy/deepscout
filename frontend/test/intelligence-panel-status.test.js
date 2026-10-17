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
    stages: {},
    planReview: null,
    ...overrides,
  };
}

const statusOf = (agents, name) => agents.find((a) => a.name === name).status;
const working = (agents) => agents.filter((a) => a.status === "active").length;

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

/* ---------------------------------------------------------------------------
 * Live stage lifecycle.
 *
 * Regression (the reported bug): a run that had been streaming for a minute
 * showed all eight agents "Queued" and the header read "0 agents working"
 * while its trace was visibly working. Status was inferred from FINISHED
 * artifacts only, so before the first artifact landed every stage was queued
 * and no stage could ever be "working". The backend now announces each node's
 * start and finish, and that is what the rail reads.
 * ------------------------------------------------------------------------- */

test("a stage that has started shows as working before any artifact exists", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run({
    stages: { pathfinder: { status: "running", startedAt: 1000, endedAt: null } },
  }));
  assert.equal(statusOf(agents, "Pathfinder"), "active");
  // Nothing else may claim to be working, and nothing may claim to be done.
  assert.equal(working(agents), 1);
  for (const a of agents.slice(1)) assert.equal(a.status, "waiting", `${a.name} should be queued`);
});

test("the rail advances as stages finish and the next one starts", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run({
    stages: {
      pathfinder: { status: "done", startedAt: 1, endedAt: 900 },
      architect: { status: "running", startedAt: 900, endedAt: null },
    },
  }));
  assert.equal(statusOf(agents, "Pathfinder"), "done");
  assert.equal(statusOf(agents, "Research Architect"), "active");
  assert.equal(working(agents), 1);
});

test("Pathfinder and Scout Swarm are separate stages, not one retrieval stage", async () => {
  const { deriveAgents } = await loadPanel();
  // The backend maps both nodes onto the `retrieval` STAGE; the wire keys on
  // the node, so the two must never collapse into one row here.
  const agents = deriveAgents(run({
    stages: {
      pathfinder: { status: "done", startedAt: 1, endedAt: 900 },
      architect: { status: "done", startedAt: 900, endedAt: 1500 },
      review_gate: { status: "done", startedAt: 1500, endedAt: 1600 },
      scout_swarm: { status: "running", startedAt: 1600, endedAt: null },
    },
  }));
  assert.equal(statusOf(agents, "Pathfinder"), "done");
  assert.equal(statusOf(agents, "Scout Swarm"), "active");
  assert.equal(working(agents), 1);
});

test("a paused plan review shows the Review Gate as the working stage", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run({
    plan: ["A", "B"],
    stages: {
      pathfinder: { status: "done", startedAt: 1, endedAt: 900 },
      architect: { status: "done", startedAt: 900, endedAt: 1500 },
      review_gate: { status: "running", startedAt: 1500, endedAt: null },
    },
    planReview: { runId: "r1", alternatives: [["A"]] },
  }));
  assert.equal(statusOf(agents, "Review Gate"), "active");
  assert.equal(agents.find((a) => a.name === "Review Gate").desc, "awaiting plan review");
});

test("the lifecycle wins over artifact inference: an unstarted stage stays queued", async () => {
  const { deriveAgents } = await loadPanel();
  // Findings exist, but no stage frame claims the writer or assembler ran.
  // Inferring "done" from a later artifact is exactly what made the rail
  // disagree with the trace it is supposed to summarise.
  const agents = deriveAgents(run({
    findings: [{ claim: "c", verified: false }],
    stages: { pathfinder: { status: "done", startedAt: 1, endedAt: 900 } },
  }));
  assert.equal(statusOf(agents, "Scout Swarm"), "waiting");
  assert.equal(statusOf(agents, "Narrative Writer"), "waiting");
  assert.equal(statusOf(agents, "Report Assembler"), "waiting");
});

test("a run whose lifecycle all completed shows every stage done", async () => {
  const { deriveAgents } = await loadPanel();
  const stages = Object.fromEntries(
    ["pathfinder", "architect", "review_gate", "scout_swarm", "narrative_writer",
     "evidence_auditor", "diagram_designer", "report_assembler"]
      .map((n) => [n, { status: "done", startedAt: 1, endedAt: 2 }]),
  );
  const agents = deriveAgents(run({ stages, done: true, snippets: 12, plan: ["A"] }));
  for (const a of agents) assert.equal(a.status, "done", `${a.name} should be done`);
  assert.equal(working(agents), 0);
});

test("a stream that dies mid-stage does not leave that stage working forever", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run({
    error: "Research workflow failed: boom",
    stages: { pathfinder: { status: "running", startedAt: 1, endedAt: null } },
  }));
  assert.notEqual(statusOf(agents, "Pathfinder"), "active");
  assert.equal(working(agents), 0);
});

test("a restored run with no lifecycle frames still falls back to artifacts", async () => {
  const { deriveAgents } = await loadPanel();
  // Sessions persisted before stage frames existed must still show their work
  // rather than an all-queued rail.
  const agents = deriveAgents(run({
    stages: {},
    snippets: 42,
    plan: ["A", "B"],
    findings: [{ claim: "c", verified: false }],
  }));
  assert.equal(statusOf(agents, "Pathfinder"), "done");
  assert.notEqual(statusOf(agents, "Report Assembler"), "done");
});

test("fast mode has no Diagram Designer row at all", async () => {
  const { deriveAgents } = await loadPanel();
  // The backend skips DiagramDesigner by design in quick mode, so the rail
  // must not show a stage that never runs. A permanently "Queued" box would
  // misrepresent the pipeline for that mode.
  const agents = deriveAgents(run({ mode: "quick", plan: ["A"], snippets: 10 }));
  const names = agents.map((a) => a.name);
  assert.equal(names.includes("Diagram Designer"), false, "quick mode runs no diagram stage");
  assert.equal(agents.length, 7, "seven stages remain");
  // The rest of the ladder is unchanged.
  assert.equal(names[0], "Pathfinder");
  assert.equal(names[names.length - 1], "Report Assembler");
});

test("standard mode still shows the Diagram Designer row", async () => {
  const { deriveAgents } = await loadPanel();
  const agents = deriveAgents(run({ mode: "standard", plan: ["A"], snippets: 10 }));
  assert.equal(agents.map((a) => a.name).includes("Diagram Designer"), true);
  assert.equal(agents.length, 8);
});

test("a finished fast run marks every remaining stage done", async () => {
  const { deriveAgents } = await loadPanel();
  // Without the diagram stage the old "reached one past the end" arithmetic
  // would leave the last row queued on a completed run.
  const agents = deriveAgents(run({
    mode: "quick", plan: ["A", "B"], snippets: 40,
    findings: [{ claim: "c", verified: true }], verifiedCount: 1, done: true,
  }));
  assert.equal(agents.every((a) => a.status === "done"), true);
});

test("fast mode fallback ladder still advances while live", async () => {
  const { deriveAgents } = await loadPanel();
  // Archived sessions carry no stage frames; the artifact ladder must still
  // show progress, and hiding the diagram must not shift which row is active.
  const agents = deriveAgents(run({ mode: "quick", plan: ["A", "B"] }));
  assert.equal(statusOf(agents, "Pathfinder"), "done");
  assert.equal(statusOf(agents, "Review Gate"), "active");
  assert.equal(statusOf(agents, "Report Assembler"), "waiting");
});
