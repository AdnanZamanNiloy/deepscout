"""One evidence conclusion, obeyed by every section of the report.

THE FAILURE THIS PREVENTS
-------------------------
The evidence gate concludes that no defensible #1 exists. The Executive Summary
says so — and then the Outlook section names a "leading" option anyway, the
Evidence section reports the "top" candidates, and a deep-dive section discusses
some unrelated mechanism retrieved along the way. Each section was written to its
own prompt, so none of them inherited the conclusion.

The report must be a FAITHFUL SYNTHESIS of the evidence review, not a second
interpretation of the evidence. When the review concludes "no ranking", every
section obeys that conclusion.

WHAT THIS MODULE PROVIDES
-------------------------
* A single REPORT STATUS ("no defensible #1", "supported cluster", "ranked") that
  all sections must respect.
* A CONSISTENCY CONTRACT instructing the writer, per section, on ranking
  language: which words are forbidden, what to call a cluster instead, and that
  a section which cannot answer or qualify the question must be empty rather than
  filled with loosely related material.
* A DETECTOR (`ranking_language_without_basis`) that finds ranking words in the
  delivered prose where no comparative basis exists — usable across all sections,
  so the Executive Summary is not held to a different standard than a deep dive.

DOMAIN AGNOSTICISM
------------------
No subject, sector, metric or candidate is named anywhere in executable code. The
ranking vocabulary is generic ("top", "highest", "leading"); the basis and the
cluster come from the other layers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

# Report status. Derived from the ranking basis and the convergence diagnosis;
# never invented here.
STATUS_RANKED = "ranked"
STATUS_CLUSTER = "supported_cluster"
STATUS_NO_NUMBER_ONE = "no_number_one"
STATUS_OPEN = "open"

# Ranking vocabulary. Generic words that assert an ORDER. Any of these in a
# section is a ranking claim, whichever section it appears in.
_RANKING_WORDS: Tuple[str, ...] = (
    "the top",
    "top ",
    "highest",
    "the highest",
    "leading",
    "leads",
    "lead the",
    "out in front",
    "best",
    "worst",
    "strongest",
    "sit at the top",
    "sits at the top",
    "at the top",
    "ranks first",
    "ranked first",
    "number one",
    "#1",
    "the single",
    "outrank",
    "outperforms",
    "beats out",
)

# Phrases that HEDGE the ranking language legitimately: "cannot rank", "no single
# #1", "not a ranking". Their presence means the ranking word is being denied,
# not asserted, so it must not be reported as a violation.
_NEGATING_PHRASES: Tuple[str, ...] = (
    "no single",
    "no defensible",
    "cannot rank",
    "cannot be ranked",
    "not ranked",
    "not a ranking",
    "no ranking",
    "not comparable",
    "no #1",
    "without ranking",
    "no occupation ranks first",
    "no clear",
)


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9#][a-z0-9#]*", (text or "").lower())


@dataclass
class ReportStatus:
    """What the evidence review concluded, applied to the whole report."""

    status: str = STATUS_OPEN
    reason: str = ""
    cluster: List[str] = field(default_factory=list)
    missing_evidence: str = ""
    allowed_ranking: bool = False

    @property
    def forbids_ranking(self) -> bool:
        return self.status in (STATUS_NO_NUMBER_ONE, STATUS_CLUSTER)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "cluster": list(self.cluster),
            "missing_evidence": self.missing_evidence,
            "allowed_ranking": self.allowed_ranking,
            "forbids_ranking": self.forbids_ranking,
        }


def report_status(
    query: str,
    *,
    convergence: Mapping[str, Any] | None = None,
    ranking_basis: Mapping[str, Any] | None = None,
    cluster: Sequence[str] = (),
) -> ReportStatus:
    """Derive the report-wide status from the review's conclusions.

    Convergence outranks the ranking basis: when the loop concluded the evidence
    cannot answer, that governs regardless of what the basis assessment saw.
    """
    convergence = convergence if isinstance(convergence, Mapping) else {}
    ranking_basis = ranking_basis if isinstance(ranking_basis, Mapping) else {}

    if convergence.get("identified"):
        return ReportStatus(
            status=STATUS_NO_NUMBER_ONE,
            reason=str(convergence.get("reason", "") or ""),
            cluster=[str(c) for c in cluster if str(c).strip()],
            missing_evidence=str(convergence.get("missing_evidence", "") or ""),
            allowed_ranking=False,
        )

    verdict = str(
        ranking_basis.get("verdict")
        or (ranking_basis.get("basis") or {}).get("verdict")
        or ""
    )
    if verdict == "ranked":
        return ReportStatus(status=STATUS_RANKED, allowed_ranking=True)
    if verdict == "shortlist":
        return ReportStatus(
            status=STATUS_CLUSTER,
            cluster=[str(c) for c in cluster if str(c).strip()],
            allowed_ranking=False,
        )
    return ReportStatus(status=STATUS_OPEN)


def render_consistency_contract(status: ReportStatus) -> str:
    """The rule every section of the report must obey.

    Written section-agnostic on purpose: the same contract is prepended to the
    single-pass prompt and to every per-section prompt, so no section can be held
    to a different standard.
    """
    if not status.forbids_ranking:
        return ""
    lines: List[str] = [
        "REPORT-WIDE CONSISTENCY — the evidence review concluded that NO "
        "defensible #1 can be established. EVERY section of this report must obey "
        "that conclusion, not only the Executive Summary.",
        "DO NOT use ranking language anywhere: 'top', 'highest', 'leading', 'the "
        "best', 'strongest', 'sit at the top', 'ranks first'. The evidence does "
        "not order these candidates, so no section may imply that it does.",
    ]
    if status.cluster:
        listed = "; ".join(status.cluster[:6])
        lines.append(
            f"If a group is discussed, call it a SUPPORTED CLUSTER (or 'the skills "
            f"associated with rising demand') — never a ranking. Supported "
            f"cluster: {listed}."
        )
    else:
        lines.append(
            "If no supported group exists, say the evidence supports no answer to "
            "this question. Do not invent one."
        )
    lines.append(
        "SAY WHY IT CANNOT BE RANKED — a section that discusses the candidates "
        "must also state that no source compares them on a common measure."
    )
    if status.missing_evidence:
        lines.append(
            f"WHAT WOULD SETTLE IT: {status.missing_evidence}. A section may name "
            "this; no section may pretend the ranking exists without it."
        )
    lines.append(
        "STAY ON THE QUESTION. Every section must answer or qualify the original "
        "research question. Do NOT introduce unrelated mechanisms, sectors, "
        "technologies or source claims merely because they were retrieved. A "
        "section with nothing relevant to say must be EMPTY rather than filled "
        "with loosely related evidence."
    )
    return "\n".join(lines)


def ranking_language_without_basis(
    text: str,
    status: ReportStatus,
) -> List[str]:
    """Ranking words asserted in `text` where no comparative basis exists.

    A word is NOT reported when a negation governs it — "no single #1",
    "cannot rank the top option", "no clear leading candidate". Those state the
    conclusion rather than violating it, and flagging them would punish a report
    for saying exactly what it was told to say.

    Negation is detected by WINDOW rather than by phrase replacement: a negator
    within a short span before the ranking word governs it. Phrase replacement
    failed on "no single #1" because removing "no single" left the "#1" behind,
    and that word is precisely the one being negated.
    """
    if not status.forbids_ranking:
        return []
    low = str(text or "").lower()
    negators = ("no", "not", "cannot", "can't", "never", "without", "neither")
    hits: List[str] = []
    for word in _RANKING_WORDS:
        start = low.find(word)
        while start != -1:
            window = low[max(0, start - 40): start]
            governed = any(re.search(rf"\b{re.escape(n)}\b", window) for n in negators)
            # Also treat a hedge immediately AFTER ("#1 is not established").
            after = low[start: start + len(word) + 30]
            if not governed and not any(
                re.search(rf"\b{re.escape(n)}\b", after) for n in negators
            ):
                hits.append(word.strip())
                break
            start = low.find(word, start + 1)
    return hits


def section_violations(
    sections: Mapping[str, str] | Sequence[Tuple[str, str]],
    status: ReportStatus,
) -> Dict[str, List[str]]:
    """Per-section ranking violations, keyed by section title.

    Per-section rather than report-wide, so a violation names WHICH section broke
    consistency — the reported failure was the Executive Summary obeying the
    conclusion while another section did not.
    """
    items: Iterable[Tuple[str, str]]
    items = sections.items() if isinstance(sections, Mapping) else sections
    out: Dict[str, List[str]] = {}
    for title, body in items:
        hits = ranking_language_without_basis(body, status)
        if hits:
            out[str(title)] = hits
    return out


def assess_report_consistency(
    query: str,
    sections: Mapping[str, str] | Sequence[Tuple[str, str]],
    *,
    convergence: Mapping[str, Any] | None = None,
    ranking_basis: Mapping[str, Any] | None = None,
    cluster: Sequence[str] = (),
) -> Dict[str, Any]:
    """Full check for the audit and tests. Total; never raises."""
    try:
        status = report_status(
            query, convergence=convergence, ranking_basis=ranking_basis, cluster=cluster
        )
        violations = section_violations(sections, status)
    except Exception:
        return {"status": {}, "violations": {}}
    return {"status": status.to_dict(), "violations": violations}
