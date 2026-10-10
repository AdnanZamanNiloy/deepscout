"""Section model: parse, order, merge, trim — one representation for all of it.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Owns the `_Section` representation, the canonical alias/heading/rank
tables, the markdown split/render/reorder/merge/strip helpers, the word-budget
trim, the readability pass, and the heading-normalisation helpers.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.core.logging import get_logger
from app.agents.evidence_utils import split_into_sentences

from app.agents.synthesis.primitives import _HEADING_RE

logger = get_logger(__name__)


@dataclass
class _Section:
    heading: str
    title: str
    blocks: List[str] = field(default_factory=list)
    key: str = ""

    def render(self) -> str:
        return "\n\n".join([self.heading] + [b for b in self.blocks if b.strip()])

    def words(self) -> int:
        return _count_words(self.render())


def _normalize_heading(text: str) -> str:
    """Comparison key for headings: casefolded, punctuation/space-insensitive."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


# Canonical section identities. Aliases are DISJOINT across keys — the previous
# version listed "source ledger" under both the evidence section and the
# ledger, so a presence check could be satisfied by one and still add the
# other, and reports shipped with three overlapping evidence sections.
_CANONICAL_ALIASES: Dict[str, Tuple[str, ...]] = {
    "executive summary": (
        "executive summary", "summary", "overview", "answer", "direct answer",
        "bottom line", "the short answer",
    ),
    "key findings": (
        "key findings", "main findings", "findings", "takeaways", "key takeaways",
    ),
    "key figures": (
        "key figures", "key quantitative figures", "key numbers",
        "quantitative findings", "the numbers",
    ),
    "counterarguments": (
        "counterarguments and disputed points", "counterarguments & disputed points",
        "counterarguments", "disputed points", "conflicting evidence",
        "contradictions", "the case against",
    ),
    "limitations": (
        "limitations and unknowns", "limitations & unknowns", "limitations",
        "unknowns", "gaps and limitations", "evidence limitations", "caveats",
    ),
    "open questions": (
        "open questions", "open questions & missing angles",
        "open questions and missing angles", "missing angles",
        "unanswered questions", "remaining gaps",
    ),
    "reasoning": (
        "reasoning", "argument", "analysis and reasoning", "reasoning structure",
        "argument structure", "conclusions",
    ),
    "evidence": (
        "evidence strength", "evidence and confidence", "evidence & confidence",
        "evidence quality", "confidence",
    ),
    "standing objections": ("standing objections",),
    "what would change": (
        "what would change this conclusion", "what would change our mind",
        "what would change the conclusion",
    ),
    "evidence integrity": ("evidence integrity",),
    "source ledger": (
        "auditable source ledger", "source ledger", "source audit",
        "evidence ledger", "full source list",
    ),
    "sources": ("sources", "references", "source list", "citations"),
}

_ALIAS_TO_KEY: Dict[str, str] = {
    _normalize_heading(alias): key
    for key, aliases in _CANONICAL_ALIASES.items()
    for alias in aliases
}

# Canonical heading text emitted when this module adds a section.
_CANONICAL_HEADING: Dict[str, str] = {
    "executive summary": "Executive Summary",
    "key findings": "Key Findings",
    "key figures": "Key Figures",
    "counterarguments": "Counterarguments & Disputed Points",
    "limitations": "Limitations & Unknowns",
    "open questions": "Open Questions & Missing Angles",
    "reasoning": "Reasoning",
    "evidence": "Evidence & Confidence",
    "standing objections": "Standing Objections",
    "what would change": "What Would Change This Conclusion",
    "evidence integrity": "Evidence Integrity",
    "source ledger": "Auditable Source Ledger",
    "sources": "Sources",
}

# Reading order. Writer sections (no canonical key) sort at _WRITER_RANK in
# their original sequence, so the deep-dives stay between findings and figures.
_WRITER_RANK = 40
_SECTION_RANK: Dict[str, int] = {
    "executive summary": 10,
    "key findings": 20,
    "key figures": 50,
    "reasoning": 55,
    "counterarguments": 60,
    "limitations": 65,
    "open questions": 70,
    "evidence": 80,
    "standing objections": 84,
    "what would change": 88,
    "evidence integrity": 92,
    "source ledger": 96,
    "sources": 99,
}


def _canonical_key(title: str) -> str:
    return _ALIAS_TO_KEY.get(_normalize_heading(title), "")


def _split_sections(text: str) -> Tuple[List[str], List[_Section]]:
    """Parse a markdown report into a preamble plus H1/H2 sections.

    Splits on H1/H2 only, so `###` sub-structure stays inside its parent
    section. The previous trim split on any `#`, which pulled sub-headings out
    as top-level sections and let a protected parent lose its children.
    """
    preamble: List[str] = []
    sections: List[_Section] = []
    current: Optional[_Section] = None
    for para in (text or "").split("\n\n"):
        stripped = para.strip()
        if not stripped:
            continue
        match = _HEADING_RE.match(stripped)
        if match and len(match.group(1)) <= 2:
            title = match.group(2).strip()
            current = _Section(heading=stripped, title=title, key=_canonical_key(title))
            sections.append(current)
        elif current is None:
            preamble.append(stripped)
        else:
            current.blocks.append(stripped)
    return preamble, sections


def _render_report(preamble: Sequence[str], sections: Sequence[_Section]) -> str:
    parts = [p for p in preamble if p.strip()]
    parts.extend(s.render() for s in sections)
    return "\n\n".join(parts).strip()


def _reorder_sections(text: str) -> str:
    """Put sections in canonical reading order, stable within each rank.

    Deterministic sections used to be appended at the end in dict order, so a
    report whose writer omitted the Executive Summary ended with it. Sorting by
    (rank, original index) fixes the order without reordering the writer's own
    deep-dive sections relative to each other.
    """
    preamble, sections = _split_sections(text)
    if len(sections) < 2:
        return text
    decorated = [
        (_SECTION_RANK.get(section.key, _WRITER_RANK), index, section)
        for index, section in enumerate(sections)
    ]
    decorated.sort(key=lambda item: (item[0], item[1]))
    return _render_report(preamble, [item[2] for item in decorated])


def _dedupe_canonical_sections(text: str) -> str:
    """Keep one section per canonical identity — the longest occurrence.

    Safety net for the case where the writer produced a section the machine
    also appends (or produced two sections that mean the same thing). Writer
    sections with no canonical key are never touched, so genuine sub-topics
    with similar names both survive.
    """
    preamble, sections = _split_sections(text)
    best_by_key: Dict[str, int] = {}
    for index, section in enumerate(sections):
        if not section.key:
            continue
        current = best_by_key.get(section.key)
        if current is None or section.words() > sections[current].words():
            best_by_key[section.key] = index
    kept: List[_Section] = []
    for index, section in enumerate(sections):
        if section.key and best_by_key.get(section.key) != index:
            logger.debug("[Synthesizer] dropped duplicate section '%s'", section.title)
            continue
        kept.append(section)
    return _render_report(preamble, kept)


def _strip_canonical_sections(text: str, keys: Iterable[str]) -> str:
    """Remove whole sections by canonical key (used before auditing)."""
    drop = {k for k in keys}
    if not drop:
        return text
    preamble, sections = _split_sections(text)
    return _render_report(preamble, [s for s in sections if s.key not in drop])


def _count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def _trim_to_budget(text: str, budget_words: int) -> str:
    """Bring a draft inside a word budget without losing required content.

    Only writer sections (those with no canonical identity) are trimmed, and
    never below their first paragraph — the one carrying the section's
    synthesis claim. Machine sections and the Executive Summary are never
    touched: a length overrun must not delete the limitations section. Drops
    the LAST paragraph of the LONGEST trimmable section, repeatedly, stopping
    as soon as the draft fits or nothing more may be trimmed.
    """
    if budget_words <= 0 or _count_words(text) <= budget_words:
        return text
    preamble, sections = _split_sections(text)
    guard = 0
    while _count_words(_render_report(preamble, sections)) > budget_words and guard < 200:
        guard += 1
        candidates = [s for s in sections if not s.key and len(s.blocks) > 1]
        if not candidates:
            break
        longest = max(candidates, key=lambda s: s.words())
        longest.blocks.pop()
    rendered = _render_report(preamble, sections)
    if _count_words(rendered) > budget_words:
        logger.info(
            "[Synthesizer] draft still %d words over budget after trimming writer "
            "sections; required sections were preserved",
            _count_words(rendered) - budget_words,
        )
    return rendered


_MAX_PARA_SENTENCES = 4
_MAX_PARA_WORDS = 95


def _split_long_paragraphs(text: str) -> str:
    """Break paragraphs that exceed the readability limits at sentence bounds.

    Rule 2 of the prompt asks for 3-4 sentence paragraphs; models comply
    unevenly and the section-wise path concatenates independently written
    prose. This enforces it deterministically. Bullet blocks, headings and
    short paragraphs pass through untouched.
    """
    out: List[str] = []
    for para in (text or "").split("\n\n"):
        stripped = para.strip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.lstrip().startswith(("-", "*", ">", "|"))
            or "\n" in stripped
        ):
            out.append(stripped)
            continue
        sentences = [s.strip() for s in split_into_sentences(stripped, max_sentences=40) if s.strip()]
        if len(sentences) <= _MAX_PARA_SENTENCES and _count_words(stripped) <= _MAX_PARA_WORDS:
            out.append(stripped)
            continue
        chunk: List[str] = []
        for sentence in sentences:
            chunk.append(sentence)
            long_enough = len(chunk) >= _MAX_PARA_SENTENCES or _count_words(
                " ".join(chunk)
            ) >= _MAX_PARA_WORDS
            if long_enough:
                out.append(" ".join(chunk))
                chunk = []
        if chunk:
            # A one-sentence tail reads as an orphan; fold it into the previous
            # chunk unless that would recreate an over-long paragraph.
            if len(chunk) == 1 and out and _count_words(out[-1]) + _count_words(chunk[0]) <= _MAX_PARA_WORDS + 25:
                out[-1] = out[-1] + " " + chunk[0]
            else:
                out.append(" ".join(chunk))
    return "\n\n".join(p for p in out if p).strip()


def _dedupe_repeated_bullets(text: str) -> str:
    """Drop an identical bullet repeated inside the same block."""
    out: List[str] = []
    for para in (text or "").split("\n\n"):
        if not para.lstrip().startswith(("- ", "* ")):
            out.append(para)
            continue
        seen: Set[str] = set()
        lines: List[str] = []
        for line in para.split("\n"):
            key = _normalize_heading(line)
            if key and key in seen:
                continue
            seen.add(key)
            lines.append(line)
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _section_map(answer: str) -> Dict[str, str]:
    """Section title -> body, for checks that compare sections against each other."""
    _, sections = _split_sections(answer)
    return {s.title: "\n\n".join(s.blocks) for s in sections if s.blocks}


def _shorten_heading(text: str) -> str:
    """Reduce a question-shaped heading to a concise label.

    The section writer is told the question to answer; it sometimes emits that
    question as its own `## ` heading. Live deep reports shipped three such
    headings on one query, each duplicating the query and padding the report.
    Deterministic, no LLM; a heading already label-shaped is returned unchanged.
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip()).strip(" ?.!,")
    if not cleaned:
        return text
    if "?" not in text and len(cleaned.split(" ")) <= 10 and len(cleaned) <= 60:
        return cleaned
    clause = re.split(r"[?:—–]|\s+-\s+", cleaned, maxsplit=1)[0].strip(" ?.!,")
    for prefix in (
        "what are the ", "what are ", "what is the ", "what is ", "what was ",
        "how did the ", "how did ", "how does the ", "how does ", "how do ",
        "how ", "why does ", "why did ", "why ", "which ", "when did ", "when ",
        "to what extent ",
    ):
        if clause.lower().startswith(prefix):
            clause = clause[len(prefix):]
            break
    words = clause.split(" ")
    if len(words) > 9:
        clause = " ".join(words[:9]).rstrip(",;:")
    clause = clause.strip(" ?.!,")
    if not clause:
        return text
    return clause[0].upper() + clause[1:]


def _dedupe_heading(answer: str) -> str:
    """Shorten any remaining question-shaped H2/H3 heading post-assembly."""
    out: List[str] = []
    for line in (answer or "").split("\n"):
        match = re.match(r"^(\s{0,3}#{1,6}\s+)(.*\S)\s*$", line)
        if not match:
            out.append(line)
            continue
        title = match.group(2)
        # Canonical machine headings are already labels and must keep their
        # exact text, or the alias table stops recognising them.
        if _canonical_key(title):
            out.append(line)
        elif title.endswith("?") or len(title.split()) > 12:
            out.append(match.group(1) + _shorten_heading(title))
        else:
            out.append(line)
    return "\n".join(out)


def _strip_duplicate_section_heading(body: str, title: str) -> str:
    """Remove a leading heading the section writer emitted for itself.

    The section-wise assembler prepends `## <title>` to every section body.
    When the writer also opens with `## <title>` (or `# / ### <title>`), the
    heading appears twice in the shipped report. Drop the writer's copy only
    when it names this section; keep any *different* heading so genuine
    sub-structure is never destroyed. Deterministic, no LLM.
    """
    if not body:
        return body
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return body
    match = re.match(r"^#{1,6}\s+(.*\S)\s*$", lines[i].strip())
    if not match:
        return body
    if _normalize_heading(match.group(1)) != _normalize_heading(title):
        return body
    remaining = lines[i + 1:]
    if remaining and not remaining[0].strip():
        remaining = remaining[1:]
    return "\n".join(remaining).strip() if remaining else ""


# Canonical headings this assembler may ADD, with the aliases that count as
# already present so a well-written report is never duplicated. Kept as a
# module-level mapping for callers that introspect it.
REQUIRED_SECTIONS: Dict[str, Tuple[str, ...]] = {
    heading: _CANONICAL_ALIASES[key]
    for key, heading in _CANONICAL_HEADING.items()
    if key in (
        "executive summary", "key findings", "evidence", "limitations",
        "counterarguments", "open questions", "key figures", "source ledger",
    )
}

_REQUIRED_KEY_BY_HEADING: Dict[str, str] = {
    heading: key for key, heading in _CANONICAL_HEADING.items()
}


def _present_section_keys(answer: str) -> Set[str]:
    """Normalized headings present in the report body."""
    present: Set[str] = set()
    for match in re.finditer(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$", answer or "", re.M):
        present.add(_normalize_heading(match.group(1)))
    return present


def _normalized_aliases(canonical: str) -> Tuple[str, ...]:
    aliases = REQUIRED_SECTIONS.get(canonical)
    if aliases is None:
        key = _REQUIRED_KEY_BY_HEADING.get(canonical, "")
        aliases = _CANONICAL_ALIASES.get(key, (canonical,))
    return tuple(_normalize_heading(alias) for alias in aliases)
