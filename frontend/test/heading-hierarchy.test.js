/* Report heading hierarchy and typography.
 *
 * The renderer must map each Markdown heading level to a semantically correct
 * element (H1 -> <h1>, H2 -> <h2>, ...) carrying a level class, so the document
 * outline matches the writer's `#`/`##`/`###` structure. The CSS must give each
 * level a distinct, consistent size/weight/margin (Claude/ChatGPT-style) rather
 * than reusing one style for several levels.
 *
 * Components are .jsx and cannot be imported by plain Node, so the render path
 * is asserted against source text (the same approach the numbered-list test
 * uses); the CSS is asserted against its declarations directly.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const CARD = readFileSync(join(HERE, "..", "src", "components", "AnswerCard.jsx"), "utf8");
const CSS = readFileSync(join(HERE, "..", "src", "components.css"), "utf8");

test("the renderer emits a semantic heading element per level", () => {
  // It builds `h${level}` from the parsed level and clamps to 1..6.
  assert.match(CARD, /const Tag = `h\$\{level\}`/);
  assert.match(CARD, /Math\.min\(Math\.max\(block\.level, 1\), 6\)/);
  assert.match(CARD, /answer-heading md-h\$\{level\}/);
});

test("every heading level has its own CSS class with a size", () => {
  for (const level of [1, 2, 3, 4, 5, 6]) {
    const re = new RegExp(`\\.md-h${level}\\s*\\{[^}]*font-size:\\s*[0-9.]+px`, "s");
    assert.match(CSS, re, `.md-h${level} must declare its own font-size`);
  }
});

test("heading sizes descend from H1 to H6 (no flat/inconsistent ramp)", () => {
  const size = (level) => {
    const m = CSS.match(new RegExp(`\\.md-h${level}\\s*\\{[^}]*font-size:\\s*([0-9.]+)px`, "s"));
    return m ? Number(m[1]) : NaN;
  };
  const sizes = [1, 2, 3, 4, 5, 6].map(size);
  for (const s of sizes) assert.ok(!Number.isNaN(s), "each level must set a font size");
  for (let i = 1; i < sizes.length; i++) {
    assert.ok(sizes[i] < sizes[i - 1], `H${i + 1} (${sizes[i]}) must be smaller than H${i} (${sizes[i - 1]})`);
  }
});

test("only the top levels carry a section rule, not every heading", () => {
  const h1 = CSS.match(/\.md-h1\s*\{[^}]*\}/s)[0];
  const h2 = CSS.match(/\.md-h2\s*\{[^}]*\}/s)[0];
  const h3 = CSS.match(/\.md-h3\s*\{[^}]*\}/s)[0];
  assert.match(h1, /border-bottom/);
  assert.match(h2, /border-bottom/);
  assert.doesNotMatch(h3, /border-bottom/);
});

test("headings have top margins that create section rhythm", () => {
  const h2 = CSS.match(/\.md-h2\s*\{[^}]*\}/s)[0];
  assert.match(h2, /margin:\s*[0-9.]+px\s+0\s+[0-9.]+px/);
});

test("narrow screens scale the heading ramp down", () => {
  const mediaBlock = CSS.slice(CSS.indexOf("@media (max-width: 620px)"));
  assert.match(mediaBlock, /\.md-h1\s*\{\s*font-size/);
  assert.match(mediaBlock, /\.md-h2\s*\{\s*font-size/);
});
