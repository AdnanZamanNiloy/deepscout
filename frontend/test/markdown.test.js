/* Report Markdown parser tests.
 *
 * The renderer must turn the writer's Markdown into clean structure: GFM tables
 * (including ragged / separator-less ones), heading hierarchy, bullet and
 * numbered lists, code fences, and citations — and it must strip literal
 * `<br/>` artifacts rather than printing them. All of this lives in the pure
 * parser (src/markdown.js) so it runs on Node's built-in runner with no DOM.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseBlocks, stripHtmlArtifacts, splitTableRow } from "../src/markdown.js";

test("<br/> artifacts are removed, not printed literally", () => {
  assert.equal(stripHtmlArtifacts("line one<br/>line two"), "line one line two");
  assert.equal(stripHtmlArtifacts("a<br>b<p>c</p>"), "a b c");
  assert.ok(!parseBlocks("Intro<br/>continued").some((b) => /<br/.test(b.text || "")));
});

test("a standard GFM pipe table parses into header + rows + alignments", () => {
  const md = [
    "| Indicator | Status | Implication |",
    "|-----------|:------:|------------:|",
    "| A | ok | big |",
    "| B | warn | small |",
  ].join("\n");
  const table = parseBlocks(md).find((b) => b.type === "table");
  assert.ok(table, "a table block must be produced");
  assert.deepEqual(table.header, ["Indicator", "Status", "Implication"]);
  assert.deepEqual(table.aligns, ["left", "center", "right"]);
  assert.equal(table.rows.length, 2);
  assert.deepEqual(table.rows[0], ["A", "ok", "big"]);
});

test("a ragged table is padded to the header width without dropping content", () => {
  const md = [
    "| A | B | C |",
    "| --- | --- | --- |",
    "| only-one |",
    "| x | y | z | extra |",
  ].join("\n");
  const table = parseBlocks(md).find((b) => b.type === "table");
  assert.equal(table.rows[0].length, 3, "short row padded");
  assert.equal(table.rows[0][1], "");
  // The overflow cell from the too-long row is preserved, not discarded.
  assert.ok(table.rows[1][2].includes("extra"));
});

test("a separator-less multi-row pipe block still becomes a table", () => {
  const md = ["| H1 | H2 |", "| v1 | v2 |", "| v3 | v4 |"].join("\n");
  const table = parseBlocks(md).find((b) => b.type === "table");
  assert.ok(table, ">=2 body rows without a separator is still a table");
  assert.equal(table.rows.length, 2);
});

test("a single pipe line in prose is NOT a table", () => {
  const blocks = parseBlocks("The ratio is a | b for most cases.");
  assert.ok(!blocks.some((b) => b.type === "table"));
});

test("heading hierarchy is preserved by level", () => {
  const blocks = parseBlocks("# One\n\n## Two\n\n### Three\n\n#### Four");
  const headings = blocks.filter((b) => b.type === "heading").map((b) => [b.level, b.text]);
  assert.deepEqual(headings, [[1, "One"], [2, "Two"], [3, "Three"], [4, "Four"]]);
});

test("bullet and numbered lists become list blocks with all items", () => {
  const md = "- first\n- second\n- third\n\n1. one\n2. two\n3. three";
  const lists = parseBlocks(md).filter((b) => b.type === "list");
  assert.equal(lists.length, 2);
  assert.equal(lists[0].ordered, false);
  assert.deepEqual(lists[0].items, ["first", "second", "third"]);
  assert.equal(lists[1].ordered, true);
  assert.deepEqual(lists[1].items, ["one", "two", "three"]);
});

test("wrapped continuation lines extend the current list item", () => {
  const md = "- a first point that\n  wraps onto the next line\n- second";
  const list = parseBlocks(md).find((b) => b.type === "list");
  assert.equal(list.items.length, 2);
  assert.ok(list.items[0].includes("wraps onto the next line"));
});

test("fenced code blocks are captured verbatim and not parsed as markdown", () => {
  const md = "text before\n\n```python\n# | not a table |\nprint('hi')\n```\n\ntext after";
  const blocks = parseBlocks(md);
  const code = blocks.find((b) => b.type === "code");
  assert.ok(code);
  assert.equal(code.lang, "python");
  assert.ok(code.text.includes("# | not a table |"));
  assert.ok(!blocks.some((b) => b.type === "table"));
});

test("blockquotes and horizontal rules are recognized", () => {
  const blocks = parseBlocks("> a quote line\n\n---\n\nafter");
  assert.ok(blocks.some((b) => b.type === "quote" && b.text === "a quote line"));
  assert.ok(blocks.some((b) => b.type === "hr"));
});

test("no content is dropped: unclassified text becomes a paragraph", () => {
  const md = "Just an ordinary sentence with no markers at all.";
  const blocks = parseBlocks(md);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "paragraph");
  assert.equal(blocks[0].text, md);
});

test("escaped pipes inside a cell are preserved as literal pipes", () => {
  const cells = splitTableRow("| a \\| b | c |");
  assert.deepEqual(cells, ["a | b", "c"]);
});