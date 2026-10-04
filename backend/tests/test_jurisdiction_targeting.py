"""Jurisdiction-aware source targeting: a `site:` operator must never exclude
the authoritative source.

Context
-------
`site:` is an exclusion as much as an inclusion. `PRIMARY_SOURCE_HINTS` is keyed
on `(search_type, domain)` — a pair that carries no information about WHICH
country a question is about — so the slot reserved for the primary source was
aimed at whichever agencies that bucket happened to name:

    statistical:general -> (worldbank.org, oecd.org, census.gov)
        "population of Malawi" was scoped to the UNITED STATES Census Bureau,
        which publishes no Malawian figure. The query returned nothing and the
        retrieval slot was spent proving it.

These tests pin the fix: a publisher bound to one country may only be targeted
by a question about that country, and a question that names a country leads with
that country's own official suffix family.

None of these assert anything about a specific real-world query's answer. They
assert the *general* mechanism, which is what has to hold for every query.
"""
from __future__ import annotations

import pytest

from app.agents.sources import (
    COUNTRY_OFFICIAL_SUFFIX,
    _KNOWN_COUNTRY_CODES,
    build_corroboration_query,
    build_dimension_primary_query,
    build_primary_source_query,
    build_substitution_query,
    grounded_site_targets,
    host_jurisdiction,
    jurisdiction_is_admissible,
    partition_site_targets,
    primary_source_hints,
    question_jurisdiction,
    wants_a_primary_source,
)


# ---------------------------------------------------------------------------
# Host -> country
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("census.gov", "us"),          # national agency under a .gov TLD
        ("bls.gov", "us"),
        ("sec.gov", "us"),
        ("ons.gov.uk", "gb"),          # multi-label government suffix
        ("gov.uk", "gb"),
        ("bbc.co.uk", "gb"),           # two-part public suffix
        ("stat.go.jp", "jp"),
        ("mospi.gov.in", "in"),
        ("bb.org.bd", "bd"),
        ("statcan.gc.ca", "ca"),
        ("destatis.de", "de"),
        ("eurostat.ec.europa.eu", "eu"),
        ("data.gov.bd", "bd"),
    ],
)
def test_national_publishers_resolve_to_their_country(host: str, expected: str) -> None:
    assert host_jurisdiction(host) == expected


@pytest.mark.parametrize(
    "host",
    [
        "worldbank.org",   # multilateral: publishes for every member
        "imf.org",
        "oecd.org",
        "arxiv.org",       # global preprint server
        "doi.org",
        "nature.com",
        "example.com",
        "some.co",         # .co is Colombia's ccTLD but a generic TLD in practice
        "foo.io",          # .io likewise
        "foo.ai",
        "bbc.com",
    ],
)
def test_global_publishers_carry_no_jurisdiction(host: str) -> None:
    """None means "no constraint", not "somewhere else" — that is what keeps a
    multilateral body targetable for every question."""
    assert host_jurisdiction(host) is None


def test_every_registered_country_has_an_official_suffix_family() -> None:
    """A country we can detect but cannot target would silently fall back to
    the generic registry, which is the bug this whole mechanism exists to fix."""
    assert not (_KNOWN_COUNTRY_CODES - set(COUNTRY_OFFICIAL_SUFFIX))


# ---------------------------------------------------------------------------
# Question -> country
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("electricity access in Bangladesh", ("bd",)),
        ("population of Malawi", ("mw",)),
        ("latest GDP revision for India", ("in",)),
        ("Kenya maize production 2024", ("ke",)),
        ("South Korea semiconductor exports", ("kr",)),
        ("policy in the United States", ("us",)),
        ("data published by bb.org.bd", ("bd",)),
    ],
)
def test_question_country_is_detected(question: str, expected: tuple) -> None:
    assert question_jurisdiction(question) == expected


def test_country_order_follows_mention_not_registry() -> None:
    """"Germany vs France" must lead with Germany. A registry-ordered scan
    returned France, so the query aimed at the wrong country's agencies."""
    assert question_jurisdiction("Germany vs France electricity prices") == ("de", "fr")
    assert question_jurisdiction("France and Germany and Italy") == ("fr", "de", "it")


def test_two_letter_country_codes_are_not_matched_in_prose() -> None:
    """`in`, `is`, `at`, `be`, `no`, `us` are English words. Matching them would
    resolve almost every question to some country."""
    for question in (
        "tell us about it",
        "what is the average income in the region",
        "no data is available at this time",
    ):
        assert question_jurisdiction(question) == ()


def test_uppercase_codes_are_matched_as_countries() -> None:
    """"US and China" means two countries; lowercase "us" is a pronoun."""
    assert question_jurisdiction("US and China semiconductor export controls") == ("us", "cn")


def test_global_questions_name_no_country_and_are_not_narrowed() -> None:
    """The common case. Narrowing these would be the same bug in reverse."""
    assert question_jurisdiction("global AI capital expenditure 2025") == ()


# ---------------------------------------------------------------------------
# Admissibility
# ---------------------------------------------------------------------------


def test_another_countrys_agency_is_not_admissible() -> None:
    assert jurisdiction_is_admissible("census.gov", ("bd",)) is False
    assert jurisdiction_is_admissible("ons.gov.uk", ("bd",)) is False


def test_own_country_and_global_publishers_stay_admissible() -> None:
    for host in ("bbs.gov.bd", "worldbank.org", "oecd.org", "arxiv.org", "imf.org"):
        assert jurisdiction_is_admissible(host, ("bd",)) is True


def test_question_naming_no_country_admits_everything() -> None:
    assert jurisdiction_is_admissible("census.gov", ()) is True


def test_supranational_publisher_is_admissible_for_any_country() -> None:
    assert jurisdiction_is_admissible("eurostat.ec.europa.eu", ("bd",)) is True


# ---------------------------------------------------------------------------
# Grounded targets
# ---------------------------------------------------------------------------


def test_a_country_question_leads_with_its_own_agencies() -> None:
    targets = grounded_site_targets(
        "population of Malawi", "statistical", "general", max_sites=2
    )
    assert targets[0] in COUNTRY_OFFICIAL_SUFFIX["mw"]
    assert "census.gov" not in targets


def test_a_wrong_country_hint_is_dropped_and_replaced() -> None:
    """The original failure: statistical:general names census.gov, so the
    Bangladesh question used to be scoped to the US Census Bureau."""
    assert "census.gov" in primary_source_hints("statistical", "general")
    query = build_primary_source_query(
        "electricity access in Bangladesh", "statistical", "general"
    )
    assert "site:census.gov" not in query
    assert "site:gov.bd" in query


def test_a_global_question_keeps_its_registry_hints() -> None:
    """Jurisdiction awareness must not over-correct: with no country named the
    registered multilateral hints are still right."""
    query = build_primary_source_query(
        "global AI capital expenditure 2025", "statistical", "economics"
    )
    assert "site:worldbank.org" in query


def test_a_comparison_of_two_countries_targets_both() -> None:
    targets = grounded_site_targets(
        "Germany vs France electricity prices", "comparison", "general", max_sites=2
    )
    assert "de" in targets
    assert "gouv.fr" in targets


def test_nothing_grounded_means_no_site_operator_at_all() -> None:
    """No hint for the bucket, no country in the question: guessing is what
    excluded the right publisher, so emit nothing."""
    assert build_primary_source_query("what is a quark", "comparison", "general") == ""


# ---------------------------------------------------------------------------
# Every builder is jurisdiction-aware
# ---------------------------------------------------------------------------


def test_corroboration_query_leads_with_the_claims_country() -> None:
    query = build_corroboration_query(
        "Bangladesh garment exports reached 45 billion", quantitative=True
    )
    assert "site:gov.bd" in query


def test_corroboration_query_still_excludes_the_known_publisher() -> None:
    query = build_corroboration_query(
        "global AI capital expenditure reached 200 billion", exclude_domain="example.com"
    )
    assert "-site:example.com" in query


def test_corroboration_query_does_not_target_another_country_needlessly() -> None:
    query = build_corroboration_query("Kenya maize production 2024", quantitative=True)
    sites = partition_site_targets(query).hard + partition_site_targets(query).soft
    assert "census.gov" not in sites


def test_substitution_query_targets_the_failed_hosts_own_jurisdiction() -> None:
    """Losing a UK statistics page must not send recovery to the World Bank."""
    query = build_substitution_query(
        "UK electricity demand 2024", "statistical", "ons.gov.uk"
    )
    assert "site:gov.uk" in query
    assert "-site:ons.gov.uk" in query


def test_substitution_query_is_not_gated_on_the_definitional_check() -> None:
    """Recovery runs because an authoritative document is known to exist there,
    so "this question has no primary source" must not silence it."""
    assert wants_a_primary_source("what is a transformer") is False
    query = build_substitution_query("what is a transformer", "general", "blocked-a.com")
    assert "site:" in query
    assert "-site:blocked-a.com" in query


# ---------------------------------------------------------------------------
# Primary-source query: only where one can exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What is retrieval augmented generation?", False),
        ("what is a quark", False),
        ("what does RAG mean", False),
        ("What is the latest revenue guidance in Nvidia's 10-K filing?", True),
        ("population of Malawi", True),
        ("What is the growth?", True),      # "growth" is a statistical demand
        ("renewable energy capacity", True),
    ],
)
def test_a_pure_definition_has_no_primary_source(question: str, expected: bool) -> None:
    assert wants_a_primary_source(question) is expected


def test_definitional_dimension_spends_no_primary_slot() -> None:
    assert build_dimension_primary_query("what is a quark", "comparison", "general") == ""


# ---------------------------------------------------------------------------
# Hard vs soft site: targets
# ---------------------------------------------------------------------------


def test_grounded_target_may_filter_but_a_registry_hint_may_not() -> None:
    grounded = partition_site_targets("population of Malawi site:gov.mw OR site:worldbank.org")
    assert grounded.hard == ("gov.mw",)
    assert grounded.soft == ("worldbank.org",)

    guessed = partition_site_targets("retrieval benchmarks site:arxiv.org")
    assert guessed.hard == ()
    assert guessed.soft == ("arxiv.org",)


def test_negative_site_is_an_exclusion_not_a_target() -> None:
    """Corroboration asks for an INDEPENDENT publisher. Reading `-site:` as a
    preference would rank the one source we must move away from to the top."""
    split = partition_site_targets(
        "exports corroboration (site:gov.bd OR site:oecd.org) -site:example.com"
    )
    assert split.excluded == ("example.com",)
    assert "example.com" not in split.hard
    assert "example.com" not in split.soft