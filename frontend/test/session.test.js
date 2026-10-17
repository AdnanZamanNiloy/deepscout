/* Chat-session persistence regression tests (frontend helpers).
 *
 * The bug: every question created a new session and renamed the chat. These
 * lock in the helper contract that App.jsx relies on — one stable active
 * session id, a chat list keyed by sessionId (not runId), a stable title
 * across follow-ups, and New Chat minting a new id.
 *
 * Runs on Node's built-in test runner with a tiny localStorage stub, so no
 * new dependency and no browser is required.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};

const {
  newSessionId,
  loadActiveSessionId,
  saveActiveSessionId,
  upsertMission,
  updateMission,
  removeMission,
  loadMissions,
  formatTime,
} = await import("../src/lib.js");

test("active session id persists across reload", () => {
  store.clear();
  assert.equal(loadActiveSessionId(), "");
  saveActiveSessionId("chat-1");
  // Simulate reload: read straight from the stub storage.
  assert.equal(loadActiveSessionId(), "chat-1");
});

test("newSessionId is non-empty and unique", () => {
  const a = newSessionId();
  const b = newSessionId();
  assert.ok(a && typeof a === "string");
  assert.notEqual(a, b);
});

test("two runs in one chat produce ONE mission keyed by sessionId", () => {
  store.clear();
  upsertMission({ sessionId: "chat-1", runId: "run-a", query: "q1", status: "running" });
  upsertMission({ sessionId: "chat-1", runId: "run-b", query: "q2", status: "completed" });
  const list = loadMissions();
  assert.equal(list.length, 1, "follow-up must not create a second chat");
  assert.equal(list[0].sessionId, "chat-1");
  // Latest run is recorded on the same session.
  assert.equal(list[0].runId, "run-b");
});

test("a different sessionId creates a separate chat (New Chat)", () => {
  store.clear();
  upsertMission({ sessionId: "chat-1", runId: "run-a", query: "q1" });
  upsertMission({ sessionId: "chat-2", runId: "run-b", query: "q2" });
  const ids = loadMissions().map((m) => m.sessionId).sort();
  assert.deepEqual(ids, ["chat-1", "chat-2"]);
});

test("multiple follow-ups keep the original title source", () => {
  store.clear();
  upsertMission({ sessionId: "chat-1", runId: "run-a", query: "what is RAG?", status: "running" });
  upsertMission({ sessionId: "chat-1", runId: "run-b", query: "and how does it work?", status: "completed" });
  upsertMission({ sessionId: "chat-1", runId: "run-c", query: "give an example", status: "completed" });
  const m = loadMissions().find((x) => x.sessionId === "chat-1");
  assert.equal(m.query, "what is RAG?", "title falls back to the FIRST message");
  assert.equal(m.title, "", "no explicit title supplied on any message");
  // Latest run still tracked for trace/replay.
  assert.equal(m.runId, "run-c");
});

test("an explicit title is never overwritten by follow-ups", () => {
  store.clear();
  upsertMission({ sessionId: "chat-1", runId: "run-a", query: "q1", title: "Renamed chat" });
  upsertMission({ sessionId: "chat-1", runId: "run-b", query: "q2" });
  const m = loadMissions().find((x) => x.sessionId === "chat-1");
  assert.equal(m.title, "Renamed chat");
  assert.equal(m.query, "q1");
});

test("a brand-new session takes the first message as its title source", () => {
  store.clear();
  upsertMission({ sessionId: "chat-9", runId: "run-1", query: "first ever message" });
  const m = loadMissions().find((x) => x.sessionId === "chat-9");
  assert.equal(m.query, "first ever message");
});

/* Regression: replayed user turns passed no timestamp, and `new Date(undefined)`
 * yields an Invalid Date whose toLocaleTimeString is the literal string
 * "Invalid Date" (it does NOT throw, so the old try/catch never caught it).
 * The bubble rendered that text. formatTime must now return "" for anything
 * that is not a real date. */
test("formatTime renders a real date, never the literal 'Invalid Date'", () => {
  const out = formatTime("2026-09-20T13:02:15.294424+00:00");
  assert.ok(/\d{1,2}:\d{2}\s?(AM|PM)/i.test(out), `expected a clock time, got ${JSON.stringify(out)}`);
  assert.ok(!/invalid/i.test(out));
});

test("formatTime returns empty string for missing/blank/invalid input", () => {
  for (const bad of [undefined, null, "", "not-a-date", NaN]) {
    const out = formatTime(bad);
    assert.equal(out, "", `formatTime(${JSON.stringify(bad)}) must be "" — got ${JSON.stringify(out)}`);
  }
});

test("formatTime accepts a Date instance unchanged", () => {
  const out = formatTime(new Date("2026-09-20T13:02:00Z"));
  assert.ok(/\d{1,2}:\d{2}\s?(AM|PM)/i.test(out));
});

test("updateMission and removeMission target the session, not the run", () => {
  store.clear();
  upsertMission({ sessionId: "chat-1", runId: "run-a", query: "q1" });
  upsertMission({ sessionId: "chat-2", runId: "run-b", query: "q2" });
  updateMission("chat-1", { title: "renamed", pinned: true });
  const afterUpdate = loadMissions();
  const c1 = afterUpdate.find((m) => m.sessionId === "chat-1");
  assert.equal(c1.title, "renamed");
  assert.equal(c1.pinned, true);
  // Removing chat-1 leaves chat-2 untouched.
  const afterRemove = removeMission("chat-1");
  assert.deepEqual(afterRemove.map((m) => m.sessionId), ["chat-2"]);
});

/* ---- the visible list must always be the cached list ----
 *
 * `updateMission` / `removeMission` read and write localStorage, while the
 * backend session sync produced missions that existed in React state ONLY. A
 * rename, pin or delete on one of those read an empty cache and wrote it
 * straight back, so the rail emptied — pinning one of 30 sessions deleted all
 * 30. Reproduced live, then fixed by persisting whatever is visible. */
const { persistMissions } = await import("../src/lib.js");

test("persistMissions caches exactly the list it is given", () => {
  store.clear();
  persistMissions([
    { sessionId: "chat-1", query: "q1" },
    { sessionId: "chat-2", query: "q2" },
  ]);
  assert.deepEqual(loadMissions().map((m) => m.sessionId), ["chat-1", "chat-2"]);
});

test("persistMissions never grows the cache past the 30-session cap", () => {
  store.clear();
  persistMissions(Array.from({ length: 45 }, (_, i) => ({ sessionId: `c${i}` })));
  assert.equal(loadMissions().length, 30, "an unbounded cache is how RUNTIME_STATE leaked");
});

test("a mutation on a freshly cached session cannot empty the list", () => {
  // The exact failure: the sync wrote 30 missions into state, the cache was
  // empty, and updateMission persisted the empty list back over it.
  store.clear();
  const synced = Array.from({ length: 30 }, (_, i) => ({ sessionId: `s${i}`, runId: `r${i}` }));
  persistMissions(synced);                       // what the sync now does
  updateMission("s0", { pinned: true });         // what a pin does
  const after = loadMissions();
  assert.equal(after.length, 30, "pinning one session must not delete the other 29");
  assert.equal(after.find((m) => m.sessionId === "s0").pinned, true);
});

test("removeMission on a cached session removes exactly that one", () => {
  store.clear();
  persistMissions(Array.from({ length: 5 }, (_, i) => ({ sessionId: `s${i}` })));
  const after = removeMission("s2");
  assert.equal(after.length, 4);
  assert.equal(after.find((m) => m.sessionId === "s2"), undefined);
});
