/* The UI's agent roster must match the shipped research pipeline.
 *
 * Regression: the intelligence sidebar and Agents view advertised a stale
 * seven-agent roster from the removed engine era (Orchestrator, Search,
 * Summarizer, Verifier, Critic, Synthesizer) that no longer exists. Every
 * surface that names the pipeline must agree on the real eight stages from
 * `app/research/agents/orchestrator.py`, under their display names:
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

test("no surface claims a 'seven-agent' pipeline", () => {
  assert.doesNotMatch(PANEL, /seven-agent/i);
  assert.doesNotMatch(LANDING, /seven-agent/i);
});
