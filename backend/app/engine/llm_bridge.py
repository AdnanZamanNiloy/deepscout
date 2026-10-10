"""LLM bridge: route engine LLM calls through DeepScout's LLM client.

The engine talks to models through a single funnel,
``gptr.utils.llm.create_chat_completion`` (every engine module imports it by
that name), plus ``multi_agents.agents.utils.llms.call_model``. Both are patched
here to delegate to the backend's :class:`app.core.llm.LLMClient`, so:

* the Model Control Center governs what actually runs — the active provider or
  the enabled fallback chain selected in the UI is what the engine uses, with no
  separate provider configuration;
* circuit breakers, the response cache, the usage ledger and the concurrency
  semaphore all apply to engine calls exactly as they do to the built-in
  pipeline.

The bridge is intentionally narrow: it accepts the engine's OpenAI-style
``messages`` list and returns a completion string. Token/JSON handling stays in
the engine, which already parses its own outputs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.llm import LLMClient
from app.core.logging import get_logger

logger = get_logger(__name__)


def _messages_to_prompt(messages: List[Dict[str, Any]]) -> tuple[str, str]:
    """Flatten OpenAI-style messages into (system_prompt, user_prompt).

    The backend's ``LLMClient`` takes a system/user pair rather than a message
    array. Multi-turn histories are joined in order into the user prompt so no
    content is dropped; a dedicated system message becomes the system prompt.
    """
    system_parts: List[str] = []
    user_parts: List[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            user_parts.append(str(message))
            continue
        role = str(message.get("role", "user") or "user").lower()
        content = message.get("content")
        if isinstance(content, list):
            # OpenAI content-parts shape: keep the text pieces in order.
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        text = str(content or "")
        if role == "system":
            system_parts.append(text)
        else:
            # Assistant turns are labelled so the model can tell them apart from
            # the current instruction when several are concatenated.
            prefix = "Assistant: " if role == "assistant" else ""
            user_parts.append(prefix + text)
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


class EngineLLMBridge:
    """Adapts :class:`LLMClient` to the engine's completion signature."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def create_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        llm_provider: Optional[str] = None,
        stream: bool = False,
        websocket: Any = None,
        llm_kwargs: Optional[Dict[str, Any]] = None,
        cost_callback: Any = None,
        reasoning_effort: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Drop-in replacement for ``gptr.utils.llm.create_chat_completion``.

        ``model``/``temperature``/``llm_provider``/``max_tokens`` from the
        engine's config are accepted but not authoritative: the selected
        provider (Model Control Center) owns the endpoint and model. A
        temperature configured on the provider row is likewise owned by the
        client.
        """
        system_prompt, user_prompt = _messages_to_prompt(messages)
        text = await self.llm._generate_with_fallback(system_prompt, user_prompt)
        if cost_callback is not None:
            # The usage ledger already records spend; the engine's own cost
            # callback is kept as a no-op seam so callers need no change.
            try:
                cost_callback(0.0)
            except Exception:  # pragma: no cover - observer must not break flow
                pass
        return text or ""


def install(llm: LLMClient) -> EngineLLMBridge:
    """Patch the engine's LLM funnel to use ``llm``. Idempotent per process.

    Import-time side effect is avoided on purpose: the engine packages can be
    imported (e.g. by tests) without an initialized LLM client. ``install`` is
    called once at app startup.
    """
    bridge = EngineLLMBridge(llm)

    import gptr.utils.llm as gptr_llm

    gptr_llm.create_chat_completion = bridge.create_chat_completion

    try:
        import multi_agents.agents.utils.llms as ma_llms

        async def _call_model(prompt, model, response_format=None):  # noqa: ANN001
            import json_repair
            from langchain_core.utils.json import parse_json_markdown

            response = await bridge.create_chat_completion(messages=prompt, model=model)
            if response_format == "json":
                return parse_json_markdown(response, parser=json_repair.loads)
            return response

        ma_llms.create_chat_completion = bridge.create_chat_completion
        ma_llms.call_model = _call_model
    except Exception as exc:  # pragma: no cover - defensive, engine must import
        logger.warning("[EngineLLMBridge] could not patch multi_agents llms: %s", exc)

    logger.info("[EngineLLMBridge] engine LLM calls routed through LLMClient")
    return bridge
