import { test } from "node:test";
import assert from "node:assert/strict";

import { isNoProviderError } from "../src/lib.js";

/* The backend says "no usable model" two ways: the pre-flight probe before
 * any work starts, and the call-time error inside the workflow. Both must
 * render as "Model access not configured" — not as a resumable "Research
 * interrupted" whose Resume button can never succeed, because no provider
 * will exist by the time the user presses it. */

test("classifies the pre-flight probe error as no-provider", () => {
  const message =
    "No LLM provider is reachable right now — no LLM provider is configured " +
    "(set GROQ_API_KEY / CUSTOM_LLM_* in .env, or add and select one in the " +
    "Providers tab). Add or switch providers in the Providers tab, or wait " +
    "for provider quotas to reset.";
  assert.equal(isNoProviderError(message), true);
});

test("classifies the call-time error as no-provider", () => {
  const message =
    "No LLM provider configured. Add one in the Providers tab " +
    "(UI: /#/model-controls) — it applies immediately, no restart needed — " +
    "or set the CUSTOM_LLM_* trio (or GROQ_API_KEY / HUGGINGFACE_API_KEY) " +
    "in backend/.env.";
  assert.equal(isNoProviderError(message), true);
});

test("still recognises the legacy key-named phrasings", () => {
  assert.equal(isNoProviderError("No LLM key found at runtime."), true);
  assert.equal(isNoProviderError("Set GROQ_API_KEY to continue"), true);
  assert.equal(isNoProviderError("HUGGINGFACE_API_KEY rejected"), true);
});

test("an ordinary failure is not mistaken for a missing provider", () => {
  assert.equal(isNoProviderError("Research workflow failed: KeyError"), false);
  assert.equal(isNoProviderError("Your run timed out"), false);
  assert.equal(isNoProviderError(""), false);
  assert.equal(isNoProviderError(undefined), false);
  assert.equal(isNoProviderError(null), false);
});
