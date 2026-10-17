/* The live findings wire: both ends must exist, for every frame type.
 *
 * Regression: the backend emits no `findings` NDJSON frame and the frontend
 * live handler has no `case "findings"` — while the REPLAY path
 * (replayPanelRun, from trace.claims) keeps rebuilding `run.findings`.
 * Result: the same run shows "Total claims 0 / Verified claims 0" live and
 * real numbers on restore, and nothing fails loudly (`applyEvent` has a
 * `default: break`, so a case-less frame is silently dropped).
 *
 * These assertions pin the contract on both ends: the backend route emits the
 * frame (locked down in backend/tests/test_findings_frame.py), the live case
 * assigns it, and the replay path keeps rebuilding the same field.
 *
 * App.jsx is .jsx (plain Node cannot import it), so these assertions scan the
 * source, matching the approach used by the other UI-contract tests.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = readFileSync(join(HERE, "..", "src", "App.jsx"), "utf8");
const PANEL = readFileSync(join(HERE, "..", "src", "components", "IntelligencePanel.jsx"), "utf8");
const EVENTS = readFileSync(join(HERE, "..", "src", "trace", "events.ts"), "utf8");

test("the live stream handler consumes the findings frame", () => {
  assert.match(APP, /case "findings"/, "a backend frame with no case is silently dropped");
  assert.match(APP, /findings:\s*evt\.items/, "the live case must assign the frame's items");
  assert.match(APP, /verifiedCount:.*evt\.items/, "the verified count must be recomputed on the same signal");
});

test("the replay path still rebuilds findings from persisted claims", () => {
  assert.match(APP, /replayPanelRun/);
  assert.match(APP, /claims\.map/);
  assert.match(APP, /verifiedCount:\s*findings\.filter/);
});

test("the panel's claim rows are driven by run.findings", () => {
  assert.match(PANEL, /label: "Total claims",\s*value:\s*String\(run\.findings\.length\)/);
  assert.match(PANEL, /label: "Verified claims",\s*value:\s*String\(verified\)/);
  assert.match(PANEL, /run\.findings\.filter\(\(f\) => f\.verified === true\)\.length/);
});

test("the wire item shape carries the verification verdict", () => {
  assert.match(EVENTS, /type FindingsEvent/);
  assert.match(EVENTS, /verified\?: boolean \| null/);
  assert.match(EVENTS, /verified_update\?: boolean/);
  assert.match(EVENTS, /challenged\?: boolean/, "live and replay must both carry the challenge flag");
});
