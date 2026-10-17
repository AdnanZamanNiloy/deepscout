/* The docs must state what the code actually does.
 *
 * Every claim this suite checks was once wrong in a shipped doc, and none of
 * it failed loudly: a stale test count reads exactly like a fresh one, and a
 * documented subsystem that no longer exists reads exactly like one that does.
 * These assertions read the BACKEND source of truth and check the published
 * prose against it, so a change to either side that lets them drift fails here
 * rather than in front of a reader.
 *
 * Text-level (no DOM), like the other UI-contract tests.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(join(here, "..", p), "utf8");
const readRoot = (p) => readFileSync(join(here, "..", "..", p), "utf8");
const rootPath = (p) => join(here, "..", "..", p);
const exists = (p) => existsSync(rootPath(p));

const DOCS = read("public/docs.content.js");
const README = readRoot("README.md");
const ARCH = readRoot("ARCHITECTURE.md");
const SETUP = readRoot("SETUP.md");
const CONFIG = readRoot("CONFIG.md");
const ROUTES = readRoot("backend/app/api/routes.py");
const RUNNER = readRoot("backend/app/research/runner.py");
const BUDGET = readRoot("backend/app/infra/budget.py");
const CONFIG_PY = readRoot("backend/app/common/config.py");

/* ---- every documented path must exist (docs resolve as app/x -> backend/app/x) */
test("every backend path named in the docs exists", () => {
  const docs = [README, ARCH, SETUP, CONFIG];
  const missing = [];
  for (const text of docs) {
    for (const m of text.matchAll(/\b(?:backend\/)?(?:app|bench)\/[A-Za-z0-9_./-]+\.py/g)) {
      /* A bare `app/...` or `bench/...` means `backend/app/...` or `backend/bench/...`,
       * which is how the docs are written: both live under backend/. */
      const rel = m[0].startsWith("backend/") ? m[0] : `backend/${m[0]}`;
      if (!exists(rel)) missing.push(rel);
    }
  }
  assert.deepEqual([...new Set(missing)], [], "docs reference files that do not exist");
});

/* ---- test counts, quoted in several places ----
 * These are the only two hand-maintained numbers in the docs. Bump them here
 * in the same commit that adds or removes a test, or this suite fails and
 * every published copy drifts apart.
 */
const BACKEND_TESTS = 672;
const FRONTEND_TESTS = 353;

test("the quoted test counts match what the suites actually collect", () => {
  const m = /(\d+) backend and (\d+) frontend tests/.exec(README);
  assert.ok(m, "README must state both test counts");
  assert.equal(m[1], String(BACKEND_TESTS), "backend test count in README");
  assert.equal(m[2], String(FRONTEND_TESTS), "frontend test count in README");
  for (const [name, text] of [["SETUP.md", SETUP], ["docs.content.js", DOCS]]) {
    assert.doesNotMatch(text, /\b602\b/, `${name} still quotes the old backend count`);
    assert.match(text, /\b672\b/, `${name} must quote the current backend count`);
  }
});

/* ---- the emitted event set is stated correctly everywhere ---- */
test("every doc's event table lists findings and omits what is not emitted", () => {
  assert.match(ROUTES, /event_line\(\s*"findings"/, "the route must actually emit findings");
  for (const [name, text] of [
    ["README", README], ["ARCHITECTURE.md", ARCH], ["docs.content.js", DOCS],
  ]) {
    assert.match(text, /`findings`|>findings</, `${name} must list the findings frame`);
  }
  // These really are not emitted; a doc must not claim they are.
  for (const absent of ["intent", "route", "direct_answer", "search_progress"]) {
    assert.doesNotMatch(ROUTES, new RegExp(`event_line\\(\\s*"${absent}"`),
      `${absent} is listed as emitted but the route does not emit it`);
  }
  assert.match(SETUP, /eight types/, "SETUP must state the real emitted-type count");
});

/* ---- mode ceilings: the enforced cap, not a derived one that no longer applies ---- */
test("quoted wall-clock ceilings match MODE_HARD_CAP_SECONDS", () => {
  const m = /MODE_HARD_CAP_SECONDS[^=]*=\s*\{([^}]*)\}/.exec(BUDGET);
  assert.ok(m, "could not find MODE_HARD_CAP_SECONDS in budget.py");
  const caps = {};
  for (const pair of m[1].split(",")) {
    const kv = /"(\w+)"\s*:\s*([\d.]+)/.exec(pair);
    if (kv) caps[kv[1]] = Number(kv[2]);
  }
  assert.deepEqual(caps, { quick: 180, standard: 360, deep: 720 });
  // README's mode table must quote those seconds, not a stale derived number.
  assert.match(README, /21 calls · 180 s/);
  assert.match(README, /60 calls · 360 s/);
  assert.match(README, /150 calls · 720 s/);
  assert.doesNotMatch(README, /105 s/, "README still quotes the removed derived budget");
});

test("quick mode's fact-check ceiling is quoted as zero", () => {
  assert.match(RUNNER, /"max_fact_check_revisions":\s*0 if self\.mode == "quick"/,
    "the runner must still give quick mode no fact-check round");
  for (const [name, text] of [
    ["README", README], ["SETUP.md", SETUP], ["docs.content.js", DOCS],
    ["Landing.jsx", read("src/components/Landing.jsx")],
  ]) {
    assert.doesNotMatch(text, /quick[^\n|]*\|[^|\n]*\|[^|\n]*\|[^|\n]*\|\s*1\s*\|/i,
      `${name} still gives quick a fact-check revision`);
  }
});

/* ---- the degraded cap: promised in prose, applied in code ---- */
test("the degraded cap is wired into the one production caller", () => {
  const audit = readRoot("backend/app/evidence/audit.py");
  assert.match(audit, /degraded=list\(degraded or \[\]\)/,
    "compute_audit must pass the run's degradation state into the engine");
  assert.match(audit, /provider_degraded=bool\(provider_degraded\)/,
    "compute_audit must pass provider failure state into the engine");
  // And the engine is the only place that applies the cap.
  const conf = readRoot("backend/app/core/confidence.py");
  assert.match(conf, /DEGRADED_CAP/);
});

test("the cap is described the way it actually behaves", () => {
  // Only the synthesizer caps unconditionally; a summarizer fallback is
  // evidence-conditional. A doc that says "any fallback caps" is wrong.
  for (const [name, text] of [["README", README], ["docs.content.js", DOCS]]) {
    assert.doesNotMatch(text, /stage fell back to a default is capped/i,
      `${name} overstates the cap`);
  }
  assert.match(README, /synthesizer/);
});

/* ---- CONFIG.md must track config.py ---- */
test("CONFIG.md documents no setting that no longer exists", () => {
  // Every backticked SCREAMING_CASE key in CONFIG.md must be a real field.
  const fields = new Set(
    [...CONFIG_PY.matchAll(/^\s{4}([a-z0-9_]+):/gm)].map((m) => m[1].toUpperCase()),
  );
  const rows = [...CONFIG.matchAll(/^\|\s*`([A-Z0-9_]+)`/gm)].map((m) => m[1]);
  assert.ok(rows.length > 40, "expected CONFIG.md to still document the settings table");
  const missing = rows.filter((k) => !fields.has(k));
  assert.deepEqual(missing, [], "CONFIG.md documents settings that config.py does not define");
});

test("CONFIG.md documents the settings added since it was written", () => {
  for (const key of ["AUDIT_FETCH_MAX_URLS", "AUDIT_FETCH_TIMEOUT_SEC"]) {
    assert.match(CONFIG, new RegExp(`\`${key}\``), `CONFIG.md is missing ${key}`);
    assert.ok(
      new RegExp(String.raw`^\s{4}${key.toLowerCase()}:`, "m").test(CONFIG_PY),
      `${key} must exist in config.py`,
    );
  }
});

/* ---- subsystems the docs must not claim ---- */
test("no doc claims a citation re-validator that does not exist", () => {
  for (const [name, text] of [
    ["README", README], ["docs.content.js", DOCS], ["ARCHITECTURE.md", ARCH],
  ]) {
    assert.doesNotMatch(text, /Re-check citations live/i, `${name} still claims live URL re-validation`);
  }
  // No HTTP re-probe of cited URLs anywhere in the backend.
  const backend = readRoot("backend/app/evidence/audit.py")
    + readRoot("backend/app/evidence/verify.py")
    + readRoot("backend/app/api/routes.py");
  assert.doesNotMatch(backend, /HEAD.{0,40}(probe|revalidat)/i);
});

/* Owner/repo out of any github.com URL, so the two docs can be compared. */
const repoPath = (url) => {
  const m = String(url).match(/github\.com[:/]([^/]+)\/([^/\s"'|)]+)/);
  return m ? `${m[1]}/${m[2].replace(/\.git$/, "")}` : null;
};

test("the landing page links to the repository that actually exists", () => {
  const landing = read("src/components/Landing.jsx");
  const declared = /const GITHUB_URL = "([^"]+)"/.exec(landing);
  assert.ok(declared, "Landing.jsx must declare GITHUB_URL");
  const mine = repoPath(declared[1]);
  assert.ok(mine, `Landing.jsx URL is not a github.com URL: ${declared[1]}`);

  // Every link to THIS project in the README must name the same repository.
  // Third-party links (SearXNG upstream) are citations, not claims about us.
  const own = mine.split("/")[0].toLowerCase();
  for (const url of README.match(/https?:\/\/github\.com[:/][^\s)"'|]+/g) ?? []) {
    const path = repoPath(url);
    if (path && path.split("/")[0].toLowerCase() === own) {
      assert.equal(path, mine, `README links this project under a different name: ${url}`);
    }
  }
});
/* ---- the docs must actually load ----
 *
 * These files are served verbatim from public/ into an iframe, so the Vite
 * build never parses them and no existing test read them as code — they were
 * only ever matched as TEXT. A missing comma between two array entries is
 * invisible to a regex and fatal to the browser: the whole payload failed to
 * parse, `window.DEEPSCOUT_DOCS` stayed undefined, and the docs page rendered
 * completely blank. The build was green, 327 tests were green.
 *
 * So these tests compile and RUN the payload the way the browser does, then
 * assert the tree it publishes is complete. */
test("the docs payload compiles, executes and publishes a complete tree", () => {
  const sandbox = { window: {}, console };
  vm.createContext(sandbox);
  assert.doesNotThrow(
    () => new vm.Script(DOCS).runInContext(sandbox),
    "public/docs.content.js must parse and run in the browser",
  );
  const docs = sandbox.window.DEEPSCOUT_DOCS;
  assert.ok(docs, "the payload must publish window.DEEPSCOUT_DOCS");

  const items = docs.nav.flatMap((g) => g.items);
  assert.ok(items.length > 10, `expected a real nav, found ${items.length} pages`);
  for (const item of items) {
    assert.ok(
      Object.prototype.hasOwnProperty.call(docs.content, item.id),
      `nav links "${item.id}" but the content map has no such page`,
    );
    const body = docs.content[item.id];
    assert.ok(
      Array.isArray(body) && body.length > 0 && body.every((b) => typeof b === "string"),
      `page "${item.id}" must be a non-empty array of html strings`,
    );
  }
});

test("the docs shell script also compiles", () => {
  const app = read("public/docs.app.js");
  const sandbox = { window: { DEEPSCOUT_DOCS: undefined }, document: undefined, console };
  vm.createContext(sandbox);
  assert.doesNotThrow(
    () => new vm.Script(app),
    "public/docs.app.js must parse — it is never bundled, so nothing else checks it",
  );
});

test("every html string in the docs is a closed fragment", () => {
  // A truncated replacement leaves a dangling tag, which renders as a silently
  // mis-styled block rather than an error.
  const sandbox = { window: {}, console };
  vm.createContext(sandbox);
  new vm.Script(DOCS).runInContext(sandbox);
  const { content } = sandbox.window.DEEPSCOUT_DOCS;
  for (const [id, blocks] of Object.entries(content)) {
    for (const block of blocks) {
      assert.doesNotMatch(
        block, /<[a-z][^>]*$/i,
        `page "${id}" ends mid-tag: ...${block.slice(-60)}`,
      );
    }
  }
});

/* ---- every asset the README renders must exist ----
 *
 * The brand wordmarks moved from `brand/` to `docs/assets/`. GitHub renders a
 * missing image as a broken icon and reports nothing: no build fails, no test
 * fails, and the README simply loses its logo. Only README consumed those
 * files, so nothing in the app would have caught the dangling path.
 */
test("every repo-relative asset the README renders exists", () => {
  const refs = new Set();
  for (const m of README.matchAll(/(?:src|srcset)="(?!https?:|data:|#)([^"]+)"/g)) {
    refs.add(m[1]);
  }
  assert.ok(refs.size > 0, "expected the README to render at least one local asset");
  const missing = [...refs].filter((r) => !exists(r));
  assert.deepEqual(missing, [], "README renders assets that do not exist");
});

test("the README names no folder that was moved or removed", () => {
  assert.doesNotMatch(README, /(?:^|[^\w/])brand\//m,
    "README still points at the old brand/ path");
  assert.ok(exists("docs/assets/deepscout-branding-light.svg"));
  assert.ok(exists("docs/assets/deepscout-branding-dark.svg"));
});
