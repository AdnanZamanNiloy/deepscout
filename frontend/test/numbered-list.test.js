/* Numbered report blocks must render as a list, never as one paragraph.
 *
 * A report whose items the writer numbered ("1) **Sense** - note") could reach
 * the renderer already flattened onto one line ("1. a 2. b 3. c"), which a
 * naive parser reads as a SINGLE list item. The Markdown parser
 * (src/markdown.js) splits the run-on form back into real list items, and
 * AnswerCard.jsx renders the parsed blocks.
 *
 * This file imports the real helper (src/markdown.js is plain .js, so Node can
 * import it directly — unlike a .jsx module, which plain Node rejects). The
 * last test asserts AnswerCard actually calls the parser so the behaviour
 * cannot drift out of the render path.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { splitInlineOrderedItems, parseBlocks } from "../src/markdown.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const CARD = readFileSync(join(HERE, "..", "src", "components", "AnswerCard.jsx"), "utf8");

const NUMBER_RE = /^\s*(\d+)[.)]\s+(.*)$/;

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
  assert.equal(parts.length, 3);
  assert.match(parts[0], /^\*\*Trends in artificial intelligence technology\*\*/);
  assert.match(parts[1], /^\*\*Business and market adoption of AI\*\*/);
  assert.match(parts[2], /^\*\*AI policy and regulation\*\*/);
});

test("the parser turns a flattened numbered line into multiple list items", () => {
  const list = parseBlocks(FLATTENED).find((b) => b.type === "list");
  assert.ok(list, "a list block must be produced");
  assert.equal(list.ordered, true);
  assert.equal(list.items.length, 3, "run-on items are recovered, not merged");
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

test("AnswerCard renders parsed blocks (parser is wired into the render path)", () => {
  // Guards the dead-code class: the parser existing but never being called
  // would render exactly the bug it was written for.
  assert.match(CARD, /import \{ parseBlocks \} from "\.\.\/markdown"/);
  assert.match(CARD, /parseBlocks\(/);
});
