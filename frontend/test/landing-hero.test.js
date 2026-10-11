/* Landing page contract.
 *
 * The hero's right column is a picture of the console, and a picture can drift
 * from the product without anything failing — it is markup, not a live view.
 * These assertions pin the two ways that drift actually bites a visitor:
 *
 *  1. "Watch demo" advertises a question and then runs a different one. The
 *     preview window, the recent list and the demo run must all read from the
 *     same DEMO_QUERY, and App must hand it to launch() rather than ignore it.
 *  2. The preview is drawn from real UI vocabulary (nav labels, mode names).
 *     When the sidebar's labels move, the picture must move with them — so the
 *     shared names are asserted against Sidebar and MODE_META, the modules
 *     that own them.
 *
 * The preview also carries role="img": a screen reader must get one sentence
 * describing the screenshot, not a fake UI full of dead "buttons".
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { MODE_META } from "../src/lib.js";

const landing = await readFile(new URL("../src/components/Landing.jsx", import.meta.url), "utf8");
const app = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const sidebar = await readFile(new URL("../src/components/Sidebar.jsx", import.meta.url), "utf8");

/** The console preview's source block — everything drawn as the product. */
const preview = landing.slice(
  landing.indexOf("function ConsolePreview"),
  landing.indexOf("export default function Landing"),
);

test("the demo query is defined once and used for the promise and the run", () => {
  const demo = landing.match(/export const DEMO_QUERY = "([^"]+)"/);
  assert.ok(demo, "Landing should export DEMO_QUERY");
  assert.ok(demo[1].length > 10, "the demo question should be a real question");
  // The preview window, the recent list and the button all read the constant,
  // so the question shown cannot differ from the question run.
  assert.match(landing, /onDemo\?\.\(DEMO_QUERY\)/, "the button must send DEMO_QUERY");
  assert.match(preview, /lp-bubble">\{DEMO_QUERY\}/, "the preview bubble must show DEMO_QUERY");
  assert.match(landing, /\{ title: DEMO_QUERY/, "the preview's recent list must list it");
  assert.ok(!preview.includes(demo[1]), "the preview must not hardcode a second copy of the question");
});

test("Watch demo is wired to a real run, not a dead button", () => {
  assert.match(app, /onDemo=\{\(q\) => launch\(q\)\}/, "App must launch the demo query");
  // launch() is the same path the composer uses, so the demo cannot diverge
  // from a typed question: same session, same stream, same audit trail.
  assert.match(app, /const launch = useCallback\(async \(queryText/, "launch must accept a query");
});

test("the console preview is one image, not a set of dead controls", () => {
  assert.match(preview, /className="lp-window"\s+role="img"\s+aria-label=/);
  // Spans only inside the preview: a focusable control here would promise an
  // interaction the block explicitly says it does not have.
  assert.doesNotMatch(preview, /<button|<a\s|<input/, "the preview must contain no interactive elements");
});

test("the preview repeats the sidebar's real navigation labels", () => {
  // Pull the labels the sidebar actually renders, so renaming one there cannot
  // leave the landing showing a product screen that no longer exists.
  const sidebarLabels = [
    ...sidebar.matchAll(/label: "([^"]+)"/g),
    ...sidebar.matchAll(/>\s*(New research)\s*</g),
  ].map((m) => m[1]);
  assert.ok(sidebarLabels.length >= 4, `expected the sidebar's nav labels, found ${sidebarLabels}`);
  for (const label of sidebarLabels) {
    assert.ok(preview.includes(label), `preview is missing the sidebar's "${label}" row`);
  }
});

test("the preview's mode chip is a mode the product actually has", () => {
  const mode = preview.match(/lp-composer-mode">\s*([A-Za-z]+)\s*</);
  assert.ok(mode, "the preview composer must name a mode");
  const labels = Object.values(MODE_META).map((m) => m.label);
  assert.ok(
    labels.includes(mode[1]),
    `preview shows "${mode[1]}", which is not one of ${labels.join(", ")}`,
  );
});

test("the advertised modes match the orchestrator's presets", () => {
  // The list used to advertise a fourth mode the orchestrator no longer accepts
  // (its handling was folded into `deep`), and the section claimed six depths.
  // Both described modes a visitor could not select.
  const block = landing.slice(landing.indexOf("const MODES = ["), landing.indexOf("/* Guarantees"));
  const names = [...block.matchAll(/name: "([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(names, ["Quick", "Standard", "Deep"]);
  const modes = Object.keys(MODE_META);
  assert.equal(names.length, modes.length, `the orchestrator has ${modes.length} modes: ${modes.join(", ")}`);
  assert.doesNotMatch(landing, /six (research )?(modes|depths)/i, "there are three modes, not six");
});