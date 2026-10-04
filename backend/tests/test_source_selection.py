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
    # Each outlet snippet names the study's subject as a real report would. The
    # topicality floor drops documents that engage nothing the question is
    # about, so a fixture whose snippets are bare DOIs would measure the floor
    # rather than the independence cap.
    results = [
        _r("Original study", f"https://doi.org/{doi}", "the study on remote work and productivity"),
        _r("Outlet A", "https://a.com/1", f"reporting on doi:{doi} and remote work evidence"),
        _r("Outlet B", "https://b.com/2", f"summary of {doi} on remote work and productivity"),
        _r("Outlet C", "https://c.net/3", f"doi:{doi} findings on remote work evidence"),
        _r("Outlet D", "https://d.org/4", f"paper {doi} about remote work productivity evidence"),
        _r("Outlet E", "https://e.com/5", f"coverage of {doi} and the remote work evidence"),
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


# ---------------------------------------------------------------------------
# Soft site: targets steer ranking instead of filtering the provider
# ---------------------------------------------------------------------------


def test_preferred_publisher_gets_a_ranking_bonus() -> None:
    """A `site:` hint the provider no longer filters on must still buy
    something, or removing the filter would have thrown the steering away."""
    question = "global AI capital expenditure 2025"
    result = _r("AI capex", "https://data.worldbank.org/ai", "AI capital expenditure")
    assert _score_result(result, question, None, ("worldbank.org",)) > _score_result(
        result, question, None
    )


def test_preference_matches_subdomains() -> None:
    from app.agents.search import _preferred_domain_bonus

    assert _preferred_domain_bonus("https://bbs.gov.bd/report", ("gov.bd",)) > 0
    assert _preferred_domain_bonus("https://data.worldbank.org/x", ("worldbank.org",)) > 0
    assert _preferred_domain_bonus("https://unrelated.org/x", ("worldbank.org",)) == 0


def test_preference_never_outranks_topicality() -> None:
    """A preferred publisher that does not discuss the question must still lose
    to one that does. Otherwise removing the provider filter would have
    reintroduced the mis-ranking it was meant to fix, one layer later."""
    question = "How many deaths were attributed to air pollution in Malawi in 2024?"
    need = classify_evidence_need(question)
    on_topic = _r(
        "Malawi air pollution deaths",
        "https://www.who.int/malawi/air-pollution",
        "Malawi recorded 12000 deaths attributed to air pollution in 2024",
    )
    preferred_but_off_topic = _r(
        "AI capex outlook",
        "https://worldbank.org/ai-capex",
        "Global artificial intelligence capital expenditure projections",
    )
    ranked = _deduplicate_and_rank(
        [preferred_but_off_topic, on_topic], question, 10, "statistical", need,
        preferred=("worldbank.org",),
    )
    assert [r.url for r in ranked][0] == on_topic.url


# ---------------------------------------------------------------------------
# Topicality: authority must not buy a pass for an off-topic document
# ---------------------------------------------------------------------------


def test_topical_engagement_reads_content_words_and_named_subjects() -> None:
    from app.agents.sources import topical_engagement

    q = "How many deaths were attributed to air pollution in Malawi in 2024?"
    on = "Malawi recorded 12000 deaths attributed to air pollution in 2024"
    off = "Curiosity rover discovered organic molecules on the Martian surface"
    assert topical_engagement(q, on, ("Malawi", "2024")) > 0.5
    assert topical_engagement(q, off, ("Malawi", "2024")) == 0.0


def test_topical_engagement_ignores_function_words() -> None:
    """Scoring on 'what/is/the/of' would make every page look equally relevant."""
    from app.agents.sources import topical_engagement

    q = "what is the population of Malawi"
    about = "the population of Malawi is estimated"
    assert topical_engagement(q, about, ()) == 1.0


def test_authoritative_off_topic_page_loses_to_weak_on_topic_page() -> None:
    """The failure this fixes: authority spans 0.95 and relevance could only
    ever add 0.25, so an authoritative page about a different subject beat the
    on-topic answer by ~0.37 and nothing dropped it."""
    question = "How many deaths were attributed to air pollution in Malawi in 2024?"
    need = classify_evidence_need(question)
    off_topic = _r(
        "Mars rover findings",
        "https://www.nasa.gov/mars-rover",
        "Curiosity rover discovered organic molecules on the Martian surface",
    )
    on_topic = _r(
        "Malawi air pollution deaths 2024",
        "https://news-site.com/malawi-deaths",
        "Malawi recorded 12000 deaths attributed to air pollution in 2024",
    )
    assert _score_result(off_topic, question, need) < _score_result(on_topic, question, need)


def test_off_topic_results_are_discarded_before_synthesis() -> None:
    question = "How many deaths were attributed to air pollution in Malawi in 2024?"
    need = classify_evidence_need(question)
    results = [
        _r("Mars rover", "https://www.nasa.gov/mars", "Curiosity rover organic molecules"),
        _r("Mars geology", "https://science.org/mars", "Martian soil chemistry and craters"),
        _r("Venus clouds", "https://noaa.gov/venus", "Venus cloud structure observations"),
        _r("Malawi deaths", "https://news-site.com/mw",
           "Malawi recorded 12000 deaths attributed to air pollution in 2024"),
    ]
    ranked = _deduplicate_and_rank(results, question, 10, "statistical", need)
    assert [r.url for r in ranked] == ["https://news-site.com/mw"]


def test_one_off_topic_result_survives_when_nothing_is_relevant() -> None:
    """Returning nothing from a non-empty provider response is a retrieval
    decision, not a quality one — but it must be the CLOSEST thing found, not
    whichever page happened to have the highest authority."""
    question = "How many deaths were attributed to air pollution in Malawi in 2024?"
    need = classify_evidence_need(question)
    results = [
        _r("Mars rover", "https://science.org/mars", "Martian soil chemistry"),
        _r("Venus clouds", "https://noaa.gov/venus", "Venus cloud structure observations"),
    ]
    ranked = _deduplicate_and_rank(results, question, 10, "statistical", need)
    assert len(ranked) == 1


def test_a_partial_match_is_kept_for_the_ranker() -> None:
    """The floor removes documents about a different subject; it must not
    second-guess the ranker about weak-but-real matches."""
    question = (
        "What is the latest revenue guidance for Nvidia from its most recent earnings filing?"
    )
    need = classify_evidence_need(question)
    definition_page = _r(
        "Forward Guidance | Meaning",
        "https://www.britannica.com/money/forward-guidance",
        "what forward guidance means",
    )
    assert definition_page.url in [
        r.url for r in _deduplicate_and_rank([definition_page], question, 10, "general", need)
    ]


def test_floor_is_not_applied_to_a_question_with_no_subject() -> None:
    """There is nothing to be irrelevant to, so nothing may be discarded."""
    from app.agents.sources import topicality_floor_applies

    assert topicality_floor_applies("what is it", ()) is False
    assert topicality_floor_applies("define RAG", ()) is False
    assert topicality_floor_applies("population of Malawi", ()) is True
    assert topicality_floor_applies("anything at all", ("Malawi",)) is True


# ---------------------------------------------------------------------------
# Unregistered domains: citable on evidence, not on TLD
# ---------------------------------------------------------------------------


def test_every_unregistered_dotcom_clears_the_same_gates() -> None:
    """The reason this exists. 0.58 clears is_high_quality_domain (authority >
    0.0), verifier's 0.55, and the summarizer filter's 0.55 fallback — the
    fallback that engages on exactly the hard queries where junk matters most."""
    from app.agents.sources import UNDOCUMENTED_AUTHORITY

    junk = UNDOCUMENTED_AUTHORITY
    assert junk < 0.55, "junk must fall below the verifier's source gate"
    assert junk < 0.55, "junk must fall below the summarizer filter's fallback"
    assert junk < 0.60 and junk < 0.62, "junk must fall below the strong cuts"


def test_affiliate_and_buying_guide_pages_are_demoted() -> None:
    from app.agents.sources import UNDOCUMENTED_AUTHORITY, documentary_authority

    assert documentary_authority(
        "https://random-blog.com/post",
        "Our buying guide: what to look for when you buy solar panels",
    ) == UNDOCUMENTED_AUTHORITY
    assert documentary_authority(
        "https://best-deals.com/x", "Buy now! 50% discount on all products. Free shipping."
    ) == UNDOCUMENTED_AUTHORITY


def test_keyword_stuffed_hostnames_are_demoted() -> None:
    from app.agents.sources import UNDOCUMENTED_AUTHORITY, documentary_authority

    assert documentary_authority(
        "https://best-solar-panel-installers-uk.com/x", "Solar panels are great"
    ) == UNDOCUMENTED_AUTHORITY


def test_ordinary_journalism_on_an_unlisted_domain_stays_citable() -> None:
    """The failure mode of the first attempt at this rule: demoting on the
    ABSENCE of documentary markers pushed every unlisted news outlet below the
    citable bar and discarded legitimate reporting along with the junk."""
    from app.agents.sources import documentary_authority

    assert documentary_authority(
        "https://news-site.com/malawi-deaths",
        "Malawi recorded 12000 deaths attributed to air pollution in 2024",
    ) > 0.55


def test_a_two_word_hyphenated_brand_is_not_keyword_stuffing() -> None:
    from app.agents.sources import looks_keyword_stuffed

    assert looks_keyword_stuffed("https://solar-panel-installers.co.uk/x") is False
    assert looks_keyword_stuffed("https://bbc.co.uk/news") is False
    assert looks_keyword_stuffed("https://best-solar-panel-installers-uk.com/x") is True


def test_documentary_evidence_rescues_a_promotional_looking_page() -> None:
    """An official statistics page that also asks for newsletter signups is
    still a source."""
    from app.agents.sources import documentary_authority

    score = documentary_authority(
        "https://data-portal.example.com/series",
        "Subscribe to our newsletter. Official statistics, methodology and sample size.",
    )
    assert score > 0.55


def test_registered_publishers_are_never_demoted() -> None:
    """The tier registries already say what a publisher is; promotional-looking
    text on an official page must not change that."""
    from app.agents.sources import documentary_authority

    assert documentary_authority(
        "https://www.nasa.gov/mars", "Buy now! Discount! Sponsored content."
    ) == 0.95
    assert documentary_authority("https://arxiv.org/abs/2401.00001", "") == 0.88


def test_no_text_means_no_judgement() -> None:
    """classify_source stays a pure URL classifier, so every legacy score holds
    for callers that have no text to offer."""
    from app.agents.sources import authority_score, documentary_authority

    for url in ("https://example.com/a", "https://random-blog.com/post"):
        assert documentary_authority(url) == authority_score(url)


def test_contract_preferred_domains_reach_the_ranker() -> None:
    """`preferred_domains` was write-only: stored on every contract, documented
    in the planner prompt, asserted by tests, and read by nothing. Since the
    planner's steering stopped being a hard provider filter, the ranking
    preference is where it belongs."""
    import asyncio

    from app.agents.search import SearchClient
    from app.core.config import Settings

    settings = Settings(
        groq_api_key="k", database_url=":memory:", _env_file=None,
        search_max_results=5,
    )
    client = SearchClient(settings)

    async def fake_providers(self, q, stype):
        return [
            _r("Preferred", "https://bbs.gov.bd/report", "Bangladesh electrification report"),
            _r("Other", "https://other-site.com/report", "Bangladesh electrification report"),
        ]

    async def fake_fetch(url, client=None):
        return "text", ""

    import app.agents.search as search_mod

    original_providers = SearchClient._providers_for
    original_fetch = search_mod._fetch_content
    SearchClient._providers_for = fake_providers
    search_mod._fetch_content = fake_fetch
    try:
        contract = {
            "question": "Bangladesh electricity access statistics",
            "search_type": "statistical",
            "domain": "general",
            "preferred_domains": ["gov.bd"],
            "primary_source_query": "",
            "variants": [],
        }
        ranked = asyncio.run(client._search(contract))
    finally:
        SearchClient._providers_for = original_providers
        search_mod._fetch_content = original_fetch

    assert ranked, "expected ranked results"
    assert ranked[0].url == "https://bbs.gov.bd/report"
