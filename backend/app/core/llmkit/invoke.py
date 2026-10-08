from __future__ import annotations

"""LLMClient provider-call methods, split into a mixin (refactor).

Moved verbatim from `app/core/llm.py`; `LLMClient` inherits this so behaviour
and the class's public surface are unchanged."""

import httpx
from typing import Any, Dict, List, Mapping

from tenacity import retry, retry_if_exception, stop_after_attempt

from app.core.logging import get_logger
from app.core.llmkit.timing import HF_FALLBACK_MODELS, _attempt_timeout, _real_key, _wait_with_retry_after, _with_total_cap
from app.core.llmkit.failures import _is_retryable
from app.core.llmkit.types import CompletionResult, PromptTooLargeError, _parse_usage

logger = get_logger(__name__)

class ProviderInvocationMixin:
        def _custom_config(self) -> Dict[str, str] | None:
            """Validated custom-provider trio, or None when not configured."""
            key = _real_key(self.settings.custom_llm_api_key)
            base = str(self.settings.custom_llm_base_url or "").strip().rstrip("/")
            model = str(self.settings.custom_llm_model or "").strip()
            if not (key and base and model):
                return None
            if base.lower().endswith("/chat/completions"):
                endpoint = base
            else:
                endpoint = base + "/chat/completions"
            return {"api_key": key, "endpoint": endpoint, "model": model}

        def _temperature_for(self, custom: Mapping[str, Any]) -> float:
            """Sampling temperature for a custom provider.

            An explicit per-provider value wins; unset (None) falls back to the
            configured default. `None` and `0` are kept distinct on purpose: 0 is
            the deterministic setting some models require, so treating a falsy 0 as
            "unset" would send 0.1 to exactly the provider that cannot take it.
            """
            value = custom.get("temperature") if isinstance(custom, Mapping) else None
            if value is None:
                return float(getattr(self.settings, "llm_temperature", 0.1) or 0.1)
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(getattr(self.settings, "llm_temperature", 0.1) or 0.1)

        @retry(
            reraise=True,
            stop=stop_after_attempt(4),
            wait=_wait_with_retry_after,
            retry=retry_if_exception(_is_retryable),
        )
        async def _call_custom(self, system_prompt: str, user_prompt: str, custom: Dict[str, str]) -> CompletionResult:
            """Generic OpenAI-compatible chat completions call.

            Uses custom_llm_timeout_sec (not the shared llm_timeout_sec):
            slower third-party providers routinely need 30-60s on planner-sized
            prompts, and a premature ReadTimeout opens the breaker and degrades
            the whole run. The whole attempt sits under a hard wall-clock cap
            (_with_total_cap) so a drip-feeding server cannot hang the stage.
            """
            timeout_base = self.settings.custom_llm_timeout_sec

            async def _do() -> CompletionResult:
                return await self._post_custom(system_prompt, user_prompt, custom, timeout_base)

            return await _with_total_cap(_do, timeout_base)

        async def _post_custom(self, system_prompt: str, user_prompt: str,
                               custom: Dict[str, str], timeout_base: float) -> CompletionResult:
            async with httpx.AsyncClient(timeout=_attempt_timeout(timeout_base)) as client:
                response = await client.post(
                    custom["endpoint"],
                    headers={
                        "Authorization": f"Bearer {custom['api_key']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": custom["model"],
                        # Per-provider temperature when the user set one, else the
                        # global default. This was hardcoded 0.1 until a model that
                        # accepts ONLY 0/0.6/1 rejected every single call with a 400
                        # ("invalid temperature"), which degraded the whole pipeline
                        # to extraction with no way for the user to fix it.
                        "temperature": self._temperature_for(custom),
                        # generate_json always JSON-parses the reply, so request
                        # JSON mode instead of hoping the model obeys the prompt.
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                if response.status_code == 413:
                    raise PromptTooLargeError(
                        "provider rejected request size (custom provider)"
                    )
                response.raise_for_status()
                data = response.json()
                tin, tout = _parse_usage(data)
                return CompletionResult(
                    text=data["choices"][0]["message"]["content"],
                    provider="custom",
                    model=custom["model"],
                    endpoint=custom["endpoint"],
                    input_tokens=tin,
                    output_tokens=tout,
                )

        @retry(
            reraise=True,
            stop=stop_after_attempt(4),
            wait=_wait_with_retry_after,
            retry=retry_if_exception(_is_retryable),
        )
        async def _call_groq(self, system_prompt: str, user_prompt: str) -> CompletionResult:
            return await _with_total_cap(
                lambda: self._post_groq(system_prompt, user_prompt),
                self.settings.llm_timeout_sec,
            )

        async def _post_groq(self, system_prompt: str, user_prompt: str) -> CompletionResult:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.settings.groq_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.settings.groq_model,
                "temperature": 0.1,
                # generate_json always JSON-parses the reply, so request JSON
                # mode instead of hoping the model obeys the prompt.
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            # Per-attempt timeout is llm_timeout_sec; the decorator bounds retries
            # so total wait stays a small multiple of one attempt.
            async with httpx.AsyncClient(
                timeout=_attempt_timeout(self.settings.llm_timeout_sec)
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 413:
                    raise PromptTooLargeError("provider rejected request size (groq)")
                response.raise_for_status()
                data = response.json()
                tin, tout = _parse_usage(data)
                return CompletionResult(
                    text=data["choices"][0]["message"]["content"],
                    provider="groq",
                    model=self.settings.groq_model,
                    endpoint=url,
                    input_tokens=tin,
                    output_tokens=tout,
                )

        @retry(
            reraise=True,
            stop=stop_after_attempt(2),
            wait=_wait_with_retry_after,
            retry=retry_if_exception(_is_retryable),
        )
        async def _call_huggingface(self, system_prompt: str, user_prompt: str) -> CompletionResult:
            return await _with_total_cap(
                lambda: self._post_huggingface(system_prompt, user_prompt),
                self.settings.llm_timeout_sec,
            )

        async def _post_huggingface(self, system_prompt: str, user_prompt: str) -> CompletionResult:
            headers = {
                "Authorization": f"Bearer {self.settings.huggingface_api_key}",
                "Content-Type": "application/json",
            }
            prompt = (
                "You are a precise research assistant. Return valid JSON only.\n\n"
                f"System: {system_prompt}\n\n"
                f"User: {user_prompt}"
            )
            payload: Dict[str, Any] = {
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 500,
                    "temperature": 0.2,
                    "return_full_text": False,
                },
            }

            model_candidates = [self.settings.huggingface_model, *[m for m in HF_FALLBACK_MODELS if m != self.settings.huggingface_model]]
            not_available_errors: List[str] = []

            async with httpx.AsyncClient(
                timeout=_attempt_timeout(self.settings.llm_timeout_sec)
            ) as client:
                for model_name in model_candidates:
                    url = f"https://api-inference.huggingface.co/models/{model_name}"
                    response = await client.post(url, headers=headers, json=payload)

                    if response.status_code in (404, 410):
                        not_available_errors.append(f"{model_name} -> {response.status_code}")
                        continue

                    response.raise_for_status()
                    data = response.json()

                    text = None
                    if isinstance(data, list) and data and "generated_text" in data[0]:
                        text = data[0]["generated_text"]
                    elif isinstance(data, dict) and "generated_text" in data:
                        text = data["generated_text"]
                    elif isinstance(data, dict) and "error" in data:
                        # Model can be valid but unavailable due to provider-side load.
                        raise RuntimeError(f"HuggingFace model '{model_name}' error: {data.get('error')}")
                    if text is not None:
                        # HF inference returns bare text: estimate token usage.
                        from app.core.llmkit.usage_accounting import _estimate_tokens
                        return CompletionResult(
                            text=text,
                            provider="huggingface",
                            model=model_name,
                            endpoint=url,
                            input_tokens=_estimate_tokens(prompt),
                            output_tokens=_estimate_tokens(text),
                        )

            tried = ", ".join(not_available_errors) if not_available_errors else "no models tried"
            raise RuntimeError(
                "HuggingFace inference models are unavailable (404/410). "
                "Set HUGGINGFACE_MODEL in .env to an available model. "
                f"Tried: {tried}"
            )
