from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.core.semantic import pair_similarity
from app.core.logging import get_logger

logger = get_logger(__name__)

from app.agents.sources.fit import (
    underlying_source_key,
)
from app.agents.sources.tiers import (
    classify_source,
)
from app.agents.sources.urls import (
    extract_domain,
)

_DEFINITION_TITLE = re.compile(
    r"\b(what (?:is|are|was|were)\b|definition\b|definitions\b|meaning\b|"
    r"explained\b|glossary\b|overview\b|introduction\b|described as\b|"
    r"how (?:it|they) work\b|simple explanation\b|what does .{0,40}\bmean\b|"
    r"everything you need to know\b|commonly confused\b)",
    re.I,
)


def looks_like_definition_page(title: str, snippet: str = "") -> bool:
    """A glossary/definition page rather than a document about the subject."""
    blob = f"{title or ''} {snippet or ''}"
    return bool(_DEFINITION_TITLE.search(blob))


def definition_misfit(title: str, snippet: str, asks_definition: bool) -> float:
    """Penalty for a definition page returned to a non-definition question.

    0.0 when the page is on-type, or when the reader actually asked what
    something is — in which case a definition page is precisely the right hit.
    """
    if asks_definition:
        return 0.0
    return -0.30 if looks_like_definition_page(title, snippet) else 0.0


TOPICALITY_AUTHORITY_FLOOR = 0.35


MIN_TOPICAL_ENGAGEMENT = 0.10


_TOPICAL_STOPWORDS: frozenset = frozenset({
    "a", "about", "an", "and", "are", "as", "at", "be", "been", "by", "compared",
    "did", "do", "does", "during", "explain", "for", "from", "give", "has",
    "have", "how", "in", "into", "is", "it", "its", "list", "many", "much",
    "of", "on", "or", "overview", "per", "report", "summarize", "summarise",
    "than", "that", "the", "their", "them", "there", "these", "this", "those",
    "to", "was", "were", "what", "when", "where", "which", "who", "why",
    "with", "within",
    # NOT "without": negation is the one word class that must never be a
    # stopword in a similarity component (see AGENTS.md bug history). With
    # "without" here, a page about states WITHOUT electricity access and one
    # about states WITH access scored identically on every overlap signal, so
    # the ranker could not tell a source proving the gap from one describing
    # progress. core.semantic keeps the same rule via _NEGATION_TOKENS_ENGINE.
    # Meta-questions about the subject rather than the subject itself. "define
    # RAG" is a question ABOUT the term "RAG"; scoring a page on having the
    # word "define" would rate every glossary page as on-topic.
    "define", "defined", "defines", "definition", "mean", "means", "meaning",
    "called", "known", "term", "actually", "really",
})


def _topical_words(text: str) -> Set[str]:
    return {
        w for w in re.findall(r"[a-z0-9][a-z0-9'&.-]*", (text or "").lower())
        if w not in _TOPICAL_STOPWORDS and len(w) > 1
    }


_ENTITY_TITLE_SALIENCE = 1.0    # the subject is in the headline


_ENTITY_REPEAT_SALIENCE = 0.65  # named several times in the body


_ENTITY_ONCE_SALIENCE = 0.25    # named once: probably an incidental mention


_ENTITY_HEAD_CHARS = 600        # lead paragraph region


_FIRST_PARTY_ENGAGEMENT_FLOOR = 0.5


_SALIENT_MAX_PENALTY = 0.40


_SALIENT_DISCOUNT = 0.25


def _entity_salience(
    text: str,
    entity_tokens: Sequence[str],
    title: str = "",
    url: str = "",
) -> float:
    """How prominently the question's named subjects appear in `text`, 0.0-1.0.

    Presence in the headline counts most, repeated body mentions count next, a
    single mention counts least. Averaged over the entities actually found, so
    a question naming three subjects is not fully satisfied by one of them.

    `url` is consulted for the strongest signal of all: a document published BY
    the named subject. An issuer's own filings index does not have to repeat the
    question's wording to be about the issuer, and demoting it for that is how
    an aggregator restating the question beat the primary source.
    """
    if not entity_tokens:
        return 0.0
    low = (text or "").lower()
    head = low[:_ENTITY_HEAD_CHARS]
    low_title = (title or "").lower()

    scores: List[float] = []
    for tok in entity_tokens:
        needle = (tok or "").strip().lower()
        if not needle:
            continue
        if first_party_match(url, (tok,)):
            scores.append(1.0)
            continue
        if needle in low_title:
            scores.append(_ENTITY_TITLE_SALIENCE)
            continue
        hits = low.count(needle)
        if hits >= 2:
            scores.append(_ENTITY_REPEAT_SALIENCE)
        elif hits == 1:
            # A single mention in the lead paragraph is about as close to
            # incidental as a single mention can get while still being real.
            scores.append(_ENTITY_ONCE_SALIENCE if needle in head else 0.0)
        else:
            scores.append(0.0)
    return (sum(scores) / len(scores)) if scores else 0.0


def topical_engagement(
    query: str,
    text: str,
    entity_tokens: Sequence[str] = (),
    title: str = "",
    url: str = "",
) -> float:
    """How much of the question's subject a result actually engages, 0.0-1.0.

    Two independent readings, because either alone is easy to fool:

      * CONTENT-WORD OVERLAP — the share of the question's subject words that
        appear in the result. Fails on paraphrases and on proper nouns the
        result abbreviates.
      * ENTITY ENGAGEMENT — whether any subject the question NAMED (a person,
        an organisation, an acronym, a figure) appears at all. Survives
        paraphrase, and catches the page that shares the question's wording
        while being about something else.

    The entity reading wins when the question named anything, because a named
    subject is the part a substitute page is most likely to drop. A question
    with no nameable subject ("what is a quark") falls back to overlap alone.

    0.0 when the query carries no subject to engage with, so callers must treat
    it as "cannot judge" rather than "irrelevant" — the ranking floor checks the
    query is substantive before discarding anything on this basis.

    WHY THE ENTITY READING NO LONGER REPLACES THE LEXICAL ONE
    --------------------------------------------------------
    The original rule was `max(lexical, named)` with `named` a boolean
    any-substring-match, which handed one incidental mention absolute veto
    power. Measured on a live SearXNG run:

        "renewable energy capacity Germany" vs a cellular-network paper
            lexical 0.000 -> reported 1.000 (Germany was in an affiliation)
        "population total 2024 Malawi" vs a US census.gov page
            lexical 0.250 -> reported 1.000 (Malawi was in a country table)

    Authority is scored separately, so a perfect topicality score on an
    official-but-wrong-domain publisher is exactly how an NCBI paper on green
    base-station power outranked the German renewables statistics page.

    The rescue this rule was invented for — a genuine paraphrase that shares no
    surface words — is now done properly by the hybrid similarity signal, which
    is idf-weighted and negation-aware, and the entity reading only ever
    MODULATES a document that already engages the subject:

        engagement = base * (0.6 + 0.4 * salience)

    so naming the subject can lift a partial match by up to 40% but can never
    promote a document that engages nothing.
    """
    q_words = _topical_words(query)
    t_words = _topical_words(text)
    lexical = (len(q_words & t_words) / len(q_words)) if q_words else 0.0
    if not entity_tokens:
        return lexical

    # Paraphrase rescue: weak surface overlap but genuinely the same subject.
    base = max(lexical, pair_similarity(query, text))

    # A document published BY the named subject is about it regardless of
    # wording. This has to be a floor rather than a multiplier, because such a
    # page is often exactly the case the multiplier cannot help: an issuer's
    # filings index shares no subject word with a question about "latest revenue
    # guidance" ("quarterly results and SEC filings"). Multiplying zero by any
    # salience still yields zero, which is how an aggregator restating the
    # question in its own words came to beat the primary source.
    first_party = first_party_match(url, entity_tokens) is not None
    if first_party:
        base = max(base, _FIRST_PARTY_ENGAGEMENT_FLOOR)

    salience = _entity_salience(text, entity_tokens, title, url)

    # Salience refines a WEAK reading; it must not re-judge a strong one.
    #
    # An earlier version applied base * (0.6 + 0.4 * salience) unconditionally
    # and that was measurably wrong: for "population total 2024 Malawi" the UN
    # page ("total population, both sexes... Malawi") matches every subject
    # word, yet a peripheral mention of Malawi elsewhere cost it 0.14 while the
    # exact-match World Bank page lost 0.40 — demoting the better answer. A
    # document that already covers the question's subject is relevant; where the
    # subject is mentioned is a different, weaker question.
    #
    # So the discount fades out as coverage rises: none at solid coverage, full
    # weight only when there is nothing else to go on.
    discount = _SALIENT_DISCOUNT + (1.0 - _SALIENT_DISCOUNT) * min(1.0, base * 2.0)
    return base * (1.0 - _SALIENT_MAX_PENALTY * (1.0 - salience) * discount)


def is_topically_irrelevant(
    query: str,
    text: str,
    entity_tokens: Sequence[str] = (),
    title: str = "",
    url: str = "",
) -> bool:
    """Does this result engage NOTHING the question is about?

    The floor rule, kept separate from `topical_engagement` because it answers a
    different question. Engagement is a continuous score for ranking; this is
    the binary "is this document about a different subject" test that justifies
    discarding the result entirely.

    Both readings must fail before a document is discarded. A genuine
    paraphrase ("forward guidance" for a question about revenue guidance) is
    rescued by the similarity signal, and a document that genuinely discusses
    the named subject is rescued by the lexical reading because the subject is
    part of the question text and so counts toward its word coverage.

    The old rule instead kept any document merely CONTAINING a named subject,
    which is why the cellular-network paper above (zero subject-word overlap)
    was never discarded at all.
    """
    if topical_engagement(query, text, entity_tokens, title, url) >= MIN_TOPICAL_ENGAGEMENT:
        return False
    return topical_engagement(query, text, ()) < MIN_TOPICAL_ENGAGEMENT


def topicality_floor_applies(query: str, entity_tokens: Sequence[str] = ()) -> bool:
    """Is this query specific enough that irrelevance is detectable?

    A question has to say something before "not about it" means anything.
    "population of Malawi" is two content words and entirely judgeable; "what is
    it" is not, and filtering on engagement there would discard results for no
    reason. The bar is deliberately low and only asks whether the query carries
    a subject at all.
    """
    if entity_tokens:
        return True
    return len(_topical_words(query)) >= 2


def entity_miss(query: str, tokens: Sequence[str], text: str, url: str = "") -> float:
    """Penalty when a result engages none of the question's subject tokens.

    Wording overlap alone can carry a result to the top while the document is
    about something else entirely. Requiring at least one subject token keeps
    the genuinely on-topic primary source competitive with it.

    A single incidental mention cancels the penalty, so salience decides: a
    subject named in the headline or repeatedly is genuinely engaged, while a
    name appearing once deep in the body — an affiliation, a country table, a
    reference — is not, and leaves the full penalty in force.
    """
    if not tokens:
        return 0.0
    if _entity_salience(text, tokens, url=url) >= _ENTITY_ONCE_SALIENCE:
        return 0.0
    # Scale with how much of the question's subject we are missing.
    return -0.22 if len(tokens) == 1 else -0.30


_HOST_SPLIT = re.compile(r"[.\-_]+")


def _host_labels(domain: str) -> Set[str]:
    """Candidate tokens for a host, ignoring the public suffix.

    `investor.nvidia.com` -> {investor, nvidia, com}. Public suffixes are not
    excluded by a full list (that would need a dependency); instead a token is
    only usable if it is not a bare TLD, which is enough to stop ".com" from
    matching a question that happens to contain "com".
    """
    labels: Set[str] = set()
    for part in _HOST_SPLIT.split((domain or "").lower()):
        if len(part) >= 3 and not part.isdigit():
            labels.add(part)
    return labels


def first_party_match(url: str, entity_tokens: Sequence[str]) -> Optional[str]:
    """The subject token this host is first-party for, if any.

    Requires the token to be at least 4 characters so a short acronym does not
    match by accident, and to appear as a whole host label.
    """
    if not entity_tokens:
        return None
    labels = _host_labels(extract_domain(url))
    if not labels:
        return None
    for tok in entity_tokens:
        t = re.sub(r"[^\w]+", "", (tok or "").lower())
        if len(t) >= 4 and t in labels:
            return t
    return None


def first_party_bonus(url: str, entity_tokens: Sequence[str]) -> float:
    """Reward a host that belongs to the question's own subject.

    Small and unconditional on tier: it says "this publisher is the subject",
    which is orthogonal to how authoritative it is. An issuer's own IR page and
    an official statistics agency are both first-party for their question.
    """
    return 0.22 if first_party_match(url, entity_tokens) else 0.0


def underlying_groups(
    items: Iterable[Tuple[str, str, str, str]],
) -> List[List[str]]:
    """Group (url, title, snippet, content) tuples by underlying source.

    Returns the groups in first-seen order. Anything without a usable URL forms
    its own group so an unidentifiable fact is never silently merged away.
    """
    order: List[str] = []
    groups: Dict[str, List[str]] = {}
    for url, title, snippet, content in items or ():
        u = (url or "").strip()
        if not u:
            order.append(f"\x00anon{len(order)}")
            groups[order[-1]] = [u]
            continue
        key = underlying_source_key(u, title or "", snippet or "", content or "")
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(u)
    return [groups[k] for k in order]


def independence_ratio(
    items: Iterable[Tuple[str, str, str, str]],
) -> float:
    """Independent sources / total facts. 1.0 = every fact stands alone.

    A report built from one study plus four summaries of it scores 0.2; the
    same five documents retrieved as five genuinely separate findings scores
    1.0. This is what stops repetition from reading as corroboration.
    """
    rows = [t for t in (items or ()) if (t[0] or "").strip()]
    if not rows:
        return 1.0
    groups = underlying_groups(rows)
    if not groups:
        return 1.0
    return len(groups) / len(rows)


def independent_primary_share(
    items: Iterable[Tuple[str, str, str, str]],
) -> float:
    """Share of INDEPENDENT sources that are primary or first-rate authority.

    The independence-aware counterpart to `primary_source_share`: one primary
    study quoted by four aggregators scores 1.0 here (one independent source,
    and it is primary) while counting four separate primaries, which is the
    number that actually flattered the old measure.
    """
    rows = [t for t in (items or ()) if (t[0] or "").strip()]
    if not rows:
        return 0.0
    groups = underlying_groups(rows)
    if not groups:
        return 0.0
    good = 0
    for group in groups:
        url = group[0]
        prof = classify_source(url)
        # Prefer the strongest document in the group: a group containing the
        # original is as primary as its original.
        if prof.is_primary or prof.authority >= 0.85:
            good += 1
    return good / len(groups)
