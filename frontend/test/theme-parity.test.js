/* Palette parity between the console theme and the docs site.
 *
 * public/docs.html is a standalone static page (it must be, to run without a
 * build step), so it carries its own copy of the colour tokens instead of
 * importing theme.css. That duplication is exactly why the docs drifted: the
 * console moved to the neutral #191919 ramp and the docs silently kept the old
 * cool grey, so switching views looked like two different products.
 *
 * This asserts the shared tokens agree. It cannot stop a token being added to
 * one file only, but it catches the common case — a palette change applied to
 * the console and forgotten here.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const theme = await readFile(new URL("../src/theme.css", import.meta.url), "utf8");
const docs = await readFile(new URL("../public/docs.html", import.meta.url), "utf8");

/** Nth declaration of a custom property in a source file. */
function token(src, name, nth = 0) {
  const hits = [...src.matchAll(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{3,8})`, "g"))];
  return hits[nth]?.[1] ?? null;
}

// Names present in BOTH files under the same meaning.
const SHARED = [
  "bg", "line", "track",
  "t1", "t2", "t3", "t4",
  "mint", "deepscout",
  "card", "card-2",
];

test("docs and console agree on every shared colour token", () => {
  const drifted = [];
  for (const name of SHARED) {
    const a = token(theme, name);
    const b = token(docs, name);
    assert.ok(a, `--${name} missing from theme.css`);
    assert.ok(b, `--${name} missing from docs.html`);
    if (a.toLowerCase() !== b.toLowerCase()) drifted.push(`${name}: theme ${a} vs docs ${b}`);
  }
  assert.deepEqual(drifted, [], `palette drift:\n  ${drifted.join("\n  ")}`);
});

test("the docs page advertises the current ground colour to browser chrome", () => {
  // <meta name="theme-color"> paints the mobile/desktop browser chrome. It sat
  // on the old #0e1013 long after the console changed, leaving a coloured strip
  // that matched neither theme.
  const ground = token(theme, "bg");
  const meta = /<meta name="theme-color" content="(#[0-9a-fA-F]{3,8})"/.exec(docs);
  assert.ok(meta, "docs.html should declare a theme-color");
  assert.equal(meta[1].toLowerCase(), ground.toLowerCase());
});

test("no retired cool-grey value survives anywhere in the docs", () => {
  // Guards the specific palette the console abandoned.
  const retired = ["#0e1013", "#14171b", "#191d22", "#21262d", "#1e2228", "#2b3138", "#eef1f5", "#5fd0a0"];
  const found = retired.filter((hex) => docs.toLowerCase().includes(hex));
  assert.deepEqual(found, [], `retired values still present: ${found.join(", ")}`);
});
