"""LLM client: provider chain, retries, circuit breaking and usage accounting.

Refactor note
-------------
The support layer (result/error types, failure classifiers, the circuit breaker
and timeout helpers) now lives in the `app.core.llmkit` package. This module is
the stable facade: it defines `LLMClient` (the public class) and
`clamp_confidence`, and re-exports every helper name the rest of the codebase
imports from `app.core.llm`, so the import surface is UNCHANGED.
"""
from __future__ import annotations

import asyncio
import json  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from typing import (  # noqa: F401
    Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple, Type,
)

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter  # noqa: F401

from app.core.config import Settings
from app.core import llm_cache
from app.core.llmkit.jsonparse import (
    payload_says_nothing as _payload_says_nothing,
)
from app.core.degradation import PROVIDER_HARD, PROVIDER_TRANSIENT, record_provider_failure  # noqa: F401
from app.core.logging import get_logger
from app.core.usage import get_run_usage  # noqa: F401
from app.core.llmkit.invoke import ProviderInvocationMixin
from app.core.llmkit.jsonparse import JSONParseMixin
from app.core.llmkit.usage_accounting import UsageAccountingMixin
from app.core.llmkit.probing import ProbingMixin

from app.core.llmkit.types import (  # noqa: F401
    CompletionResult,
    _parse_usage,
    AllProvidersFailedError,
    NoProviderConfiguredError,
    PromptTooLargeError,
)
from app.core.llmkit.failures import (  # noqa: F401
    _PERMANENT_400_SIGNATURES,
    _permanent_client_error,
    _is_fail_fast_error,
    _is_retryable,
    _is_rate_limit,
    classify_provider_failure,
    _CHAIN_FAILOVER_STATUSES,
    chain_failover_eligible,
)
from app.core.llmkit.breaker import (  # noqa: F401
    CircuitBreaker,
)
from app.core.llmkit.timing import (  # noqa: F401
    _attempt_timeout,
    _with_total_cap,
    _real_key,
    _retry_after_hint,
    _wait_with_retry_after,
    HF_FALLBACK_MODELS,
)

logger = get_logger(__name__)


class LLMClient(ProviderInvocationMixin, JSONParseMixin, UsageAccountingMixin, ProbingMixin):
    def __init__(self, settings: Settings):
        self.settings = settings
        # Seed the response cache (idempotent; disabled flag respected).
        try:
            llm_cache.configure(settings)
        except Exception as exc:  # pragma: no cover - cache must never block startup
            logger.warning("llm cache configure failed: %s", exc)
        # Breaker state lives on the client instance (created once at app
        # startup), is bounded, and resets on success — not per-request state.
        self.groq_breaker = CircuitBreaker(**self._breaker_kwargs())
        self.custom_breaker = CircuitBreaker(**self._breaker_kwargs())
        # Concurrency cap across ALL LLM calls (planner, N summarizer workers,
        # critic, synthesizer). Without it, one expansion pass fires up to
        # max_parallel_agents simultaneous prompts — the observed cause of
        # Groq 429/TPD exhaustion on free tiers. Starts low per AGENTS.md §5.
        self._llm_semaphore = asyncio.Semaphore(max(1, int(getattr(settings, "max_parallel_llm", 2) or 2)))
        # Last failure per provider, surfaced when the whole chain fails.
        self._last_errors: Dict[str, str] = {}
        # The custom/active-provider leg shares one breaker keyed to the
        # resolved (endpoint, model) identity. Switching providers (Providers
        # tab) must NOT inherit the previous provider's failure state — a
        # dead provider A timed out once would otherwise block healthy
        # provider B for the full cooldown.
        self._custom_identity: tuple | None = None
        # Pre-flight probe cache: successes hold 30s, failures 10s. Without
        # it every stream request re-pinged every provider — free-tier
        # rate meters ticked for pings, and users waited for them.
        self._probe_cache: tuple[float, bool, str] | None = None
        # Negative cache for the provider-store lookup. When the DB has no
        # `llm_providers` table (bench/scripts, or a route that runs before
        # init_db), every generation used to re-query and log a full
        # traceback — hundreds of identical warnings per benchmark run. A
        # short hold (10s) collapses the spam while still retrying soon
        # enough that a startup-ordering issue self-heals.
        self._provider_store_down_until: float = 0.0
        # Per-chain-member circuit breakers, keyed by (endpoint, model).
        # Independently bounded by MAX_CHAIN_MEMBERS, reset on success, and
        # evicted when a chain changes — not per-request state (AGENTS.md
        # §4.3). Keying by identity (not name) means reordering/renaming a
        # provider never inherits another provider's failure count.
        self._chain_breakers: Dict[tuple, CircuitBreaker] = {}
        # Last chain outcome for this process, for diagnostics/UI: which
        # provider succeeded and why earlier members were skipped/failed.
        # Bounded (one entry overwritten per call) — never grows.
        self._last_chain_run: Dict[str, Any] | None = None

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        retries: int = 3,
        response_model: Type[BaseModel] | None = None,
    ) -> Dict[str, Any]:
        """Call an LLM and return the parsed JSON as a dict.

        When `response_model` is provided, the parsed payload is validated
        against the Pydantic model; validation failures are treated like any
        other failed attempt and trigger a retry instead of returning garbage.

        Timeouts are never retried at this level: every provider already had
        its single budgeted chance inside _generate_with_fallback, so
        re-running the whole chain would multiply slow-provider time past
        the research timeout. The caller falls back immediately instead.
        AllProvidersFailedError is likewise never retried: providers did not
        become healthy 0.7s later inside the same request. Neither is
        NoProviderConfiguredError — a backend with no provider yet (keys get
        added in the Providers tab) fails identically on all three attempts,
        so retrying only adds two warnings and ~1.4s of sleep before the
        same error reaches the caller.
        """
        for attempt in range(retries):
            try:
                text = await self._generate_with_fallback(system_prompt, user_prompt)
                payload = self._extract_json(text)
                if response_model is not None:
                    # A schema whose fields all have defaults validates an
                    # EMPTY response successfully — which is how a reasoning
                    # model that spent its whole budget on hidden reasoning
                    # (measured live: 2703 of 3000 completion tokens) returned
                    # HTTP 200, validated cleanly, and then degraded the run.
                    #
                    # Judging emptiness on the RAW payload rather than the
                    # filled dump is what makes this correct: defaults are
                    # exactly what the model failed to supply, so comparing
                    # against them would call every real answer empty.
                    if _payload_says_nothing(payload):
                        raise ValueError(
                            "model returned an empty object; every field is default"
                        )
                    validated = response_model.model_validate(payload)
                    return validated.model_dump()
                return payload
            except (
                httpx.TimeoutException,
                TimeoutError,
                AllProvidersFailedError,
                PromptTooLargeError,
                NoProviderConfiguredError,
            ):
                raise
            except ValidationError:
                # A schema mismatch is deterministic: the same model returned
                # the same wrong shape, so re-calling cannot fix it and only
                # burns the research budget. Surface it immediately so the
                # caller's deterministic fallback runs once.
                raise
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                logger.warning(
                    "[LLM] attempt %d/%d failed, retrying: %s", attempt + 1, retries, exc, exc_info=exc
                )
                await asyncio.sleep(0.7 * (attempt + 1))
        return {}

    def _breaker_wait_seconds(
        self, custom: Optional[Dict[str, Any]], groq_key: str, hf_key: str
    ) -> Optional[float]:
        """Seconds to wait before one retry, or None when waiting cannot help.

        Returns the SHORTEST remaining cooldown among the providers that are
        configured but breaker-blocked — the one that recovers first, so the
        retry has the best chance and the wait is as short as possible.

        None means "do not wait": nothing is configured (the no-provider path
        must keep its own error), or the smallest remaining cooldown already
        exceeds the cap, which would spend more research budget than the wait is
        worth.
        """
        breakers = []
        if custom:
            breakers.append(self.custom_breaker)
        if groq_key:
            breakers.append(self.groq_breaker)
        # HuggingFace is attempted unconditionally (it has no breaker), so if it
        # is the only thing configured there is nothing transient to wait for.
        if not breakers:
            return None
        waits = [_breaker_seconds_left(b) for b in breakers if b.is_open()]
        if not waits:
            return None
        wait = min(waits)
        cap = float(getattr(self.settings, "llm_breaker_wait_cap_sec", 25.0) or 0.0)
        if cap <= 0 or wait <= 0 or wait > cap:
            return None
        return wait

    async def _generate_with_chain(self, system_prompt: str, user_prompt: str) -> str | None:
        """Execute the enabled provider chain in configured order.

        Returns the completion text on success, or None when no chain is
        enabled (caller falls through to the legacy single-provider logic).
        When a chain IS enabled but every member fails, raises
        AllProvidersFailedError with per-member reasons — the same fail-fast
        contract the rest of the pipeline already handles, so callers' own
        deterministic fallbacks engage exactly as before.

        Session/request state is preserved across attempts: prompts, the
        semaphore, the usage ledger and the response cache all persist on
        `self`, so a fallback attempt is the SAME logical request on a
        different provider, never a fresh one.
        """
        chain = await self._resolve_chain()
        if not chain:
            return None

        # Cache is keyed on the PRIMARY (member #1) identity: a cached answer
        # for this exact prompt means the chain already succeeded, so no
        # member is contacted. Fallback members share the same logical request.
        primary = chain[0]
        cached = llm_cache.get(primary["endpoint"], primary["model"], system_prompt, user_prompt)
        if cached is not None:
            self._record_usage(
                cached["text"], provider="cache", model=primary["model"],
                input_tokens=cached.get("input_tokens") or None,
                output_tokens=cached.get("output_tokens") or None,
                cached=True,
            )
            return cached["text"]

        failures: List[Dict[str, str]] = []
        size_failures = 0
        attempted = 0
        async with self._llm_semaphore:
            for index, config in enumerate(chain):
                breaker = self._chain_breaker(config)
                if breaker.is_open():
                    reason = "circuit breaker open (recent provider failures)"
                    failures.append({"provider": config["name"], "reason": reason, "kind": "skipped"})
                    logger.warning(
                        "[LLM] chain member %d/%d '%s' skipped: %s",
                        index + 1, len(chain), config["name"], reason,
                    )
                    continue
                attempted += 1
                try:
                    result = await self._call_custom(system_prompt, user_prompt, config)
                    breaker.record_success()
                    self._cache_and_record(result, system_prompt, user_prompt)
                    self._last_chain_run = {
                        "succeeded": config["name"],
                        "succeeded_index": index,
                        "total": len(chain),
                        "failures": failures,
                    }
                    if index > 0:
                        logger.info(
                            "[LLM] chain fell over to member %d/%d '%s' after %d failure(s)",
                            index + 1, len(chain), config["name"], len(failures),
                        )
                    return result.text
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {str(exc)[:160] or type(exc).__name__}"
                    self._last_errors[config["name"]] = detail
                    if isinstance(exc, PromptTooLargeError):
                        size_failures += 1
                    self._record_provider_failure(breaker, exc)
                    self._record_degradation(exc)
                    kind = classify_provider_failure(exc) or "unknown"
                    eligible = chain_failover_eligible(exc)
                    failures.append({
                        "provider": config["name"], "reason": detail,
                        "kind": kind, "failover": eligible,
                    })
                    logger.warning(
                        "[LLM] chain member %d/%d '%s' failed (%s, failover=%s): %s",
                        index + 1, len(chain), config["name"], kind, eligible, detail,
                    )
                    if not eligible:
                        # A user-invalid / non-retryable request: advancing
                        # would spend every other provider to reach the same
                        # error. Stop here and surface the real cause.
                        self._last_chain_run = {
                            "succeeded": None, "succeeded_index": None,
                            "total": len(chain), "failures": failures,
                        }
                        raise AllProvidersFailedError(
                            f"Provider chain stopped at member {index + 1}/{len(chain)} "
                            f"'{config['name']}': {detail}. This failure is not eligible for "
                            "fallback (the request itself was rejected)."
                        ) from exc

        self._last_chain_run = {
            "succeeded": None, "succeeded_index": None,
            "total": len(chain), "failures": failures,
        }
        if attempted > 0 and size_failures == attempted:
            # Every member rejected the SIZE: healthy providers, oversized
            # payload. Let the caller's ladder shrink and retry.
            raise PromptTooLargeError(
                f"all {attempted} chain provider(s) rejected the request size as too large"
            )
        detail = "; ".join(f"{f['provider']}: {f['reason']}" for f in failures)
        raise AllProvidersFailedError(
            f"All {len(chain)} provider(s) in the fallback chain failed — "
            f"{detail or 'unknown errors'}. The run will continue on deterministic fallbacks."
        )

    async def _generate_with_fallback(
        self, system_prompt: str, user_prompt: str, *, breaker_wait_ok: bool = True
    ) -> str:
        # ---- Enabled provider CHAIN takes over the whole call ----
        # A chain is an explicit, ordered user choice: try member #1, fall
        # over in order, stop at the first success. It replaces (does not
        # extend) the legacy custom→groq→hf logic; when no chain is enabled
        # this returns None and the code below runs byte-for-byte as before,
        # so single-provider selection stays fully compatible.
        chain_result = await self._generate_with_chain(system_prompt, user_prompt)
        if chain_result is not None:
            return chain_result

        groq_key = _real_key(self.settings.groq_api_key)
        hf_key = _real_key(self.settings.huggingface_api_key)
        custom, exclusive = await self._resolve_custom()
        # Strict exclusivity (default) zeroes the env keys: a failing active
        # provider degrades the run instead of silently spending another
        # provider's key. active_provider_fallback relaxes exactly that —
        # built for flaky free proxies: primary when healthy, rescued by
        # Groq/HF when it stalls.
        fallback_ok = exclusive and bool(getattr(self.settings, "active_provider_fallback", False))
        if exclusive and not fallback_ok:
            groq_key = ""
            hf_key = ""
        # Identity-keyed breaker reset (see __init__): switching to a
        # DIFFERENT endpoint+model gets a fresh breaker, never the previous
        # provider's failure count. The first resolution (None → identity)
        # keeps any pre-existing state — only a real switch resets.
        identity = (custom["endpoint"], custom["model"]) if custom else None
        if identity is not None and self._custom_identity is not None and identity != self._custom_identity:
            self.custom_breaker = CircuitBreaker(**self._breaker_kwargs())
        self._custom_identity = identity

        # ---- Response cache: exact-prompt hits skip providers entirely ----
        cache_endpoint = (custom or {}).get("endpoint", "https://api.groq.com/openai/v1/chat/completions")
        cache_model = (custom or {}).get("model", self.settings.groq_model)
        cached = llm_cache.get(cache_endpoint, cache_model, system_prompt, user_prompt)
        if cached is not None:
            self._record_usage(
                cached["text"], provider="cache", model=cache_model,
                input_tokens=cached.get("input_tokens") or None,
                output_tokens=cached.get("output_tokens") or None,
                cached=True,
            )
            return cached["text"]

        attempted = 0
        size_failures = 0
        async with self._llm_semaphore:
            if custom and not self.custom_breaker.is_open():
                attempted += 1
                try:
                    result = await self._call_custom(system_prompt, user_prompt, custom)
                    self.custom_breaker.record_success()
                    self._cache_and_record(result, system_prompt, user_prompt)
                    return result.text
                except Exception as exc:
                    self._last_errors["custom"] = f"{type(exc).__name__}: {exc}"
                    size_failures += 1 if isinstance(exc, PromptTooLargeError) else 0
                    self._record_provider_failure(self.custom_breaker, exc)
                    self._record_degradation(exc)
                    logger.warning(
                        "[LLM] Custom provider call failed (breaker failures=%d), falling back: %s",
                        self.custom_breaker._consecutive_failures,
                        exc,
                        exc_info=exc,
                    )
                    # A size rejection is not a provider failure: the provider is
                    # healthy and the payload is too big, so the caller's
                    # shrink-and-retry ladder must receive the size signal.
                    # Wrapping it here instead turned a fixable oversized prompt
                    # into terminal degradation.
                    if isinstance(exc, PromptTooLargeError):
                        raise
                    if exclusive and not fallback_ok:
                        raise AllProvidersFailedError(
                            f"Active provider '{custom.get('name', 'custom')}' failed: "
                            f"{type(exc).__name__}. No fallback providers run while "
                            "one is selected — the run continues on deterministic fallbacks."
                        ) from exc
            if groq_key and not self.groq_breaker.is_open():
                attempted += 1
                try:
                    result = await self._call_groq(system_prompt, user_prompt)
                    self.groq_breaker.record_success()
                    self._cache_and_record(result, system_prompt, user_prompt)
                    return result.text
                except Exception as exc:
                    self._last_errors["groq"] = f"{type(exc).__name__}: {exc}"
                    size_failures += 1 if isinstance(exc, PromptTooLargeError) else 0
                    self._record_provider_failure(self.groq_breaker, exc)
                    self._record_degradation(exc)
                    logger.warning(
                        "[LLM] Groq call failed (breaker failures=%d), falling back: %s",
                        self.groq_breaker._consecutive_failures,
                        exc,
                        exc_info=exc,
                    )

            if hf_key:
                attempted += 1
                try:
                    result = await self._call_huggingface(system_prompt, user_prompt)
                    self._cache_and_record(result, system_prompt, user_prompt)
                    return result.text
                except Exception as exc:
                    self._last_errors["huggingface"] = f"{type(exc).__name__}: {exc}"
                    size_failures += 1 if isinstance(exc, PromptTooLargeError) else 0
                    self._record_degradation(exc)
                    raise

        # Every attempted provider rejected the REQUEST SIZE: the providers
        # are healthy — the payload is not. Raise the sizing signal so the
        # caller's ladder can shrink and retry instead of degrading.
        if attempted > 0 and size_failures == attempted:
            raise PromptTooLargeError(
                f"all {attempted} provider(s) rejected the request size as too large"
            )

        if attempted == 0 and breaker_wait_ok:
            # EVERY provider was skipped because its breaker is open. That is
            # transient state, not a verdict — but with a single provider (the
            # common case: a UI-selected one is exclusive and runs no
            # fallbacks) it is catastrophic for the whole run. Measured live:
            # one 90s analyst timeout opened the breaker, and the analyst plus
            # ALL FOUR synthesizer section calls then failed instantly in the
            # same second, degrading a run that would otherwise have succeeded.
            #
            # The breaker exists so a broken provider is not hammered, and it
            # still does that: we wait for its own cooldown exactly once rather
            # than retrying in a loop, and the cap keeps the extra wait well
            # inside the research budget. Hard failures are NOT retried — no
            # keys, a rejected authorization, an oversized prompt all fall
            # through to the existing error paths unchanged.
            wait = self._breaker_wait_seconds(custom, groq_key, hf_key)
            if wait is not None:
                logger.info(
                    "[LLM] all providers breaker-blocked; waiting %.1fs for "
                    "cooldown, then one retry",
                    wait,
                )
                await asyncio.sleep(wait)
                return await self._generate_with_fallback(
                    system_prompt, user_prompt, breaker_wait_ok=False
                )

        if attempted == 0:
            # Keys existed but every provider's breaker was open — or nothing
            # was configured at all. The nothing-configured case keeps the
            # legacy message (routes.py matches it for a friendly NDJSON
            # error); breaker-open is a different failure with its own error.
            if not (custom or groq_key or hf_key):
                # The `routes.py` substring match on "No LLM provider configured"
                # keys this friendly error off — keep the prefix intact.
                raise NoProviderConfiguredError(
                    "No LLM provider configured. Add one in the Providers tab "
                    "(UI: /#/model-controls) — it applies immediately, no "
                    "restart needed — or set the CUSTOM_LLM_* trio (or "
                    "GROQ_API_KEY / HUGGINGFACE_API_KEY) in backend/.env."
                )
            configured = [name for name, ok in (
                ("custom", bool(custom)), ("groq", bool(groq_key)), ("huggingface", bool(hf_key)),
            ) if ok]
            if exclusive and custom and not fallback_ok:
                configured = [f"active provider '{custom.get('name', 'custom')}' (exclusive, no fallbacks)"]
            raise AllProvidersFailedError(
                "All LLM providers skipped this attempt — circuit breakers open "
                f"for: {', '.join(configured)}. Wait for the breaker cooldown "
                "(60s) or check provider quotas."
            )
        def _brief(msg: str) -> str:
            first = (msg or "").strip().splitlines()[0] if (msg or "").strip() else "unknown error"
            return first[:160]

        detail = "; ".join(f"{name}: {_brief(msg)}" for name, msg in self._last_errors.items())
        raise AllProvidersFailedError(
            f"All {attempted} configured LLM provider(s) failed — {detail or 'unknown errors'}. "
            "The run will continue on deterministic fallbacks."
        )


def _breaker_seconds_left(breaker: Any) -> float:
    """Seconds until an open breaker closes on its own.

    `app.core.llmkit.breaker.CircuitBreaker` (the one the LLM client uses)
    exposes only `_opened_at` + `cooldown_sec` and no `retry_in()`, while
    `app.agents.reliability.CircuitBreaker` has the opposite surface. Rather
    than duplicating that knowledge in two places, prefer a public accessor and
    fall back to the timestamp — a breaker shape this cannot read returns 0.0,
    which disables the wait rather than guessing a wrong one.
    """
    accessor = getattr(breaker, "retry_in", None)
    if callable(accessor):
        try:
            return max(0.0, float(accessor()))
        except (TypeError, ValueError):
            return 0.0
    opened_at = getattr(breaker, "_opened_at", None)
    cooldown = float(getattr(breaker, "cooldown_sec", 0.0) or 0.0)
    if opened_at is None or cooldown <= 0:
        return 0.0
    return max(0.0, cooldown - (time.monotonic() - float(opened_at)))




def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
