from __future__ import annotations

"""LLMClient JSON-parsing methods, split into a mixin (refactor).

Moved verbatim from `app/core/llm.py`; `LLMClient` inherits this so behaviour
and the class's public surface are unchanged."""

import json
import re
from typing import Any, Dict

from app.core.logging import get_logger

logger = get_logger(__name__)

class JSONParseMixin:
        def _extract_json(self, text: str) -> Dict[str, Any]:
            """Best-effort JSON object extraction from a model response.

            Live providers return HTTP 200 with output that is not clean JSON:
            markdown code fences, leading prose ("Here is the JSON:"), trailing
            commentary, or a bare list. The old implementation stripped nothing and
            took a greedy `{.*}` slice, so a fence-wrapped payload or one with a
            brace in the trailing prose failed to parse — which cascaded the
            summarizer into its deterministic fallback and a false "degraded" run.
            This now unwraps fences and prose and parses the first balanced JSON
            value; a bare list is wrapped under `facts` so callers expecting an
            object still work.
            """
            text = (text or "").strip()
            if not text:
                raise ValueError("Empty model output")

            # Unwrap a markdown code fence (```json ... ``` or ``` ... ```).
            fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
            if fence:
                text = fence.group(1).strip()

            # Fast path: the whole (unwrapped) text is valid JSON.
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return {"facts": parsed}
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

            # Scan for the first balanced {...} or [...] value, ignoring braces in
            # leading/trailing prose and inside strings.
            for opening, closing in (("{", "}"), ("[", "]")):
                start = text.find(opening)
                if start == -1:
                    continue
                candidate = self._balanced_slice(text, start, opening, closing)
                if candidate is None:
                    continue
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, list):
                    return {"facts": parsed}
                if isinstance(parsed, dict):
                    return parsed
                return {"value": parsed}
            raise ValueError("No JSON object found in model output")

        @staticmethod
        def _balanced_slice(text: str, start: int, opening: str, closing: str) -> str | None:
            """Return the first bracket-balanced substring starting at `start`.

            String-aware: brackets inside quoted strings (and escaped quotes) do
            not change depth, so a claim containing "{" is not mistaken for the
            object's end. Returns None when no balanced value exists (a truncated
            generation) so the caller can try the other bracket type.
            """
            depth = 0
            in_string = False
            escaped = False
            for i in range(start, len(text)):
                ch = text[i]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == opening:
                    depth += 1
                elif ch == closing:
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
            return None
