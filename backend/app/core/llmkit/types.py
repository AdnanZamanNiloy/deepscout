from __future__ import annotations

from dataclasses import dataclass

from typing import Any, Tuple



@dataclass
class CompletionResult:
    """One provider completion with the accounting the budget governor needs.

    `input_tokens`/`output_tokens` come from the provider's `usage` field
    when present; HF's text-only responses fall back to char estimates.
    """

    text: str
    provider: str
    model: str
    endpoint: str
    input_tokens: int = 0
    output_tokens: int = 0


def _parse_usage(data: Any) -> Tuple[int, int]:
    """Extract (prompt_tokens, completion_tokens) from an OpenAI-compatible
    response; (0, 0) when the provider omits usage (callers estimate)."""
    try:
        usage = data.get("usage") or {}
        return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0, 0


class AllProvidersFailedError(RuntimeError):
    """Every configured provider refused the call within this attempt.

    Distinct from "no keys configured": keys exist but each provider failed
    (rate limit, quota, auth, outage). Deterministic within a request —
    retrying the same chain immediately only burns the research budget —
    so generate_json treats it as fail-fast and the caller's deterministic
    fallback engages.
    """


class NoProviderConfiguredError(RuntimeError):
    """No LLM provider exists to call — nothing configured anywhere.

    The configuration-free state is a SUPPORTED one: keys are normally added
    in the Providers tab and live in the database, so a backend started before
    anyone has added a provider reaches this on its first call. Deterministic
    by construction — no retry, and no other provider can become configured
    inside the same request — so generate_json treats it as fail-fast. Its
    subclass of RuntimeError keeps every existing `except RuntimeError`
    handler working, and the "No LLM provider configured" prefix is what
    routes.py matches to emit the friendly NDJSON error.
    """


class PromptTooLargeError(RuntimeError):
    """The provider rejected the request size (HTTP 413).

    Observed live: Groq 413s between ~21KB and ~41KB request payloads —
    exactly where the summarizer's 12-source excerpt prompt lands. Retry-
    ing the identical payload can only fail identically, so this is fail-
    fast at every level; the CALLER (summarizer/synthesizer) retries with
    a smaller prompt via its own ladder instead of degrading to extraction.
    """
