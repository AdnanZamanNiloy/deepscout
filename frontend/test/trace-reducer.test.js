/* Pipeline-trace reducer tests.
 *
 * The contract these lock down: the timeline is built ONLY from real wire
 * events. An unknown or empty stream must yield an empty timeline — never a
 * plausible-looking placeholder story. A trace that invents steps is worse
 * than no trace, because the reader believes it.
 *
 * Runs on Node's built-in test runner with native type stripping.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

const { buildTrace, summarize, hostOf, isKnownFrame } = await import(
  "../src/trace/events.ts"
);

const hits = (n, host) =>
  Array.from({ length: n }, (_, i) => ({
    title: `Doc ${i + 1} about the topic`,
    url: `https://www.${host}.com/article-${i + 1}`,
    source: host,
  }));

test("an empty stream produces an empty timeline, never placeholder steps", () => {
  assert.deepEqual(buildTrace([]), []);
});

test("unknown event types are ignored rather than rendered as noise", () => {
  const steps = buildTrace([
    { type: "progress", message: "Query received", __ts: 1 },
    { type: "some_future_event", payload: { a: 1 } },
    { type: "another_new_thing", detail: "x" },
  ]);
  assert.equal(steps.length, 1, "only the known event becomes a step");
  assert.equal(steps[0].title, "Run started");
});

test("isKnownFrame accepts known types and rejects unknown ones", () => {
  assert.equal(isKnownFrame({ type: "intent" }), true);
  assert.equal(isKnownFrame({ type: "plan_review" }), false);
});

test("reasoning prose comes from the real intent fields", () => {
  const [step] = buildTrace([
    {
      type: "intent",
      query_type: "analytical",
      domain: "economics",
      explanation_level: "practical",
      ambiguity: true,
      __ts: 10,
    },
  ]);
  assert.equal(step.title, "Understand the question");
  assert.match(step.content, /analytical/);
  assert.match(step.content, /economics/);
  assert.match(step.content, /ambiguous/i, "ambiguity must surface from the wire");
});

test("the plan becomes a to-do step carrying the real sub-questions", () => {
  const [step] = buildTrace([
    { type: "plan", items: ["q one", "q two", "q three"], __ts: 20 },
  ]);
  assert.equal(step.kind, "todo");
  assert.equal(step.title, "Update to-do list");
  assert.match(step.content, /3 lines/);
  assert.deepEqual(step.children.map((c) => c.title), ["q one", "q two", "q three"]);
});

test("each search_query becomes a step with real result chips", () => {
  const steps = buildTrace([
    { type: "search_query", query: "battery storage 2025 additions", results: hits(3, "iea"), __ts: 30 },
    { type: "search_query", query: "lithium cell prices", results: hits(2, "bloomberg"), __ts: 31 },
  ]);
  assert.equal(steps.length, 2, "one step per real query, never merged");
  assert.match(steps[0].content, /battery storage 2025 additions/);
  assert.equal(steps[0].chips.length, 3);
  assert.equal(steps[0].chips[0].label, "iea.com", "chip label is the hostname");
  assert.equal(steps[1].chips.length, 2);
});

test("the aggregate counter is superseded once per-query detail arrives", () => {
  const steps = buildTrace([
    { type: "search_progress", snippets: 12, __ts: 29 },
    { type: "search_query", query: "q", results: hits(2, "a"), __ts: 30 },
  ]);
  assert.ok(
    !steps.some((s) => s.title === "Gathering sources"),
    "the 'N sources' counter must not linger once real queries are known",
  );
});

test("findings become an evidence step with per-claim children", () => {
  const [step] = buildTrace([
    {
      type: "findings",
      items: [
        { claim: "Additions reached 108 GW", source: "https://iea.org/x", verified: true },
        { claim: "Pack prices fell", source: "https://bnef.com/y", verified: false },
      ],
      __ts: 40,
    },
  ]);
  assert.equal(step.kind, "evidence");
  assert.equal(step.children.length, 2);
  assert.equal(step.children[0].status, "completed", "verified claims are done");
  assert.equal(step.children[1].status, "pending", "unverified claims are not");
});

test("verified re-emission is labelled distinctly", () => {
  const [step] = buildTrace([
    { type: "findings", verified_update: true, items: [{ claim: "c", source: "s" }], __ts: 41 },
  ]);
  assert.equal(step.title, "Verify extracted claims");
});

test("a critique round is a gate step carrying the real reason", () => {
  const [step] = buildTrace([
    { type: "critic", iteration: 2, reason: "evidence too thin", __ts: 50 },
  ]);
  assert.equal(step.kind, "gate");
  assert.match(step.content, /round 2/);
  assert.match(step.content, /evidence too thin/);
});

test("an error attaches to the running step rather than orphaning a row", () => {
  const steps = buildTrace([
    { type: "search_progress", snippets: 3, __ts: 60 },
    { type: "error", message: "Research workflow failed: boom", __ts: 61 },
  ]);
  assert.equal(steps.length, 1, "no orphan error row");
  assert.equal(steps[0].status, "error");
  assert.match(steps[0].error, /boom/);
});

test("an error with nothing running still surfaces", () => {
  const steps = buildTrace([{ type: "error", message: "pre-flight failed", __ts: 70 }]);
  assert.equal(steps.length, 1);
  assert.equal(steps[0].status, "error");
});

test("timestamps prefer the client's arrival stamp over render time", () => {
  const [step] = buildTrace([{ type: "progress", message: "go", __ts: 12345 }], {
    now: () => 999,
  });
  assert.equal(step.timestamp, 12345);
});

test("timestamps fall back to now() when no stamp was supplied", () => {
  const [step] = buildTrace([{ type: "progress", message: "go" }], { now: () => 777 });
  assert.equal(step.timestamp, 777);
});

test("'Less steps' keeps milestones and drops intermediate reasoning", () => {
  const steps = buildTrace([
    { type: "intent", query_type: "factual", __ts: 1 },
    { type: "route", path: "research", reason: "needs evidence", __ts: 2 },
    { type: "plan", items: ["q"], __ts: 3 },
    { type: "final_report", report: "the answer", __ts: 4 },
  ]);
  const less = summarize(steps);
  assert.ok(less.length < steps.length, "summaries must be shorter");
  assert.ok(less.some((s) => s.kind === "final"), "the answer is never hidden");
  assert.ok(less.some((s) => s.kind === "todo"), "the plan is a milestone");
});

test("summarize falls back to everything when every step is minor", () => {
  const steps = buildTrace([
    { type: "intent", query_type: "factual", __ts: 1 },
    { type: "route", path: "research", __ts: 2 },
  ]);
  assert.equal(summarize(steps).length, steps.length, "never render an empty trace");
});

test("hostOf strips www and tolerates junk", () => {
  assert.equal(hostOf("https://www.forbes.com/a/b"), "forbes.com");
  assert.equal(hostOf("http://iea.org"), "iea.org");
  assert.equal(hostOf("not a url"), "");
  assert.equal(hostOf(""), "");
});

test("a repeated search_query merges into one step instead of duplicating", () => {
  // The backend re-emits a query as its results arrive. Appending would show
  // one "Search the web" row per emission rather than one per real search.
  const steps = buildTrace([
    { type: "search_query", query: "q one", results: [], __ts: 10 },
    { type: "search_query", query: "q one", results: hits(2, "iea"), __ts: 11 },
    { type: "search_query", query: "q two", results: hits(1, "bnef"), __ts: 12 },
  ]);
  const searches = steps.filter((s) => s.kind === "tool" && s.title === "Search the web");
  assert.equal(searches.length, 2, "two real searches, two steps");
  const merged = searches.find((s) => s.query === "q one");
  assert.equal(merged.chips.length, 2, "chips accumulate onto the existing step");
  assert.match(merged.children[0].content, /iea\.com/);
});

test("merging de-duplicates repeated URLs", () => {
  const same = hits(2, "iea");
  const steps = buildTrace([
    { type: "search_query", query: "q", results: same, __ts: 20 },
    { type: "search_query", query: "q", results: same, __ts: 21 },
  ]);
  const step = steps.find((s) => s.query === "q");
  assert.equal(step.chips.length, 2, "the same URLs must not double up");
});

test("intent prose does not produce 'a analytical'", () => {
  const [step] = buildTrace([
    { type: "intent", query_type: "analytical", domain: "engineering", __ts: 30 },
  ]);
  assert.doesNotMatch(step.content, /\ba analytical\b/);
  assert.match(step.content, /Read this as analytical question/);
  assert.match(step.content, /engineering domain/);
});
