/* Verify the diagram viewer's zoom anchoring math.
 *
 * This replicates the exact formula in zoomAt() and asserts the property that
 * motivates pointer-anchored zoom: the point under the cursor stays fixed. It
 * also locks the purity requirement — the transform must be a pure function of
 * the previous state, because React StrictMode invokes state updaters twice
 * and a non-pure updater (one that calls another setter) doubles its effect.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, "..", "src", "components", "MermaidDiagram.jsx"), "utf8");

const MIN_SCALE = 0.2;
const MAX_SCALE = 8;
const clampScale = (s) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));

/** Mirror of zoomAt(): same arithmetic, no React. */
function zoom(prev, nextScale, originX = 0, originY = 0) {
  const next = clampScale(nextScale);
  if (next === prev.scale) return prev;
  const ratio = next / prev.scale;
  return {
    scale: next,
    x: originX - (originX - prev.x) * ratio,
    y: originY - (originY - prev.y) * ratio,
  };
}

test("the point under the cursor stays fixed while zooming", () => {
  // Zoom about a point 120px right of centre; that point must map back to the
  // same screen position after the scale change.
  const originX = 120;
  const originY = -80;
  const prev = { scale: 1, x: 0, y: 0 };
  const next = zoom(prev, 2, originX, originY);
  const screenBefore = originX * prev.scale + prev.x;
  const screenAfter = originX * next.scale + next.x;
  assert.ok(Math.abs(screenBefore - screenAfter) < 1e-9,
    `anchor drifted: ${screenBefore} -> ${screenAfter}`);
});

test("zooming about the centre scales the existing pan with it", () => {
  // The canvas uses `transform-origin: center center`, so a pan is expressed
  // in the scaled frame: doubling the scale doubles the offset, which is what
  // keeps the previously-centred content centred.
  const prev = { scale: 1.4, x: 30, y: -12 };
  const next = zoom(prev, 2.8, 0, 0);
  assert.ok(Math.abs(next.x - 60) < 1e-9, `x was ${next.x}`);
  assert.ok(Math.abs(next.y - -24) < 1e-9, `y was ${next.y}`);
});

test("scale is clamped at both ends", () => {
  assert.equal(zoom({ scale: 4, x: 0, y: 0 }, 100).scale, MAX_SCALE);
  assert.equal(zoom({ scale: 1, x: 0, y: 0 }, 0.001).scale, MIN_SCALE);
});

test("a no-op zoom returns the identical state object", () => {
  const prev = { scale: MAX_SCALE, x: 5, y: 5 };
  assert.equal(zoom(prev, 99), prev, "must not allocate when the scale cannot move");
});

test("the zoom updater is pure, so StrictMode's double invoke is harmless", () => {
  // React StrictMode calls state updaters twice. A pure updater (computing the
  // whole transform from `prev`) yields the same result both times. The bug
  // this guards against: calling setView for the pan INSIDE the setView
  // updater, which applied the shift twice and made zoom drift.
  const prev = { scale: 1, x: 0, y: 0 };
  assert.deepEqual(zoom(prev, 2, 120, 0), zoom(prev, 2, 120, 0));
  const body = SRC.slice(SRC.indexOf("const zoomAt = useCallback"),
                         SRC.indexOf("// Wheel zoom."));
  // The only setState in the updater's scope is the setView call itself.
  const calls = body.match(/set[A-Z]\w*\(/g) || [];
  assert.deepEqual(calls, ["setView("],
    "the updater must not invoke another setter (render-phase side effect)");
});
