from __future__ import annotations

from typing import Any, Dict, Sequence

from app.agents.sources import TIER_PEER_REVIEWED
from app.agents.sources import canonical_url
from app.agents.sources import classify_source
from app.agents.sources import evidence_freshness
from app.agents.sources import is_primary_source
from app.agents.sources import primary_source_share
from app.agents.evidence.domain import (
    extract_domain,
)


def evidence_stats(facts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """One pass over the evidence pool producing every count the pipeline's
    gates need. Previously each stage recomputed its own subset with slightly
    different rules, so the critic, the report and the API could disagree
    about how many verified facts existed."""
    facts = [f for f in (facts or []) if isinstance(f, dict)]
    urls = [str(f.get("source", "") or "") for f in facts if f.get("source")]
    domains = {extract_domain(u) for u in urls} - {""}
    documents = {canonical_url(u) for u in urls} - {""}
    verified = [f for f in facts if f.get("verified") is True]
    confidences = [float(f.get("confidence", 0.0) or 0.0) for f in facts]
    axes = {str(f.get("sub_question", "") or "").strip() for f in facts} - {""}
    corroborated = [f for f in facts if int(f.get("corroboration_count", 1) or 1) > 1]
    primary_docs = {u for u in documents if is_primary_source(u)}
    single_source = [
        f for f in facts if int(f.get("corroboration_count", 1) or 1) <= 1
    ]
    # Source-ledger composition: the share of the pool that each category
    # contributes, by fact count. Regulation dominance is the failure this
    # exists to catch (reports that read as legal summaries of an AI-trend
    # query); non-Western under-representation is the other.
    regulation_count = sum(
        1 for f in facts if str(f.get("axis", "") or "") == "regulation"
    )
    non_western_count = sum(
        1 for f in facts if _is_non_western_source(str(f.get("source", "") or ""))
    )
    peer_reviewed_count = sum(
        1 for f in facts
        if classify_source(str(f.get("source", "") or "")).tier == TIER_PEER_REVIEWED
    )

    return {
        "total": len(facts),
        "verified": len(verified),
        "unverified": len(facts) - len(verified),
        "distinct_domains": len(domains),
        "distinct_documents": len(documents),
        "domains": sorted(domains),
        "axes_covered": len(axes),
        "axes": sorted(axes),
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "avg_verified_confidence": round(
            sum(float(f.get("confidence", 0.0) or 0.0) for f in verified) / len(verified), 4
        ) if verified else 0.0,
        "corroborated": len(corroborated),
        "single_source": len(single_source),
        "primary_documents": len(primary_docs),
        "primary_share": round(primary_source_share(urls), 4),
        "freshness": evidence_freshness(facts),
        "regulation_share": round(regulation_count / len(facts), 4) if facts else 0.0,
        "non_western_share": round(non_western_count / len(facts), 4) if facts else 0.0,
        # Share of evidence from peer-reviewed literature. Exists so a
        # source-class dominance check can see that a report drew on
        # scholarship, not only on regulation or on raw primary documents —
        # a literature review that never consulted data and a legal summary
        # that never consulted research are different failures.
        "peer_reviewed_share": round(peer_reviewed_count / len(facts), 4) if facts else 0.0,
    }


_NON_WESTERN_MARKERS = (
    "gov.cn", "xinhuanet", "chinadaily", "scmp.com", "caixin", "thepaper.cn",
    "36kr", "alibabacloud", "baidu", "tencent", "huawei", "moonshot", "zhipu",
    "deepseek", "qwen", "alibaba", "bytedance", "sensetime", "iflytek",
    ".jp", ".kr", ".in", ".sg", ".cn", "riken", "naver", "kakao", "line.me",
    "nii.ac.jp", "u-tokyo", "kaist", "nus.edu", "iitb", "iisc",
    "gov.br", "scielo", "conicet", "african", "uneca", "gulfnews",
)


def _is_non_western_source(url: str) -> bool:
    """True for a source published outside the US/EU anglosphere.

    Deliberately heuristic and recall-oriented: the metric is surfaced in the
    ledger so under-representation is visible, never used to silently drop
    evidence.
    """
    low = str(url or "").lower()
    return any(marker in low for marker in _NON_WESTERN_MARKERS)
