/* Agent-flow transform tests.
 *
 * The contract these lock down: the reader-facing timeline is built ONLY from
 * real wire events, and folding frames into phases never loses or invents data.
 *
 * The two failure modes this guards against are the expensive ones:
 *  - a trace that INVENTS a plausible-looking search story (worse than no
 *    trace, because the reader believes it), and
 *  - a fold that DISCARDS evidence to make the view look tidy — the reader
 *    would conclude nothing was found when plenty was.
 *
 * Runs on Node's built-in test runner with native type stripping.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

const { transformToDeerFlowSteps, flattenRawSteps, domainsOf } = await import(
  "../src/trace/flow.ts"
);

const hit = (host, i = 1) => ({
  title: `Doc ${i} on the topic`,
  url: `https://www.${host}.com/article-${i}`,
  source: "tavily",
});

const finding = (claim, source, extra = {}) => ({
  claim,
  source,
  agent: "extractor",
  ...extra,
});

const frames = (...evs) => evs.map((e, i) => ({ __ts: 1000 + i * 100, ...e }));

// --- honesty ---------------------------------------------------------------

test("an empty stream produces no phases, never placeholder ones", () => {
  assert.deepEqual(transformToDeerFlowSteps([]), []);
});

test("unknown event types produce no phases rather than noise", () => {
  const steps = transformToDeerFlowSteps(frames({ type: "quantum_telemetry", payload: {} }));
  assert.deepEqual(steps, []);
});

test("a phase is only created by real events for it", () => {
  // A run that only ever searched has no plan or review phase to claim.
  const steps = transformToDeerFlowSteps(
    frames({ type: "search_query", query: "grid storage 2026", results: [hit("iea", 1)] }),
  );
  assert.equal(steps.length, 1);
  assert.equal(steps[0].kind, "research");
  assert.deepEqual(steps.map((s) => s.kind), ["research"]);
});

test("titles are derived from the wire, not from a fixed template", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "plan", items: ["a", "b", "c", "d"] },
      { type: "critic", iteration: 1, reason: "thin" },
      { type: "critic", iteration: 2, reason: "better" },
    ),
  );
  assert.equal(steps[0].title, "Planned 4 investigation angles");
  assert.equal(steps[1].title, "Evidence review · 2 rounds");
});

// --- grouping --------------------------------------------------------------

test("many searches collapse into one research phase with one child per query", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      ...Array.from({ length: 6 }, (_, i) => ({
        type: "search_query",
        query: `query ${i + 1}`,
        results: [hit("iea", i + 1)],
      })),
    ),
  );
  assert.equal(steps.length, 1, "six searches must not become six phases");
  const research = steps[0];
  assert.equal(research.stats.queries, 6);
  assert.equal(research.details.length, 6);
});

test("a re-emitted query merges instead of duplicating", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "search_query", query: "grid storage", results: [hit("iea", 1)] },
      { type: "search_query", query: "grid storage", results: [hit("iea", 2)] },
    ),
  );
  assert.equal(steps[0].details.length, 1, "one query is one child");
  assert.equal(steps[0].details[0].hits.length, 2, "results accumulate on it");
});

test("a repeated URL within one query is not counted twice", () => {
  const steps = transformToDeerFlowSteps(
    frames({
      type: "search_query",
      query: "grid storage",
      results: [hit("iea", 1), hit("iea", 1)],
    }),
  );
  assert.equal(steps[0].details[0].hits.length, 1);
});

test("the aggregate counter frame is a stat, not a search of its own", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "search_progress", snippets: 12 },
      { type: "search_query", query: "", results: [], issued: 7 },
      { type: "search_query", query: "grid storage", results: [hit("iea", 1)] },
    ),
  );
  assert.equal(steps.length, 1);
  assert.equal(steps[0].details.length, 1, "the counter must not become a child");
  assert.equal(steps[0].stats.snippets, 12);
  assert.equal(steps[0].stats.issued, 7, "issued count is still reported");
});

test("a stale lower counter tick does not rewind the source count", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "search_progress", snippets: 40 },
      { type: "search_progress", snippets: 9 },
    ),
  );
  assert.equal(steps[0].stats.snippets, 40);
});

test("domains are counted per host and ranked by frequency", () => {
  const d = domainsOf([
    hit("iea", 1),
    hit("iea", 2),
    hit("reuters", 1),
    hit("reuters", 2),
    hit("reuters", 3),
    hit("bloomberg", 1),
  ]);
  assert.deepEqual(d.map((x) => [x.label, x.count]), [
    ["reuters.com", 3],
    ["iea.com", 2],
    ["bloomberg.com", 1],
  ]);
});

// --- evidence --------------------------------------------------------------

test("re-emitted claims merge, and verification annotations win", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "findings", items: [finding("Additions hit 108 GW.", "https://iea.org/a")] },
      {
        type: "findings",
        verified_update: true,
        items: [
          finding("Additions hit 108 GW.", "https://iea.org/a", {
            verified: true,
            verification_score: 0.91,
          }),
        ],
      },
    ),
  );
  const claims = steps[0].details[0].claims;
  assert.equal(claims.length, 1, "the same claim must not be counted twice");
  assert.equal(claims[0].verified, true);
  assert.equal(claims[0].verification_score, 0.91);
});

test("a later merge never erases a reason already recorded", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      {
        type: "findings",
        verified_update: true,
        items: [finding("Claim one.", "https://iea.org/a", { verification_reason: "matches source" })],
      },
      { type: "findings", items: [finding("Claim one.", "https://iea.org/a", { verified: false })] },
    ),
  );
  const c = steps[0].details[0].claims[0];
  assert.equal(c.verification_reason, "matches source");
});

test("claim text differing only in punctuation is the same claim", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "findings", items: [finding("Storage grew 40% in 2025!", "https://iea.org/a")] },
      { type: "findings", verified_update: true, items: [finding("Storage grew 40% in 2025", "https://iea.org/a", { verified: true })] },
    ),
  );
  assert.equal(steps[0].details[0].claims.length, 1);
  assert.equal(steps[0].stats.verified, 1);
});

test("the evidence headline reports verified and unconfirmed honestly", () => {
  const steps = transformToDeerFlowSteps(
    frames({
      type: "findings",
      verified_update: true,
      items: [
        finding("A", "https://iea.org/a", { verified: true }),
        finding("B", "https://iea.org/b", { verified: true }),
        finding("C", "https://reuters.com/c"),
      ],
    }),
  );
  assert.equal(steps[0].title, "Verified 2 of 3 claims");
  assert.match(steps[0].summary, /2 confirmed/);
  assert.match(steps[0].summary, /1 unconfirmed/);
});

test("no findings means no evidence phase, not an empty one", () => {
  const steps = transformToDeerFlowSteps(frames({ type: "findings", items: [] }));
  assert.deepEqual(steps, []);
});

// --- review ----------------------------------------------------------------

test("critic rounds group into one review phase carrying every reason", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "critic", iteration: 1, reason: "too thin" },
      { type: "critic", iteration: 2, reason: "still thin" },
      { type: "critic", iteration: 3, reason: "good enough" },
    ),
  );
  assert.equal(steps.length, 1);
  assert.equal(steps[0].details.length, 3, "no round is dropped");
  assert.equal(steps[0].details[2].reason, "good enough");
  assert.match(steps[0].summary, /good enough/);
});

// --- ordering, status, errors --------------------------------------------

test("phases appear in the order their first event arrived", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "plan", items: ["a"] },
      { type: "search_query", query: "q", results: [hit("iea", 1)] },
      { type: "intent", query_type: "factual", domain: "energy" },
      { type: "final_report", report: "Answer.", confidence: 0.8 },
    ),
  );
  assert.deepEqual(steps.map((s) => s.kind), ["plan", "research", "understand", "answer"]);
});

test("the trailing phase is running mid-stream and completed once final", () => {
  const live = frames({ type: "intent", query_type: "factual" }, { type: "plan", items: ["a"] });
  assert.equal(transformToDeerFlowSteps(live)[1].status, "running");
  assert.equal(transformToDeerFlowSteps(live, { final: true })[1].status, "completed");
  assert.equal(transformToDeerFlowSteps(live, { final: true })[0].status, "completed");
});

test("an error attaches to the phase in flight instead of orphaning a row", () => {
  const steps = transformToDeerFlowSteps(
    frames(
      { type: "intent", query_type: "factual" },
      { type: "plan", items: ["a"] },
      { type: "error", message: "provider exploded" },
    ),
    { final: true },
  );
  assert.equal(steps.length, 2, "no extra error phase");
  assert.equal(steps[1].status, "error");
  assert.equal(steps[1].error, "provider exploded");
});

test("an error with nothing in flight still surfaces", () => {
  const steps = transformToDeerFlowSteps(frames({ type: "error", message: "bad request" }), {
    final: true,
  });
  assert.equal(steps.length, 1);
  assert.equal(steps[0].kind, "error");
  assert.equal(steps[0].status, "error");
});

// --- prose and time --------------------------------------------------------

test("intent prose never produces 'a analytical'", () => {
  const steps = transformToDeerFlowSteps(
    frames({ type: "intent", query_type: "analytical", domain: "energy", explanation_level: "practical" }),
  );
  assert.equal(steps[0].summary, "analytical question, energy domain, practical explanation");
  assert.doesNotMatch(steps[0].summary, /a analytical/);
});

test("timestamps come from the frames, not from render time", () => {
  const steps = transformToDeerFlowSteps([
    { type: "intent", query_type: "factual", __ts: 5000 },
    { type: "plan", items: ["a"], __ts: 9000 },
  ]);
  assert.equal(steps[0].startedAt, 5000);
  assert.equal(steps[1].startedAt, 9000);
  assert.equal(steps[1].endedAt, 9000);
});

test("a phase spanning several frames reports the widest window", () => {
  const steps = transformToDeerFlowSteps([
    { type: "search_query", query: "a", results: [], __ts: 1000 },
    { type: "search_progress", snippets: 5, __ts: 7000 },
  ]);
  assert.equal(steps[0].startedAt, 1000);
  assert.equal(steps[0].endedAt, 7000);
});

// --- purity ----------------------------------------------------------------

test("the transform is pure: same input, same output, input untouched", () => {
  const input = frames(
    { type: "intent", query_type: "factual" },
    { type: "search_query", query: "q", results: [hit("iea", 1)] },
    { type: "findings", items: [finding("C", "https://iea.org/a")] },
  );
  const snapshot = JSON.stringify(input);
  const a = transformToDeerFlowSteps(input);
  const b = transformToDeerFlowSteps(input);
  assert.deepEqual(a, b);
  assert.equal(JSON.stringify(input), snapshot, "input frames must not be mutated");
});

// --- full trace view -------------------------------------------------------

test("the raw view keeps one row per frame, including unmodelled types", () => {
  const rows = flattenRawSteps(
    frames(
      { type: "intent", query_type: "factual" },
      { type: "quantum_telemetry", payload: { x: 1 } },
      { type: "search_query", query: "q", results: [hit("iea", 1), hit("reuters", 2)] },
    ),
  );
  assert.equal(rows.length, 3);
  assert.equal(rows[1].known, false, "an unhandled type is flagged, not hidden");
  assert.match(rows[2].detail, /iea\.com/);
});

test("the raw view reports the query count frame as a count", () => {
  const rows = flattenRawSteps(frames({ type: "search_query", query: "", results: [], issued: 7 }));
  assert.match(rows[0].detail, /7 queries issued/);
});

test("the raw view drops malformed frames instead of throwing", () => {
  assert.doesNotThrow(() => flattenRawSteps([null, undefined, {}, 7]));
  assert.deepEqual(flattenRawSteps([null, undefined, {}]), []);
});