"""What KIND of evidence does this question require?

The pipeline already ranks by domain authority, which answers "how much should
we trust this publisher". It does not answer the question that actually decides
whether a result is usable: "is this the KIND of document the question is
asking for?"

A live run showed the cost. Asked for *the latest revenue guidance from Nvidia's
most recent earnings filing*, the top-ranked results were eight Britannica pages
defining "forward guidance", plus arXiv and OECD pages. The genuine primary
sources — `nvidianews.nvidia.com`, `investor.nvidia.com` — were retrieved but
buried. Every one of those publishers is authoritative; they were simply the
wrong *type* of document. Britannica is the right publisher for "what is a
forward guidance".

So this module classifies the question's FORM and hands the ranker a target to
steer toward.

Deliberately general: every signal below describes question shape (does it want
a filing, a dataset, a trial, a statute?), never a subject area. Nothing here
knows what Nvidia or batteries or air quality are. Subject-specific publisher
hints live in `sources.PRIMARY_SOURCE_HINTS` and stay out of this file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Tuple

# --- the evidence types ----------------------------------------------------

EV_FILING = "filing"            # corporate/official disclosure: the record itself
EV_STATISTICAL = "statistical"  # official counts, rates, datasets
EV_ACADEMIC = "academic"        # studies, trials, preprints
EV_LEGAL = "legal"              # statutes, regulations, rulings
EV_CURRENT = "current"          # what happened most recently
EV_COMPARISON = "comparison"    # weighed against alternatives
EV_ENCYCLOPEDIC = "encyclopedic"  # definitions, orientation

EVIDENCE_TYPES: Tuple[str, ...] = (
    EV_FILING, EV_STATISTICAL, EV_ACADEMIC, EV_LEGAL,
    EV_CURRENT, EV_COMPARISON, EV_ENCYCLOPEDIC,
)

# --- form signals ----------------------------------------------------------
#
# Weighted so a decisive phrase ("annual report", "peer-reviewed") outvotes an
# incidental one ("report" alone). Patterns are matched on word boundaries.

_SIGNALS: Dict[str, Tuple[Tuple[str, float], ...]] = {
    EV_FILING: (
        (r"\b10-?k\b", 3.0), (r"\b10-?q\b", 3.0), (r"\b8-?k\b", 3.0),
        (r"\bs-?1\b", 1.5), (r"\bannual report\b", 3.0), (r"\bquarterly report\b", 3.0),
        (r"\bearnings?\b", 2.5), (r"\brevenue guidance\b", 3.0), (r"\bfinancial guidance\b", 3.0), (r"\bguidance\b", 0.6),
        (r"\bfiling\b", 2.5), (r"\bfiled\b", 1.5), (r"\bs-?1\b", 1.0),
        (r"\bbalance sheet\b", 2.0), (r"\bprofit and loss\b", 2.0),
        (r"\bshareholder letter\b", 2.5), (r"\binvestor relations?\b", 2.5),
        (r"\bsec\b", 1.5), (r"\bedgar\b", 3.0), (r"\bfinancial statements?\b", 2.0),
        (r"\bdividend\b", 1.5), (r"\bmarket cap(italisation|ization)?\b", 1.5),
        (r"\bearnings call\b", 2.0), (r"\boutlook\b", 1.0),
    ),
    EV_STATISTICAL: (
        (r"\bhow many\b", 2.5), (r"\bhow much\b", 1.5),
        (r"\bwhat (?:share|percentage|percent|proportion|fraction)\b", 2.5),
        (r"\bper capita\b", 3.0), (r"\bpopulation\b", 1.0),
        (r"\bstatistic(s|al)?\b", 2.0), (r"\bdataset\b", 2.5),
        (r"\bdata set\b", 2.0), (r"\bmedian\b", 2.0), (r"\baverage\b", 1.2),
        (r"\brate\b", 1.0), (r"\bestimate[ds]?\b", 1.2),
        (r"\bdeaths?\b", 1.0), (r"\bprevalence\b", 2.0), (r"\bincidence\b", 1.5),
        (r"\bmortality\b", 1.5), (r"\bcensus\b", 2.0), (r"\bsurvey\b", 1.0),
        (r"\btotal number\b", 2.0), (r"\bby country\b", 1.5), (r"\bby region\b", 1.5),
        (r"\btrends?\b", 1.5), (r"\bover time\b", 1.5),
        (r"\bchange[ds]? over\b", 2.0), (r"\bhow (?:has|have)\b", 0.8),
        (r"\bgrowth\b", 1.2), (r"\bevolution\b", 1.2),
    ),
    EV_ACADEMIC: (
        (r"\bstud(?:y|ies)\b", 2.0), (r"\bresearch\b", 1.2),
        (r"\bevidence (?:that|which|whether)\b", 2.5),
        (r"\btrial\b", 2.5), (r"\bmeta-?analys[ei]s\b", 3.0),
        (r"\bsystematic review\b", 3.0), (r"\bpeer-?reviewed\b", 3.0),
        (r"\bhypothes[ei]s\b", 2.0), (r"\bcohort\b", 2.0),
        (r"\bdouble-?blind\b", 3.0), (r"\bplacebo\b", 2.5),
        (r"\bcausal\b", 2.0), (r"\bdoi\b", 2.5), (r"\bpreprint\b", 2.0),
        (r"\bsample size\b", 2.0), (r"\bp\s*[<=>]\s*0?\.\d", 2.5),
        (r"\bpublished in\b", 1.5), (r"\bexperimental\b", 1.5),
    ),
    EV_LEGAL: (
        (r"\bregulation(s|al)?\b", 2.5), (r"\bdirective\b", 2.5),
        (r"\bstatute(s)?\b", 3.0), (r"\blegislation\b", 2.5),
        (r"\blaw\b", 1.5), (r"\bact\b", 0.8), (r"\bcourt\b", 2.0),
        (r"\bruling\b", 2.5), (r"\bdecision\b", 1.0),
        (r"\bcompliance\b", 2.0), (r"\bliability\b", 1.5),
        (r"\benforced?\b", 1.5), (r"\bclause\b", 1.5),
        (r"\barticle \d+\b", 2.0), (r"\bsection \d+\b", 1.2),
        (r"\bpenalt(?:y|ies)\b", 2.5), (r"\bsanctions?\b", 2.0),
        (r"\brule[ds]?\b", 2.0), (r"\bmandate[ds]?\b", 2.0),
        (r"\bdisclosure\b", 1.5), (r"\bguidance (?:on|issued)\b", 1.5),
        (r"\bfines?\b", 2.0), (r"\bdata breach(?:es)?\b", 2.0),
        (r"\bprohibition\b", 2.0), (r"\bnon-?compliance\b", 2.5),
        (r"\bregulator(?:y|s)?\b", 2.0), (r"\bwaiver\b", 1.5),
        (r"\bcourt of (?:appeal|justice)\b", 3.0), (r"\bprecedent\b", 2.0),
    ),
    EV_CURRENT: (
        (r"\blatest\b", 2.5), (r"\bmost recent\b", 2.5),
        (r"\bthis (?:week|month|year|quarter)\b", 2.0),
        (r"\b(?:today|yesterday|this morning)\b", 2.0),
        (r"\brecent(?:ly)?\b", 1.5), (r"\bcurrent(?:ly)?\b", 1.0),
        (r"\bas of\b", 1.5), (r"\bannounced\b", 1.2),
        (r"\bup to date\b", 2.0), (r"\bnewest\b", 2.0), (r"\bnow\b", 0.6),
        (r"\b20(?:2[4-9]|3\d)\b", 1.0),  # an explicit future-ish year implies currency
    ),
    EV_COMPARISON: (
        (r"\bvs\.?\b", 3.0), (r"\bversus\b", 3.0), (r"\bcompare[ds]?\b", 2.5),
        (r"\bcomparison\b", 2.5), (r"\bbetter than\b", 2.0),
        (r"\bdifference between\b", 2.0), (r"\bwhich (?:is|one) (?:is )?better\b", 2.5),
        (r"\bpros and cons\b", 3.0), (r"\balternatives?\b", 1.5),
        (r"\btrade-?offs?\b", 2.0), (r"\bpros\b", 1.0), (r"\bcons\b", 1.0),
    ),
    EV_ENCYCLOPEDIC: (
        (r"what does .{0,40}\bmean\b", 2.5),
        (r"\bwhat (?:is|are|was|were)\b", 1.5),
        (r"\bdefine\b", 3.0), (r"\bdefinition of\b", 3.0),
        (r"\bmeaning of\b", 3.0), (r"\boverview of\b", 2.0),
        (r"\bintroduction to\b", 2.5), (r"\bexplains?\b", 1.5),
        (r"\bbackground on\b", 2.0), (r"\bhistory of\b", 1.5),
    ),
}

# A question that explicitly asks what something IS wants orientation, so the
# definition-page penalty must not fire on it.
_ASKS_DEFINITION = re.compile(
    r"\b(what (?:is|are|was|were)\b|define\b|definition of\b|meaning of\b|"
    r"overview of\b|introduction to\b|explain\b|what does .{0,40}\bmean\b)",
    re.I,
)

# "What is X?" asks for a definition. "What is the LATEST X?" asks for the most
# recent document about X, so the same opening words must not disable the
# definition-page penalty. These markers are what separate the two.
_SPECIFICITY_RE = re.compile(
    r"\b(latest|most recent|recent|current|currently|today|now|this (?:year|month|week)|"
    r"newest|updated|up to date|recently|20[0-9]{2})\b",
    re.I,
)


@dataclass(frozen=True)
class EvidenceNeed:
    """What kind of document would actually answer this question."""

    primary: str = EV_ENCYCLOPEDIC
    scores: Dict[str, float] = field(default_factory=dict)
    signals: Tuple[str, ...] = ()
    wants_fresh: bool = False
    asks_definition: bool = False
    entity_tokens: Tuple[str, ...] = ()


_COMPILED: Dict[str, Tuple[Tuple[re.Pattern[str], float], ...]] = {
    ev: tuple((re.compile(p, re.I), w) for p, w in pats)
    for ev, pats in _SIGNALS.items()
}

# Tokens that mark a question as being about a SPECIFIC subject rather than a
# concept: capitalised words that are not sentence-initial, digits, and short
# all-caps acronyms. Used to spot a result that matches the question's wording
# while missing its subject entirely.
_SENTENCE_START = re.compile(r"^\s*\S")
_ENTITY = re.compile(
    r"\b(?:"
    r"[A-Z][a-zA-Z0-9]*(?:[ -][A-Z][a-zA-Z0-9]*)*"   # Camel / spaced proper nouns
    r"|[A-Z]{2,}"                                       # acronyms: FDA, SEC, GDP
    r"|\d[\d,.]*"                                       # figures and years
    r")\b"
)
_GENERIC = {
    "the", "a", "an", "what", "which", "how", "many", "much", "does", "do", "is",
    "are", "was", "were", "in", "on", "of", "for", "to", "and", "or", "by", "with",
    "latest", "most", "recent", "current", "currently", "new", "newest", "report",
    "compare", "comparison", "versus", "vs", "list", "explain", "describe",
    "summarise", "summarize", "identify", "evaluate", "assess", "review",
    "data", "study", "research", "information", "about", "this", "that", "it",
    "its", "as", "at", "from", "between", "over", "under", "vs", "versus",
}


def entity_tokens(question: str) -> Tuple[str, ...]:
    """Distinctive subject tokens a result must engage with to be on-topic.

    Sentence-initial capitals are skipped ("How many..."), as are stock words.
    What survives is the part of the question that names *what it is about* —
    "Nvidia", "SEC", "2024". A page that never mentions them is not answering
    the question, however well its wording overlaps.
    """
    out: List[str] = []
    for raw in _ENTITY.finditer(question or ""):
        tok = raw.group(0).strip()
        if not tok or tok.lower() in _GENERIC:
            continue
        # A capital that merely opens the sentence is not a subject. Drop the
        # opening word and let the rest of the question speak.
        if raw.start() == 0 and len(out) == 0 and tok.isalpha() and tok[0].isupper():
            if tok.lower() in _GENERIC:
                continue
        out.append(tok)
    # De-duplicate case-insensitively, keep order.
    seen = set()
    uniq = []
    for t in out:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return tuple(uniq)


def classify_evidence_need(question: str) -> EvidenceNeed:
    """Classify what kind of evidence a question requires.

    Returns the dominant type plus the full score table, so a caller can reward
    a runner-up too — a question about "the latest GDP revision" is both
    statistical and current, and both signals should pull ranking the same way.
    """
    q = question or ""
    scores: Dict[str, float] = {}
    signals: List[str] = []
    for ev, pats in _COMPILED.items():
        total = 0.0
        for pat, weight in pats:
            m = pat.search(q)
            if not m:
                continue
            # A decisive phrase counts once, however often it repeats:
            # repetition is emphasis, not extra evidence of question type.
            total += weight
            signals.append(m.group(0).lower())
        if total:
            scores[ev] = round(total, 3)

    if not scores:
        primary = EV_ENCYCLOPEDIC
    else:
        primary = max(scores.items(), key=lambda kv: (kv[1], -EVIDENCE_TYPES.index(kv[0])))[0]

    return EvidenceNeed(
        primary=primary,
        scores=scores,
        signals=tuple(dict.fromkeys(signals)),
        wants_fresh=scores.get(EV_CURRENT, 0.0) >= 2.0,
        asks_definition=(
            bool(_ASKS_DEFINITION.search(q)) and not _SPECIFICITY_RE.search(q)
        ),
        entity_tokens=entity_tokens(q),
    )


def required_types(need: EvidenceNeed, threshold: float = 1.5) -> FrozenSet[str]:
    """Every evidence type the question meaningfully demands."""
    return frozenset(ev for ev, sc in need.scores.items() if sc >= threshold)
