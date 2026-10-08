from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any, Dict, List, Sequence, Set

from app.agents.evidence_utils import extract_domain
from app.agents.evidence_utils import semantic_similarity
from app.agents.quality.primitives import (
    _safe_int,
)


_ECHO_SIMILARITY = 0.82


@dataclass
class IndependenceReport:
    """How many genuinely independent voices are behind this evidence."""

    nominal_sources: int = 0
    effective_sources: int = 0
    echo_groups: List[List[str]] = field(default_factory=list)
    dominant_domain: str = ""
    dominant_share: float = 0.0

    @property
    def echoed_claims(self) -> int:
        return sum(len(g) for g in self.echo_groups)

    def warning(self) -> str:
        parts: List[str] = []
        if self.echo_groups:
            parts.append(
                f"{len(self.echo_groups)} claim(s) appear in near-identical wording "
                f"across {self.echoed_claims} sources, which indicates syndication "
                "or a shared origin rather than independent confirmation; "
                f"{self.nominal_sources} nominal sources reduce to about "
                f"{self.effective_sources} independent ones."
            )
        if self.dominant_share > 0.55 and self.dominant_domain:
            parts.append(
                f"{self.dominant_share:.0%} of the evidence comes from "
                f"{self.dominant_domain} alone; this report largely reflects one "
                "publisher's account."
            )
        return " ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nominal_sources": self.nominal_sources,
            "effective_sources": self.effective_sources,
            "echo_groups": len(self.echo_groups),
            "echoed_claims": self.echoed_claims,
            "dominant_domain": self.dominant_domain,
            "dominant_share": self.dominant_share,
        }


def assess_independence(facts: Sequence[Dict[str, Any]]) -> IndependenceReport:
    """Find claims that are the same text wearing different domain names.

    Two claims from DIFFERENT domains whose wording is near-identical are a
    wire story, a press release, or one outlet quoting another. Counting them
    as two corroborating sources is how a research system manufactures false
    confidence. Same-domain duplicates are ignored here — that is ordinary
    repetition within one publisher, and the deduper already handles it.
    """
    report = IndependenceReport()
    items = [f for f in (facts or []) if isinstance(f, dict) and str(f.get("claim", "")).strip()]
    if not items:
        return report

    domains = [extract_domain(str(f.get("source", "") or "")) or "" for f in items]
    distinct = {d for d in domains if d}
    report.nominal_sources = len(distinct)

    if distinct:
        counts: Dict[str, int] = {}
        for domain in domains:
            if domain:
                counts[domain] = counts.get(domain, 0) + 1
        report.dominant_domain, top = max(counts.items(), key=lambda kv: kv[1])
        report.dominant_share = round(top / max(1, len([d for d in domains if d])), 3)

    # Union-find over cross-domain near-duplicates.
    parent = list(range(len(items)))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> None:
        ri, rj = _find(i), _find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if not domains[i] or not domains[j] or domains[i] == domains[j]:
                continue
            try:
                similarity = semantic_similarity(
                    str(items[i].get("claim", "")), str(items[j].get("claim", ""))
                )
            except Exception:  # noqa: BLE001
                continue
            if similarity >= _ECHO_SIMILARITY:
                _union(i, j)

    clusters: Dict[int, List[int]] = {}
    for index in range(len(items)):
        clusters.setdefault(_find(index), []).append(index)

    echoed_domains: Set[str] = set()
    for members in clusters.values():
        if len(members) < 2:
            continue
        member_domains = sorted({domains[m] for m in members if domains[m]})
        if len(member_domains) < 2:
            continue
        report.echo_groups.append(member_domains)
        echoed_domains.update(member_domains[1:])

    # Each echo group collapses to one independent voice.
    report.effective_sources = max(1, report.nominal_sources - len(echoed_domains)) if distinct else 0
    return report


def apply_independence(
    facts: Sequence[Dict[str, Any]],
    report: IndependenceReport,
) -> List[Dict[str, Any]]:
    """Stamp each fact with `independent_corroboration`, never inflating it.

    The raw `corroboration_count` is preserved so nothing downstream breaks;
    the new field is what confidence and the findings bullets should read. When
    a claim belongs to an echo group its independent count is 1, because every
    copy traces to one origin.
    """
    out: List[Dict[str, Any]] = []
    echo_domains: Set[str] = {d for group in report.echo_groups for d in group}
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        item = dict(fact)
        nominal = max(1, _safe_int(item.get("corroboration_count", 1), 1))
        domain = extract_domain(str(item.get("source", "") or "")) or ""
        item["independent_corroboration"] = 1 if domain in echo_domains else nominal
        out.append(item)
    return out


def independent_corroboration(fact: Dict[str, Any]) -> int:
    """Independent-source count, falling back to the nominal one."""
    if "independent_corroboration" in fact:
        return max(1, _safe_int(fact.get("independent_corroboration"), 1))
    return max(1, _safe_int(fact.get("corroboration_count", 1), 1))
