"""Response models for the agents added in this upgrade.

They live here rather than in `app.core.schemas` so this package drops into an
existing DeepScout checkout without touching the core module. Import them from
`app.core.schemas` instead if you prefer to consolidate later; the names are
chosen to not collide.

Pydantic is imported defensively. If it is unavailable the models degrade to
`None`, and the agents pass `response_model=None` — losing schema validation but
not the call, which is the right trade for an optional hardening layer.
"""
from __future__ import annotations

from typing import List

try:  # pragma: no cover - environment dependent
    from pydantic import BaseModel, Field

    _HAVE_PYDANTIC = True
except Exception:  # noqa: BLE001
    _HAVE_PYDANTIC = False
    BaseModel = object  # type: ignore

    def Field(default=None, **_kwargs):  # type: ignore
        return default


if _HAVE_PYDANTIC:

    class DecisionOptionModel(BaseModel):
        label: str = Field(default="")
        description: str = Field(default="")
        upside: str = Field(default="")
        downside: str = Field(default="")
        confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    class DecisionBriefModel(BaseModel):
        """Executive decision layer output (vision Feature 18)."""

        recommendation: str = Field(default="")
        rationale: str = Field(default="")
        options: List[DecisionOptionModel] = Field(default_factory=list)
        risks: List[str] = Field(default_factory=list)
        what_would_change_our_mind: List[str] = Field(default_factory=list)

else:  # pragma: no cover - only hit when pydantic is missing
    DecisionOptionModel = None  # type: ignore
    DecisionBriefModel = None  # type: ignore


__all__ = [
    "DecisionOptionModel",
    "DecisionBriefModel",
]
