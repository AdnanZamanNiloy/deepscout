/* Session rows must be actionable from the rail.
 *
 * The "…" per-row menu was removed when the rail was flattened, on the
 * reasoning that session management belongs on the session header. That left
 * the rail as a list you could read but not act on — a worse index than one
 * that can — so it is back, with the same three actions and the same handlers.
 *
 * The row and the "…" are SIBLING buttons rather than a control nested in the
 * row: the row must stay one button so it keeps a single hit target and a
 * single accessible name (the title). A nested button is invalid HTML and is
 * unreachable by keyboard.
 *
 * Sidebar is .jsx, so it is bundled with esbuild and rendered for real — these
 * assertions read the markup the browser gets, not a scan of the source.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { build } from "esbuild";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

const ENTRY = new URL("../src/components/Sidebar.jsx", import.meta.url).pathname;

let modPromise = null;
function loadSidebar() {
  if (modPromise) return modPromise;
  modPromise = (async () => {
    const out = await build({
      entryPoints: [ENTRY],
      bundle: true,
      write: false,
      format: "esm",
      jsx: "automatic",
      target: "node20",
      external: ["react", "react-dom", "react/jsx-runtime"],
      loader: { ".svg": "text" },
      logLevel: "silent",
    });
    const dir = join(dirname(ENTRY), "..", "..", "node_modules", ".sidebar-test");
    await mkdir(dir, { recursive: true });
    const file = join(dir, "sidebar.mjs");
    await writeFile(file, out.outputFiles[0].text);
    return import(file);
  })();
  return modPromise;
}

const MISSIONS = [
  { sessionId: "chat-1", runId: "run-a", query: "Does collaborative debate help?", status: "completed" },
  { sessionId: "chat-2", runId: "run-b", query: "Can dynamic role assignment help?", status: "completed" },
];

async function renderSidebar(props = {}) {
  const { default: Sidebar } = await loadSidebar();
  return renderToStaticMarkup(createElement(Sidebar, {
    view: "workspace",
    onNavigate() {},
    missions: MISSIONS,
    activeSessionId: "chat-1",
    onOpenMission() {},
    onNew() {},
    open: true,
    ...props,
  }));
}

/** Inner HTML of the first row button — used to prove nothing is nested in it.
 *  Attribute order is React's, not ours: `type` precedes `class`. */
function firstRowInner(html) {
  const m = /<button[^>]*class="session-row[^"]*"[^>]*>([\s\S]*?)<\/button>/.exec(html);
  assert.ok(m, "expected a session row button");
  return m[1];
}

test("every session row has its own actions button", async () => {
  const html = await renderSidebar();
  const labels = [...html.matchAll(/aria-label="Actions for ([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(labels, MISSIONS.map((m) => m.query));
});

test("the actions button is a sibling of the row, never nested inside it", async () => {
  const html = await renderSidebar();
  const inner = firstRowInner(html);
  assert.doesNotMatch(inner, /<button/,
    "a button inside the row is invalid HTML and unreachable by keyboard");
  // Both exist in the same list item, which is what makes them siblings.
  assert.match(html, /<li class="session-row-wrap[^"]*">[\s\S]*?session-row[\s\S]*?session-menu-btn/);
});

test("the actions button declares itself a menu and reports its state", async () => {
  const html = await renderSidebar();
  assert.match(html, /[^>]*class="session-menu-btn"[^>]*aria-haspopup="menu"/);
  assert.match(html, /class="session-menu-btn"[^>]*aria-expanded="false"/,
    "a closed menu must not claim to be expanded");
});

test("the row keeps its title as the tooltip and the active row its aria-current", async () => {
  const html = await renderSidebar();
  assert.match(html, /class="session-row active"[^>]*aria-current="true"/);
  assert.match(html, /title="Does collaborative debate help\?"/);
});

test("no menu is rendered until one is opened", async () => {
  const html = await renderSidebar();
  assert.doesNotMatch(html, /session-row-menu/, "the popup must not exist while closed");
  // The three actions still exist in the component; they are what the popup holds.
  assert.match(html, /session-menu-btn/);
});

test("the actions are the sessions' own, wired by sessionId", async () => {
  // Pin/Rename/Delete must key on sessionId: updateMission and removeMission
  // match on it. The header menu used to pass `mission.runId`, so its three
  // actions silently did nothing — pinned/deleting by run id matched no session.
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(ENTRY, "utf8");
  for (const action of ["onTogglePin?.(m.sessionId)", "onRename?.(m.sessionId, clean)", "onDelete?.(m.sessionId)"]) {
    assert.ok(src.includes(action), `Sidebar must call ${action}`);
  }
  assert.doesNotMatch(src, /on(?:Rename|TogglePin|Delete)\?\.\(m\.runId\)/,
    "no action may be keyed by runId");
});