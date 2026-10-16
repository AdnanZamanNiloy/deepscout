"""Confidence Engine tests (Phase 2.4)."""
from app.core.confidence import compute_confidence

STRONG_FACTS = [
    {"claim": "RAG retrieves documents before generation", "source": "https://arxiv.org/abs/1", "confidence": 0.9, "verified": True, "verification_score": 0.9},
    {"claim": "RAG conditions generation on retrieved documents", "source": "https://en.wikipedia.org/wiki/RAG", "confidence": 0.85, "verified": True, "verification_score": 0.8},
    {"claim": "RAG reduces hallucination in open-domain answers", "source": "https://www.nature.com/x", "confidence": 0.8, "verified": True, "verification_score": 0.7},
]

WEAK_FACTS = [
    {"claim": "Claim one about something", "source": "https://randomblog.com/a", "confidence": 0.5, "verified": False, "verification_score": 0.1},
]


def test_strong_evidence_scores_high():
    result = compute_confidence(STRONG_FACTS, {"is_sufficient": True}, 1, 3)
    assert result["overall"] > 0.7
    assert result["signals"]["citation_coverage"] == 1.0
    assert result["signals"]["critic_survival"] == 1.0


def test_weak_evidence_scores_low():
    result = compute_confidence(WEAK_FACTS, {"is_sufficient": False}, 3, 3)
    assert result["overall"] < 0.4
    assert result["signals"]["citation_coverage"] == 0.0
    # Survival is evidence-grounded: a bare fail with no named gaps is a hedge,
    # not a demonstrated weakness (the old constant 0.4 was uncorrelated with
    # evidence quality).
    assert result["signals"]["critic_survival"] == 0.6


def test_single_fact_or_single_domain_is_not_diverse():
    result = compute_confidence(WEAK_FACTS, {"is_sufficient": True}, 1, 3)
    assert result["signals"]["source_diversity"] == 0.0
    same_domain = [
        {"claim": "a one", "source": "https://a.com/1", "confidence": 0.9, "verified": True, "verification_score": 0.9},
        {"claim": "a two", "source": "https://a.com/2", "confidence": 0.9, "verified": True, "verification_score": 0.9},
    ]
    result2 = compute_confidence(same_domain, {"is_sufficient": True}, 1, 3)
    assert result2["signals"]["source_diversity"] == 0.0


def test_unverified_facts_get_no_coverage_credit():
    facts = [{**f, "verified": False} for f in STRONG_FACTS]
    result = compute_confidence(facts, {"is_sufficient": True}, 1, 3)
    assert result["signals"]["citation_coverage"] == 0.0


def test_unmeasured_verification_strength_is_omitted_not_zeroed():
    """Verification that never ran must not count as a measured zero.

    The audit's reconstructed pool used to carry no verification_score at all,
    so the 0.15 weight multiplied a structural 0.0 on every live run. An
    absent signal is omitted and the remaining weights normalized; when the
    score IS measured, weights and score arithmetic stay historical.
    """
    unverified_pool = [
        {"claim": "RAG retrieves documents before generation", "source": "https://arxiv.org/abs/1", "confidence": 0.9, "verified": True},
        {"claim": "RAG conditions generation on retrieved documents", "source": "https://en.wikipedia.org/wiki/RAG", "confidence": 0.85, "verified": True},
        {"claim": "RAG reduces hallucination in open-domain answers", "source": "https://www.nature.com/x", "confidence": 0.8, "verified": True},
    ]
    critique = {"is_sufficient": True}
    absent = compute_confidence(unverified_pool, critique, 1, 3)
    assert "claim_verification_strength" not in absent["signals"]
    assert any("verification not measured" in note for note in absent["notes"])

    measured = compute_confidence(STRONG_FACTS, critique, 1, 3)
    assert "claim_verification_strength" in measured["signals"]
    assert measured["signals"]["claim_verification_strength"] == 0.8

    # Per-signal values are identical between the two runs; only the omitted
    # signal's weight differentiates the overalls.
    for key, value in absent["signals"].items():
        assert measured["signals"][key] == value
    assert measured["overall"] != absent["overall"]


def test_breakdown_structure_for_frontend():
    result = compute_confidence(STRONG_FACTS, {"is_sufficient": True}, 1, 3)
    assert {"overall", "signals", "weights", "notes"} <= set(result.keys())
    assert abs(sum(result["weights"].values()) - 1.0) < 1e-9
    assert result["signals"]["freshness"] == 0.0
    assert "freshness" in result["notes"][0]


def test_cross_source_agreement_needs_different_domains():
    same_domain = [
        {"claim": "RAG retrieves documents before generation", "source": "https://a.com/1", "confidence": 0.9},
        {"claim": "RAG retrieves documents before generation now", "source": "https://a.com/2", "confidence": 0.9},
    ]
    result = compute_confidence(same_domain, {"is_sufficient": True}, 1, 3)
    assert result["signals"]["cross_source_agreement"] == 0.0


def test_parse_published_date_formats():
    from app.evidence.utils import parse_published_date

    assert parse_published_date("2026-09-10T12:00:00Z") == "2026-09-10"
    assert parse_published_date("2026-09-10") == "2026-09-10"
    assert parse_published_date("Wed, 21 Oct 2015 07:28:00 GMT") == "2015-10-21"
    assert parse_published_date("Mar 17, 2026") == "2026-03-17"
    assert parse_published_date("not a date") == ""
    assert parse_published_date("") == ""
    assert parse_published_date(None) == ""


def test_freshness_unmeasured_keeps_legacy_weights():
    from app.core.confidence import WEIGHTS, compute_confidence

    result = compute_confidence([], {"is_sufficient": True}, 1, 3)
    assert result["signals"]["freshness"] == 0.0
    assert result["weights"] == WEIGHTS


def test_freshness_measured_uses_fresh_weights():
    from datetime import date, timedelta

    from app.core.confidence import WEIGHTS_FRESH, compute_confidence

    recent = (date.today() - timedelta(days=10)).isoformat()
    old = (date.today() - timedelta(days=1000)).isoformat()
    result = compute_confidence([], {"is_sufficient": True}, 1, 3,
                                source_dates=[recent, old, "garbage"])
    assert result["weights"] == WEIGHTS_FRESH
    assert 0.0 < result["signals"]["freshness"] < 1.0
    assert sum(WEIGHTS_FRESH.values()) == 1.0


def test_degraded_summarizer_caps_confidence_below_sufficiency():
    """A run whose evidence came from the extractive fallback must not be
    scored above the sufficiency threshold: every fallback claim trivially
    overlaps its own source, so verification signals read high regardless of
    actual quality (live: a fully-degraded run scored 0.772 'High')."""
    import importlib

    from app.common.config import get_settings  # noqa: F401

    confidence = importlib.import_module("app.core.confidence")

    facts = [
        {"claim": f"Claim {i} about energy economics", "source": f"https://src{i}.org/a",
         "confidence": 0.8, "verified": True, "verification_score": 0.95}
        for i in range(6)
    ]
    critique = {"is_sufficient": False, "reason": "ok"}
    healthy = confidence.compute_confidence(facts, critique, 1, 3)
    degraded = confidence.compute_confidence(facts, critique, 1, 3, degraded=["summarizer"])
    assert degraded["overall"] <= confidence.DEGRADED_CAP
    assert degraded["overall"] < 0.75, "degraded run crossed the sufficiency threshold"
    assert degraded["overall"] < healthy["overall"]
    assert any("capped" in note for note in degraded["notes"])


def test_degraded_penalty_ignores_non_extractive_agents():
    """Planner/critic fallbacks do not make the EVIDENCE extractive — no cap."""
    from app.core.confidence import compute_confidence

    facts = [
        {"claim": f"Claim {i} about energy economics", "source": f"https://src{i}.org/a",
         "confidence": 0.8, "verified": True, "verification_score": 0.95}
        for i in range(6)
    ]
    critique = {"is_sufficient": False, "reason": "ok"}
    healthy = compute_confidence(facts, critique, 1, 3)
    flagged = compute_confidence(facts, critique, 1, 3, degraded=["planner", "critic"])
    assert flagged["overall"] == healthy["overall"]
