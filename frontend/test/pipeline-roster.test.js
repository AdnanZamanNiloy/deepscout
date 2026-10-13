/* The UI's agent roster must match the shipped research pipeline.
 *
 * Regression: the intelligence sidebar and Agents view advertised a stale
 * seven-agent roster from the removed engine era (Orchestrator, Search,
 * Summarizer, Verifier, Critic, Synthesizer) that no longer exists. Every
 * surface that names the pipeline must agree on the real eight graph nodes
 * (`app/agents/team.py`), under their display names:
 * Pathfinder, Research Architect, Review Gate, Scout Swarm, Narrative Writer,
 * Evidence Auditor, Diagram Designer, Report Assembler.
 *
 * Components are .jsx (plain Node cannot import them), so these assertions scan
 * the source, matching the other UI-contract tests.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(join(HERE, "..", "src", "components", p), "utf8");
const PANEL = read("IntelligencePanel.jsx");
const AGENTS = read("AgentsView.jsx");
const LANDING = read("Landing.jsx");
/* The published docs are a roster surface too — they quoted a nine-node diagram
 * and stale module paths while the app showed eight. */
const DOCS = readFileSync(join(HERE, "..", "public", "docs.content.js"), "utf8");

const REAL_ROSTER = [
  "Pathfinder",
  "Research Architect",
  "Review Gate",
  "Scout Swarm",
  "Narrative Writer",
  "Evidence Auditor",
  "Diagram Designer",
  "Report Assembler",
];

// Agent names that were removed with the engine era and must not appear as
// pipeline stages. ("Critic" and "Verifier" are deliberately excluded: they
// survive as accurate metric labels — the "Critic survival" confidence signal
// and "Critic passes" health row read real backend fields — so they are not
// stale-roster evidence.)
const STALE = ["Orchestrator", "Summarizer", "Synthesizer"];

test("the real eight pipeline stages appear in every roster surface", () => {
  for (const name of REAL_ROSTER) {
    assert.ok(PANEL.includes(name), `IntelligencePanel is missing stage: ${name}`);
    assert.ok(AGENTS.includes(name), `AgentsView is missing stage: ${name}`);
    assert.ok(LANDING.includes(name), `Landing is missing stage: ${name}`);
  }
});

test("no surface advertises the removed engine-era agents", () => {
  for (const name of STALE) {
    assert.ok(!PANEL.includes(name), `IntelligencePanel still names the removed agent: ${name}`);
    assert.ok(!AGENTS.includes(name), `AgentsView still names the removed agent: ${name}`);
    assert.ok(!LANDING.includes(name), `Landing still names the removed agent: ${name}`);
  }
});

test("no surface claims a seven-agent pipeline", () => {
  // Both spellings: the footer said "7-agent pipeline" while the page's own
  // roster listed eight stages, and the old check only looked for the word.
  for (const [label, src] of [["IntelligencePanel", PANEL], ["Landing", LANDING], ["docs", DOCS]]) {
    assert.doesNotMatch(src, /seven[- ]agent/i, `${label} claims a seven-agent pipeline`);
    assert.doesNotMatch(src, /\b7[- ]agent/i, `${label} claims a 7-agent pipeline`);
  }
});

test("the docs roster matches the pipeline, and its stage list is eight", () => {
  for (const name of REAL_ROSTER) {
    assert.ok(DOCS.includes(name), `docs are missing stage: ${name}`);
  }
  // The architecture diagram must not reintroduce non-node stages.
  for (const phantom of ["Search + context", "Review + revise", "Fetch + filter"]) {
    assert.ok(!DOCS.includes(phantom), `docs still show a non-node stage: ${phantom}`);
  }
});

test("the docs do not cite modules that were removed", () => {
  // Every path the docs name must exist, or the doc is describing a codebase
  // that is no longer here.
  for (const dead of [
    "report_agents.py", "agents/editor.py", "agents/research_unit.py",
    "agents/search.py", "app/graph/",
  ]) {
    assert.ok(!DOCS.includes(dead), `docs cite the removed module: ${dead}`);
  }
});
