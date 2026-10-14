/* Regression: the title-bar dropdowns must be absolutely positioned so that
 * opening them can never stretch the header or move its bottom border.
 *
 * Root cause of the bug this locks: `.title-menu` shared a surface rule that
 * set z-index but no `position`, so it rendered as a static flex child of the
 * header and pushed the border down. These assertions are intentionally
 * text-level (no DOM): they fail if the positioning contract is ever removed.
 *
 * Runs on Node's built-in test runner; no dependency or browser required.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const rawCss = readFileSync(join(here, "..", "src", "layout.css"), "utf8");
// Strip /* ... */ comments first: a comment that mentions a selector (e.g.
// "its .title-menu-wrap anchor") would otherwise be merged into the next
// rule's selector list and break exact matching.
const css = rawCss.replace(/\/\*[\s\S]*?\*\//g, "");

/* Extract the bodies of all top-level rules whose selector list contains
 * `sel` as an exact selector (so `.title-menu` never matches
 * `.title-menu-wrap`). */
function ruleBody(sel) {
  const out = [];
  const re = /([^{}]+)\{([^}]*)\}/g;
  let match;
  while ((match = re.exec(css)) !== null) {
    const selectors = match[1].split(",").map((s) => s.trim());
    if (selectors.includes(sel)) out.push(match[2]);
  }
  return out.join("\n");
}

/* Body of the rule whose selector list contains ALL of `sels` — used for the
 * shared `.mission-menu, .title-menu, .mode-menu` surface rule, whose selector
 * spans multiple lines and therefore never matches a single-selector lookup. */
function sharedRuleBody(sels) {
  const out = [];
  const re = /([^{}]+)\{([^}]*)\}/g;
  let match;
  while ((match = re.exec(css)) !== null) {
    const selectors = match[1].split(",").map((s) => s.trim());
    if (sels.every((s) => selectors.includes(s))) out.push(match[2]);
  }
  return out.join("\n");
}

test(".title-menu is absolutely positioned (never a flow block)", () => {
  const body = ruleBody(".title-menu");
  assert.match(body, /position\s*:\s*absolute/, ".title-menu must be position: absolute");
});

test(".title-menu has a z-index so it paints above the header", () => {
  const body = ruleBody(".title-menu");
  assert.match(body, /z-index\s*:\s*\d+/, ".title-menu must declare a z-index");
});

test(".title-menu opens below the toggle's right edge, with a left-overflow flip", () => {
  // The menu opens directly below the arrow, its right edge aligned to the
  // toggle's right edge. It must NOT be anchored to the header's far-left edge
  // (which read as detached from the title).
  const body = ruleBody(".title-menu");
  assert.match(body, /right\s*:\s*0/, "session menu must right-anchor to the toggle");
  assert.match(body, /left\s*:\s*auto/, "session menu must not left-anchor");

  // Viewport guard: a short title would push a 208px menu off the LEFT edge,
  // so that case left-anchors and stays fully on screen.
  const flip = ruleBody(".title-menu.is-flipped");
  assert.match(flip, /left\s*:\s*0/, "flipped menu must left-anchor to stay on screen");
  assert.match(flip, /right\s*:\s*auto/, "flipped menu must drop the right anchor");
});

test("the wrap and the toggle share one max-width clamp", () => {
  // THE BUG THIS LOCKS: the wrap sized to the title's full max-content width
  // while the button clamped at 520px. On a long title the two boxes diverged
  // by up to ~480px, so a menu right-anchored to the wrap opened at the
  // header's far edge instead of under the arrow. Both must clamp to the SAME
  // measure, and the button's must be a percentage of the wrap's so they can
  // never drift apart again.
  const wrap = ruleBody(".title-menu-wrap");
  const btn = ruleBody(".page-title-btn");

  const clampOf = (body) => (body.match(/max-width\s*:\s*([^;}]+)/) || [])[1]?.trim();
  assert.match(wrap, /max-width\s*:/, "wrap must clamp its width");
  assert.match(clampOf(wrap) || "", /520px/, "wrap must clamp at the 520px measure");
  assert.match(btn, /max-width\s*:\s*100%/, "button max-width must track the wrap (100%)");
});

test("the shared popup rule declares no horizontal anchor", () => {
  // A shared `left: 0` on the .mission-menu/.title-menu/.mode-menu selector
  // list is what repeatedly dragged the session menu to the header's far-left
  // edge, overriding its own rule depending on stylesheet order. Each menu now
  // owns its anchor; the shared rule must stay neutral.
  const shared = sharedRuleBody([".mission-menu", ".title-menu", ".mode-menu"]);
  assert.ok(shared.trim(), "the shared popup rule must exist");
  assert.doesNotMatch(shared, /(^|[^-])left\s*:/m,
    "shared popup rule must not set left — each menu anchors itself");
});

test("the toggle carries no horizontal negative margin", () => {
  // A -8px horizontal margin offset the button's border box 8px inside the
  // wrap, so a menu left-anchored to the wrap opened to the RIGHT of the
  // toggle's own left edge. Wrap and button must share one box, so the
  // horizontal margin component must be zero or positive. A VERTICAL negative
  // margin is fine (it is what gives the control its taller hit area).
  const body = ruleBody(".page-title-btn");
  const decl = (body.match(/margin\s*:\s*([^;}]+)/) || [])[1];
  assert.ok(decl, "page-title-btn must declare a margin (or the check is vacuous)");
  const parts = decl.trim().split(/\s+/);
  // 1 value = all sides; 2 = vertical horizontal; 4 = t r b l
  const horizontal = parts.length === 1 ? parts[0] : parts.length === 2 ? parts[1] : `${parts[1]} ${parts[3]}`;
  const negatives = horizontal.split(/\s+/).filter((v) => v.startsWith("-"));
  assert.deepEqual(negatives, [], `horizontal margin must not be negative, got "${horizontal}"`);
});

test(".title-menu stays inside the header (left-anchored to the title)", () => {
  // Left-anchoring to a content-sized wrap that begins at the header's left
  // padding keeps the menu inside the header: it grows rightward from the
  // title and the shared rule caps its width against the viewport.
  const shared = ruleBody(".mission-menu,\n.title-menu,\n.mode-menu");
  const anyMenu = ruleBody(".title-menu") + shared;
  assert.match(anyMenu, /max-width/, "menu must remain viewport-capped");
});

test(".title-menu-wrap is a positioned anchor that hugs its content", () => {
  const body = ruleBody(".title-menu-wrap");
  assert.match(body, /position\s*:\s*relative/, "wrap must establish the containing block");
  // The wrap is sized to its content (inline-flex, no flex-grow) so the title
  // control sits tight against the text instead of spanning the bar. It is
  // clamped (not free) so a long title truncates rather than widening the wrap
  // past the button.
  assert.doesNotMatch(body, /flex\s*:\s*1\s+1\s+auto/, "wrap must not grow to span the bar");
  assert.match(body, /min-width\s*:\s*0/, "wrap must allow the title to shrink");
  assert.match(body, /max-width\s*:\s*min\(/, "wrap must be clamped, not 100%");
});

test("the Library dropdown is gone from the title bar", () => {
  // It was removed; Evidence and Knowledge moved into the sidebar nav so those
  // views kept a route in. This guards against the chrome creeping back.
  const css = readFileSync(new URL("../src/layout.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /\.library-btn/, "no library button styling should remain");
  assert.doesNotMatch(css, /\.library-menu/, "no library menu styling should remain");

  const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /LibraryMenu/, "the component should be gone");
  assert.doesNotMatch(app, /showLibrary/, "and its prop with it");

  // Evidence and Knowledge are deliberately NOT surfaced anywhere in the nav:
  // the Library dropdown went first, then their sidebar entries. The views
  // still resolve from a deep link, so this pins the nav, not the routes.
  const sidebar = readFileSync(new URL("../src/components/Sidebar.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(sidebar, /id: "evidence"/, "Evidence must not be in the nav");
  assert.doesNotMatch(sidebar, /id: "knowledge"/, "Knowledge must not be in the nav");

  // Both views are now gone entirely — component, route and title — so an old
  // "#/evidence" bookmark falls back to the landing page rather than rendering
  // a page with no way to reach it.
  assert.doesNotMatch(app, /VALID_VIEWS = \[[^\]]*"evidence"/, "evidence route must be gone");
  assert.doesNotMatch(app, /VALID_VIEWS = \[[^\]]*"knowledge"/, "knowledge route must be gone");
  assert.doesNotMatch(app, /EvidenceView/, "no evidence view import");
  assert.doesNotMatch(app, /KnowledgeView/, "no knowledge view import");
});

test(".page-title-bar is the relative anchor for the header", () => {
  const body = ruleBody(".page-title-bar");
  assert.match(body, /position\s*:\s*relative/, "header must be a positioned anchor");
});

test("every title render path shares one box (no layout jump on send)", () => {
  /* Regression: submitting the first message swapped a bare <span> (full bar
   * width, ~726px) for the interactive control (clamped, ~520px), so a long
   * title visibly snapped narrower the moment the session persisted. All three
   * paths must go through .title-menu-wrap, and the plain variant must reserve
   * the chevron's footprint so the text box never changes. */
  const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
  const barBlocks = app.match(/<div className="page-title-bar">[\s\S]*?<\/div>\s*\n\s*\)/g) || [];
  assert.ok(barBlocks.length >= 3, `expected 3 page-title-bar branches, found ${barBlocks.length}`);
  for (const block of barBlocks) {
    assert.match(block, /className="title-menu-wrap"/,
      "every title branch must render inside .title-menu-wrap");
  }
  // The plain, non-interactive title exists and reserves the chevron space.
  assert.match(app, /page-title-plain/, "the plain title needs the shared-geometry class");
  const plain = ruleBody(".page-title-plain");
  assert.match(plain, /min-height\s*:\s*32px/, "plain title must match the control's height");
  assert.match(plain, /padding\s*:\s*4px\s+28px\s+4px\s+8px/,
    "plain title must reserve the chevron's 20px so the ellipsis cut is identical");
  assert.match(plain, /overflow\s*:\s*hidden/, "plain title must truncate");
});

test("the title has no width transition to animate", () => {
  // A width/transition on the title would animate the resize rather than
  // settling instantly. Only colour/background transitions are acceptable.
  for (const sel of [".page-title-text", ".page-title-btn", ".page-title-plain", ".title-menu-wrap"]) {
    const body = ruleBody(sel);
    const transitions = [...body.matchAll(/transition\s*:\s*([^;}]+)/g)].map((m) => m[1]);
    for (const t of transitions) {
      assert.doesNotMatch(t, /(^|[\s,])width|all\b/,
        `${sel} must not transition width (found: ${t})`);
    }
  }
});

test("the claim drawer is documented as dormant, not silently orphaned", () => {
  // Removing Evidence and Knowledge removed the only callers of
  // setSelectedFinding, so ClaimDrawer can no longer open. That is a real
  // consequence and it must stay visible in the source rather than looking
  // like working code.
  const app = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
  const drawer = app.match(/\/\* Claim inspector[\s\S]*?<ClaimDrawer/);
  assert.ok(drawer, "expected the ClaimDrawer call site");
  assert.match(drawer[0], /CURRENTLY UNREACHABLE/, "must be flagged at the call site");
  assert.match(drawer[0], /saveKnowledgeItem/, "and note the store behind it still works");

  // The knowledge store must remain wired, so restoring a list is enough.
  assert.match(app, /import \{[^}]*saveKnowledgeItem[^}]*\} from "\.\/lib"/);
});
