"""The live wire must carry the same claims its replay shows.

``run.findings`` feeds the IntelligencePanel counts ("Total claims" /
"Verified claims"), the scout_swarm stage note, and the export summary
("N of M claims verified"). For a LIVE run nothing populated it: the backend
emitted no ``findings`` frame, and the live handler had no case for one
(``applyEvent``'s ``default: break`` drops an unmapped frame silently), while
a RESTORED run rebuilds ``run.findings`` from the persisted ``claims`` rows
(``replayPanelRun``). The same run read 0 claims live and real numbers on
reopen. The routes now emit one end-of-run ``findings`` frame built from the
annotated facts they already persist.

These tests pin the frame's shape (the frontend ``Finding`` contract, with
internal verification checks staying server-side), its ordering (before
``final_report``), its challenge flags, and its absence when there are no
facts.
"""

import json

import httpx

from app.api.routes import limiter, router as api_router
from app.common.config import Settings
from app.db.sqlite import init_db

FACTS = [
    {
        "claim": "Solid-state batteries improve energy density.",
        "source": "https://example.test/ssb",
        "confidence": 0.8,
        "agent": "pipeline",
        "verified": True,
        "verification_score": 0.71,
        "verification_reason": "overlap 0.71",
        "verification_checks": {"overlap": 0.71, "numeric": True},
    },
    {
        "claim": "Costs remain the bottleneck for solid-state cells.",
        "source": "https://example.test/cost",
        "confidence": 0.6,
        "agent": "pipeline",
        "verified": False,
        "verification_score": 0.12,
        "verification_reason": "no captured text",
        "verification_checks": {},
    },
]

# One side matches FACTS[1] case/whitespace-insensitively, so the live frame
# must mark it challenged exactly like the DB flagging does.
CONTRADICTIONS = [
    {"claim_a": "  COSTS remain the   bottleneck for Solid-State cells. ",
     "claim_b": "Cell costs are falling fast."},
]


class FakeRunner:
    """Minimal stand-in for ``PipelineRunner``'s route-facing surface."""

    facts = FACTS

    def __init__(self, **kwargs):
        self._final = {
            "final_report": "# Answer\n\n" + " ".join(f["claim"] for f in self.facts),
            "sources": [f["source"] for f in self.facts],
            "source_index": [],
            "facts": [dict(f) for f in self.facts],
            "contradictions": CONTRADICTIONS if self.facts else [],
            "confidence": 0.55,
        }

    async def run(self):
        if False:  # pragma: no cover - an empty async generator by design
            yield {}

    async def capture_cited_sources(self):
        return None

    def final_state(self):
        return dict(self._final)


class NoFactsRunner(FakeRunner):
    facts = []


async def _make_app(tmp_path, monkeypatch, runner_cls):
    db_path = str(tmp_path / "findings.db")
    await init_db(db_path)

    from app.runtime import orchestrator

    monkeypatch.setattr(orchestrator, "PipelineRunner", runner_cls)

    from fastapi import FastAPI

    app = FastAPI()
    app.state.settings = Settings(groq_api_key="k", database_url=db_path, _env_file=None)
    app.state.limiter = limiter
    app.include_router(api_router, prefix="/api")
    return app


async def _stream_frames(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/research/stream",
            json={"query": "solid state battery costs", "mode": "quick"},
        )
    assert response.status_code == 200
    frames = []
    for line in response.text.splitlines():
        line = line.strip()
        if line:
            frames.append(json.loads(line))
    errors = [f for f in frames if f.get("type") == "error"]
    assert not errors, errors
    return frames


async def test_findings_frame_arrives_before_final_report(tmp_path, monkeypatch):
    app = await _make_app(tmp_path, monkeypatch, FakeRunner)
    frames = await _stream_frames(app)

    types = [f.get("type") for f in frames]
    assert "findings" in types, "a live run must stream its claims, not only replay them"
    assert "final_report" in types
    findings_frame = frames[types.index("findings")]
    assert types.index("findings") < types.index("final_report"), (
        "claim counts belong with the answer, not after it"
    )
    assert findings_frame["verified_update"] is True

    items = findings_frame["items"]
    assert [i["verified"] for i in items] == [True, False]
    assert [i["claim"] for i in items] == [f["claim"] for f in FACTS]
    # Contradiction sides are matched by normalized text, like the DB flagging.
    assert [i["challenged"] for i in items] == [False, True]
    # Exactly the frontend `Finding` contract — internal checks stay server-side.
    assert set(items[0]) == {
        "claim", "source", "verified", "verification_score",
        "verification_reason", "agent", "confidence", "challenged",
    }
    assert "verification_checks" not in items[0]


async def test_run_without_facts_streams_no_findings_frame(tmp_path, monkeypatch):
    app = await _make_app(tmp_path, monkeypatch, NoFactsRunner)
    frames = await _stream_frames(app)

    types = [f.get("type") for f in frames]
    assert "final_report" in types
    assert "findings" not in types, (
        "zero claims is a truthful 0 — never invent an empty claims frame"
    )
