/* Render tests for AgentPipelineTrace.
 *
 * These exist because a scope bug shipped to the browser as a blank screen:
 * `traceStatus` read `run`, which is only destructured inside a later branch,
 * so React threw during render and the error boundary replaced the whole
 * message list. `npm run build` passed the entire time — undefined variables
 * are a RUNTIME fault, and nothing in the suite ever rendered the component.
 *
 * The reducer tests cover the data model. This file covers the thing that
 * actually broke: does the component mount, and does it emit the narrative
 * structure the reference design calls for.
 *
 * Node's runner strips types but not JSX, so the component is bundled with
 * esbuild first, then server-rendered with react-dom/server.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { build } from "esbuild";

const ENTRY = new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url).pathname;

/** Bundle the TSX to a temp ESM module and import it. Cached across tests. */
let modPromise = null;
function loadComponent() {
  if (modPromise) return modPromise;
  modPromise = (async () => {
    const out = await build({
      entryPoints: [ENTRY],
      bundle: true,
      write: false,
      format: "esm",
      jsx: "automatic",
      target: "node20",
      external: ["react", "react-dom", "react/jsx-runtime", "framer-motion"],
      logLevel: "silent",
    });
    // Written INSIDE the project so Node resolves the externalized react /
    // framer-motion by walking up to ./node_modules. A temp dir under the OS
    // tmp path cannot resolve them, and bundling a second React copy would
    // break hooks against react-dom/server.
    const dir = join(dirname(ENTRY), "..", "..", "node_modules", ".apt-test");
    await mkdir(dir, { recursive: true });
    const file = join(dir, "component.mjs");
    await writeFile(file, out.outputFiles[0].text);
    return import(file);
  })();
  return modPromise;
}

/** The shared inline renderer, bundled the same way. */
let inlinePromise = null;
function loadInline() {
  if (inlinePromise) return inlinePromise;
  inlinePromise = (async () => {
    const entry = new URL("../src/trace/inline.ts", import.meta.url).pathname;
    const out = await build({
      entryPoints: [entry],
      bundle: true,
      write: false,
      format: "esm",
      jsx: "automatic",
      target: "node20",
      external: ["react", "react-dom", "react/jsx-runtime"],
      logLevel: "silent",
    });
    const dir = join(dirname(entry), "..", "..", "node_modules", ".apt-test");
    await mkdir(dir, { recursive: true });
    const file = join(dir, "inline.mjs");
    await writeFile(file, out.outputFiles[0].text);
    return import(file);
  })();
  return inlinePromise;
}

/** A realistic slice of the live NDJSON stream. */
function wireEvents() {
  return [
    { type: "progress", message: "Research run started.", __ts: 1 },
    {
      type: "intent",
      query_type: "analytical",
      domain: "energy",
      explanation_level: "deep",
      __ts: 2,
    },
    {
      type: "route",
      path: "deep_research",
      reason: "needs multiple sources",
      __ts: 3,
    },
    {
      type: "plan",
      items: ["battery storage additions 2026", "grid scale deployment"],
      __ts: 4,
    },
    {
      type: "search_query",
      query: "global battery storage capacity additions 2026",
      results: [
        { title: "Global Energy Review", url: "https://www.iea.org/reports/ger2026", source: "tavily" },
        { title: "Grid storage", url: "https://example.org/storage", source: "tavily" },
      ],
      __ts: 5,
    },
    {
      type: "findings",
      items: [
        { claim: "Additions reached 90 GW.", source: "IEA", verified: true },
        { claim: "Grid scale led growth.", source: "IEA", verified: false },
      ],
      __ts: 6,
    },
    { type: "critic", iteration: 1, reason: "evidence is thin", __ts: 7 },
    { type: "final_report", report: "# Answer\n\nStorage grew.", __ts: 8 },
  ];
}

async function render(props) {
  const { default: Trace } = await loadComponent();
  return renderToStaticMarkup(createElement(Trace, props));
}

test("mounts without throwing — regression guard for the blank-screen crash", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /<section class="apt\s*"/);
});

test("renders the narrative structure: rail, rows, prose bullets", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-flow"/, "expected the single-rail flow list");
  assert.match(html, /class="apt-row"/);
  // Prose reasoning renders as paragraphs, not a status table.
  assert.match(html, /apt-prose/);
  // The reference design carries no uppercase kind labels or timestamps.
  assert.doesNotMatch(html, />EVIDENCE</);
  assert.doesNotMatch(html, /class="apt-time"/);
});

test("tool rows carry a monochrome icon, not a coloured status dot", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-gutter"/);
  assert.match(html, /<svg/);
  assert.doesNotMatch(html, /class="apt-dot"/);
});

test("row icons carry an intrinsic size so they can never fill the card", async () => {
  // Regression: the SVGs shipped with a viewBox but no className and no
  // width/height, so the CSS size rule never applied and a viewBox-only SVG
  // stretched to its container — a full-page magnifying glass over the trace.
  // Both the class and the attributes are asserted, because either alone
  // leaves a path back to that bug (CSS not loaded, or rule renamed).
  const html = await render({ events: wireEvents(), status: "done" });
  const svgs = html.match(/<svg[^>]*>/g) ?? [];
  assert.ok(svgs.length > 0, "expected inline icons");
  for (const tag of svgs) {
    assert.match(tag, /class="apt-icon"/, `icon missing its class: ${tag}`);
    assert.match(tag, /width="15"/, `icon has no intrinsic width: ${tag}`);
    assert.match(tag, /height="15"/, `icon has no intrinsic height: ${tag}`);
    assert.doesNotMatch(tag, /width="100%"/);
  }
  // The gutter must not contribute layout of its own.
  assert.doesNotMatch(html, /class="apt-gutter"[^>]*>\s*<svg(?![^>]*class="apt-icon")/);
});

test("search results render as monospace chips linking to the source", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-code"/);
  assert.match(html, /href="https:\/\/www\.iea\.org\/reports\/ger2026"/);
});

test("chips sit in a wrapping flex row, not stacked full-width bars", async () => {
  // Regression: the chips container shipped with no className at all, so the
  // block-level chips stacked one per line and filled the card width — a wall
  // of grey bars instead of pills that flow like text. The container class is
  // the only thing carrying the layout, so assert it is actually emitted.
  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-chips"/);
  const wrap = html.match(/<div[^>]*class="apt-chips"[^>]*>([\s\S]*?)<\/div>/);
  assert.ok(wrap, "expected an apt-chips wrapper");
  assert.ok(
    (wrap[1].match(/class="apt-code"/g) ?? []).length >= 1,
    "chips must live inside the wrapper, not beside it",
  );
});

test("header reads 'Less steps' and reports elapsed working time", async () => {
  // Elapsed time replaced the raw step count. The count survives in the
  // tooltip, so this asserts the duration is present and the old inline
  // "N steps" text is gone.
  const events = wireEvents().map((e, i) => ({ ...e, __ts: 1_700_000_000_000 + i * 30_000 }));
  const html = await render({ events, status: "done" });
  assert.match(html, /Less steps/);
  assert.match(html, /Worked for 3m 30s/);
  assert.doesNotMatch(html, />\d+ steps?<\//);
  assert.match(html, /title="8 steps"/, "step count retained in the tooltip");
});

test("formatDuration covers seconds, minutes and hours", async () => {
  const { formatDuration } = await loadComponent();
  assert.equal(formatDuration(0), "0s");
  assert.equal(formatDuration(45_000), "45s");
  assert.equal(formatDuration(161_000), "2m 41s");
  assert.equal(formatDuration(3_599_000), "59m 59s");
  assert.equal(formatDuration(3_900_000), "1h 5m");
  // A backwards clock must never render a negative duration.
  assert.equal(formatDuration(-5_000), "0s");
});

test("no duration is claimed from a single frame", async () => {
  // One frame has no span; "Worked for 0s" on the first event is noise.
  const html = await render({
    events: [{ type: "progress", message: "Query received", __ts: 1 }],
    status: "done",
  });
  assert.doesNotMatch(html, /Worked for/);
});

test("a replayed run still shows its duration", async () => {
  // Regression, and one this suite previously locked IN. __ts is stamped in the
  // browser, so persisted frames never carried it — every restored session
  // rendered no duration at all, and the old test asserted that absence as
  // correct. The server's own `ts` stamp is what survives persistence, so the
  // elapsed time must fall back to it.
  const replayed = wireEvents().map(({ __ts, ...rest }, i) => ({
    ...rest,
    ts: 1_700_000_000_000 + i * 20_000,
  }));
  const html = await render({ events: replayed, status: "done" });
  assert.match(html, /Worked for/);
  assert.doesNotMatch(html, /NaN/);
});

test("no duration is invented when no frame carries any timestamp", async () => {
  const html = await render({
    events: wireEvents().map(({ __ts, ...rest }) => rest),
    status: "done",
  });
  assert.doesNotMatch(html, /Worked for/);
});

test("the client arrival stamp wins over the server stamp when both exist", async () => {
  // For a live run the browser knows when the frame actually arrived; the
  // server clock can drift. __ts must take precedence.
  const events = wireEvents().map((e, i) => ({
    ...e,
    __ts: 2_000_000_000_000 + i * 5_000,
    ts: 1_000_000_000_000 + i * 60_000,
  }));
  const html = await render({ events, status: "done" });
  assert.match(html, /Worked for 35s/, "client stamps win (7 gaps x 5s)");
  assert.doesNotMatch(html, /Worked for \d+m/, "server stamps must be ignored");
});

test("the trace grows with its content: no fixed height, no inner scroller", async () => {
  // The panel used to cap at 560px and scroll inside itself, which put a second
  // scrollbar inside an already-scrolling page and hid the end of a run behind
  // an inner gesture. The trace must be as tall as its content.
  const css = await readFile(new URL("../src/trace/trace.css", import.meta.url), "utf8");
  const body = css.match(/\.apt-body \{([\s\S]*?)\n\}/);
  assert.ok(body, "expected an .apt-body rule");
  assert.doesNotMatch(body[1], /max-height/, "the trace must not have a fixed height");
  assert.doesNotMatch(body[1], /overflow-y/, "the trace must not scroll inside itself");
  assert.doesNotMatch(css, /\.apt-body \{[^}]*62vh/);

  // The auto-follow machinery existed only to pin the bottom of that scroller.
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  for (const dead of ["listRef", "pinnedRef", "onScroll"]) {
    assert.doesNotMatch(src, new RegExp(dead), `${dead} only served the removed scroller`);
  }
});

test("row markers never overlap their own label", async () => {
  // Regression: nested rows had 14px of indent while the 15px icon was placed
  // at left:-9px, so it spanned 5..20px against a label starting at 16px and
  // printed over the text. Asserted as geometry, since the collision is
  // invisible to every DOM-level check.
  const css = await readFile(new URL("../src/trace/trace.css", import.meta.url), "utf8");
  const px = (block, prop) => {
    const m = block.match(new RegExp(`${prop}:\\s*(-?\\d+(?:\\.\\d+)?)px`));
    return m ? Number(m[1]) : null;
  };
  // Two `.apt-nested` rules exist: a shared list reset with no indent, and the
  // positioning rule that sets it. Select the one carrying the indent.
  const nestedBlocks = [...css.matchAll(/\.apt-nested \{([\s\S]*?)\n\}/g)]
    .map((m) => m[1])
    .filter((b) => /padding-left/.test(b));
  assert.equal(nestedBlocks.length, 1, "expected exactly one indenting .apt-nested rule");
  const indent = px(nestedBlocks[0], "padding-left");
  assert.ok(indent != null, "nested rows need an indent");

  const iconRule = css.match(/\.apt-icon \{([\s\S]*?)\n\}/);
  const iconW = px(iconRule[1], "width");
  assert.ok(iconW != null, "expected an icon width");

  const offsets = css.match(/\.apt-nested \.apt-icon,[\s\S]*?\{ left:\s*(-?\d+(?:\.\d+)?)px; \}/);
  assert.ok(offsets, "expected a nested icon offset");
  const offset = Number(offsets[1]);

  const rowPad = px(css.match(/\.apt-nested \.apt-row \{([^}]*)\}/)?.[1] ?? "", "padding-left") ?? 0;
  const labelStart = indent + rowPad;
  const iconStart = indent + offset;
  const iconEnd = iconStart + iconW;

  assert.ok(
    iconEnd <= labelStart,
    `nested icon ends at ${iconEnd}px but the label starts at ${labelStart}px — ` +
      `they overlap by ${(iconEnd - labelStart).toFixed(1)}px`,
  );
  assert.ok(iconStart >= 0, `nested icon starts off-canvas at ${iconStart}px`);
});

test("a long source title truncates instead of stretching the pill", async () => {
  // Two things had to be true at once. The stylesheet capped the pill's width,
  // and the text sat in its own block-ish span — `text-overflow` on a flex
  // container does nothing, so an over-long title was hard-clipped mid-word
  // with no ellipsis while the pill itself spanned the whole card.
  const css = await readFile(new URL("../src/trace/trace.css", import.meta.url), "utf8");
  const pill = css.match(/\.apt-code \{([\s\S]*?)\n\}/);
  assert.ok(pill, "expected a .apt-code rule");
  assert.match(pill[1], /max-width:\s*min\(/, "chip width must be capped, not 100%");
  assert.doesNotMatch(pill[1], /max-width:\s*100%/);

  // Density guard. These are a deliberate compactness budget: chips are
  // metadata around the narrative, not the content, and were reading as
  // large enough to compete with the prose above them.
  const px = (rule, prop) => {
    const m = rule.match(new RegExp(`${prop}:\\s*(\\d+(?:\\.\\d+)?)px`));
    return m ? Number(m[1]) : null;
  };
  const cap = Number(/min\((\d+)px/.exec(pill[1])?.[1]);
  assert.ok(cap && cap <= 260, `chip max-width should stay compact, got ${cap}px`);
  assert.ok(px(pill[1], "font-size") <= 10.5, "chip font-size should stay near 10px");
  // Monospace is much wider per character, which clipped titles to a few
  // characters while the pill still read as oversized.
  assert.match(pill[1], /font-family:\s*inherit/, "chips should use the proportional UI font");

  // The mobile override must not exceed the desktop size, or phones render
  // bigger chips than desktop and the compaction is undone where it matters.
  const mobile = css.match(/@media \(max-width: 640px\) \{([\s\S]*?)\n\}/);
  assert.ok(mobile, "expected a mobile media query");
  const mobileChip = mobile[1].match(/\.apt-code \{[^}]*font-size:\s*(\d+(?:\.\d+)?)px/);
  assert.ok(mobileChip, "expected a chip font-size in the mobile query");
  assert.ok(
    Number(mobileChip[1]) <= px(pill[1], "font-size"),
    `mobile chip font (${mobileChip[1]}px) must not exceed desktop (${px(pill[1], "font-size")}px)`,
  );

  const label = css.match(/\.apt-code-label \{([\s\S]*?)\n\}/);
  assert.ok(label, "expected a .apt-code-label rule");
  assert.match(label[1], /text-overflow:\s*ellipsis/);
  assert.match(label[1], /overflow:\s*hidden/);

  const html = await render({ events: wireEvents(), status: "done" });
  assert.match(html, /class="apt-code-label"/, "title text needs its own truncating box");
});

test("a large evidence batch is previewed, not dumped in full", async () => {
  // A deep run yields dozens of claims. Rendering every one inline buried the
  // rest of the trace in a wall of paragraphs, so a collapsed evidence row shows
  // a short preview with each claim clipped, behind an explicit toggle.
  const many = Array.from({ length: 14 }, (_, i) => ({
    claim: `Claim ${i + 1}: ${"detail ".repeat(30)}`,
    source: `src${i}.org`,
  }));
  const html = await render({
    events: [{ type: "findings", items: many, __ts: 9 }],
    status: "done",
  });
  const block = html.match(/data-kind="evidence"[\s\S]*?<\/li>/)?.[0] ?? "";
  assert.ok(block, "expected an evidence row");
  assert.equal(
    (block.match(/apt-claim/g) ?? []).length,
    4,
    "only the first few claims render while collapsed",
  );
  assert.match(block, /Show all 14 claims/, "the rest must be one explicit click away");
  assert.doesNotMatch(block, /Claim 14/, "no later claim leaks into the preview");

  const css = await readFile(new URL("../src/trace/trace.css", import.meta.url), "utf8");
  const clamp = css.match(/\.apt-claim \{([\s\S]*?)\n\}/);
  assert.ok(clamp, "expected an .apt-claim rule");
  assert.match(clamp[1], /-webkit-line-clamp:\s*2/);
  assert.match(clamp[1], /overflow:\s*hidden/);
});

test("a huge critic rationale is clipped, not expanded on arrival", async () => {
  // The auto-open rule used to be "open it if it's long", which expanded
  // precisely the rows that were unwieldy — a critic rationale runs to
  // thousands of characters. Long prose must now stay clipped until asked.
  const rationale =
    "Review round 3. This is the final iteration and the evidence, while rich " +
    "on capability and evaluation mechanics, is skewed toward one framing. ".repeat(12);
  const html = await render({
    events: [{ type: "critic", iteration: 3, reason: rationale, __ts: 9 }],
    status: "done",
  });
  const block = html.match(/data-kind="gate"[\s\S]*?<\/li>/)?.[0] ?? "";
  assert.ok(block, "expected a critic row");
  assert.match(block, /apt-claim/, "long prose must be line-clamped while collapsed");
  assert.match(block, /Show more/, "and stay behind an explicit toggle");
  assert.doesNotMatch(block, /aria-expanded="true"/, "must not auto-expand");
  // Clipping is visual, not destructive: the full rationale stays in the DOM so
  // find-in-page and screen readers still get all of it. Only the painted
  // height is limited.
  assert.match(block, /skewed toward one framing/, "no text is discarded");
});

test("short prose needs no toggle", async () => {
  const html = await render({
    events: [{ type: "critic", iteration: 1, reason: "Sufficient after verification.", __ts: 9 }],
    status: "done",
  });
  const block = html.match(/data-kind="gate"[\s\S]*?<\/li>/)?.[0] ?? "";
  assert.doesNotMatch(block, /Show more/, "nothing to expand");
});

test("the trace paints no surface of its own, so backgrounds stay unified", async () => {
  // The trace used to fill itself with --card while the page used --bg, which
  // read as a second theme dropped inside the page. Asserted against the
  // stylesheet because this failure is invisible to markup tests.
  const css = await readFile(new URL("../src/trace/trace.css", import.meta.url), "utf8");
  const block = css.match(/^\.apt \{([\s\S]*?)\n\}/m);
  assert.ok(block, "expected a .apt rule");
  assert.match(block[1], /background:\s*transparent/);
  assert.doesNotMatch(css, /var\(--card/, "must not reference --card for its surface");
  assert.doesNotMatch(css, /box-shadow/, "elevation shadow re-creates the panel look");
  assert.doesNotMatch(css, /--apt-bg/, "the separate background token is gone");
});

test("the header names the stage the run is really in", async () => {
  // Derived from the last real frame, so it cannot claim a stage never reached.
  const stage = async (frames) => {
    const html = await render({ events: frames, status: "running" });
    const m = /class="apt-stage" data-stage="([a-z]+)"/.exec(html);
    return m ? m[1] : null;
  };
  assert.equal(await stage([{ type: "progress", message: "x", __ts: 1 }]), "thinking");
  assert.equal(await stage([{ type: "plan", items: ["a"], __ts: 1 }]), "planning");
  assert.equal(await stage([{ type: "search_query", query: "q", __ts: 1 }]), "searching");
  assert.equal(await stage([{ type: "findings", items: [{ claim: "c" }], __ts: 1 }]), "reading");
  assert.equal(await stage([{ type: "critic", iteration: 1, __ts: 1 }]), "critiquing");
  assert.equal(await stage([{ type: "final_report", report: "x", __ts: 1 }]), "writing");
  // The LAST frame wins, so a run mid-critique reads as critiquing.
  assert.equal(
    await stage([
      { type: "search_query", query: "q", __ts: 1 },
      { type: "critic", iteration: 2, __ts: 2 },
    ]),
    "critiquing",
  );
});

test("the stage sits after the last row, as a footer", async () => {
  // It belongs at the bottom of the trace, under the last row — where the eye
  // already is while reading a live run — not in the header competing with the
  // elapsed time.
  const html = await render({
    events: [
      { type: "search_query", query: "battery", __ts: 1 },
      { type: "critic", iteration: 2, __ts: 2 },
    ],
    status: "running",
  });
  const stage = html.indexOf('class="apt-stage"');
  const lastRow = html.lastIndexOf('class="apt-row"');
  assert.ok(stage > 0, "expected a stage line");
  assert.ok(lastRow > 0, "expected rows");
  assert.ok(stage > lastRow, "the stage must come after the final row");
  assert.ok(stage > html.indexOf('class="apt-body"'), "and inside the trace body");
  // It is a footer, so it must not sit in the header.
  assert.ok(stage > html.indexOf("</header>"), "the stage must not be in the header");
});

test("no stage is claimed for an empty or unknown stream", async () => {
  const empty = await render({ events: [], status: "running" });
  assert.doesNotMatch(empty, /class="apt-stage"/);
  const unknown = await render({
    events: [{ type: "brand_new_event", x: 1 }],
    status: "running",
  });
  assert.doesNotMatch(unknown, /class="apt-stage"/);
});

test("the stage is hidden once the run is finished", async () => {
  const html = await render({ events: wireEvents(), status: "done" });
  assert.doesNotMatch(html, /class="apt-stage"/);
});

test("the panel never animates its height, which caused the blank flash", async () => {
  // `overflow: hidden` is needed to clip a height animation, so while the
  // animated height lagged the real content, arriving frames were clipped and
  // the trace flashed blank — worst when frames arrived fastest. With no fixed
  // height to animate, the body must not animate height at all.
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  const body = src.match(/className="apt-body"[\s\S]*?>/);
  assert.ok(body, "expected the apt-body motion element");
  assert.doesNotMatch(body[0], /height:/, "the body must not animate height");
  assert.match(body[0], /opacity/, "it should still fade in");
});

test("row stagger runs from the newest row backwards", async () => {
  // Delaying by absolute index made a row arriving at position 40 wait 0.56s
  // before it was even visible, so bursts read as a blank gap then a pop.
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  assert.match(
    src,
    /total - 1 - index/,
    "the stagger must be relative to the newest row",
  );
  assert.doesNotMatch(
    src,
    /Math\.min\(index, \d+\) \* STAGGER/,
    "absolute-index stagger must be gone",
  );
});

test("chips arriving on an already-open row still animate", async () => {
  // The backend re-emits a search as its results land, on a row that is
  // already open — keyed only on "chips" nothing re-animated, so sources
  // appeared with no transition.
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  assert.match(src, /key=\{`chips-\$\{step\.chips\.length\}`\}/);
});

test("the duration stops counting once the run is aborted", async () => {
  // An aborted run is done=false with no error, so it previously reported
  // status "running" and the header timer advanced forever after the user had
  // stopped the run. Asserted at the status layer that drives the tick.
  const src = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
  assert.match(
    src,
    /traceRun\?\.error \|\| traceRun\?\.aborted/,
    "an aborted run must not be reported as still running",
  );

  // With a terminal status the elapsed figure is fixed at last-frame - first.
  const events = wireEvents().map((e, i) => ({ ...e, __ts: 1_000 + i * 1_000 }));
  const done = await render({ events, status: "done" });
  const errored = await render({ events, status: "error" });
  assert.match(done, /Worked for 7s/);
  assert.equal(
    (errored.match(/Worked for 7s/g) ?? []).length,
    1,
    "a terminal run reports a fixed elapsed time",
  );
});

test("opens automatically when a run starts streaming", async () => {
  // The trace mounts with its message, before any frame arrives, so the run is
  // idle at mount and `defaultOpen` cannot express "open when the run starts" —
  // a useState initialiser never re-reads a prop. The component watches the
  // idle -> running edge instead.
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  assert.match(src, /autoOpenStep\(wasLive\.current, status\)/, "must use the edge helper");
  assert.match(src, /wasLive\.current = next/, "and store the next edge value");
  // Regression: seeded with `status === "running"`, the ref claimed the
  // transition had already been handled. The run card mounts while the run is
  // ALREADY live, so the trace never opened — the exact case this exists for.
  assert.match(
    src,
    /const wasLive = useRef\(false\)/,
    "wasLive must start false, or a trace mounted mid-run never auto-opens",
  );
  assert.doesNotMatch(
    src,
    /useRef\(status === "running"\)/,
    "seeding from the current status defeats the edge detection",
  );
});

test("auto-open fires on the exact mount case that was broken", async () => {
  // Behavioural table, not source-reading: the run card mounts while its run
  // is ALREADY live, so the very first evaluation must expand.
  const { autoOpenStep } = await loadComponent();

  // The bug: a trace mounted mid-run stayed collapsed forever.
  assert.deepEqual(
    autoOpenStep(false, "running"),
    { expand: true, wasLive: true },
    "a trace mounted into a live run must expand",
  );

  // Already open and still live: must not re-fire (would fight the toggle).
  assert.equal(autoOpenStep(true, "running").expand, false);

  // Idle -> live is the normal path.
  const idle = autoOpenStep(false, "idle");
  assert.equal(idle.expand, false);
  assert.equal(idle.wasLive, false);
  assert.equal(autoOpenStep(idle.wasLive, "running").expand, true);

  // A finished or restored trace must stay shut.
  assert.equal(autoOpenStep(false, "done").expand, false);
  assert.equal(autoOpenStep(true, "done").expand, false);
  assert.equal(autoOpenStep(false, "idle").expand, false);
  assert.equal(autoOpenStep(false, "error").expand, false);
});

test("a restored run does not unroll itself into the thread", async () => {
  // Replaying a finished session should not auto-expand a long trace.
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  // Gating now lives in the extracted helper; the behavioural table above
  // proves it expands only for a live run.
  assert.match(src, /export function autoOpenStep/, "the edge logic is extracted");
  assert.match(
    src,
    /const live = status === "running";/,
    "and is gated on the running status alone",
  );
  // Both call sites keep defaultOpen false, so a replayed message starts shut.
  const app = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
  const calls = app.match(/<AgentPipelineTrace[\s\S]*?\/>/g) ?? [];
  assert.ok(calls.length >= 1, "expected AgentPipelineTrace call sites");
  for (const call of calls) {
    assert.match(call, /defaultOpen=\{false\}/, "replay must stay collapsed");
  }
});

test("stays collapsed when defaultOpen is false", async () => {
  const html = await render({ events: wireEvents(), status: "done", defaultOpen: false });
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /class="apt-flow"/);
});

test("an empty stream renders an honest empty state, not invented steps", async () => {
  const html = await render({ events: [], status: "done" });
  assert.match(html, /No pipeline steps were recorded/);
  assert.doesNotMatch(html, /class="apt-row"/);
});

test("unknown event types are ignored rather than rendered as noise", async () => {
  const html = await render({
    events: [{ type: "totally_new_agent", payload: "x" }, ...wireEvents()],
    status: "done",
  });
  assert.doesNotMatch(html, /totally_new_agent/);
  assert.match(html, /class="apt-row"/);
});

test("a malformed frame does not crash the trace", async () => {
  const html = await render({
    events: [null, undefined, 42, { type: "search_query", results: null }, ...wireEvents()],
    status: "done",
  });
  assert.match(html, /<section class="apt\s*"/);
});
test("the final report can be collapsed again once shown", async () => {
  // Regression: the report row offered "Show report" but, once expanded, no way
  // to close it — the reader had to collapse the whole trace. Both controls must
  // exist on the final row.
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  assert.match(src, /Show report/, "the collapsed affordance must exist");
  assert.match(src, /"Hide report"/, "the expanded affordance must exist");
  // Both are wired to the same per-step toggle, so opening and closing act on
  // the same row state.
  assert.match(src, /isFinal\s*\?\s*"Hide report"/);
});

test("a final report renders with a collapse control when the trace is open", async () => {
  const html = await render({
    events: [
      { type: "plan", items: ["a"], __ts: 1 },
      { type: "final_report", report: "# Answer\n\nBody text.", __ts: 2 },
    ],
    status: "done",
  });
  assert.match(html, /Show report/, "the final row starts collapsed with a Show control");
});

// ---------------------------------------------------------------------------
// Inline markdown in the live trace.
//
// Regression: the trace had its OWN inline renderer that handled only URLs and
// quoted spans, so `**bold**`, `*italic*` and `` `code` `` reached the browser
// as literal asterisks — raw markdown printed in the middle of a streaming run.
// The answer card had a complete renderer; the trace did not. Both now share
// one implementation (trace/inline.ts).
// ---------------------------------------------------------------------------

/** The exact shape that shipped as raw text in the live trace. */
const RAW_MARKDOWN_STEP = [
  "**Checks performed:**",
  "",
  "1. **698 incidents / 183,420 transcripts / Oct 2025–Mar 2026 window** — consistent throughout (intro, section, conclusion). ✓",
  "",
  "2. **4.9x vs 1.7x comparison** — consistent throughout. ✓",
  "",
  "3. **Corpus rate ~0.38%** — 698/183,420 = 0",
].join("\n");

test("inline markdown renders instead of printing its markers", async () => {
  const { renderInline } = await loadInline();
  const html = renderToStaticMarkup(
    createElement("div", null, renderInline("**Checks performed:**"))
  );
  assert.match(html, /<strong>Checks performed:<\/strong>/);
  assert.doesNotMatch(html, /\*\*/, "no raw asterisks may reach the DOM");
});

test("bold, italic and code all render in one string", async () => {
  const { renderInline } = await loadInline();
  const html = renderToStaticMarkup(
    createElement("div", null, renderInline("the **4.9x vs 1.7x** *ratio* uses `bm25`"))
  );
  assert.match(html, /<strong>4\.9x vs 1\.7x<\/strong>/);
  assert.match(html, /<em>ratio<\/em>/);
  assert.match(html, /<code>bm25<\/code>/);
  assert.doesNotMatch(html, /`/);
});

test("a markdown link becomes an anchor with a safe target", async () => {
  const { renderInline } = await loadInline();
  const html = renderToStaticMarkup(
    createElement("div", null, renderInline("see [the paper](https://arxiv.org/abs/1234.56)"))
  );
  assert.match(html, /<a href="https:\/\/arxiv\.org\/abs\/1234\.56"/);
  assert.match(html, /rel="noreferrer noopener"/);
});

test("a non-http link target is not linkified", async () => {
  const { renderInline } = await loadInline();
  const html = renderToStaticMarkup(
    createElement("div", null, renderInline("[click](javascript:alert(1))"))
  );
  assert.doesNotMatch(html, /<a /, "only http(s) links may become anchors");
  assert.doesNotMatch(html, /href=/);
});

test("backend text cannot inject markup", async () => {
  const { renderInline } = await loadInline();
  const html = renderToStaticMarkup(
    createElement("div", null, renderInline("<img src=x onerror=alert(1)> and <b>raw</b>"))
  );
  // Rendered as TEXT, never as an element built from the payload. The escaped
  // string still contains the characters — that is correct; what must not
  // exist is a real <img> element or an onerror attribute in the output.
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /&lt;img/, "the payload must appear escaped");
});

test("incomplete markdown stays literal and stable while streaming", async () => {
  const { renderInline } = await loadInline();
  // Mid-arrival, the closing marker has not landed. Emitting literal text is
  // the honest render and — being a pure function of the string — is identical
  // across re-renders, so the row cannot flicker.
  const partial = renderToStaticMarkup(
    createElement("div", null, renderInline("**Checks perfor"))
  );
  assert.match(partial, /\*\*Checks perfor/);
  const again = renderToStaticMarkup(
    createElement("div", null, renderInline("**Checks perfor"))
  );
  assert.equal(partial, again, "an unchanged string must render unchanged");

  // Once the pair completes it becomes bold — the intended convergence.
  const complete = renderToStaticMarkup(
    createElement("div", null, renderInline("**Checks performed:**"))
  );
  assert.match(complete, /<strong>Checks performed:<\/strong>/);
});

test("ordered and bulleted prefixes are recognised and stripped", async () => {
  const { orderedItemIndex, stripOrderedItem, bulletItem, stripBullet } = await loadInline();
  assert.equal(orderedItemIndex("1. **698 incidents**"), 1);
  assert.equal(orderedItemIndex("3) also ordered"), 3);
  assert.equal(stripOrderedItem("2. **4.9x vs 1.7x comparison**"), "**4.9x vs 1.7x comparison**");
  assert.equal(orderedItemIndex("not a list line"), null);

  assert.equal(bulletItem("- finding"), true);
  assert.equal(bulletItem("* finding"), true);
  assert.equal(stripBullet("- finding"), "finding");
  assert.equal(bulletItem("finding"), false);
});

test("the whole reported step renders with no raw markdown anywhere", async () => {
  // The exact content that shipped as visible `**asterisks**` in the live
  // trace, delivered the way the stream delivers it.
  const html = await render({
    events: [
      {
        type: "findings",
        items: RAW_MARKDOWN_STEP.split("\n").map((claim) => ({ claim, verified: true })),
        __ts: 10,
      },
    ],
    status: "done",
  });
  assert.doesNotMatch(html, /\*\*/, "raw bold markers must not reach the DOM");
  assert.match(html, /<strong>Checks performed:<\/strong>/);
  assert.match(html, /<ol/, "the numbered lines must become a real list");
  assert.match(html, /<li>/);
  assert.doesNotMatch(html, /1\. <strong>/, "the literal 1. prefix must be consumed by <ol>");
});

test("the trace and the answer card share ONE inline renderer", async () => {
  // Two renderers for one job means one of them is always behind — which is
  // exactly how the trace ended up printing raw markdown while the answer card
  // rendered it correctly. Assert the answer card no longer defines its own.
  const src = await readFile(new URL("../src/components/AnswerCard.jsx", import.meta.url), "utf8");
  assert.match(src, /from "\.\.\/trace\/inline"/, "AnswerCard must import the shared renderer");
  assert.doesNotMatch(src, /function renderInline\(/, "AnswerCard must not redefine the renderer");
  assert.doesNotMatch(src, /const INLINE_RE/, "the inline regex must live in one module only");

  const traceSrc = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  assert.match(traceSrc, /from "\.\/inline"/, "the trace must use the shared renderer too");
});

// ---------------------------------------------------------------------------
// Block-level markdown in the trace.
//
// Regression: the trace grouped lines itself and only knew paragraphs and
// lists, so `## Heading` printed its markers. A critic's revise notes are full
// markdown, so "## Critical Errors" reached the browser literally. The block
// structure now comes from the shared parser (markdown.js), the same one the
// answer card uses.
// ---------------------------------------------------------------------------

test("a markdown heading in a step renders as a heading, not literal hashes", async () => {
  const html = await render({
    events: [{
      type: "critic", iteration: 1,
      reason: "Review round 1. Notes.\n\n## Critical Errors\n\nBody text here.",
      __ts: 1,
    }],
    status: "done",
  });
  assert.match(html, /<h[3-6] class="apt-prose apt-heading"/);
  assert.match(html, />Critical Errors</);
  assert.doesNotMatch(html, /##\s*Critical/);
});

test("a critic reason is not double-labelled for its round", async () => {
  const html = await render({
    events: [{ type: "critic", iteration: 1, reason: "Review round 1. Found issues.", __ts: 1 }],
    status: "done",
  });
  assert.equal((html.match(/Review round 1\./g) || []).length, 1);
});

test("a critic reason without a round prefix gets one", async () => {
  const html = await render({
    events: [{ type: "critic", iteration: 2, reason: "Found issues.", __ts: 1 }],
    status: "done",
  });
  assert.match(html, /Review round 2\./);
});

test("a multi-line progress message becomes a list body, not a flattened title", async () => {
  // The backend used to interpolate the raw Python list, printing
  // "['A', 'B']". It now sends markdown; this asserts the reducer routes a
  // bulky message to the BODY so the list renders, keeping a short label.
  const html = await render({
    events: [{
      type: "progress",
      message: "Researching 3 sections in parallel:\n- First section\n- Second section\n- Third section",
      __ts: 1,
    }],
    status: "running",
  });
  // ONE list of three items — not three single-item lists. Counting <li>
  // globally would also match the trace's own flow <ol>, so scope to the
  // prose list.
  const lists = [...html.matchAll(/<ol class="apt-prose apt-list"[^>]*>([\s\S]*?)<\/ol>/g)];
  assert.equal(lists.length, 1, "consecutive bullet lines must form ONE list");
  assert.equal((lists[0][1].match(/<li>/g) || []).length, 3);
  assert.match(html, /Researching 3 sections in parallel:/);
  assert.doesNotMatch(html, /\[\s*'/, "no raw list repr may reach the DOM");
});

test("a short progress message stays a one-line label", async () => {
  const html = await render({
    events: [{ type: "progress", message: "Searching the web", __ts: 1 }],
    status: "running",
  });
  assert.match(html, /apt-tool-label/);
  assert.doesNotMatch(html, /apt-list/);
});

test("markdown block structure comes from the shared parser", async () => {
  const src = await readFile(new URL("../src/trace/AgentPipelineTrace.tsx", import.meta.url), "utf8");
  assert.match(src, /import \{ parseBlocks \} from "\.\.\/markdown"/);
  // The local grouping helpers must be gone, or the two parsers drift again.
  for (const dead of ["orderedItemIndex", "stripOrderedItem", "bulletItem", "stripBullet"]) {
    assert.doesNotMatch(src, new RegExp(dead), `${dead} belonged to the local block grouping`);
  }
});

// ---------------------------------------------------------------------------
// Truncated content must not print dangling markdown markers.
//
// Regression: the adapter sliced the critic's notes to 400 characters, cutting
// mid-token. A review ending inside `**Lattice-based` left an unpaired `**` that
// can never resolve, so it sat in the UI as literal asterisks. The cap is gone,
// and a truncated note is now cleaned instead of printed.
// ---------------------------------------------------------------------------

test("an unpaired marker from truncated text is removed, not printed", async () => {
  const html = await render({
    events: [{
      type: "critic", iteration: 1,
      reason: "Review round 1. Checking sources:\n  - ESP32 heap figures — consistent. ✓\n  - **Lattice-based sc",
      __ts: 1,
    }],
    status: "done",
  });
  assert.doesNotMatch(html, /\*\*/, "an unpaired marker must not reach the DOM");
  assert.match(html, /Lattice-based sc/, "the visible words must survive");
});

test("paired markers and literal asterisks are left alone", async () => {
  const { trimDanglingMarkers } = await loadInline();
  // Paired spans must survive intact — a naive strip corrupted this.
  assert.equal(trimDanglingMarkers("**both** and `ok`"), "**both** and `ok`");
  assert.equal(trimDanglingMarkers("paired **a** and **b**"), "paired **a** and **b**");
  // A literal asterisk in prose is not a marker and must never be deleted.
  assert.equal(trimDanglingMarkers("rate is 5*3 per unit"), "rate is 5*3 per unit");
  assert.equal(trimDanglingMarkers("see note*"), "see note*");
  // An unpaired opener is stripped.
  assert.equal(trimDanglingMarkers("- **Lattice-based sc"), "- Lattice-based sc");
  assert.equal(trimDanglingMarkers("unpaired *only"), "unpaired only");
});

test("a truncated markdown link does not print its brackets", async () => {
  const { trimDanglingMarkers } = await loadInline();
  assert.equal(trimDanglingMarkers("see [the paper](https://arxiv.org/abs"), "see");
});

test("the critic reason is sent whole, not sliced in the adapter", async () => {
  // A hard slice is what cut a marker in half in the first place.
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(
    new URL("../../backend/app/runtime/event_adapter.py", import.meta.url), "utf8");
  assert.doesNotMatch(src, /reason=str\(notes\)\[:\d+\]/,
    "the critic reason must not be truncated with a character slice");
});

test("a quoted span keeps its chip while markdown still renders", async () => {
  // Regression from the shared-renderer refactor: swapping in renderInline
  // dropped the quoted-span chip, because the old renderer applied both in one
  // pass. Quotes are now applied AFTER markdown, so they compose.
  const html = await render({
    events: [{
      type: "critic", iteration: 1,
      reason: 'Checked the "quantum key distribution" claim and the **post-quantum** standard.',
      __ts: 1,
    }],
    status: "done",
  });
  assert.match(html, /<code class="apt-inline">quantum key distribution<\/code>/);
  assert.match(html, /<strong>post-quantum<\/strong>/);
  assert.doesNotMatch(html, /"quantum key distribution"/, "the quotes must be consumed");
});
