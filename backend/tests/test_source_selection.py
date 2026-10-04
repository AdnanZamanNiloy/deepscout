"""Tests for evidence-aware source selection.

Context
-------
A live research run asked "What is the latest revenue guidance for Nvidia from
its most recent earnings filing?" and returned eight encyclopedia pages
explaining what forward guidance means, above the issuer's own investor
relations pages. Authority scoring was working as designed; it just answered a
different question from the one that mattered. These tests pin the behaviour
that fixes that — evidence-type fit, definition misfit, entity engagement,
first-party preference — and the independence cap that stops one study's
republications from reading as corroboration.
"""
from __future__ import annotations

import pytest

from app.agents.evidence_type import classify_evidence_need, required_types
from app.agents.search import (
    ORIGIN_CAP,
    SearchResult,
    _deduplicate_and_rank,
    _score_result,
)
from app.agents.sources import (
    detect_primary_refs,
    definition_misfit,
    entity_miss,
    evidence_fit,
    classify_source,
    first_party_bonus,
    first_party_match,
    independent_primary_share,
    independence_ratio,
    is_original_source,
    looks_like_definition_page,
    underlying_source_key,
)


def _r(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


# ---------------------------------------------------------------------------
# Question form -> evidence type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What is the latest revenue guidance for Nvidia from its most recent earnings filing?", "filing"),
        ("How many deaths were attributed to air pollution globally in 2024?", "statistical"),
        ("Does peer-reviewed evidence support that remote work reduces productivity?", "academic"),
        ("What are the GDPR penalties for data breaches?", "legal"),
        ("What happened to the volcano in Iceland most recently?", "current"),
        ("Compare solid-state and sodium-ion grid storage viability in 2025", "comparison"),
        ("What is reinforcement learning?", "encyclopedic"),
    ],
)
def test_question_form_selects_evidence_type(question: str, expected: str) -> None:
    assert classify_evidence_need(question).primary == expected


def test_regulator_name_alone_does_not_make_a_question_a_filing() -> None:
    """The SEC is a filing authority, but "what did the SEC RULE about X" asks
    for the rule itself. Naming the body must not outrank the question's form."""
    assert classify_evidence_need("What did the SEC rule about climate disclosure rules?").primary == "legal"


def test_definition_question_is_not_flagged_as_non_definitional() -> None:
    """Regression guard on the two facts together: "What is the latest X" must
    NOT set asks_definition (it wants the latest X, not a definition of X),
    while "What is X" must."""
    assert classify_evidence_need("What is the latest Nvidia revenue guidance?").asks_definition is False
    assert classify_evidence_need("What is reinforcement learning?").asks_definition is True


def test_required_types_are_empty_for_an_unrecognised_question() -> None:
    """An unrecognised question must fit everything rather than block results."""
    need = classify_evidence_need("Hmm.")
    assert need.primary  # still has a safe default
    assert required_types(need) == frozenset()


# ---------------------------------------------------------------------------
# Definition misfit and entity engagement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Forward Guidance | Meaning, How It Works, & Examples",
        "What Is Forward Guidance?",
        "Glossary: forward guidance",
        "Forward guidance explained",
    ],
)
def test_definition_pages_are_recognised(title: str) -> None:
    assert looks_like_definition_page(title, "")


def test_definition_misfit_applies_to_non_definition_questions_only() -> None:
    page = "Forward Guidance | Meaning, How It Works"
    assert definition_misfit(page, "what forward guidance means", asks_definition=False) < 0
    assert definition_misfit(page, "what forward guidance means", asks_definition=True) == 0.0


def test_definition_penalty_is_applied_by_the_ranker() -> None:
    question = "What is the latest revenue guidance for Nvidia from its most recent earnings filing?"
    definition = _r("Forward Guidance | Meaning", "https://www.britannica.com/money/forward-guidance", "what forward guidance means")
    with_need = _score_result(definition, question, classify_evidence_need(question))
    without_need = _score_result(definition, question)
    assert with_need < without_need


def test_entity_miss_penalises_off_topic_pages_only() -> None:
    assert entity_miss("q", ("Nvidia",), "Nvidia raised its outlook") == 0.0
    assert entity_miss("q", ("Nvidia",), "a guide to energy markets") < 0
    # No subject tokens to miss: never penalise.
    assert entity_miss("q", (), "anything at all") == 0.0


# ---------------------------------------------------------------------------
# First-party sources
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,tokens,expected",
    [
        ("https://investor.nvidia.com/financial-reports", ("Nvidia",), "nvidia"),
        ("https://nvidianews.nvidia.com/news/q2", ("Nvidia",), "nvidia"),
        ("https://www.benzinga.com/quote/NVDA", ("Nvidia",), None),
        ("https://www.britannica.com/money/x", ("Nvidia",), None),
        ("https://example.com/a", ("com",), None),
    ],
)
def test_first_party_match(url: str, tokens: tuple, expected: str | None) -> None:
    assert first_party_match(url, tokens) == expected


def test_first_party_bonus_prefers_the_issuer_over_an_aggregator() -> None:
    issuer = "https://investor.nvidia.com/financial-reports"
    aggregator = "https://www.benzinga.com/quote/NVDA/earnings"
    assert first_party_bonus(issuer, ("Nvidia",)) > 0
    assert first_party_bonus(aggregator, ("Nvidia",)) == 0.0


# ---------------------------------------------------------------------------
# Evidence-type fit
# ---------------------------------------------------------------------------


def test_evidence_fit_prefers_official_for_a_filing_question() -> None:
    official = classify_source("https://www.sec.gov/Archives/edgar/data/1")
    reference = classify_source("https://www.britannica.com/money/forward-guidance")
    assert evidence_fit(official, ("filing",))[0] > evidence_fit(reference, ("filing",))[0]


def test_evidence_fit_prefers_peer_reviewed_for_an_academic_question() -> None:
    study = classify_source("https://doi.org/10.1038/s41586-020-2649-2")
    tabloid = classify_source("https://www.benzinga.com/quote/NVDA/earnings")
    assert evidence_fit(study, ("academic",))[0] > evidence_fit(tabloid, ("academic",))[0]


def test_evidence_fit_is_empty_for_unknown_evidence_types() -> None:
    profile = classify_source("https://www.sec.gov/x")
    assert evidence_fit(profile, ("not-a-real-type",))[0] == 0.0


# ---------------------------------------------------------------------------
# Original-source detection and independence
# ---------------------------------------------------------------------------


def test_primary_refs_normalise_doi_spellings() -> None:
    """A doi.org link, a bare DOI, and a doi: prefix are one original."""
    forms = [
        "https://doi.org/10.1038/S41586-020-2649-2",
        "The DOI is 10.1038/s41586-020-2649-2.",
        "as shown in doi:10.1038/s41586-020-2649-2",
    ]
    for form in forms:
        assert underlying_source_key(form, "Title") == "doi:10.1038/s41586-020-2649-2"


def test_arxiv_version_suffixes_collapse_to_one_original() -> None:
    v1 = underlying_source_key("https://arxiv.org/abs/2401.00001v1", "T")
    v2 = underlying_source_key("https://arxiv.org/abs/2401.00001", "T")
    cited = underlying_source_key("https://someblog.com/post", "T", "see arXiv:2401.00001v3")
    assert v1 == v2 == cited


def test_origins_are_recognised_and_quoting_pages_are_not() -> None:
    assert is_original_source("https://doi.org/10.1038/s41586-020-2649-2", "Title")
    assert not is_original_source("https://someblog.com/post", "Title", "we cite doi:10.1038/s41586-020-2649-2")


def test_detect_primary_refs_is_empty_for_plain_prose() -> None:
    assert detect_primary_refs("just some ordinary sentence") == ()


def test_republications_of_one_study_do_not_count_as_independent() -> None:
    doi = "10.1038/s41586-020-2649-2"
    original = (f"https://doi.org/{doi}", "Original", "study", "")
    summaries = [
        ("https://a.com/1", "A", f"reporting on doi:{doi}", ""),
        ("https://b.com/2", "B", f"summary of {doi}", ""),
    ]
    rows = [original, *summaries]
    assert independence_ratio(rows) == pytest.approx(1 / 3)
    # One independent source, and it is the primary study.
    assert independent_primary_share(rows) == 1.0


def test_genuinely_distinct_sources_are_all_independent() -> None:
    rows = [
        ("https://www.who.int/air", "WHO", "", ""),
        ("https://doi.org/10.1/x", "Paper", "", ""),
        ("https://arxiv.org/abs/2401.1", "Preprint", "", ""),
    ]
    assert independence_ratio(rows) == 1.0


def test_origins_are_independent_when_no_identifier_is_present() -> None:
    rows = [("https://a.com/1", "A", "", ""), ("https://b.com/2", "B", "", "")]
    assert independence_ratio(rows) == 1.0
    assert independence_ratio([]) == 1.0


# ---------------------------------------------------------------------------
# Ranking integration
# ---------------------------------------------------------------------------


def test_issuer_outranks_definition_pages_for_a_filing_question() -> None:
    """The regression that motivated all of this, as a test."""
    question = "What is the latest revenue guidance for Nvidia from its most recent earnings filing?"
    need = classify_evidence_need(question)
    results = [
        _r("Forward Guidance | Meaning, How It Works", "https://www.britannica.com/money/forward-guidance", "what forward guidance means"),
        _r("Forward Guidance | Explained", "https://www.britannica.com/business/forward-guidance", "a forward guidance statement gives revenue"),
        _r("NVIDIA Announces Financial Results", "https://nvidianews.nvidia.com/news/nvidia-q2", "NVIDIA announced quarterly revenue and raised its outlook"),
        _r("NVIDIA Corporation - Financial Reports", "https://investor.nvidia.com/financial-reports", "quarterly results and SEC filings for NVIDIA"),
        _r("NVIDIA earnings estimates", "https://www.benzinga.com/quote/NVDA/earnings", "NVIDIA earnings estimates and revenue guidance history"),
    ]
    ranked = [r.url for r in _deduplicate_and_rank(results, question, 10, "general", need=need)]
    assert ranked[0] == "https://nvidianews.nvidia.com/news/nvidia-q2"
    assert ranked[1] == "https://investor.nvidia.com/financial-reports"
    # Both definition pages must now sit below the issuer's own documents.
    assert ranked.index("https://www.britannica.com/money/forward-guidance") > 1
    assert ranked.index("https://www.britannica.com/business/forward-guidance") > 1


def test_definition_pages_still_win_when_a_definition_was_asked_for() -> None:
    """The penalties must not make encyclopedic questions unanswerable."""
    question = "What is reinforcement learning?"
    need = classify_evidence_need(question)
    results = [
        _r("Reinforcement learning", "https://en.wikipedia.org/wiki/Reinforcement_learning", "an area of machine learning"),
        _r("An Introduction to Reinforcement Learning", "https://www.britannica.com/science/reinforcement-learning", "what reinforcement learning is and how it works"),
        _r("Reinforcement Learning: A Review", "https://doi.org/10.1038/s41586-020-2649-2", "peer reviewed survey of reinforcement learning"),
    ]
    ranked = [r.url for r in _deduplicate_and_rank(results, question, 10, "general", need=need)]
    # Both orientation pages must outrank the peer-reviewed survey: for a
    # "what is X" question the survey is the wrong document type, however
    # authoritative its publisher is. Which orientation page wins is left to
    # ordinary authority ranking and is not the point of this test.
    survey = "https://doi.org/10.1038/s41586-020-2649-2"
    assert ranked.index("https://en.wikipedia.org/wiki/Reinforcement_learning") < ranked.index(survey)
    assert ranked.index("https://www.britannica.com/science/reinforcement-learning") < ranked.index(survey)


def test_republications_of_one_study_are_capped() -> None:
    """Five outlets quoting one study must not fill the result set."""
    doi = "10.1038/s41586-020-2649-2"
    results = [
        _r("Original study", f"https://doi.org/{doi}", "the study on remote work and productivity"),
        _r("Outlet A", "https://a.com/1", f"reporting on doi:{doi}"),
        _r("Outlet B", "https://b.com/2", f"summary of {doi}"),
        _r("Outlet C", "https://c.net/3", f"doi:{doi} findings"),
        _r("Outlet D", "https://d.org/4", f"paper {doi}"),
        _r("Outlet E", "https://e.com/5", f"coverage of {doi}"),
    ]
    selected = _deduplicate_and_rank(results, "evidence on remote work", 10, "academic")
    # The original is exempt from the cap, so it survives plus ORIGIN_CAP others.
    assert len(selected) == ORIGIN_CAP + 1
    assert f"https://doi.org/{doi}" in [r.url for r in selected]


def test_ranking_is_unchanged_when_no_evidence_need_is_supplied() -> None:
    """Callers that pass no need must get the previous behaviour, so this
    change cannot silently alter unrelated code paths."""
    question = "What is the latest revenue guidance for Nvidia?"
    result = _r("Forward Guidance | Meaning", "https://www.britannica.com/money/forward-guidance", "what forward guidance means")
    assert _score_result(result, question) == _score_result(result, question, None)
