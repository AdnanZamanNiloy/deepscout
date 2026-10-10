"""Regression for the essay-length search-query defect (2026-10).

Live failure: "suggest me some highly demanding research topic in computer
science."

The planner emitted whole compound interrogatives as sub-questions, and
`contract_queries` sent them VERBATIM to the search provider:

    "What are the highest-strain open research problems in computer science as
     of 2026, ranked by community urgency and resource intensity, according to
     authoritative sources such as ACM, IEEE, CRA, and major conference
     keynote/roadmap reports?"

No search engine can match that. It returned generic arXiv/semantic-scholar
noise, so the critic re-reported the same unsourced angle every round and the
run eventually degraded. The full question must stay in the plan/UI; the SEARCH
must receive a compact, keyword query derived from it.
"""
from __future__ import annotations

from app.agents.searchkit.queries import _compact_search_query, contract_queries

_ESSAY = (
    "What are the highest-strain open research problems in computer science as "
    "of 2026, ranked by community urgency and resource intensity, according to "
    "authoritative sources such as ACM, IEEE, CRA, and major conference "
    "keynote/roadmap reports?"
)


def test_essay_length_question_is_compacted_for_search():
    out = _compact_search_query(_ESSAY)
    assert len(out.split()) <= 14
    # The subject and ask survive; the un-matchable qualifier tail does not.
    assert "research problems" in out.lower()
    assert "according to" not in out.lower()
    assert "keynote/roadmap" not in out.lower()


def test_short_search_ready_question_is_unchanged():
    for q in ("What is retrieval augmented generation?",
              "ACM IEEE grand challenges computer science 2026 roadmap",
              "NSF DARPA funding trends emerging computer science areas"):
        assert _compact_search_query(q) == q


def test_contract_queries_issues_a_compacted_base_query():
    contract = {
        "question": _ESSAY,
        "search_type": "academic",
        "domain": "general",
    }
    queries = contract_queries(contract)
    assert queries, "a contract must issue at least one query"
    base = queries[0]
    assert len(base.split()) <= 14
    assert "according to" not in base.lower()


def test_compaction_never_produces_an_empty_query():
    # Pathological input still yields something usable.
    out = _compact_search_query("What are the x as of 2026, ranked by a, according to b?")
    assert out.strip()
