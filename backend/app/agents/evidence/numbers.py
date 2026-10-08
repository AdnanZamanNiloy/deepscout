from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Set



_NUMBER_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<sign>[-+]?)"
    # Thousands separators: comma (1,600), narrow/regular space and thin space
    # (1 600 / 1 600) as used by SI, most statistical agencies and the EU. Space
    # grouping was previously unparsed, so "1 600 GW" read as 600 GW — a source
    # figure the report could then never match, flagging a correct number as a
    # fabrication.
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d{1,3}(?:[\u00a0\u202f\u2009 ]\d{3})+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?)"
    r"\s*"
    r"(?P<unit>%|percentage points?|percent|bps|"
    r"trillion|billion|million|thousand|bn|mn|"
    r"crore|lakh|lakhs|"
    r"twh|gwh|mwh|kwh|tw|gw|mw|kw|"
    r"usd|eur|gbp|jpy|inr|bdt|"
    r"dollars?|euros?|pounds?|yen|rupees?|taka|"
    r"years?|months?|days?|hours?|"
    r"tonnes?|tons?|kg|km|cm|mm)?",
    re.IGNORECASE,
)


_SCALE: Dict[str, float] = {
    "trillion": 1e12, "billion": 1e9, "bn": 1e9,
    "million": 1e6, "mn": 1e6, "thousand": 1e3,
    # Indian numbering system, ubiquitous in South-Asian fiscal reporting:
    # "73,746.06 crore Bangladeshi taka". Without these, the amount parsed as a
    # scale-free dimensionless count and could be "compared" against an
    # unrelated count (38 countries) as if they measured the same thing.
    "crore": 1e7, "lakh": 1e5, "lakhs": 1e5,
}


_UNIT_ALIASES: Dict[str, str] = {
    "percent": "%", "percentage point": "%", "percentage points": "%",
    "tons": "tonne", "ton": "tonne", "tonnes": "tonne", "tonne": "tonne",
    "years": "year", "months": "month", "days": "day", "hours": "hour",
    "dollar": "usd", "dollars": "usd", "euro": "eur", "euros": "eur",
    "pound": "gbp", "pounds": "gbp", "rupee": "inr", "rupees": "inr",
}


_CURRENCY_RE = re.compile(r"[$€£¥₹৳]")


@dataclass(frozen=True)
class Quantity:
    """A number lifted out of prose, with scale folded into the value."""

    value: float
    unit: str          # "%", "gw", "usd", "year", "" (dimensionless)
    raw: str
    is_year: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "raw": self.raw,
            "is_year": self.is_year,
        }


def extract_numbers(text: str, limit: int = 12) -> List[Quantity]:
    """Pull quantities out of a claim or passage.

    Scale words are folded in ("2.4 billion" -> 2.4e9) so two sources can be
    compared even when one writes 2.4bn and the other writes 2,400,000,000.
    Bare 4-digit values in 1500-2099 are flagged `is_year`: comparing a year
    to a magnitude is a category error the contradiction engine must avoid.
    """
    out: List[Quantity] = []
    haystack = text or ""
    for match in _NUMBER_RE.finditer(haystack):
        raw_num = re.sub(r"[,\u00a0\u202f\u2009 ]", "", match.group("num"))
        try:
            value = float(raw_num)
        except ValueError:
            continue
        if match.group("sign") == "-":
            value = -value

        unit = (match.group("unit") or "").strip().lower()
        if unit in _SCALE:
            value *= _SCALE[unit]
            unit = ""
        unit = _UNIT_ALIASES.get(unit, unit)

        is_year = (
            not unit
            and float(raw_num).is_integer()
            and len(raw_num.split(".")[0]) == 4
            and 1500 <= value <= 2099
        )

        # Currency symbol immediately before the number gives the unit when
        # no unit word follows ("$4.2 billion" -> usd).
        if not unit and match.start() > 0:
            prefix = haystack[max(0, match.start() - 2): match.start()]
            if _CURRENCY_RE.search(prefix):
                unit = "usd" if "$" in prefix else "currency"
                is_year = False

        out.append(Quantity(value=value, unit=unit, raw=match.group(0).strip(), is_year=is_year))
        if len(out) >= max(1, limit):
            break
    return out


def _significant_quantities(text: str) -> List[Quantity]:
    """Quantities worth grounding: skips years and small ordinals, which are
    ubiquitous and produce false "unsupported" verdicts."""
    return [
        q for q in extract_numbers(text)
        if not q.is_year and (abs(q.value) >= 2 or q.unit)
    ]


_ANCHOR_STOPWORDS: frozenset = frozenset("""
a about above after again against all also am an and any are as at be because
been before being below between both but by can could did do does doing during
each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself now
of off on once only or other our ours ourselves out over own same she should
so some such than that the their theirs them themselves then there these they
this those through to too under until very was we were what when where which
while who whom why will with you your yours yourself yourselves shall may
might must upon among within without across per via etc said says say
according based new one two three four five six seven eight nine ten first
second third last next many much several various including included include
still also become became becoming make made making use used using
""".split())


def rare_content_tokens(text: str, min_length: int = 4) -> Set[str]:
    """Distinctive content tokens of a text (numeric or long, non-stopword).

    These are the "rare anchors" of a claim: the tokens that carry its
    specificity (numbers, named entities, domain nouns). Used by the
    corroboration anchor matcher, which needs a signal that survives the
    paraphrase gap where full-claim similarity collapses. Numeric tokens
    matter most for quantitative claims ("200", "billion"), so they are kept
    regardless of length; ordinary words must be at least `min_length` chars
    to count, which filters the function vocabulary that leaked through the
    stopword list.
    """
    out: Set[str] = set()
    for token in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if token in _ANCHOR_STOPWORDS:
            continue
        if token.isdigit() or len(token) >= min_length:
            out.add(token)
    return out


def numbers_grounded(claim: str, source_text: str, tolerance: float = 0.02) -> bool:
    """True when every significant number in `claim` appears in `source_text`.

    Matching is value-based with a small relative tolerance, so "1.2 billion"
    grounds "1,200,000,000" and rounding differences do not fail an honest
    claim. Claims with no significant numbers are vacuously grounded.
    """
    claim_numbers = _significant_quantities(claim)
    if not claim_numbers:
        return True
    source_values = [q.value for q in extract_numbers(source_text or "", limit=400)]
    if not source_values:
        return False
    for q in claim_numbers:
        target = abs(q.value)
        scale = max(target, 1.0)
        if not any(abs(abs(v) - target) <= tolerance * scale for v in source_values):
            return False
    return True


def numeric_conflict(
    a: str, b: str, divergence: float = 0.20
) -> Optional[Dict[str, Any]]:
    """Compare like-united quantities in two claims; report a real conflict.

    Only quantities sharing a unit are compared — a "%" against a "gw" is not
    a disagreement — and years are excluded. Returns None when the claims are
    numerically compatible or not comparable at all.
    """
    qa = {q.unit: q for q in _significant_quantities(a)}
    qb = {q.unit: q for q in _significant_quantities(b)}
    shared = [u for u in qa if u in qb]
    for unit in shared:
        va, vb = qa[unit].value, qb[unit].value
        scale = max(abs(va), abs(vb), 1e-9)
        rel = abs(va - vb) / scale
        if rel >= divergence:
            return {
                "unit": unit or "dimensionless",
                "value_a": va,
                "value_b": vb,
                "relative_divergence": round(rel, 4),
                "raw_a": qa[unit].raw,
                "raw_b": qb[unit].raw,
            }
    return None
