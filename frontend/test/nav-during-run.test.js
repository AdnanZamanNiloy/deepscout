/* Navigating away from a streaming run.
 *
 * Switching chats used to be refused outright while a run was live
 * (`if (replaying || running) return`), which pinned the user to one chat until
 * the run finished. Removing the guard alone is not enough: a live run patches
 * the single global `messages` list by tempId, so swapping that list would
 * orphan the run — its card would vanish and every later frame would silently
 * no-op. These assertions pin both halves of the fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const src = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");

test("switching chats is no longer refused while a run streams", () => {
  const openReplay = src.match(/const openReplay = useCallback\([\s\S]*?\n  \}, \[/);
  assert.ok(openReplay, "expected openReplay");
  assert.doesNotMatch(
    openReplay[0],
    /if \(replaying \|\| running\) return;/,
    "the running guard must be gone",
  );
  assert.match(openReplay[0], /if \(replaying\) return;/, "re-entrancy is still guarded");
});

test("a run's updates are routed to the session that owns it", () => {
  // patchRun / pushTrace / the raw-frame append must all go through the
  // owner-aware helper, or the run dies the moment the user looks away.
  assert.match(src, /const applyToOwner = useCallback/, "owner-aware apply helper exists");
  assert.match(
    src,
    /const owner = runOwnerRef\.current\.get\(tempId\)/,
    "it resolves the owning session",
  );
  assert.match(
    src,
    /bgMessagesRef\.current\.set\(owner, fn\(list\)\)/,
    "and writes to that session's offscreen list",
  );

  for (const [name, pattern] of [
    ["patchRun", /const patchRun = useCallback\(\[?[\s\S]*?applyToOwner\(tempId/],
    ["pushTrace", /const pushTrace = useCallback\(\[?[\s\S]*?applyToOwner\(tempId/],
  ]) {
    const m = src.match(pattern);
    assert.ok(m, `${name} must route through applyToOwner`);
  }
  // The raw-frame append is what the pipeline trace renders from.
  assert.match(
    src,
    /applyToOwner\(tempId,[\s\S]{0,200}traceEvents/,
    "trace frames must reach the owning session too",
  );
});

test("run ownership is recorded at launch", () => {
  assert.match(
    src,
    /runOwnerRef\.current\.set\(tempId, sessionIdRef\.current\)/,
    "a run must know which chat it belongs to",
  );
});

test("the offscreen cache is bounded — released when the run settles", () => {
  // A full message list per session, retained for the page's life, is the leak
  // the repo's own rules warn about. The server holds the authoritative copy
  // once a run finishes, so the entry must be dropped.
  assert.match(
    src,
    /runOwnerRef\.current\.delete\(settled\)/,
    "ownership record must be released",
  );
  assert.match(
    src,
    /bgMessagesRef\.current\.delete\(owner\)/,
    "the cached message list must be released",
  );
});

test("returning to the streaming chat restores it live, not stale", () => {
  const openReplay = src.match(/const openReplay = useCallback\([\s\S]*?\n  \}, \[/);
  assert.ok(openReplay);
  // Leaving: stash the chat being abandoned (whatever it currently is).
  assert.match(
    openReplay[0],
    /bgMessagesRef\.current\.set\(sessionIdRef\.current, messagesRef\.current\)/,
    "the chat being left behind is stashed",
  );
  // Returning: read the target back out, but only when it is the live run's.
  assert.match(
    openReplay[0],
    /const cached = bgMessagesRef\.current\.get\(targetSessionId\)/,
    "the target chat is read back from cache",
  );
  assert.match(
    openReplay[0],
    /runOwnerRef\.current\.get\(live\) === targetSessionId/,
    "only when it is genuinely the live run",
  );
  // ...otherwise fall through to a server fetch.
  assert.match(openReplay[0], /await fetchSession\(targetSessionId\)/);
});

/* The composer during a live run.
 *
 * The textarea used to be `disabled={running}`, so a user watching a run could
 * not draft a follow-up — they could only wait. The requirement is narrower:
 * the field must accept text, while SENDING stays gated so a draft cannot
 * silently queue behind the active run.
 */
test("the composer accepts text while a run streams", async () => {
  const composer = await readFile(new URL("../src/components/Composer.jsx", import.meta.url), "utf8");
  const area = composer.match(/<textarea[\s\S]*?\/>/);
  assert.ok(area, "expected the textarea");
  assert.doesNotMatch(area[0], /disabled/, "the field must not be disabled during a run");
  assert.doesNotMatch(area[0], /readOnly/, "nor read-only");
});

test("sending stays blocked while a run streams", async () => {
  const composer = await readFile(new URL("../src/components/Composer.jsx", import.meta.url), "utf8");
  // canSend gates the button, submit gates Enter and clicks.
  assert.match(composer, /const canSend = value\.trim\(\)\.length >= 5 && !running;/);
  assert.match(composer, /if \(v\.length < 5 \|\| running\) return;/);
  // While running, Enter must fall through to a newline rather than being
  // preventDefault'd into doing nothing at all.
  assert.match(composer, /if \(running\) return;\s*\n\s*e\.preventDefault\(\);/);
});

test("a queued draft says why it cannot be sent", async () => {
  const composer = await readFile(new URL("../src/components/Composer.jsx", import.meta.url), "utf8");
  assert.match(
    composer,
    /running && value\.trim\(\)\.length >= 5/,
    "the hint appears once there is something to send",
  );
  assert.match(composer, /Stop the current run to send this question\./);
  assert.match(
    composer,
    /stop the run to send/,
    "and the placeholder explains it before typing starts",
  );
});
