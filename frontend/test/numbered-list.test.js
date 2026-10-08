/* Numbered report blocks must render as a list, never as one paragraph.
 *
 * A report whose items the writer numbered ("1) **Sense** - note") could reach
 * the renderer already flattened onto one line ("1. a 2. b 3. c"), which the
 * line-oriented parser read as a SINGLE list item — one <li> containing the
 * literal "2)" and "3)" text. Two layers address it: the backend sanitizer now
 * preserves the line breaks, and the renderer splits the run-on form so reports
 * persisted before that fix still display correctly.
 *
 * The helper is re-implemented here rather than imported from AnswerCard.jsx:
 * a plain-Node import of a .jsx module rejects with "Unknown file extension",
 * and a top-level `await import(...)` that rejects aborts the whole file — the
 * assertions never run and `node --test` still reports the FILE as passing.
 * That is a test that verifies nothing, so this file stays on plain .js, and
 * the last test asserts the real component actually calls the helper so the
 * copy cannot drift into verifying nothing.
 *
 * Runs on Node's built-in test runner; no DOM or new dependency required.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const CARD = readFileSync(join(HERE, "..", "src", "components", "AnswerCard.jsx"), "utf8");

const NUMBER_RE = /^\s*(\d+)[.)]\s+(.*)$/;
const INLINE_ORDERED_RE = /(?:^|\s)(\d{1,2})[.)]\s+(?=\S)/g;

function splitInlineOrderedItems(body, startNum) {
  INLINE_ORDERED_RE.lastIndex = 0;
  const marks = [];
  let m;
  while ((m = INLINE_ORDERED_RE.exec(body)) !== null) marks.push(m);
  if (!marks.length) return null;
  const nums = marks.map((mk) => Number(mk[1]));
  if (!nums.every((n, i) => n === startNum + 1 + i)) return null;
  const parts = [];
  for (let i = 0; i < marks.length; i++) {
    const from = marks[i].index + marks[i][0].length;
    const to = i + 1 < marks.length ? marks[i + 1].index : body.length;
    const part = body.slice(from, to).trim();
    if (part) parts.push(part);
  }
  if (!parts.length || !parts.every((p) => p.startsWith("**"))) return null;
  return parts;
}

// The exact run-on shape shipped by the "what is the current trend of ai" run.
const FLATTENED =
  "1) **Trends in artificial intelligence technology**, the direction of model " +
  "progress itself: agentic systems, reasoning, multimodal capability. " +
  "2) **Business and market adoption of AI**, how organisations actually put AI " +
  "to work. 3) **AI policy and regulation**, legislation, governance and safety policy.";

test("a flattened numbered block splits back into separate list items", () => {
  const num = FLATTENED.match(NUMBER_RE);
  assert.ok(num, "line must start with an ordered marker");
  assert.equal(Number(num[1]), 1);

  const parts = splitInlineOrderedItems(num[2], Number(num[1]));
  assert.ok(parts, "the run-on items must be recovered");
  assert.equal(parts.length, 2);
  assert.match(parts[0], /^\*\*Business and market adoption of AI\*\*/);
  assert.match(parts[1], /^\*\*AI policy and regulation\*\*/);
});

test("prose that merely contains a number and a period is left alone", () => {
  const unchanged = [
    ["phase 2) of the plan", 1],
    ["the U.S. and 3. See", 1],
    ["about 5. million users", 2],
    ["1.5 million records", 1],
    ["only one item here", 1],
    ["**A** x 2) **B** y but 4) skipped ahead", 1],
  ];
  for (const [body, start] of unchanged) {
    assert.equal(
      splitInlineOrderedItems(body, start),
      null,
      `must not split: ${JSON.stringify(body)}`
    );
  }
});

test("AnswerCard splits inline ordered items inside the list-item branch", () => {
  // Guards the dead-code class: the helper existing but never being called
  // would render exactly the bug it was written for.
  assert.match(CARD, /function splitInlineOrderedItems/);
  assert.match(CARD, /const INLINE_ORDERED_RE/);
  assert.match(CARD, /splitInlineOrderedItems\(itemBody, Number\(num\[1\]\)\)/);
});