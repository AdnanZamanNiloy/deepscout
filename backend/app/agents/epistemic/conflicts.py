from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.agents.evidence_utils import extract_numbers
from app.agents.evidence_utils import parse_published_date
from app.agents.evidence_utils import semantic_similarity
from app.agents.sources import classify_source
from app.agents.epistemic.claims import (
    _independent_sources,
)
from app.agents.epistemic.primitives import (
    _guard,
    _safe_float,
    _text,
)


class ConflictKind:
    TIME_SERIES = "time_series"
    SCOPE_MISMATCH = "scope_mismatch"
    UNIT_MISMATCH = "unit_mismatch"
    GENUINE = "genuine"


_SCOPE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "geography": (
        "global", "worldwide", "international", "us", "u.s.", "united states",
        "american", "eu", "european", "europe", "uk", "british", "china",
        "chinese", "india", "indian", "asia", "asian", "africa", "domestic",
        "regional", "national",
    ),
    "aggregation": (
        "annual", "annually", "per year", "yearly", "quarterly", "monthly",
        "cumulative", "total", "lifetime", "per capita", "per unit", "average",
        "median", "peak",
    ),
    "accounting": (
        "gross", "net", "adjusted", "nominal", "real", "pre-tax", "post-tax",
        "before tax", "after tax", "operating", "reported",
    ),
    "population": (
        "enterprise", "consumer", "retail", "wholesale", "public", "private",
        "urban", "rural", "adult", "child",
    ),
}


_TIME_HINT_RE = re.compile(
    r"\b(?:in|for|during|as of|by|through|fy|q[1-4])\s*"
    r"((?:19|20)\d{2})|\b((?:19|20)\d{2})\b", re.I
)


_TIME_VARYING_RE = re.compile(
    r"\b(revenue|sales|profit|loss|price|cost|valuation|market cap|share|"
    r"users?|subscribers?|customers?|headcount|employees?|population|"
    r"capacity|production|output|emissions?|temperature|rate|adoption|"
    r"penetration|deployment|installed|shipments?|volume|traffic|"
    r"unemployment|inflation|gdp|debt|funding|investment)\b", re.I
)


def _years_in(text: str) -> Set[int]:
    years: Set[int] = set()
    for match in _TIME_HINT_RE.finditer(text or ""):
        for group in match.groups():
            if group:
                years.add(int(group))
    return years


def _fact_year(fact: Dict[str, Any]) -> Optional[int]:
    """Year the claim is ABOUT: stated in the text, else its publication year."""
    claim_years = _years_in(_text(fact, "claim"))
    if claim_years:
        return max(claim_years)
    published = _text(fact, "published_at", "published", "date")
    if published:
        parsed = _guard(lambda: parse_published_date(published), "")
        match = re.search(r"((?:19|20)\d{2})", str(parsed or published))
        if match:
            return int(match.group(1))
    return None


def _scope_signature(text: str) -> Dict[str, Set[str]]:
    """Which scope qualifiers this claim carries, by group."""
    low = f" {(text or '').lower()} "
    out: Dict[str, Set[str]] = {}
    for group, terms in _SCOPE_GROUPS.items():
        hits = {term for term in terms if f" {term} " in low or f" {term}," in low}
        if hits:
            out[group] = hits
    return out


def _units_of(text: str) -> Set[str]:
    return {
        str(getattr(q, "unit", "") or "").lower()
        for q in _guard(lambda: extract_numbers(text or "", limit=8), [])
        if str(getattr(q, "unit", "") or "")
    }


def classify_conflict(
    contradiction: Dict[str, Any],
    fact_a: Optional[Dict[str, Any]] = None,
    fact_b: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Decide whether a detected conflict is a real disagreement.

    Returns (kind, explanation). The three non-genuine kinds are the ones the
    numeric detector cannot see, because it compares magnitudes and topic
    similarity and nothing else:

    * TIME SERIES — the same time-varying quantity in two different periods.
      Reporting "$2bn to $3bn" as a range when the truth is "grew from $2bn in
      2022 to $3bn in 2024" is an error the system invents on its own.
    * SCOPE MISMATCH — US vs global, annual vs cumulative, gross vs net. Both
      numbers are correct and they are not about the same thing.
    * UNIT MISMATCH — the values are in different units, so the comparison
      that produced the "conflict" was never valid.
    """
    claim_a = str(contradiction.get("claim_a", "") or "")
    claim_b = str(contradiction.get("claim_b", "") or "")
    if not claim_a or not claim_b:
        return ConflictKind.GENUINE, ""

    kind = str(contradiction.get("kind", "numeric") or "numeric")
    if kind != "numeric":
        # Polarity and other non-numeric conflicts are genuine by construction:
        # "does reduce" vs "does not reduce" is not a scope difference.
        return ConflictKind.GENUINE, ""

    # -- unit mismatch --------------------------------------------------
    units_a = _units_of(claim_a)
    units_b = _units_of(claim_b)
    if units_a and units_b and not (units_a & units_b):
        return (
            ConflictKind.UNIT_MISMATCH,
            f"measured in different units ({'/'.join(sorted(units_a))} vs "
            f"{'/'.join(sorted(units_b))}), so the two figures were never comparable",
        )

    # -- scope mismatch -------------------------------------------------
    scope_a = _scope_signature(claim_a)
    scope_b = _scope_signature(claim_b)
    for group in set(scope_a) & set(scope_b):
        if not (scope_a[group] & scope_b[group]):
            return (
                ConflictKind.SCOPE_MISMATCH,
                f"different {group} scope "
                f"({'/'.join(sorted(scope_a[group]))} vs {'/'.join(sorted(scope_b[group]))}); "
                "both figures can be correct",
            )
    # One side qualifies its scope and the other does not: weaker signal, but a
    # bare figure next to an explicitly scoped one is usually the broader one.
    for group in ("geography", "aggregation"):
        if (group in scope_a) != (group in scope_b):
            qualified = scope_a.get(group) or scope_b.get(group) or set()
            return (
                ConflictKind.SCOPE_MISMATCH,
                f"one figure is qualified by {group} ({'/'.join(sorted(qualified))}) "
                "and the other is not, so they may not describe the same quantity",
            )

    # -- time series ----------------------------------------------------
    combined = f"{claim_a} {claim_b}"
    if _TIME_VARYING_RE.search(combined):
        year_a = _years_in(claim_a) or ({_fact_year(fact_a)} if fact_a else set())
        year_b = _years_in(claim_b) or ({_fact_year(fact_b)} if fact_b else set())
        year_a = {y for y in year_a if y}
        year_b = {y for y in year_b if y}
        if year_a and year_b and not (year_a & year_b):
            return (
                ConflictKind.TIME_SERIES,
                f"the same time-varying quantity measured in different periods "
                f"({min(year_a)} vs {min(year_b)}); this is change over time, "
                "not a disagreement",
            )

    return ConflictKind.GENUINE, ""


@dataclass
class Resolution:
    """The verdict on one conflict, and the rule that produced it."""

    kind: str
    resolved: bool
    winner: str = ""          # "a" | "b" | ""
    rule: str = ""
    explanation: str = ""
    claim_a: str = ""
    claim_b: str = ""
    source_a: str = ""
    source_b: str = ""

    @property
    def is_real_conflict(self) -> bool:
        return self.kind == ConflictKind.GENUINE

    def render(self) -> str:
        """One line the report can print verbatim."""
        if not self.is_real_conflict:
            return f"Not a conflict — {self.explanation}."
        if self.resolved:
            winning = self.claim_a if self.winner == "a" else self.claim_b
            losing = self.claim_b if self.winner == "a" else self.claim_a
            return (
                f"Resolved in favour of \"{winning[:130]}\" over "
                f"\"{losing[:130]}\": {self.explanation}."
            )
        return (
            f"Unresolved conflict between \"{self.claim_a[:120]}\" and "
            f"\"{self.claim_b[:120]}\" — {self.explanation or 'no rule separates them'}; "
            "report both."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "resolved": self.resolved,
            "winner": self.winner,
            "rule": self.rule,
            "explanation": self.explanation,
            "claim_a": self.claim_a[:200],
            "claim_b": self.claim_b[:200],
            "source_a": self.source_a,
            "source_b": self.source_b,
        }


_ESTIMATE_RE = re.compile(
    r"\b(estimat\w+|approximat\w+|roughly|about|around|some|nearly|"
    r"project\w+|forecast\w+|expect\w+|could|may|might)\b", re.I
)


_MEASURED_RE = re.compile(
    r"\b(reported|filed|recorded|measured|audited|official|census|"
    r"according to the (?:filing|report|statement)|disclosed|published)\b", re.I
)


def _side(fact: Optional[Dict[str, Any]], claim: str, url: str) -> Dict[str, Any]:
    """Everything adjudication needs about one side of a conflict."""
    fact = fact if isinstance(fact, dict) else {}
    profile = _guard(lambda: classify_source(url), None) if url else None
    return {
        "claim": claim,
        "url": url,
        "primary": bool(fact.get("is_primary")) or bool(getattr(profile, "is_primary", False)),
        "authority": _safe_float(getattr(profile, "authority", 0.0)),
        "verified": fact.get("verified") is True,
        "independent": _independent_sources(fact) if fact else 1,
        "year": _fact_year(fact) if fact else (max(_years_in(claim)) if _years_in(claim) else None),
        "estimated": bool(_ESTIMATE_RE.search(claim)),
        "measured": bool(_MEASURED_RE.search(claim)),
        "specificity": len(_guard(lambda: extract_numbers(claim, limit=8), [])),
    }


def _rule_primary(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if a["primary"] and not b["primary"]:
        return "a", "the first is a primary source (filing, dataset or official report) and the second is not"
    if b["primary"] and not a["primary"]:
        return "b", "the second is a primary source (filing, dataset or official report) and the first is not"
    return None


def _rule_measured_over_estimated(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if a["measured"] and b["estimated"] and not a["estimated"]:
        return "a", "the first reports a measured figure and the second is an estimate"
    if b["measured"] and a["estimated"] and not b["estimated"]:
        return "b", "the second reports a measured figure and the first is an estimate"
    return None


def _rule_verified(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if a["verified"] and not b["verified"]:
        return "a", "the first was verified against its cited source and the second was not"
    if b["verified"] and not a["verified"]:
        return "b", "the second was verified against its cited source and the first was not"
    return None


def _rule_corroboration(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if a["independent"] >= b["independent"] + 2:
        return "a", f"the first is carried by {a['independent']} independent sources against {b['independent']}"
    if b["independent"] >= a["independent"] + 2:
        return "b", f"the second is carried by {b['independent']} independent sources against {a['independent']}"
    return None


def _rule_recency(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Newer supersedes older ONLY for quantities that change over time.

    Applying recency to a constant is wrong — a 2025 blog does not supersede a
    2019 peer-reviewed measurement of something that does not move. The
    time-varying test gates this rule for exactly that reason.
    """
    if not (a["year"] and b["year"]) or a["year"] == b["year"]:
        return None
    if not _TIME_VARYING_RE.search(f"{a['claim']} {b['claim']}"):
        return None
    if a["year"] > b["year"]:
        return "a", f"the first reflects {a['year']} and supersedes the {b['year']} figure for a quantity that changes over time"
    return "b", f"the second reflects {b['year']} and supersedes the {a['year']} figure for a quantity that changes over time"


def _rule_authority(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    gap = a["authority"] - b["authority"]
    if gap >= 0.25:
        return "a", f"the first source carries materially higher authority ({a['authority']:.2f} vs {b['authority']:.2f})"
    if gap <= -0.25:
        return "b", f"the second source carries materially higher authority ({b['authority']:.2f} vs {a['authority']:.2f})"
    return None


_ADJUDICATION_RULES: Sequence[Tuple[str, Any]] = (
    ("primary_source", _rule_primary),
    ("measured_over_estimated", _rule_measured_over_estimated),
    ("verified", _rule_verified),
    ("recency_for_time_varying", _rule_recency),
    ("independent_corroboration", _rule_corroboration),
    ("source_authority", _rule_authority),
)


def adjudicate(
    contradiction: Dict[str, Any],
    facts: Sequence[Dict[str, Any]] = (),
) -> Resolution:
    """Classify a conflict and, if genuine, decide it on stated rules.

    "Sources disagree" is the right answer only when the sources are actually
    comparable in quality. When a regulator's filing contradicts a trade-press
    estimate, presenting both as equally weighted is not neutrality — it is a
    failure to do the analysis the reader came for. Every verdict names the
    rule that produced it, so a reader can reject the reasoning rather than
    having to trust it.
    """
    claim_a = str(contradiction.get("claim_a", "") or "")
    claim_b = str(contradiction.get("claim_b", "") or "")
    source_a = str(contradiction.get("source_a", "") or "")
    source_b = str(contradiction.get("source_b", "") or "")

    fact_a = _find_fact(facts, claim_a)
    fact_b = _find_fact(facts, claim_b)

    kind, explanation = _guard(
        lambda: classify_conflict(contradiction, fact_a, fact_b),
        (ConflictKind.GENUINE, ""),
    )
    base = Resolution(
        kind=kind, resolved=False, claim_a=claim_a, claim_b=claim_b,
        source_a=source_a, source_b=source_b, explanation=explanation,
    )
    if kind != ConflictKind.GENUINE:
        # Not a disagreement: suppressed from the conflict list, and the
        # explanation is what the report should say instead of a fake range.
        return base

    side_a = _side(fact_a, claim_a, source_a)
    side_b = _side(fact_b, claim_b, source_b)
    for rule_name, rule in _ADJUDICATION_RULES:
        verdict = _guard(lambda: rule(side_a, side_b), None)
        if verdict:
            winner, why = verdict
            base.resolved = True
            base.winner = winner
            base.rule = rule_name
            base.explanation = why
            return base

    base.explanation = (
        "both sources are comparable in authority, recency and corroboration"
    )
    return base


def _find_fact(facts: Sequence[Dict[str, Any]], claim: str) -> Optional[Dict[str, Any]]:
    """Locate the fact behind a contradiction's claim text.

    Contradictions carry claim strings, not fact references, so the richer
    metadata (verification standing, primary flag, publication date) has to be
    recovered by matching. Exact match first, then best similarity above a
    floor, so a truncated claim string still resolves.
    """
    if not claim:
        return None
    target = claim.strip()
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        text = str(fact.get("claim", "") or "").strip()
        if not text:
            continue
        if text == target:
            return fact
        score = _guard(lambda: semantic_similarity(text, target), 0.0)
        if score > best_score:
            best, best_score = fact, score
    return best if best_score >= 0.6 else None


def adjudicate_all(
    contradictions: Sequence[Dict[str, Any]],
    facts: Sequence[Dict[str, Any]] = (),
) -> List[Resolution]:
    """Adjudicate every detected conflict, in input order."""
    out: List[Resolution] = []
    for item in contradictions or ():
        if isinstance(item, dict):
            out.append(adjudicate(item, facts))
    return out
