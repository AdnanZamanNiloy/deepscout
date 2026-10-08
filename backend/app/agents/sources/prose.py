from __future__ import annotations

import re
from typing import Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


MACHINE_SECTIONS: Tuple[str, ...] = (
    "## Evidence & Confidence",
    "## Evidence Strength",
    "## Limitations",
    "## Limitations & Unknowns",
    "## Counterarguments & Disputed Points",
    "## Evidence integrity",
    "## Source ledger",
    "## Standing objections",
    "## What Would Change Our Mind",
    "## Reasoning",
    "## Sources",
)


def strip_machine_sections(text: str) -> str:
    """Drop appended machine-generated sections, keeping the writer's prose.

    Splits on the canonical headings so a heading buried mid-document is
    handled the same as a trailing appendix. Case-sensitive on the exact
    heading text the synthesizer emits, so ordinary prose containing the word
    "Limitations" is never truncated by accident.
    """
    body = text or ""
    cut = len(body)
    for heading in MACHINE_SECTIONS:
        match = re.search(rf"(?m)^\s*{re.escape(heading)}\s*$", body)
        if match and match.start() < cut:
            cut = match.start()
    return body[:cut]


_INLINE_CITE_RE = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\](?=\s*\[\d+)|\s*\[\d+(?:\s*,\s*\d+)*\]")


_EM_DASH_RE = re.compile(r"\s*\u2014\s*")


_HR_RE = re.compile(r"(?m)^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$")


def clean_writer_prose(text: str) -> str:
    """Strip presentation noise from the WRITER'S prose only.

    Two things the model emits that read as machine output rather than an
    answer, and that the reader should not have to see:

    1. Inline citation markers (`[4]`, `[1][6]`). These are load-bearing
       DURING the run — citation density is scored from them, and the evidence
       ledger enumerates by the same numbers — so they are removed here, after
       scoring, and only from prose. The ledger keeps its numbering.
    2. Thematic breaks (`---`). Headings already delimit sections, so a rule
       under every heading is redundant scaffolding.

    Both are applied to the writer's body only: `strip_machine_sections` runs
    first so the audit sections (source ledger, confidence panel) are never
    touched. Machine sections legitimately use rules to separate themselves.
    """
    body = strip_machine_sections(text or "")
    body = _INLINE_CITE_RE.sub("", body)
    body = _HR_RE.sub("", body)
    # Em dash -> comma. A colon would read better after a lead-in clause, but
    # that needs parsing; a comma is grammatical in both directions and never
    # produces a run-on.
    body = _EM_DASH_RE.sub(", ", body)
    # Collapse the blank-line runs the removals leave behind, and any run of
    # three or more newlines, without touching single paragraph breaks.
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    # Tidy the punctuation the dash swap can double up, and drop a comma that
    # would now sit directly before sentence-ending punctuation.
    body = re.sub(r",\s*([.,;:])", r"\1", body)
    body = re.sub(r"\(\s*,\s*", "(", body)
    body = re.sub(r"\s+,", ",", body)
    return body.strip()
