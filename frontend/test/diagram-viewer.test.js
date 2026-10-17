/* Fullscreen diagram viewer: the contract the report relies on.
 *
 * Diagrams are routinely wider than the answer column, so the inline SVG is
 * scaled down until labels stop being readable. The viewer opens the SAME
 * rendered SVG over an overlay with zoom and pan. These assertions pin the
 * parts that are easy to regress silently:
 *
 *  - the viewer renders through a portal (an in-flow overlay would be clipped
 *    by the answer card's `overflow-x: auto`),
 *  - it reuses the already-sanitized SVG rather than re-parsing the source,
 *  - zoom is clamped so the reader can never lose the diagram off-screen,
 *  - the page scroll is locked only while the viewer is open.
 *
 * The component is .jsx (plain Node cannot import it), so the assertions scan
 * the source, matching the approach used by the other UI-contract tests.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, "..", "src", "components", "MermaidDiagram.jsx"), "utf8");

test("the viewer is portalled so the answer card cannot clip it", () => {
  assert.match(SRC, /import \{[^}]*createPortal[^}]*\} from "react-dom"/);
  assert.match(SRC, /createPortal\(/);
  assert.match(SRC, /document\.body/);
});

test("the viewer reuses the sanitized SVG instead of re-rendering", () => {
  // One mermaid.render call for the figure; the viewer receives that string.
  const renders = (SRC.match(/mermaid\s*\n?\s*\.render\(/g) || []).length;
  assert.equal(renders, 1, "the diagram must be parsed once, not again in the viewer");
  assert.match(SRC, /function DiagramViewer\(\{ svg, onClose \}\)/);
  assert.match(SRC, /dangerouslySetInnerHTML=\{\{ __html: svg \}\}/);
});

test("zoom is clamped so the diagram cannot be lost", () => {
  assert.match(SRC, /const MIN_SCALE = [\d.]+/);
  assert.match(SRC, /const MAX_SCALE = [\d.]+/);
  assert.match(SRC, /Math\.min\(MAX_SCALE, Math\.max\(MIN_SCALE,/);
});

test("wheel zoom is non-passive so the page behind does not scroll", () => {
  assert.match(SRC, /addEventListener\("wheel", onWheel, \{ passive: false \}\)/);
  assert.match(SRC, /removeEventListener\("wheel", onWheel\)/, "the listener must be torn down");
});

test("the page scroll is locked only while the viewer is open", () => {
  assert.match(SRC, /document\.body\.style\.overflow = "hidden"/);
  assert.match(SRC, /document\.body\.style\.overflow = previous/, "the lock must be restored");
});

test("Escape closes the viewer", () => {
  assert.match(SRC, /ev\.key === "Escape"/);
  assert.match(SRC, /onClose\(\)/);
});

test("the inline figure offers a fullscreen affordance", () => {
  assert.match(SRC, /answer-figure-expand/);
  assert.match(SRC, /aria-label="Open diagram fullscreen"/);
});
