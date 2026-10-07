/* Confirmation after saving a model in Model Controls.
 *
 * Adding a model produced NO feedback: `submit()` called `onSaved()` and left
 * the form filled in, so the only sign the save had worked was the model count
 * changing in a header and a tab badge elsewhere on the page. Next to a form
 * that still looked untouched, that reads as "nothing happened" — which is
 * exactly the report this test locks down.
 *
 * The wording lives in lib.js so it can be asserted directly; a plain-Node
 * import of a .jsx module rejects with "Unknown file extension", and a
 * top-level `await import(...)` that rejects aborts the whole file so
 * `node --test` reports the FILE as passing (see provider-tabs.test.js). The
 * wiring itself is asserted as source text.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { savedModelNotice } from "../src/lib.js";

const view = readFileSync(
  fileURLToPath(new URL("../src/components/ModelControlsView.jsx", import.meta.url)),
  "utf8",
);
const css = readFileSync(
  fileURLToPath(new URL("../src/components/model-controls.css", import.meta.url)),
  "utf8",
);

/* --- the message --------------------------------------------------------- */

test("an add is confirmed by name, and points at the next step", () => {
  const msg = savedModelNotice({ editing: false, name: "Opencode", modelName: "fledge-alpha-free" });
  assert.match(msg, /^Added /);
  assert.match(msg, /Opencode/);
  assert.match(msg, /fledge-alpha-free/, "the model label is what identifies the new card");
  assert.match(msg, /Serving mode/, "an added model is not live until it is selected");
});

test("an update never claims something was added", () => {
  const msg = savedModelNotice({ editing: true, name: "Opencode", modelName: "fledge-alpha-free" });
  assert.match(msg, /^Saved changes to /);
  assert.doesNotMatch(msg, /Added/);
  assert.doesNotMatch(msg, /Select it in Serving mode/, "nothing new needs selecting on an update");
});

test("the model label is optional", () => {
  const msg = savedModelNotice({ editing: false, name: "Groq", modelName: "" });
  assert.match(msg, /Added “Groq”\./);
  assert.doesNotMatch(msg, /·/, "no dangling separator when there is no label");
});

test("whitespace-only input does not produce an empty confirmation", () => {
  assert.equal(savedModelNotice({ editing: false, name: "  ", modelName: "  " }), "");
  assert.equal(savedModelNotice({}), "");
  assert.equal(savedModelNotice(), "");
});

/* --- the wiring ---------------------------------------------------------- */

test("a successful save sets a confirmation", () => {
  assert.match(view, /const \[saved, setSaved\] = useState\(""\)/, "confirmation state must exist");
  assert.match(view, /setSaved\(savedModelNotice\(\{ editing: false/, "the create path must confirm");
  assert.match(view, /setSaved\(savedModelNotice\(\{ editing: true/, "the update path must confirm");
});

test("the confirmation is announced, not silently painted", () => {
  // role="status" announces without stealing focus; an assertive alert would
  // interrupt, and the save did succeed.
  assert.match(view, /className="pv-notice" role="status"/);
});

test("a successful add clears the form", () => {
  // Keeping the values is half of why the old save looked like a no-op.
  assert.match(view, /setForm\(EMPTY\);\s*\n\s*setShowKey\(false\);/);
});

test("editing a field clears a stale confirmation", () => {
  // "Added X" sitting above a form that now holds different values is a claim
  // about the present that is no longer true.
  assert.match(
    view,
    /const set = \(key\) => \(e\) => \{\s*\n\s*setSaved\(""\);/,
    "typing must dismiss the previous confirmation",
  );
  assert.match(view, /const setBaseUrl = \(base_url\) => \{\s*\n\s*setSaved\(""\);/,
    "picking a base-URL preset must dismiss it too");
});

test("switching between add and edit clears the confirmation", () => {
  const start = view.indexOf("useEffect(() => {");
  const end = view.indexOf("}, [editing]);", start);
  assert.ok(start !== -1 && end !== -1, "the editing effect must exist");
  const effect = view.slice(start, end);
  assert.match(effect, /setSaved\(""\)/, "a new target must not inherit the last one's confirmation");
});

test("starting a save clears the previous confirmation and error", () => {
  assert.match(view, /setSaving\(true\);\s*\n\s*setFormError\(""\);\s*\n\s*setSaved\(""\);/);
});

test("the confirmation is styled with the existing success tokens", () => {
  assert.match(css, /\.pv-notice\{/);
  // Green is reserved for genuine runtime status in this view (see
  // theme-contrast.test.js), and a confirmed save is exactly that.
  assert.match(css, /\.pv-notice\{[^}]*var\(--probe-ok\)/);
  assert.match(css, /\.pv-notice\{[^}]*var\(--green-wash\)/);
});

test("the notice sits directly above the submit row", () => {
  const notice = view.indexOf('className="pv-notice"');
  const actions = view.indexOf('className="pv-form-actions"');
  assert.ok(notice !== -1 && actions !== -1);
  assert.ok(notice < actions, "the confirmation must read as the result of the button");
});
