"""Generation funnel for the research pipeline.

Every model call in the pipeline goes through :func:`create_chat_completion`,
which flattens the pipeline's OpenAI-style message list into the
(system, user) pair that :class:`app.core.llm.LLMClient` expects and delegates
to it. Routing through the client means the Model Control Center's selected
provider (and its fallback chain), circuit breakers, response cache, usage
ledger and concurrency semaphore all apply to pipeline calls exactly as they do
elsewhere in the product.

:func:`call_model` is the JSON-shaped convenience used by the planning and
review stages: it asks the client for a parsed object and degrades to ``{}``
when the model returns nothing usable, so a caller can always ``.get(...)`` the
result.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.llm import LLMClient
from app.core.logging import get_logger

logger = get_logger(__name__)


def flatten_messages(messages: List[Dict[str, Any]]) -> tuple[str, str]:
    """Flatten an OpenAI-style message list into (system_prompt, user_prompt).

    The clinical client takes a system/user pair. A dedicated system message
    becomes the system prompt; every other turn is joined into the user prompt
    in order, with assistant turns labelled so the model can tell them from the
    current instruction when several are concatenated. Content-part lists (the
    OpenAI ``[{type: text, text: ...}]`` shape) keep only their text pieces.
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
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        text = str(content or "")
        if role == "system":
            system_parts.append(text)
        else:
            prefix = "Assistant: " if role == "assistant" else ""
            user_parts.append(prefix + text)
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


class LLMFunnel:
    """Adapter binding the pipeline to a :class:`LLMClient` for one run.

    Held by the researcher and passed to the skills. It is intentionally thin —
    the client owns retries, timeouts and provider selection — so this class
    exists only to translate the pipeline's call shape and to keep a single,
    testable seam.
    """

    def __init__(self, client: LLMClient):
        self.client = client

    async def create_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        websocket: Any = None,
        cost_callback: Any = None,
        **kwargs: Any,
    ) -> str:
        """Return a completion string for ``messages``.

        ``model``/``temperature``/``max_tokens``/``stream`` come from the call
        site's config but are not authoritative: the selected provider owns the
        endpoint, model and sampling settings. They are accepted so existing
        call sites need no change.
        """
        system_prompt, user_prompt = flatten_messages(messages)
        try:
            text = await self.client._generate_with_fallback(system_prompt, user_prompt)
        except Exception as exc:
            logger.warning("[LLMFunnel] generation failed: %s", exc, exc_info=exc)
            text = ""
        if cost_callback is not None:
            # The usage ledger records real spend; the pipeline's own cost
            # callback stays a no-op seam so callers are unchanged.
            try:
                cost_callback(0.0)
            except Exception:  # pragma: no cover - observer must not break flow
                pass
        return text or ""

    async def call_model_json(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """Return the model's JSON response as a dict, or ``{}`` on failure."""
        system_prompt, user_prompt = flatten_messages(messages)
        try:
            return await self.client.generate_json(
                system_prompt, user_prompt, retries=retries
            )
        except Exception as exc:
            logger.warning("[LLMFunnel] JSON generation failed: %s", exc, exc_info=exc)
            return {}
