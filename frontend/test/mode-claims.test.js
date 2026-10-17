/* The mode picker must not claim what the backend does not do.
 *
 * Root cause this locks: the picker advertised "2 agents · 1 pass · fastest",
 * "3 agents · up to 3 passes" and "5 agents · up to 5 passes". None of those
 * numbers existed — there is no per-mode agent count (MAX_PARALLEL_AGENTS is a
 * fixed hardware cap), and the real revision ceilings are 1/2/2 with the
 * fact-check ceiling frozen at 1 for every mode. A UI that overstates what a
 * mode does is the same class of bug as a report that overstates its evidence.
 *
 * These assertions read the BACKEND source of truth and check the frontend
 * copy against it, so a change to either side that lets them drift fails here
 * rather than in front of a user. Text-level (no DOM), like the other
 * contracts in this suite.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(join(here, "..", p), "utf8");
const readRoot = (p) => readFileSync(join(here, "..", "..", p), "utf8");

const lib = read("src/lib.js");
const landing = read("src/components/Landing.jsx");
const runner = read("../backend/app/research/runner.py");
const budget = read("../backend/app/infra/budget.py");

/* Section counts the runner actually uses. */
function backendSections() {
  const m = /_MODE_SECTIONS\s*=\s*\{([^}]*)\}/.exec(runner);
  assert.ok(m, "could not find _MODE_SECTIONS in runner.py");
  const out = {};
  for (const pair of m[1].split(",")) {
    const kv = /"(\w+)"\s*:\s*(\d+)/.exec(pair);
    if (kv) out[kv[1]] = Number(kv[2]);
  }
  return out;
}

/* Wall-clock caps the budget actually enforces, in seconds. */
function backendCaps() {
  const m = /MODE_HARD_CAP_SECONDS[^=]*=\s*\{([^}]*)\}/.exec(budget);
  assert.ok(m, "could not find MODE_HARD_CAP_SECONDS in budget.py");
  const out = {};
  for (const pair of m[1].split(",")) {
    const kv = /"(\w+)"\s*:\s*([\d.]+)/.exec(pair);
    if (kv) out[kv[1]] = Number(kv[2]);
  }
  return out;
}

test("the picker advertises the section counts the runner uses", () => {
  const sections = backendSections();
  for (const [mode, count] of Object.entries(sections)) {
    // The hint must mention this mode's real section count.
    const re = new RegExp(`${mode}:\\s*\\{[^}]*hint:\\s*"[^"]*${count} sections`);
    assert.match(lib, re, `${mode} hint must say "${count} sections"`);
  }
});

test("the picker never advertises a per-mode agent count", () => {
  // No such mapping exists; MAX_PARALLEL_AGENTS is one fixed cap for all modes.
  assert.doesNotMatch(lib, /\d+\s+agents?\b/i, "no mode may claim a number of agents");
  assert.doesNotMatch(landing, /\d+\s+agents?\b/i, "landing must not claim agents per mode");
});

test("the picker's stated time is a realistic expectation under the cap", () => {
  /* The hint used to quote the ENFORCED CEILING (~3/6/12 min), which is where
   * a stalled provider gets stopped, not what a run takes. Measured p50 was
   * ~2.2 / 3.4 / 3.9 min, so the picker made every mode look 2-3x slower than
   * it is. It now states the expected completion time.
   *
   * The claim must still be BOUNDED BY REALITY: a stated time below the
   * measured floor would be the same overstatement bug in the other
   * direction, and a stated time at/above the hard cap would be meaningless
   * (the run is killed there). Both ends are checked against the backend.
   */
  const caps = backendCaps();
  // Lower bounds from measured runs (bench/results/MODE_BASELINE.md); a mode
  // that finishes faster than this in practice means the copy is too slow.
  const measuredFloorMin = { quick: 1.0, standard: 2.0, deep: 2.5 };
  for (const [mode, seconds] of Object.entries(caps)) {
    const m = new RegExp(`${mode}:\\s*\\{[^}]*hint:\\s*"[^"]*~(\\d+(?:\\.\\d+)?) min`).exec(lib);
    assert.ok(m, `${mode} hint must state an expected "~N min" time`);
    const stated = Number(m[1]);
    assert.ok(
      stated < seconds / 60,
      `${mode} states ~${stated} min, which is not below the ${seconds / 60} min hard cap`
    );
    assert.ok(
      stated >= measuredFloorMin[mode],
      `${mode} states ~${stated} min, below the measured floor of ${measuredFloorMin[mode]} min`
    );
  }
});

test("the picker no longer quotes the enforced cap as the expected time", () => {
  // Regression guard: the caps are 3/6/12 min; quoting them was the defect.
  const caps = backendCaps();
  for (const [mode, seconds] of Object.entries(caps)) {
    const m = new RegExp(`${mode}:\\s*\\{[^}]*hint:\\s*"[^"]*~(\\d+(?:\\.\\d+)?) min`).exec(lib);
    assert.ok(m, `${mode} hint must state a time`);
    const stated = Number(m[1]);
    const capMin = seconds / 60;
    assert.ok(
      stated < capMin,
      `${mode} still quotes the ${capMin} min ceiling as if it were the expected time`
    );
  }
});

test("the landing modes list states the same section counts as the backend", () => {
  const sections = backendSections();
  for (const [mode, count] of Object.entries(sections)) {
    // "Quick" / "Standard" / "Deep" -> 3 / 5 / 8 sections
    const label = mode[0].toUpperCase() + mode.slice(1);
    const re = new RegExp(`name:\\s*"${label}"[^}]*${count} sections`);
    assert.match(landing, re, `landing ${label} must say "${count} sections"`);
  }
});

test("the docs test-count stat matches the collected suites", () => {
  // The authoritative values are asserted by the backend suite itself and by
  // docs-accuracy.test.js. What matters here is that the docs print two
  // DIFFERENT numbers for two DIFFERENT things and both are self-consistent:
  // the hero stat is the combined total, the quickstart comment is the
  // backend suite. (These were once required to be equal, which only held
  // while both printed the backend count.)
  const docs = read("public/docs.content.js");
  const hero = /(\d{3,})\s*<\/div><div class="k">Offline tests/g;
  const quickstart = /#\s*(\d{3})(?: \w+)? tests, fully offline/.exec(docs);

  const stats = [...docs.matchAll(hero)];
  assert.equal(stats.length, 1, "expected exactly one hero test-count stat");
  assert.ok(quickstart, "expected the quickstart test-count comment");

  const readme = readRoot("README.md");
  const counts = /(\d+) backend and (\d+) frontend tests/.exec(readme);
  assert.ok(counts, "README must state both suite counts");

  const backend = Number(quickstart[1]);
  assert.equal(backend, Number(counts[1]), "the quickstart comment must be the backend count");
  assert.equal(
    Number(stats[0][1]),
    Number(counts[1]) + Number(counts[2]),
    "the hero stat must be the combined backend + frontend total",
  );
});
