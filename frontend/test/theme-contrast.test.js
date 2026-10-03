/* Palette guard for theme.css.
 *
 * The stylesheet documents its own contract: each text token must clear 4.5:1
 * against the WORST surface it can land on — not against --bg. Small meta text
 * sits on chips, raised rows and menu hovers far more often than on the page
 * background, so verifying against --bg would pass a palette that fails in
 * practice. A theme swap is exactly the change that breaks this quietly, so the
 * ratios are asserted rather than eyeballed.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const css = await readFile(new URL("../src/theme.css", import.meta.url), "utf8");

/** Values from the dark (default) block only — the first :root wins. */
function tokens() {
  const block = css.slice(css.indexOf(":root {"), css.indexOf('[data-theme="light"]'));
  const out = {};
  for (const m of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    if (!(m[1] in out)) out[m[1]] = m[2];
  }
  return out;
}

const channel = (v) => {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
function luminance(hex) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const T = tokens();

/** Planes a user can actually read text on.
 *
 *  Marks are deliberately excluded: --track is a progress-bar fill and --bar /
 *  --dash / --dot-idle are glyphs, none of which host text. Including --track
 *  made the worst-case surface #303030 and failed a palette that is compliant
 *  everywhere text is ever drawn. The lightest real plane is #262626. */
const SURFACES = [
  "bg", "bg-soft", "side", "panel", "card", "card-2",
  "inset", "inset-2", "chip", "menu", "menu-hover",
  "row-hover", "row-active",
];

test("every dark text token clears AA against the worst surface", () => {
  const worst = SURFACES
    .map((k) => T[k])
    .filter(Boolean)
    .reduce((a, b) => (luminance(a) >= luminance(b) ? a : b));
  assert.ok(worst, "expected surface tokens in theme.css");
  for (const name of ["t1", "t2", "t3", "t4"]) {
    const ratio = contrast(T[name], worst);
    assert.ok(
      ratio >= 4.5,
      `--${name} ${T[name]} is ${ratio.toFixed(2)}:1 on the worst surface ${worst}, needs 4.5:1`,
    );
  }
});

test("the text ramp stays ordered and visibly stepped", () => {
  for (const [a, b] of [["t1", "t2"], ["t2", "t3"], ["t3", "t4"]]) {
    assert.ok(
      contrast(T[a], T[b]) > 1.05,
      `--${a} and --${b} are too close to tell apart (${contrast(T[a], T[b]).toFixed(3)}:1)`,
    );
  }
});

test("depth planes stay visually distinct", () => {
  for (const [a, b] of [["side", "bg"], ["bg", "card"], ["card", "card-2"]]) {
    assert.ok(
      contrast(T[a], T[b]) > 1.03,
      `--${a} and --${b} are indistinguishable (${contrast(T[a], T[b]).toFixed(3)}:1)`,
    );
  }
});

test("semantic status colours stay legible on the lightest plane", () => {
  // Status hues are used as text (a "Serving" label, a tone-good tag), so each
  // must clear AA on the plane it is most likely to sit on. A palette swap is
  // exactly what leaves a vivid hue stranded on a neutral ground.
  const lightest = SURFACES
    .map((k) => T[k])
    .filter(Boolean)
    .reduce((a, b) => (luminance(a) >= luminance(b) ? a : b));
  for (const name of ["mint", "amber", "red", "blue"]) {
    assert.ok(T[name], `--${name} missing from theme.css`);
    const ratio = contrast(T[name], lightest);
    assert.ok(
      ratio >= 4.5,
      `--${name} ${T[name]} is ${ratio.toFixed(2)}:1 on ${lightest}, needs 4.5:1`,
    );
  }
});

test("the status green is not left stranded at terminal saturation", () => {
  // Guards the reason the green was changed: a 54%-saturation mint on a fully
  // neutral surface ramp reads as a foreign colour rather than part of the UI.
  const hex = T.mint.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const mx = Math.max(r, g, b);
  const mn = Math.min(r, g, b);
  const saturation = mx === 0 ? 0 : (mx - mn) / mx;
  assert.ok(
    saturation <= 0.35,
    `--mint saturation ${(saturation * 100).toFixed(0)}% is too vivid for the neutral ramp`,
  );
});

test("borders read as edges against the planes they divide", () => {
  assert.ok(contrast(T.line, T.bg) >= 1.15, `--line is too faint on --bg`);
  assert.ok(contrast(T.line, T.card) >= 1.10, `--line is too faint on --card`);
});

test("selection is expressed as luminance, not as a status hue", async () => {
  // A selected surface used to be painted with the status green, so a chosen
  // model also looked verified — two different facts sharing one colour.
  // Green must stay reserved for genuine runtime status.
  const css = await readFile(new URL("../src/components/model-controls.css", import.meta.url), "utf8");
  const stateful = [
    ".pv-summary-item.on",
    ".pv-serving-card.active",
    ".pv-serving-card.active .pv-radio",
    ".pv-radio-dot",
    ".pv-serving-option-meta.serving",
  ];
  for (const sel of stateful) {
    const rule = css.match(
      new RegExp(`${sel.replace(/[.[\]]/g, "\\$&")}\\s*\\{([^}]*)\\}`),
    );
    assert.ok(rule, `expected a rule for ${sel}`);
    assert.doesNotMatch(
      rule[1],
      /probe-ok|mint|green-wash/,
      `${sel} still signals selection with a status hue`,
    );
  }
  // Selection needs a visible cue: white alone on a dark plane is too quiet,
  // hence the accent bar plus an edge, not just a brighter hairline.
  assert.match(css, /\.pv-serving-card\.active::before/, "selected card needs its accent bar");
  assert.match(css, /var\(--act-line\)/, "selected card must reference the neutral ladder");
});

test("the Serving pill is distinguishable from the Reachable pill", async () => {
  // Both used class tone-good, so the UI asserted a chosen model was verified.
  const src = await readFile(
    new URL("../src/components/ModelControlsView.jsx", import.meta.url),
    "utf8",
  );
  const serving = src.match(/<span className="pv-status ([a-z-]+)"><span className="pv-status-dot" \/>Serving/);
  const reachable = src.match(/<span className="pv-status ([a-z-]+)"><span className="pv-status-dot" \/>Reachable/);
  assert.ok(serving, "expected a Serving pill");
  assert.ok(reachable, "expected a Reachable pill");
  assert.equal(serving[1], "tone-active", "Serving is selection, so it must be neutral");
  assert.equal(reachable[1], "tone-good", "Reachable is health, so it keeps the status hue");
});
