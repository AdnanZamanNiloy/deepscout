"""Step 5: evidence grades (§A/B/C/D) drive the synthesis layer.

The synthesizer node returns the measured `evidence_distribution` in its
state update; the writer brief consumes it to set claim register. The UI
grade widget was removed, so the distribution is no longer streamed on the
`final_report` NDJSON event.
"""
import pytest

from app.core.config import Settings


async def test_synthesizer_node_returns_evidence_distribution(monkeypatch):
    """The synthesizer node must put the measured distribution in its update."""
    import app.graph.workflow as wf

    usable_facts = [
        {
            "claim": "RAG combines retrieval with generation",
            "source": "https://a.com",
            "confidence": 0.9,
            "verified": True,
        },
        {
            "claim": "Dense indexes serve the retriever",
            "source": "https://b.com",
            "confidence": 0.8,
            "verified": True,
        },
    ]

    class _Answer:
        passed = True
        overall = 90
        failures = []

        def to_dict(self):
            return {"overall": 90, "passed": True}

    async def fake_synthesizer(llm, query, facts, context):
        return "answer [1]"

    async def fake_check_citations(*args, **kwargs):
        return {"checked": 0, "sources": [], "summary": {}, "enabled": False}

    monkeypatch.setattr(wf, "synthesizer_agent", fake_synthesizer)
    monkeypatch.setattr(wf, "verify_answer_support", lambda *a, **k: {"rate": 1.0, "unsupported": []})
    monkeypatch.setattr(wf, "evaluate_answer", lambda *a, **k: _Answer())
    monkeypatch.setattr(
        "app.agents.citation_check.check_citations", fake_check_citations
    )

    class _LLM:
        settings = Settings(groq_api_key="k", _env_file=None)

    graph = wf.create_workflow(llm=_LLM(), search_client=None, entry_node=None)
    node = graph.nodes["synthesizer"]
    update = await node.ainvoke({
        "query": "what is RAG",
        "facts": usable_facts,
        "confidence": 0.8,
        "mode": "standard",
    })
    dist = update.get("evidence_distribution")
    assert isinstance(dist, dict)
    assert set(dist) == {"A", "B", "C", "D"}
    assert sum(dist.values()) == len(usable_facts)
