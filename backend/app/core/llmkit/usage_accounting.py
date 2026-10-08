from __future__ import annotations

"""LLMClient usage/accounting methods, split into a mixin (refactor).

Moved verbatim from `app/core/llm.py`; `LLMClient` inherits this so behaviour
and the class's public surface are unchanged."""

import httpx

from app.core import llm_cache
from app.core.degradation import record_provider_failure
from app.core.logging import get_logger
from app.core.llmkit.breaker import CircuitBreaker
from app.core.llmkit.failures import _is_rate_limit, classify_provider_failure
from app.core.llmkit.types import CompletionResult, PromptTooLargeError
from app.core.usage import get_run_usage

logger = get_logger(__name__)

class UsageAccountingMixin:
        @staticmethod
        def _record_usage(
            text: str,
            *,
            provider: str,
            model: str,
            input_tokens: int | None,
            output_tokens: int | None,
            cached: bool = False,
            system_prompt: str = "",
            user_prompt: str = "",
        ) -> None:
            """Feed the per-run ledger if one is active (research runs only).

            Outside a run (provider probes, tests) there is no ledger and
            nothing is recorded — the call still works exactly as before."""
            usage = get_run_usage()
            if usage is None:
                return
            stage = usage.stage_hint or "llm"
            try:
                # `cached` is threaded into the budget so a cache hit records $0
                # at write time — no post-hoc "refund the last record" step that
                # could subtract the wrong amount if records interleave.
                usage.record_llm(
                    stage,
                    prompt=f"{system_prompt}\n{user_prompt}"[:20000],
                    completion=text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model=model or None,
                    cached=cached,
                )
            except Exception as exc:  # accounting must never break generation
                logger.warning("usage recording failed: %s", exc)

        def _cache_and_record(
            self, result: "CompletionResult", system_prompt: str, user_prompt: str
        ) -> None:
            """Persist a successful completion and feed the run ledger."""
            llm_cache.put(
                result.endpoint,
                result.model,
                system_prompt,
                user_prompt,
                result.text,
                input_tokens=result.input_tokens or None,
                output_tokens=result.output_tokens or None,
            )
            tin, tout = result.input_tokens, result.output_tokens
            if not tin:
                from app.agents.budget import estimate_tokens
                tin = estimate_tokens(f"{system_prompt}\n{user_prompt}")
            if not tout:
                from app.agents.budget import estimate_tokens
                tout = estimate_tokens(result.text)
            self._record_usage(
                result.text,
                provider=result.provider,
                model=result.model,
                input_tokens=tin,
                output_tokens=tout,
                cached=False,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

        @staticmethod
        def _record_provider_failure(breaker: CircuitBreaker, exc: Exception) -> None:
            """Timeouts open the breaker immediately (one 25s stall is enough
            signal inside a 90s budget); fast failures use the normal
            threshold counter. PromptTooLargeError records NOTHING: the provider
            is healthy — our payload was too big — and sizing retries must not
            trip the breaker into skipping that provider. A 429 records a
            rate-limit (brief, non-opening) rather than a failure: a throttle is
            not an outage."""
            if isinstance(exc, PromptTooLargeError):
                return
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                breaker.record_timeout()
            elif _is_rate_limit(exc):
                breaker.record_rate_limit()
            else:
                breaker.record_failure()

        @staticmethod
        def _record_degradation(exc: Exception) -> None:
            """Attribute an exhausted provider leg to a transport cause.

            Recorded ONLY when the exception escaped the per-provider retry loop
            (a 429 that recovered in-place never reaches here, so a healthy retry
            records nothing). The classification — transient vs hard — is exactly
            what lets a run say "the provider failed" instead of silently reading
            as weak evidence (reliability #4)."""
            if isinstance(exc, PromptTooLargeError):
                # Provider is healthy; caller shrinks the payload. Not degradation.
                return
            kind = classify_provider_failure(exc)
            if kind:
                record_provider_failure(kind, f"{type(exc).__name__}: {exc}"[:200])
