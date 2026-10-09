/* A retrieval engine must never be displayed as a publisher.
 *
 * Search hits used to carry one conflated `source` string, "searxng:google cse",
 * and the trace chip rendered it as the source label. Every Google-CSE hit
 * therefore looked like it came from a publisher called "google cse" — including
 * papers on arxiv.org and mdpi.com. Source identity (who published it) and
 * retrieval provenance (which index found it) are now separate fields, and this
 * locks the UI half of that split.
 *
 * `sourceLabelOf` / `provenanceOf` are re-implemented here rather than imported
 * from events.ts: a plain-Node import of a .ts module is not available to
 * `node --test`, and a top-level `await import(...)` that rejects aborts the
 * whole file — the assertions never run and `node --test` still reports the
 * FILE as passing. The final test greps the real source so the copy cannot drift
 * into verifying nothing.
 *
 * Runs on Node's built-in test runner; no DOM or new dependency required.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const EVENTS = readFileSync(join(HERE, "..", "src", "trace", "events.ts"), "utf8");
const CARD = readFileSync(
  join(HERE, "..", "src", "trace", "AgentPipelineTrace.tsx"), "utf8"
);

const str = (v) => (v == null ? "" : String(v));

function hostOf(url) {
  const m = /^[a-z]+:\/\/([^/?#]+)/i.exec(url || "");
  if (!m) return "";
  const authority = m[1];
  const hostOnly = authority.slice(authority.lastIndexOf("@") + 1).split(":")[0];
  return hostOnly.replace(/^www\./i, "").toLowerCase();
}

const LEGACY_COMPOSITE_PROVIDER_RE = /^[a-z0-9_-]+:\s*\S/i;

function sourceLabelOf(h, url) {
  const domain = str(h.domain).trim().toLowerCase();
  if (domain) return domain;
  const publisher = str(h.publisher).trim();
  if (publisher && !LEGACY_COMPOSITE_PROVIDER_RE.test(publisher)) return publisher;
  const host = hostOf(url);
  if (host) return host;
  const source = str(h.source).trim();
  if (source && !LEGACY_COMPOSITE_PROVIDER_RE.test(source)) return source;
  return "";
}

function provenanceOf(h) {
  return str(h.via).trim();
}

test("a Google CSE hit from example.com displays example.com", () => {
  const hit = {
    title: "A paper",
    url: "https://www.example.com/paper",
    source: "example.com",
    domain: "example.com",
    via: "google cse",
  };
  const label = sourceLabelOf(hit, hit.url);
  assert.equal(label, "example.com");
  assert.ok(!label.includes("cse"), "engine must not appear in the source label");
});

test("retrieval provenance is preserved separately from the source", () => {
  const hit = { url: "https://arxiv.org/abs/2301.1", domain: "arxiv.org", via: "google cse" };
  assert.equal(sourceLabelOf(hit, hit.url), "arxiv.org");
  assert.equal(provenanceOf(hit), "google cse");
});

test("a legacy replayed frame does not resurrect the engine as a publisher", () => {
  /* Runs persisted before this fix still hold source: "searxng:google cse".
   * Replaying one must show the hostname, not the engine. */
  const hit = { url: "https://www.mdpi.com/2227-7390/13/5/856", source: "searxng:google cse" };
  assert.equal(sourceLabelOf(hit, hit.url), "mdpi.com");
});

test("www, subdomain and duplicate-URL forms resolve to one source", () => {
  const variants = [
    "https://example.com/a",
    "https://www.example.com/a",
    "https://user@example.com:8443/a",
  ];
  const labels = variants.map((u) => sourceLabelOf({ url: u }, u));
  assert.deepEqual(labels, ["example.com", "example.com", "example.com"]);
});

test("a subdomain stays itself rather than collapsing to the parent", () => {
  assert.equal(sourceLabelOf({ url: "https://news.bbc.co.uk/x" }, "https://news.bbc.co.uk/x"),
    "news.bbc.co.uk");
});

test("results from different engines keep their own provenance", () => {
  const hits = [
    { url: "https://a.com/1", domain: "a.com", via: "google cse" },
    { url: "https://b.com/2", domain: "b.com", via: "duckduckgo" },
    { url: "https://c.com/3", domain: "c.com", via: "mwmbl" },
  ];
  assert.deepEqual(hits.map((h) => sourceLabelOf(h, h.url)), ["a.com", "b.com", "c.com"]);
  assert.deepEqual(hits.map(provenanceOf), ["google cse", "duckduckgo", "mwmbl"]);
});

test("missing metadata never lets the engine masquerade as the publisher", () => {
  const cases = [
    [{ url: "https://example.com/x" }, "example.com"],
    [{ url: "not-a-url", via: "google cse" }, ""],
    [{ url: "", source: "searxng:google cse", via: "google cse" }, ""],
    [{}, ""],
  ];
  for (const [hit, expected] of cases) {
    const label = sourceLabelOf(hit, hit.url || "");
    assert.equal(label, expected);
    assert.ok(!/cse|duckduckgo|mwmbl/.test(label), "engine leaked into source label");
  }
});

test("a named publisher is preferred over the bare hostname", () => {
  const hit = { url: "https://doi.org/10.1/x", domain: "doi.org", publisher: "Springer Nature" };
  assert.equal(sourceLabelOf(hit, hit.url), "doi.org"); // domain wins when present
  assert.equal(sourceLabelOf({ url: hit.url, publisher: "Springer Nature" }, hit.url),
    "Springer Nature");
});

test("the real source wires identity and provenance into both chip sites", () => {
  /* Dead-code guard: the helpers existing but never being called would render
   * exactly the bug this file exists to prevent. */
  assert.match(EVENTS, /meta: sourceLabelOf\(h, str\(h\.url\)\)/);
  assert.match(EVENTS, /via: provenanceOf\(h\)/);
  assert.match(EVENTS, /function sourceLabelOf/);
  assert.match(EVENTS, /function provenanceOf/);
  // `str(h.source)` as a chip meta would be the old conflated behaviour.
  assert.doesNotMatch(EVENTS, /meta: str\(h\.source\)/);
  assert.match(CARD, /apt-chip-via">via \{chip\.via\}/);
  // hostOf must strip userinfo/port/case, or a URL with credentials leaks.
  assert.match(EVENTS, /lastIndexOf\("@"\)/);
  assert.doesNotMatch(EVENTS, /return m\[1\]\.replace\(\/\^www\\\./);
});