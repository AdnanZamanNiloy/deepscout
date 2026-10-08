from __future__ import annotations

"""LLMClient breaker/probing methods, split into a mixin (refactor).

Moved verbatim from `app/core/llm.py`; `LLMClient` inherits this so behaviour
and the class's public surface are unchanged."""

import asyncio
import time
from typing import Any, Dict, List

import httpx

from app.core.logging import get_logger
from app.core.llmkit.breaker import CircuitBreaker
from app.core.llmkit.timing import _real_key

logger = get_logger(__name__)

class ProbingMixin:
        def _breaker_kwargs(self) -> dict:
            """Circuit-breaker geometry, from Settings.

            The cooldown used to be a literal 60.0 at four construction sites. Sized
            against what a user waits for, 60s is indefensible: a healthy direct
            answer is ~2-3s end to end, so one upstream 503 parked the whole chain
            for a minute — observed as a 68s wait for a one-line answer. Now
            configurable, and short enough that a transient blip clears inside a
            user's patience.

            A 429 still does NOT open the breaker (see `CircuitBreaker.note_throttled`)
            — this is for outages, not throttling.
            """
            return {
                "threshold": int(getattr(self.settings, "llm_breaker_threshold", 3) or 3),
                "cooldown_sec": float(
                    getattr(self.settings, "llm_breaker_cooldown_sec", 15.0) or 15.0
                ),
            }

        def _chain_breaker(self, config: Dict[str, str]) -> CircuitBreaker:
            identity = (config["endpoint"], config["model"])
            breaker = self._chain_breakers.get(identity)
            if breaker is None:
                breaker = CircuitBreaker(**self._breaker_kwargs())
                self._chain_breakers[identity] = breaker
            return breaker

        async def probe_targets(self) -> List[Dict[str, str]]:
            """Providers a pre-flight probe should ping. An enabled chain → all
            of its members (the chain is the run's whole provider story).
            Exclusive active provider → that one only; otherwise the env chain."""
            chain = await self._resolve_chain()
            if chain:
                return [{**config, "style": "openai"} for config in chain]
            custom, exclusive = await self._resolve_custom()
            fallback_ok = bool(getattr(self.settings, "active_provider_fallback", False))
            targets: List[Dict[str, str]] = []
            if custom:
                targets.append({
                    "name": str(custom.get("name", "custom")),
                    "endpoint": custom["endpoint"],
                    "api_key": custom["api_key"],
                    "model": custom["model"],
                    "style": "openai",
                })
            if not exclusive or fallback_ok:
                groq_key = _real_key(self.settings.groq_api_key)
                if groq_key:
                    targets.append({
                        "name": "groq",
                        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
                        "api_key": groq_key,
                        "model": self.settings.groq_model,
                        "style": "openai",
                    })
                hf_key = _real_key(self.settings.huggingface_api_key)
                if hf_key:
                    targets.append({
                        "name": "huggingface",
                        "endpoint": f"https://api-inference.huggingface.co/models/{self.settings.huggingface_model}",
                        "api_key": hf_key,
                        "model": self.settings.huggingface_model,
                        "style": "hf",
                    })
            return targets

        async def probe_all(self, timeout: float = 10.0) -> tuple[bool, str]:
            """Pre-flight check for the research stream: one tiny parallel ping
            per configured provider, each capped at `timeout` seconds.

            Results are cached briefly (30s success / 10s failure) so a burst of
            queued requests doesn't re-ping providers — the probes themselves
            consume free-tier rate budget.

            Returns (any_ok, failure_detail). Fail-open on internal errors —
            a probe bug must never block research; the pipeline's own
            degradation handling still applies. When every provider fails the
            run is doomed (it would degrade to extraction), so the caller can
            fail fast with the per-provider reasons instead of wasting the
            research budget.
            """
            now = time.monotonic()
            if self._probe_cache is not None and now < self._probe_cache[0]:
                return self._probe_cache[1], self._probe_cache[2]
            from app.core.providers import ProviderSecretUnavailableError

            try:
                targets = await self.probe_targets()
                # Exclusivity scope: when a UI-selected active provider leads the
                # chain, it is the ONLY provider that matters for pre-flight health
                # (unless the user opted into env fallback). A healthy env provider
                # must never mask the active provider's 429 — that starts a run
                # optimistically against a failing primary and degrades mid-run.
                custom, exclusive = await self._resolve_custom()
                fallback_ok = bool(getattr(self.settings, "active_provider_fallback", False))
                active_name = str(custom.get("name", "custom")) if (custom and exclusive) else ""
            except ProviderSecretUnavailableError:
                # Fail-CLOSED for this one, unlike a probe bug. The store holds
                # keys that cannot be decrypted; letting the run start would spend
                # the whole research budget before failing on the first LLM call,
                # and the user would see "no provider configured" instead of the
                # real cause.
                raise
            except Exception as exc:
                logger.warning("probe target resolution failed, skipping pre-flight: %s", exc, exc_info=exc)
                return True, ""
            if not targets:
                return False, (
                    "no LLM provider is configured "
                    "(set GROQ_API_KEY / CUSTOM_LLM_* in .env, or add and select one in the Providers tab)"
                )

            async def _ping(target: Dict[str, str]) -> tuple[bool, str, str]:
                try:
                    if target["style"] == "hf":
                        payload: Dict[str, Any] = {
                            "inputs": "Reply with exactly: ok",
                            "parameters": {"max_new_tokens": 4, "return_full_text": False},
                        }
                    else:
                        payload = {
                            "model": target["model"],
                            "max_tokens": 8,
                            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                        }
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(
                            target["endpoint"],
                            headers={"Authorization": f"Bearer {target['api_key']}", "Content-Type": "application/json"},
                            json=payload,
                        )
                        response.raise_for_status()
                    return True, target["name"], ""
                except Exception as exc:
                    detail = str(exc).strip() or type(exc).__name__
                    return False, target["name"], f"{type(exc).__name__}: {detail[:140]}"

            results = await asyncio.gather(*(_ping(t) for t in targets))
            by_name = {name: (ok, err) for ok, name, err in results}
            # Exchange-scoped health: an exclusive active provider is the gate.
            # Its failure is reported even when a fallback env provider would
            # answer — the run's primary is down. With env fallback enabled the
            # chain can still serve calls, so a healthy fallback keeps the run
            # viable (any_ok semantics), but the active failure is named first.
            if active_name and not fallback_ok:
                ok, err = by_name.get(active_name, (False, "probe did not run"))
                if not ok:
                    detail = f"{active_name}: {err}" if err else active_name
                    self._probe_cache = None
                    return False, detail
                self._probe_cache = (time.monotonic() + 30.0, True, "")
                return True, ""
            any_ok = any(ok for ok, _, _ in results)
            if any_ok:
                # Cache successes only: a healthy provider stays healthy for the
                # next 30s, but failures must re-probe (the test suite's recovery
                # case, and users retrying after a quota reset, need fresh state).
                self._probe_cache = (time.monotonic() + 30.0, True, "")
                return True, ""
            detail = "; ".join(f"{name}: {err}" for _, name, err in results)
            self._probe_cache = None
            return False, detail

        async def _resolve_custom(self) -> tuple[Dict[str, str] | None, bool]:
            """(config, exclusive). A DB-selected active provider wins and is
            EXCLUSIVE — the user's explicit choice, so no other key is spent.
            Otherwise the env CUSTOM_LLM_* trio (non-exclusive, legacy chain)."""
            now = time.monotonic()
            if now >= self._provider_store_down_until:
                try:
                    from app.core.providers import (
                        ProviderSecretUnavailableError,
                        get_active_provider,
                    )

                    active = await get_active_provider(self.settings.database_url)
                except ProviderSecretUnavailableError:
                    # Stored keys exist but cannot be decrypted. Deliberately NOT
                    # folded into the "store unreadable, fall back to env" branch
                    # below: that branch degrades to env config and then reports
                    # "no provider configured", which is a lie when the UI is
                    # listing a selected provider. This is a repairable data
                    # problem, and it must reach the user as itself.
                    raise
                except Exception as exc:
                    # Warn once, then stay quiet for the hold window: the same
                    # missing table would otherwise log a traceback on every LLM
                    # call. exc_info is included only on the first failure.
                    logger.warning(
                        "[LLM] provider store unreadable, using env config: %s", exc,
                        exc_info=exc,
                    )
                    self._provider_store_down_until = now + 10.0
                    active = None
                else:
                    self._provider_store_down_until = 0.0
            else:
                active = None
            if active:
                base = str(active.get("base_url", "") or "").strip().rstrip("/")
                endpoint = base if base.lower().endswith("/chat/completions") else base + "/chat/completions"
                return {
                    "api_key": str(active.get("api_key", "") or ""),
                    "endpoint": endpoint,
                    "model": str(active.get("model", "") or ""),
                    "name": str(active.get("name", "") or "active provider"),
                    # May be None (unset), which _post_custom resolves to the global
                    # default. Carried as-is, never coerced here: 0 is a real choice.
                    "temperature": active.get("temperature"),
                }, True
            return self._custom_config(), False

        async def _resolve_chain(self) -> List[Dict[str, str]]:
            """Ordered, enabled chain members as call configs, or [] when no chain
            is enabled/unreadable. Members carry the decrypted key server-side
            only. Deduped by provider id by the store (loop protection)."""
            if not bool(getattr(self.settings, "provider_chains_enabled", True)):
                return []
            now = time.monotonic()
            if now < self._provider_store_down_until:
                return []
            try:
                from app.core.providers import (
                    ProviderSecretUnavailableError,
                    get_chain_providers,
                )

                rows = await get_chain_providers(self.settings.database_url)
            except ProviderSecretUnavailableError:
                # See _resolve_custom: an undecryptable store is a repairable data
                # problem, not an unreadable one. Propagate instead of silently
                # reporting an empty chain.
                raise
            except Exception as exc:
                logger.warning(
                    "[LLM] provider chain store unreadable, using single-provider chain: %s",
                    exc, exc_info=exc,
                )
                self._provider_store_down_until = now + 10.0
                return []
            self._provider_store_down_until = 0.0
            configs: List[Dict[str, str]] = []
            for row in rows or []:
                base = str(row.get("base_url", "") or "").strip().rstrip("/")
                if not base:
                    continue
                endpoint = base if base.lower().endswith("/chat/completions") else base + "/chat/completions"
                key = str(row.get("api_key", "") or "")
                model = str(row.get("model", "") or "")
                if not (key and model):
                    # A member with no usable key/model is skipped, not fatal:
                    # it must not block the rest of the chain.
                    logger.warning("[LLM] chain member '%s' has no key/model, skipping", row.get("name"))
                    continue
                configs.append({
                    "api_key": key,
                    "endpoint": endpoint,
                    "model": model,
                    "name": str(row.get("name", "") or "provider"),
                    "temperature": row.get("temperature"),
                })
            return configs
