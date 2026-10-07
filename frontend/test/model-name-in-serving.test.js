/* Model name in the Model Controls serving view.
 *
 * A provider has three separate strings: `name` (human label for the ENDPOINT,
 * e.g. "codebuddy"), `model` (the id sent to the provider API, e.g.
 * "cbai/deepseek-v4.1-flash") and `model_name` (the display label, e.g.
 * "deepseek-v4.1-flash"). The provider CARD shows all three, but the serving
 * picker showed only `name` — so a row read "codebuddy" and the reader could
 * not tell which MODEL was serving, which is the thing being selected. Same for
 * the header's "serving: <name>".
 *
 * These tests pin the label resolution (importable from lib.js) and the JSX
 * call sites as source text: a plain-Node import of a .jsx module rejects with
 * "Unknown file extension", and a top-level `await import(...)` that rejects
 * aborts the whole file — the assertions never run and `node --test` still
 * reports the FILE as passing. Same approach as provider-tabs.test.js.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { modelLabel } from "../src/lib.js";

const view = readFileSync(
  fileURLToPath(new URL("../src/components/ModelControlsView.jsx", import.meta.url)),
  "utf8",
);

test("modelLabel prefers the human label over the model id", () => {
  assert.equal(
    modelLabel({ name: "codebuddy", model: "cbai/deepseek-v4.1-flash", model_name: "deepseek-v4.1-flash" }),
    "deepseek-v4.1-flash",
  );
});

test("modelLabel falls back to the model id when no label was set", () => {
  // Providers saved before the label field existed must still render.
  assert.equal(modelLabel({ name: "codebuddy", model: "cbai/deepseek-v4.1-flash" }), "cbai/deepseek-v4.1-flash");
});

test("modelLabel tolerates missing and empty fields", () => {
  assert.equal(modelLabel(null), "");
  assert.equal(modelLabel(undefined), "");
  assert.equal(modelLabel({}), "");
  assert.equal(modelLabel({ name: "x", model: "", model_name: "" }), "");
  assert.equal(modelLabel({ name: "x", model: "  ", model_name: "" }), "");
});

test("modelLabel trims stray whitespace from a stored label", () => {
  assert.equal(modelLabel({ model: "gpt-4o-mini", model_name: "  GPT-4o mini  " }), "GPT-4o mini");
});

test("the serving picker renders a model line under the provider name", () => {
  // The regression: `{p.name}` alone in the option row.
  assert.match(
    view,
    /pv-serving-option-model/,
    "serving option rows must render the model, not just the provider name",
  );
  assert.match(view, /\{label\}/, "the rendered model line should use the resolved label");
});

test("the header summary names the serving model", () => {
  assert.match(view, /servingModel/, "header should resolve a model for the active provider");
  assert.match(view, /pv-serving-inline-model/, "header should render it next to the provider name");
});

test("the active-model sentence names the model too", () => {
  assert.match(
    view,
    /every agent runs on[\s\S]{0,200}modelLabel\(active\)/,
    "\"every agent runs on X\" should include X's model",
  );
});

test("the exact model id stays reachable as a tooltip", () => {
  // The compact row shows the label; the full id (often namespaced) is one
  // hover away rather than pushed into the row.
  assert.match(view, /title=\{p\.model \? `Model ID: \$\{p\.model\}`/, "row should expose the model id on hover");
});
