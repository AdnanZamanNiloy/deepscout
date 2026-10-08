from __future__ import annotations

from app.core.degradation import PROVIDER_HARD, PROVIDER_TRANSIENT

import httpx

from app.core.llmkit.types import (
    PromptTooLargeError,
)


_PERMANENT_400_SIGNATURES = (
    "insufficient balance", "insufficient credit", "credit insufficient",
    "no credits", "quota exceeded", "exceeded your quota",
    "invalid api key", "invalid_api_key", "model_not_found",
    "model not found", "does not exist", "deprecated",
)


def _permanent_client_error(exc: BaseException) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response is None:
        return False
    if exc.response.status_code == 404:
        return True
    if exc.response.status_code != 400:
        return False
    try:
        body = exc.response.text.lower()
    except Exception:
        return False
    return any(sig in body for sig in _PERMANENT_400_SIGNATURES)


def _is_fail_fast_error(exc: BaseException) -> bool:
    """401/402/403 must fail fast to fallback — retrying bad credentials or
    an empty wallet only burns time and worsens rate limiting."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response is not None
        and exc.response.status_code in (401, 402, 403)
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, PromptTooLargeError):
        # The identical payload can only 413 again; the caller shrinks it.
        return False
    if _permanent_client_error(exc):
        # Empty wallet / unknown model / bad key: deterministic failure.
        return False
    if _is_fail_fast_error(exc):
        return False
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        # A per-attempt timeout means this provider cannot serve the prompt
        # inside llm_timeout_sec; retrying the same slow call would burn the
        # 90s research budget before the fallback chain is ever reached.
        # Fail fast to the next provider instead. Fast failures (connect
        # errors, 5xx, 429) stay retryable below.
        return False
    return isinstance(exc, (httpx.HTTPError, RuntimeError))


def _is_rate_limit(exc: BaseException) -> bool:
    """A provider throttle (429) — distinct from a genuine failure."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response is not None
        and exc.response.status_code == 429
    )


def classify_provider_failure(exc: BaseException) -> str:
    """Classify a provider error as transient or hard (or "" for neither).
    Reused by the breaker and by degradation tracking so transport causes are
    reported as provider failures, never confused with weak evidence:

      transient  — 429, 5xx, timeouts, connection/transport errors. The
                   provider is reachable; the condition may clear.
      hard       — 401/402/403, permanent 400 signatures, 404, oversize
                   (413/PromptTooLargeError), and "no provider configured".
                   Retrying the identical call cannot succeed.

    PromptTooLargeError is hard at the provider level but the CALLER can fix
    it by shrinking the payload; the breaker must not treat it as a provider
    outage (see _record_provider_failure), and neither should degradation.
    """
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return PROVIDER_TRANSIENT
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        status = exc.response.status_code
        if status == 429 or 500 <= status < 600:
            return PROVIDER_TRANSIENT
        if status in (400, 401, 402, 403, 404, 413):
            return PROVIDER_HARD
        return PROVIDER_TRANSIENT if 400 <= status < 500 else ""
    if isinstance(exc, PromptTooLargeError):
        return PROVIDER_HARD
    if isinstance(exc, (httpx.HTTPError, RuntimeError)):
        return PROVIDER_TRANSIENT
    return ""


_CHAIN_FAILOVER_STATUSES = frozenset({401, 402, 403, 404, 408, 409, 413, 425, 429})


def chain_failover_eligible(exc: BaseException) -> bool:
    """Should an enabled provider CHAIN advance to the next member on `exc`?

    True only for failures that are specific to the provider or the transport
    and that a different provider may not share:

      * timeouts / connection / transport errors — the provider is unreachable
      * 5xx / 429 / 408 / 425 — outage, throttle, provider-side stall
      * 401/402/403 — this provider's key/wallet/quota, not the request
      * 404 model-not-found or permanent 400 bodies (bad key, no credits,
        unknown model) — provider-side determinism
      * PromptTooLargeError — the CALLER shrinks the payload; a different
        provider with a bigger window may accept it, so advancing is fine

    False for a generic 4xx (a malformed/user-invalid request): the SAME
    payload would be rejected identically everywhere, so advancing would spend
    every other provider's quota to reach the same error. Also False for
    anything unrecognized — an unknown error must not silently burn a chain.
    """
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return True
    if isinstance(exc, PromptTooLargeError):
        return True
    if _permanent_client_error(exc):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        status = exc.response.status_code
        if status in _CHAIN_FAILOVER_STATUSES:
            return True
        if 500 <= status < 600:
            return True
        return False
    if isinstance(exc, httpx.HTTPError):
        return True
    return False
