from __future__ import annotations

"""Node factory `make_finalize_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
from app.core.decision import build_decision_layer
from app.graph.reports import build_answer_audit, build_conversation_report, build_direct_answer_report, build_markdown_report
from app.graph.state import FinalizeUpdate, ResearchState


def make_finalize_node():
    async def _node(state: ResearchState) -> FinalizeUpdate:
        # Direct-answer path (R3): the answer was produced with no facts,
        # sources or verification. A research-shaped report (supporting
        # evidence, contradictions, decision layer) would be fabricated
        # structure around an ungrounded answer, so the direct path gets its
        # own minimal, honest report instead.
        direct = str(state.get("direct_answer", "") or "").strip()
        if direct:
            meta = state.get("direct_answer_meta") or {}
            if meta.get("conversation_kind"):
                return {
                    "final_report": build_conversation_report(state),
                    "final_audit": "",
                    "decision_options": [],
                }
            return {
                "final_report": build_direct_answer_report(state),
                "final_audit": "",
                "decision_options": [],
            }
        # Decision options computed once and shared with the report builder.
        options = build_decision_layer(state)
        # Primary answer is the synthesizer's output verbatim; the machine-owned
        # disclosure moves to the separate audit document.
        return {
            "final_report": build_markdown_report(state, decision_options=options),
            "final_audit": build_answer_audit(state, decision_options=options),
            "decision_options": options,
        }
    return _node
