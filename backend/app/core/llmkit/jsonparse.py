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


def payload_is_empty_container(payload: Any) -> bool:
    """True when a well-formed container carries an EMPTY collection.

    `{"facts": []}` is the shape a model returns when it was asked to extract
    from sources that contain nothing relevant: it answered the question, and
    the answer is "nothing here". That is information — a correct, honest "no"
    — and must be distinguished from a model that emitted no fields at all
    (which is what `payload_says_nothing` covers).

    Specifically: the payload has at least one key whose value is a list/tuple,
    and every value is empty (empty collection, "", or None). A payload with a
    non-empty string (e.g. `{"reason": "no sources matched"}`) is NOT an empty
    container — it carries prose and must be treated as a real answer.
    """
    if not isinstance(payload, dict) or not payload:
        return False
    has_collection = False
    for value in payload.values():
        if isinstance(value, (list, tuple, set)):
            has_collection = True
            if any(
                (item.strip() if isinstance(item, str) else bool(item))
                for item in value
            ):
                return False
        elif isinstance(value, dict):
            if len(value) > 0:
                return False
        elif isinstance(value, str):
            if value.strip():
                return False
        elif isinstance(value, bool):
            return False
        elif isinstance(value, (int, float)):
            return False
        elif value is not None:
            return False
    return has_collection


def payload_says_nothing(payload: Any) -> bool:
    """True when a model's JSON carries no information.

    An empty object, or one where every value is empty (None, "", [], {}).
    This is the shape a model that emitted nothing but still returned 200
    produces, and it is distinct from a wrong-shaped answer, which contains
    information and is handled by validation.
    """
    if not isinstance(payload, dict) or not payload:
        return True
    for value in payload.values():
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return False
        elif isinstance(value, (list, tuple, set)):
            # A list of empty strings is an empty list in meaning: the model
            # produced slots, not content.
            if any(
                (item.strip() if isinstance(item, str) else bool(item))
                for item in value
            ):
                return False
        elif isinstance(value, dict):
            if len(value) > 0:
                return False
        elif isinstance(value, bool):
            # An explicit False is a real answer (e.g. "no contradiction").
            return False
        elif isinstance(value, (int, float)):
            # Any number, including 0, is a real answer.
            return False
        else:
            return False
    return True


def text_says_nothing(text: str) -> bool:
    """True when a model response carries no JSON content worth caching.

    Checks the RAW text rather than a parsed payload so it can be used at the
    cache-write site, which has not parsed anything yet. A parse failure is NOT
    "nothing" — a truncated body is a malformed response, not an empty one, and
    the parsed check in `generate_json` handles that case by retrying.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped in ("{}", "[]", "null", "None"):
        return True
    # Unwrap code fences before judging, so a fence-wrapped empty object is
    # still recognised as empty.
    fenced = stripped
    if fenced.startswith("```"):
        fenced = fenced.split("\n", 1)[-1] if "\n" in fenced else fenced
        fenced = re.sub(r"```\s*$", "", fenced).strip()
    try:
        inner = json.loads(fenced)
    except Exception:
        return False
    return inner is None or inner == {} or inner == []

