/* The panel's "Sources analyzed" must show the REAL source count.
 *
 * Regression: the backend mapped a character count onto `search_progress`
 * snippets, so a run displayed "24229 sources" (a ~24 KB text blob) instead of
 * the true tens-of-sources total. The final_report frame now carries the real
 * `source_count`, and the UI reads it there.
 *
 * Components are .jsx (plain Node cannot import them), so these assertions scan
 * the source, matching the approach used by the other UI-contract tests.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = readFileSync(join(HERE, "..", "src", "App.jsx"), "utf8");
const PANEL = readFileSync(join(HERE, "..", "src", "components", "IntelligencePanel.jsx"), "utf8");

test("the final_report handler adopts the real source_count", () => {
  assert.match(APP, /evt\.source_count/);
  assert.match(APP, /snippets:\s*typeof evt\.source_count === "number"/);
});

test("the sources-analyzed row is driven by the run's source count", () => {
  assert.match(PANEL, /label: "Sources analyzed",\s*value:[^}]*run\.snippets/);
});

test("the sources-analyzed row shows an honest placeholder before completion", () => {
  // It must not print a bare 0 as if it were a real count mid-run.
  assert.match(PANEL, /run\.done \? String\(run\.snippets\)/);
  assert.match(PANEL, /"—"/);
});
