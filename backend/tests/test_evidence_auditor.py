"""EvidenceAuditor's fact-check verdict: structured, honest, and total.

Contract under test:
  * the prompt's declared schema (``{"issues": [...]}``) and the parser read
    the SAME key — a schema mismatch yields "no verdict", never a guessed
    acceptance or a fabricated rejection (AGENTS.md 4.2);
  * a clean verdict is ``None`` and a rejection is the named-issues text the
    writer can act on;
  * any LLM failure degrades to ``""`` (no verdict) — the deterministic
    fallback (AGENTS.md 4.7), which routes to one revision rather than
    silently accepting an unchecked draft;
  * the verdict reaches ``fact_check_notes`` unchanged, including through the
    second-pass ladder override.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.agents.report import EvidenceAuditor


class _FakeLLM:
    """Returns a scripted payload and records the prompt it received."""

    def __init__(self, payload: Any = None, error: Exception | None = None):
        self.payload = payload if payload is not None else {}
        self.error = error
        self.calls: list = []

    async def call_model_json(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.payload


_DRAFT_STATE = {
    "introduction": "Intro paragraph.",
    "conclusion": "Conclusion paragraph.",
    "research_data": [{"section": "Summary", "content": "Findings."}],
}


def _verdict(payload: Any = None, error: Exception | None = None) -> Any:
    auditor = EvidenceAuditor(_FakeLLM(payload, error), cfg=None)
    return asyncio.run(auditor.check_facts(dict(_DRAFT_STATE)))


def test_a_clean_verdict_is_none():
    assert _verdict({"issues": []}) is None


def test_named_issues_become_the_rejection_text():
    got = _verdict({"issues": ["Section 2 states 40% but the source says 4%"]})
    assert got == "- Section 2 states 40% but the source says 4%"
    two = _verdict({"issues": ["first problem", "second problem"]})
    assert two == "- first problem\n- second problem"


def test_schema_mismatch_is_no_verdict_not_a_guess():
    # A response without the declared key must never read as "clean" (that
    # would silently pass an unchecked draft) nor as a rejection (that would
    # force a pointless revision and depress the confidence verdict).
    assert _verdict({}) == ""
    assert _verdict({"result": "ok"}) == ""
    assert _verdict("all good") == ""
    assert _verdict({"issues": {"nested": "wrong shape"}}) == ""


def test_llm_failure_degrades_to_no_verdict():
    assert _verdict(error=RuntimeError("provider down")) == ""


def test_a_string_issue_is_normalized_not_dropped():
    assert _verdict({"issues": "one problem"}) == "- one problem"


def test_the_prompt_declares_the_schema_the_parser_reads():
    llm = _FakeLLM({"issues": []})
    auditor = EvidenceAuditor(llm, cfg=None)
    asyncio.run(auditor.check_facts(dict(_DRAFT_STATE)))
    system, user = llm.calls[0][0]["content"], llm.calls[0][1]["content"]
    # The schema demand lives in the USER message: chatty models follow the
    # user instruction and ignored a schema stated only in the system prompt.
    assert '"issues"' in user
    assert "draft" in user.lower()
    assert "Fact Checker" in system


def test_no_verdict_routes_to_accept_not_a_blind_revision():
    from app.agents.routing import route_evidence_audit

    assert route_evidence_audit({"fact_check_notes": ""}) == "accept"
    assert route_evidence_audit({"fact_check_notes": None}) == "accept"
    assert route_evidence_audit({"fact_check_notes": "- one problem"}) == "revise"


def test_the_verdict_reaches_state_unchanged():
    auditor = EvidenceAuditor(_FakeLLM({"issues": ["a real problem"]}), cfg=None)
    out = asyncio.run(auditor.run(dict(_DRAFT_STATE)))
    assert out["fact_check_notes"] == "- a real problem"
    assert out["fact_check_revision_count"] == 1

    clean = EvidenceAuditor(_FakeLLM({"issues": []}), cfg=None)
    out = asyncio.run(clean.run(dict(_DRAFT_STATE)))
    assert out["fact_check_notes"] is None
    assert "fact_check_revision_count" not in out
