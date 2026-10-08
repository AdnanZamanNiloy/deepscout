from __future__ import annotations

"""Node factory `make_verifier_node` (moved verbatim from `app/graph/workflow.py`).

Named node dependencies arrive as explicit parameters so `create_workflow`
passes its own module globals, preserving the monkeypatch test seam.
"""
import re
from app.graph.state import ResearchState, VerifierUpdate
from app.core.logging import get_logger

logger = get_logger(__name__)


def make_verifier_node(verify_facts):
    async def _node(state: ResearchState) -> VerifierUpdate:
        verified = verify_facts(
            facts=state.get("facts", []),
            search_results=state.get("search_results", []),
        )
        # Temporal integrity gate (hard validation against the system clock):
        # future-dated ASSERTED events are dropped, future-dated projections are
        # kept but labelled. Runs after verification so it sees published_at
        # that verification surfaced.
        from app.core.temporal import apply_temporal_gate

        verified, temporal_stats = apply_temporal_gate(verified)
        stats = {
            "total": len(verified),
            "verified": sum(1 for f in verified if f.get("verified")),
            "temporal_dropped": temporal_stats.get("dropped", 0),
            "temporal_projections": temporal_stats.get("projections", 0),
        }

        # Raw content is discarded once verification completes — verification
        # is the last consumer of full page text (it checks claims against
        # content, not just snippets); nothing downstream (critic, synthesizer,
        # finalize, later iterations) needs the full page. Blank in place: these
        # dict objects are shared with the contexts built in summarizer_node.
        # Before blanking, retain a BOUNDED excerpt: the expansion-pass
        # corroboration linker runs after verification and otherwise has only
        # short snippets to match a pending claim against. The excerpt is a
        # few hundred chars (memory-release rule still holds — the full page is
        # released), falling back to the snippet when content was already empty.
        from app.core.evidence_grade import CORROBORATION_EXCERPT_CHARS

        for result in state.get("search_results", []):
            if not isinstance(result, dict) or "content" not in result:
                continue
            raw = str(result.get("content", "") or "")
            if not raw:
                raw = str(result.get("snippet", "") or "")
            if raw and not result.get("corroboration_excerpt"):
                result["corroboration_excerpt"] = re.sub(
                    r"\s+", " ", raw
                ).strip()[:CORROBORATION_EXCERPT_CHARS]
            result["content"] = ""

        logger.info("verifier_done", **stats)
        return {"facts": verified, "verification_stats": stats}
    return _node
