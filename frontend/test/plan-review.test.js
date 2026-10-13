/* Interactive plan review (human-in-the-loop).
 *
 * When a run is started with `hitl`, the backend pauses after planning and
 * streams a `plan_review` frame carrying the proposed outlines. The UI must:
 *   - accept the frame type (not drop it as unknown),
 *   - store it on the run so PlanReviewCard renders,
 *   - POST the decision to /api/research/{runId}/plan.
 *
 * App.jsx / components are .jsx (plain Node cannot import them), so these
 * assertions scan the source, matching the other UI-contract tests.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = readFileSync(join(HERE, "..", "src", "App.jsx"), "utf8");
const API = readFileSync(join(HERE, "..", "src", "api.js"), "utf8");
const CARD = readFileSync(join(HERE, "..", "src", "components", "PlanReviewCard.jsx"), "utf8");
const EVENTS = readFileSync(join(HERE, "..", "src", "trace", "events.ts"), "utf8");

test("the plan_review frame is a known wire event", () => {
  assert.match(EVENTS, /type: "plan_review"/);
  assert.match(EVENTS, /"plan_review",/);
});

test("the app stores the plan review on the run and renders the card", () => {
  assert.match(APP, /case "plan_review":/);
  assert.match(APP, /planReview: \{/);
  assert.match(APP, /<PlanReviewCard/);
  assert.match(APP, /run\.planReview \?/);
});

test("startResearch sends the hitl flag", () => {
  assert.match(API, /hitl: hitl \?\? null/);
  assert.match(APP, /startResearch\(\{[^}]*hitl/);
});

test("the decision is POSTed to the plan endpoint", () => {
  assert.match(API, /export async function submitPlanDecision/);
  assert.match(API, /\/api\/research\/\$\{encodeURIComponent\(runId\)\}\/plan/);
  assert.match(API, /body: JSON\.stringify\(\{ action, sections, feedback \}\)/);
});

test("the card offers selectable sections, a step nav, and a direction field", () => {
  assert.match(CARD, /role="group"/);
  assert.match(CARD, /type="checkbox"/);
  assert.match(CARD, /const selected = list\.filter/);
  assert.match(CARD, /className="pr-add"/);
  assert.match(CARD, /action: "approve"/);
  assert.match(CARD, /action: "revise"/);
});

test("the card steps between outlines and commits on the last step", () => {
  assert.match(CARD, /\{step \+ 1\} of \{total\}/);
  assert.match(CARD, /const isLast = total <= 1 \|\| step >= total - 1;/);
  // Navigating away and back must not discard what was ticked.
  assert.match(CARD, /const \[lists, setLists\] = useState\(\(\) => alternatives\.map\(\(alt\) => \[\.\.\.alt\]\)\)/);
  assert.match(CARD, /const \[picked, setPicked\] = useState/);
});

test("a revised plan discards the picks made for the outline it replaced", () => {
  // Re-requesting review reuses the run id, so the card has to key on the
  // per-request identity instead: otherwise the user approves sections that
  // no longer exist.
  assert.match(APP, /requestId: typeof evt\.ts === "number" \? evt\.ts : null/);
  assert.match(CARD, /\}, \[review\?\.requestId, review\?\.runId, total\]\);/);
});

test("every proposed section starts selected so approve means approve-as-proposed", () => {
  assert.match(CARD, /useState\(\(\) => alternatives\.map\(\(alt\) => new Set\(alt\)\)\)/);
});

test("closing the card starts research instead of leaving the run paused", () => {
  assert.match(CARD, /onClick=\{approve\}[\s\S]{0,320}?aria-label="Skip the review and start research with this outline"/);
});

test("an expired review window closes the card instead of stranding it", () => {
  // The reported bug: the user never approved, the run continued on its own,
  // and the live approval card stayed pinned above the finished report. Three
  // separate exits have to close it — expiry, failure, and the run settling.
  assert.match(APP, /if \(evt\.status === "expired"\) \{\s*patchRun\(tempId, \{ planReview: null \}\);/);
  assert.match(APP, /const run = \{ \.\.\.m\.run, error: message, resuming: false, resumable, planReview: null \};/);
  assert.match(APP, /const run = \{ \.\.\.m\.run, aborted: true, resuming: false, planReview: null \};/);
  assert.match(APP, /if \(settled\) patchRun\(tempId, \{ planReview: null \}\);/);
});

test("the review deadline is the server's, shown as a countdown", () => {
  assert.match(APP, /deadlineTs: typeof evt\.deadline_ts === "number" \? evt\.deadline_ts : null/);
  assert.match(CARD, /function secondsLeft\(deadlineTs\)/);
  assert.match(CARD, /Starts automatically in \$\{formatCountdown\(remaining\)\}/);
  assert.match(CARD, /Review window closed — continuing automatically\./);
});

test("a closed window stops the card accepting a decision nobody will read", () => {
  assert.match(CARD, /const closed = Number\.isFinite\(deadlineTs\) && remaining <= 0;/);
  const ungated = CARD.match(/disabled=\{submitting(?! \|\| closed)/g) || [];
  assert.deepEqual(ungated, [], "every control must also respect the deadline");
});

test("the stage frame is a known wire event", () => {
  // A backend event type with no frontend case is silently dropped
  // (applyEvent has a `default: break`), which would leave the rail blind
  // again — exactly the desync this frame exists to fix.
  assert.match(EVENTS, /type: "stage"/);
  assert.match(EVENTS, /"stage",/);
});

test("the app stores stage lifecycle frames on the run", () => {
  assert.match(APP, /case "stage":/);
  assert.match(APP, /status: running \? "running" : "done"/);
  assert.match(APP, /stages: \{\},/);
});

test("patchRun accepts an updater so the stage map merges safely", () => {
  assert.match(APP, /typeof patch === "function" \? patch\(m\.run\) : patch/);
});

test("a plan_review frame only opens the card when it is awaiting an outline", () => {
  // Regression: a restored run carries legacy frames written before `status`
  // existed. They arrive as `done` with no alternatives, and treating "not
  // expired" as "awaiting" rendered an EMPTY card — no sections, nothing to
  // approve, dead Start button.
  assert.match(APP, /if \(evt\.status !== "awaiting"\) break;/);
  assert.match(APP, /if \(!Array\.isArray\(evt\.alternatives\) \|\| !evt\.alternatives\.length\) break;/);
});

test("the card renders nothing rather than an empty outline", () => {
  // The second line of defence: even if a review object somehow reaches the
  // card with no sections, it must not draw a choice list with zero choices.
  assert.match(CARD, /const alternatives = \(review\?\.alternatives \|\| \[\]\)\.filter\(/);
  assert.match(CARD, /if \(!alternatives\.length\) return null;/);
});
