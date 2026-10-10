import { test } from "node:test";
import assert from "node:assert/strict";

import { describeDetail } from "../src/api.js";

/* A backend error body's `detail` is a STRING for our own HTTPExceptions but a
 * LIST OF OBJECTS for a FastAPI validation failure (422). The client used to do
 * `String(data.detail)`, which turns that list into "[object Object]" — the
 * entire error message a user saw ("Research interrupted: [object Object]").
 * These cases pin the readable rendering of every shape the backend can send. */

test("a string detail passes through unchanged", () => {
  assert.equal(describeDetail("Research timed out after 90s.", "fallback"), "Research timed out after 90s.");
});

test("a FastAPI 422 validation list renders as readable text", () => {
  const detail = [
    { type: "missing", loc: ["body", "query"], msg: "Field required", input: {} },
  ];
  const out = describeDetail(detail, "fallback");
  assert.equal(out, "query: Field required");
  assert.ok(!out.includes("[object Object]"), out);
});

test("multiple validation entries are joined, not collapsed", () => {
  const detail = [
    { loc: ["body", "query"], msg: "Field required" },
    { loc: ["body", "mode"], msg: "Input should be 'quick', 'standard' or 'deep'" },
  ];
  const out = describeDetail(detail, "fallback");
  assert.match(out, /query: Field required/);
  assert.match(out, /mode: Input should/);
  assert.ok(!out.includes("[object Object]"), out);
});

test("an object detail uses its message, never the JS placeholder", () => {
  assert.equal(describeDetail({ message: "boom" }, "fallback"), "boom");
  const out = describeDetail({ code: 500, info: "x" }, "fallback");
  assert.ok(!out.includes("[object Object]"), out);
});

test("an unknown shape falls back to the caller's generic message", () => {
  assert.equal(describeDetail(null, "Request failed (500)"), "Request failed (500)");
  assert.equal(describeDetail("", "Request failed (500)"), "Request failed (500)");
  assert.equal(describeDetail([], "Request failed (500)"), "Request failed (500)");
});
