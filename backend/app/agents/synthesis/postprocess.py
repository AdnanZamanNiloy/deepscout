"""Post-generation cleanup: scrub pipeline telemetry, normalise, disambiguate.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). These run on the writer's prose AFTER synthesis: reduce repeated audit
language, drop sentences that narrate the pipeline's own metrics, normalise
markdown block structure, shorten question-shaped headings, and guarantee the
disambiguation contract is present.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.agents.evidence_utils import split_into_sentences

from app.agents.synthesis.primitives import order_by_assumed_reading
from app.agents.synthesis.sections import _dedupe_heading


# Sentences that expose the pipeline's own internals rather than the subject.
# The model is told not to write these (system prompt rule 6), but live runs
# proved it copies the metadata block verbatim. The machine-appended appendix
# states these numbers correctly; the body must not narrate the research system.
#
# Patterns are anchored to SELF-REFERENTIAL phrasing. The previous version
# matched bare "evidence pool", "quality review", "pipeline stages" and
# "verified facts", which are ordinary English in a report ABOUT research,
# software or evaluation — and because sanitization joins each paragraph into a
# single line, one such match deleted the entire paragraph.
_PIPELINE_TELEMETRY_RE = re.compile(
    r"(?i)("
    r"\bpipeline (?:confidence|reports?|itself|stages?)\b|"
    r"\bthis (?:report|pipeline|system)'?s own (?:confidence|score)\b|"
    r"\brelevance \d{1,3}\s*/\s*\d{1,3}\b|"
    r"\bquality (?:score|gate|review) (?:of |is |was |returned )\b|"
    r"\bbelow the \d{1,3}\s*/\s*\d{1,3} floor\b|"
    r"\bbelow the \d\.\d+ threshold\b|"
    r"\bconfidence (?:score )?(?:is|was) \d\.\d+\b|"
    r"\b\d+ of \d+ facts? (?:were |was )?verified\b|"
    r"\b(?:the )?evidence pool (?:contains?|held?|has)\b|"
    r"\bfacts? in the (?:evidence )?pool\b|"
    r"\bself-?verif(?:ied|ication) (?:rate|pass)\b|"
    r"\bdeterministic fallback\b|\bdegraded run\b|"
    # Process-mechanics phrasing the task calls out explicitly. These describe
    # how the research was run, not what was found.
    r"\bcorroboration attempts?\b|"
    r"\btargeted corroboration\b|"
    r"\bevidence grade [A-D]\b|"
    r"\bsearch budget\b|\btoken budget\b|"
    r"\binternal confidence (?:calculation|score)\b|"
    r"\buncovered (?:research )?dimensions?\b|"
    r"\bagent state\b|"
    r"\bcapability gaps? on\b|"
    r"\bsingle-source after \d+\b"
    r")"
)


# Sentence-level audit-language openings that convey the same idea ("the
# evidence does not establish X"). When an answer repeats this idea, only the
# first uncited instance is kept; later uncited repeats are dropped. A sentence
# carrying a [n] citation is a substantive qualification, never removed.
_REDUNDANT_AUDIT_RE = re.compile(
    r"(?i)\b("
    r"the (?:available )?evidence (?:does not|doesn't|cannot|can't|is unable to)\b|"
    r"what the evidence (?:does not|doesn't|lacks|omits)\b|"
    r"(?:could not|cannot|can't) be (?:verified|determined|established|confirmed)\b|"
    r"the (?:research|evidence) pool (?:does not|doesn't|lacks|contains no)\b|"
    r"this cannot be determined\b|"
    r"there is (?:insufficient|not enough) evidence\b|"
    r"not enough (?:evidence|information) (?:to|is)\b|"
    r"remains? (?:unclear|unknown|uncertain)\b|"
    r"it is (?:unclear|unknown|impossible to (?:say|determine))\b"
    r")"
)

# One is the correct proportionality: the answer states the limitation once.
MAX_AUDIT_LANGUAGE_SENTENCES = 1


def _reduce_redundant_audit_language(text: str) -> str:
    """Collapse repeated audit-language formulations to a single statement.

    Phase 11.1 §2: an answer must LEAD with the best-supported conclusion, not
    repeat "the evidence does not establish…" in several places. Proportionate:
    the first uncited audit-language sentence is retained (the limitation is
    still stated) and later UNCITED repeats are dropped. A cited limitation is
    substantive evidence handling and is always kept, so grounding and citation
    enforcement are untouched. Deterministic; never adds or changes a fact.

    Runs after `_scrub_pipeline_telemetry`, which removes whole pipeline-
    telemetry sentences; this targets the softer repeated limitation phrasing.
    """
    if not text or not _REDUNDANT_AUDIT_RE.search(text):
        return text
    kept = 0
    out_paragraphs: List[str] = []
    for para in text.split("\n\n"):
        stripped = para.strip()
        # Headings, bullets and tables are structural; leave them untouched.
        if not stripped or stripped.startswith(("#", "-", "*", "|")):
            out_paragraphs.append(para)
            continue
        sentences = split_into_sentences(para, max_sentences=40)
        if not sentences:
            out_paragraphs.append(para)
            continue
        surviving: List[str] = []
        for sentence in sentences:
            is_audit = bool(_REDUNDANT_AUDIT_RE.search(sentence))
            has_citation = bool(re.search(r"\[\d+\]", sentence))
            if not is_audit or has_citation:
                surviving.append(sentence)
                continue
            if kept < MAX_AUDIT_LANGUAGE_SENTENCES:
                kept += 1
                surviving.append(sentence)
            # else: drop the repeated, uncited limitation sentence.
        out_paragraphs.append(" ".join(s.strip() for s in surviving).strip())
    return "\n\n".join(p for p in out_paragraphs if p.strip())


def _scrub_pipeline_telemetry(text: str) -> str:
    """Drop sentences that narrate the pipeline's own metrics.

    Applied to the report body only, BEFORE the measured appendix is appended,
    so the correct figures in the appendix are never touched. Genuinely
    SENTENCE-granular: the previous version filtered line by line, and because
    `_sanitize_answer_text` joins a paragraph into one line, a single match
    deleted the whole paragraph. Headings and list structure are preserved; a
    paragraph that loses every sentence collapses away.
    """
    if not text:
        return text
    kept_paras: List[str] = []
    for para in text.split("\n\n"):
        stripped = para.strip()
        if not stripped or stripped.startswith("#"):
            kept_paras.append(para)
            continue
        kept_lines: List[str] = []
        for line in para.split("\n"):
            if not line.strip():
                continue
            if not _PIPELINE_TELEMETRY_RE.search(line):
                kept_lines.append(line)
                continue
            if line.lstrip().startswith(("- ", "* ")):
                # A bullet is one unit; drop it whole.
                continue
            sentences = split_into_sentences(line, max_sentences=40)
            surviving = [
                s.strip() for s in sentences
                if s.strip() and not _PIPELINE_TELEMETRY_RE.search(s)
            ]
            if surviving:
                kept_lines.append(" ".join(surviving))
        if kept_lines:
            kept_paras.append("\n".join(kept_lines))
    return "\n\n".join(kept_paras).strip()


def _normalize_query_concept(query: str) -> str:
    text = re.sub(r"\s+", " ", (query or "").strip()).strip(" ?.!")
    lower = text.lower()
    if lower.startswith("what is "):
        text = text[8:].strip()
    elif lower.startswith("what are "):
        text = text[9:].strip()
    elif lower.startswith("define "):
        text = text[7:].strip()
    if text:
        return text[0].upper() + text[1:]
    return "This topic"


# Inline bullet separators: models sometimes emit bullets on one line
# ("... induction [4]. - Electrical transformers modify ..."). A " - " right
# after a sentence end / citation bracket is a list separator, not prose;
# splitting on it restores real bullet lines. Only applied inside lines that
# already start with "- ".
_BULLET_SEP_RE = re.compile(r"(?<=[.!?\]])\s+-\s+(?=[A-Z0-9\"'(])")


# An ordered list item ("1) ", "2. "). The marker is capped at two digits so a
# line that merely opens with a year or a decimal ("2026 was ...", "3.5 billion
# people") is not mistaken for a list item and swallowed into the paragraph.
_ORDERED_ITEM_RE = re.compile(r"^\d{1,2}[.)]\s+\S")


def _sanitize_answer_text(answer: str, query: str) -> str:
    """Normalize the writer's markdown into the report's block structure.

    Preserves visual hierarchy: markdown headings become their own blocks,
    consecutive "- " and "N) " lines stay distinct list items (joined into one
    list block), and paragraph breaks are kept. Only intra-line whitespace
    collapses; consecutive prose lines still merge, so the model's line-wrapping
    does not become line breaks.

    Ordered markers are kept exactly as the writer wrote them and are never
    rewritten to "N." — `_ensure_disambiguation` recognises an existing numbered
    block by its "N) **" shape, so normalizing the marker would make it prepend a
    second, duplicate block.
    """
    raw_lines = (answer or "").replace("\r\n", "\n").split("\n")
    paras: List[str] = []
    current: List[str] = []
    bullets: List[str] = []
    ordered: List[str] = []

    def _flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            paras.append("\n".join(bullets))
            bullets = []

    def _flush_ordered() -> None:
        nonlocal ordered
        if ordered:
            paras.append("\n".join(ordered))
            ordered = []

    def _flush_lists() -> None:
        _flush_ordered()
        _flush_bullets()

    def _flush_prose() -> None:
        nonlocal current
        if current:
            paras.append(" ".join(current))
            current = []

    for raw_line in raw_lines:
        line = re.sub(r"\s+", " ", raw_line).strip()
        is_heading = bool(re.match(r"^#{1,6}\s+\S", line))
        if line.startswith("- "):
            _flush_prose()
            _flush_ordered()
            for part in _BULLET_SEP_RE.split(line):
                part = part.strip()
                if part:
                    bullets.append(part if part.startswith("- ") else f"- {part}")
        elif _ORDERED_ITEM_RE.match(line):
            # A numbered item is its own list entry, never prose. Merging these
            # into the surrounding paragraph is what flattened the ambiguity
            # block's "1. a 2. b 3. c" into one run-on sentence. Consecutive
            # items accumulate into one block (like bullets) and are flushed by
            # the next non-ordered line.
            _flush_prose()
            _flush_bullets()
            ordered.append(line)
        elif is_heading:
            _flush_lists()
            _flush_prose()
            paras.append(line)
        elif line:
            _flush_lists()
            current.append(line)
        else:
            _flush_lists()
            _flush_prose()
    _flush_lists()
    _flush_prose()
    text = "\n\n".join(paras).strip()

    # Fix malformed opening pattern like: "what is X refers to ..."
    q = (query or "").strip().rstrip("?")
    if q:
        text = re.sub(
            rf"(?i)^{re.escape(q)}\s+refers to",
            f"{_normalize_query_concept(query)} refers to",
            text,
        )

    # Collapse immediate repeated clause: "X is ... X is ..."
    text = re.sub(r"(?i)(\b[A-Z][A-Za-z\s\-]{2,40}\s+is\b[^.]*\.)\s+\1", r"\1", text)

    # Question-shaped headings are not labels: shorten them so a raw planner
    # question or writer-emitted question never becomes a section heading.
    text = _dedupe_heading(text)

    return text.strip()


def _deterministic_disambiguation(
    intent: Dict[str, Any], policy: Dict[str, Any] | None = None
) -> str:
    """The numbered 'n) **Sense** — explanation' block the gate requires.

    `policy` is the ambiguity DECISION. When it names an assumed reading that
    reading leads, so "meaning 1" is the meaning the pipeline actually chose —
    rather than whatever order the classifier happened to return. The two
    disagreed live: the pipeline assumed "Stressful or difficult" and the report
    announced "taken to mean the skills carrying the heaviest employer demand".
    """
    if not isinstance(intent, dict) or not intent.get("ambiguity"):
        return ""
    senses = [
        s for s in (intent.get("senses") or [])
        if isinstance(s, dict) and str(s.get("label", "")).strip()
    ]
    if len(senses) < 2:
        return ""
    senses = order_by_assumed_reading(senses, policy, lambda s: str(s.get("label", "")))
    lines = []
    for i, sense in enumerate(senses[:3], 1):
        label = str(sense.get("label", "")).strip()
        note = str(sense.get("note", "") or "").strip()
        if not note:
            note = str(sense.get("domain", "") or "distinct meaning").strip()
        lines.append(f"{i}) **{label}** — {note}")
    return "\n".join(lines)


def _ensure_disambiguation(answer: str, ctx: Dict[str, Any]) -> str:
    """Guarantee the ambiguity contract is in the shipped body.

    If the writer already opened with numbered sense lines, leave it alone;
    otherwise prepend the deterministic block so an ambiguous query never
    silently picks one meaning. Deterministic, no LLM.
    """
    intent = ctx.get("intent") or {}
    if not isinstance(intent, dict) or not intent.get("ambiguity"):
        return answer
    # A guidance request ("suggest me topics") is not made ambiguous by a
    # multi-reading term inside it; do not prepend the disambiguation opener.
    from app.agents.synthesis.context_blocks import _is_guidance_query

    if _is_guidance_query(intent):
        return answer
    if re.search(r"^\s*\d+\)\s*\*\*[^*]{2,120}\*\*", answer or "", re.M):
        return answer
    block = _deterministic_disambiguation(
        intent, ctx.get("ambiguity") if isinstance(ctx.get("ambiguity"), dict) else None
    )
    if not block:
        return answer
    heading = re.search(r"^##\s+Executive Summary\s*$", answer or "", re.M)
    if heading:
        head = answer[: heading.end()]
        tail = answer[heading.end():].lstrip()
        return f"{head}\n\n{block}\n\n{tail}"
    return f"{block}\n\n{answer.lstrip()}"
