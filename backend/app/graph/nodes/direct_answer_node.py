from __future__ import annotations

"""Node factory `make_direct_answer_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from typing import Any, Dict
from app.graph.state import ResearchState
from app.core.logging import get_logger

logger = get_logger(__name__)


def make_direct_answer_node(llm, direct_answer_agent):
    async def _node(state: ResearchState) -> Dict[str, Any]:
        """Answer a stable-knowledge query without any research (R3).

        Reached only when route_after_intent chose the direct path. The
        agent may refuse (needs_research) — in that case this node records
        the refusal and route_after_direct sends the query on to the normal
        planner, so a wrong routing decision costs one call, not an answer.

        Confidence is the model's OWN self-assessment, capped strictly below
        the research sufficiency threshold: an ungrounded answer must never
        read as a sourced one. No facts, no verification, no citations —
        the report states plainly that this was a direct answer.
        """
        result = await direct_answer_agent(
            llm, state["query"], intent=state.get("intent") or {}
        )
        meta = result.to_dict()

        if not result.usable:
            # Escape hatch: the agent refused, was unsure, or failed. Record
            # the refusal and let routing fall through to the research path.
            logger.info(
                "direct_answer_refused",
                needs_research=result.needs_research,
                confidence=result.confidence,
            )
            return {"direct_answer": "", "direct_answer_meta": meta}

        # Cap: strictly below the research sufficiency threshold (0.75) even
        # at full self-confidence. An ungrounded answer must never read as a
        # sourced one, and the cap is configurable so the gap is explicit.
        cap = float(
            getattr(
                getattr(llm, "settings", None),
                "direct_answer_confidence_cap",
                0.55,
            )
            or 0.55
        )
        capped = min(float(result.confidence), cap)
        logger.info(
            "direct_answer_delivered",
            chars=len(result.answer),
            self_confidence=result.confidence,
            capped_confidence=round(capped, 3),
        )
        return {
            "direct_answer": result.answer,
            "direct_answer_meta": meta,
            "confidence": capped,
        }
    return _node
