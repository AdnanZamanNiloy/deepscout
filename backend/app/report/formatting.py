"""Report markdown normalisation, shared by the pipeline and the export path.

Normalises ragged pipe tables into aligned GFM tables, drops template
placeholders and repeated/adjacent duplicate headings, unwraps placeholder
links, and strips "Author not specified" reference filler. Content is never
dropped: a table row with too many cells keeps its overflow in the last column.
"""
from __future__ import annotations

import re
from typing import List


_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(
    r"</?(?:p|div|span|strong|em|b|i|u|ul|ol|li|h[1-6]|table|thead|tbody|tr|td|th)\s*/?>",
    re.IGNORECASE,
)
_TABLE_SEP_CELL_RE = re.compile(r"^\s*:?-{1,}:?\s*$")
_PLACEHOLDER_HEADING_RE = re.compile(r"^#{1,6}\s+new header\b.*$", re.IGNORECASE)
_PLACEHOLDER_NONE_RE = re.compile(r"^#{1,6}\s*none\s*$", re.IGNORECASE)
_PLACEHOLDER_DATE_RE = re.compile(r"^#{1,6}\s*date\s*:\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _remove_placeholder_lines(lines: List[str]) -> List[str]:
    """Drop unfilled template headings/lines ("New header", "# None", "Date:")."""
    out: List[str] = []
    skipping = False
    for line in lines:
        if _PLACEHOLDER_DATE_RE.match(line) or _PLACEHOLDER_NONE_RE.match(line):
            continue
        if _PLACEHOLDER_HEADING_RE.match(line):
            skipping = True
            continue
        if skipping:
            if _HEADING_RE.match(line):
                skipping = False
            else:
                continue
        out.append(line)
    return out


def _collapse_adjacent_headings(lines: List[str]) -> List[str]:
    """Drop the first of two identical headings with only blank lines between.

    The publisher template emits ``## Introduction`` and the writer's model
    output frequently begins with its own ``## Introduction`` heading, so the
    assembled report can carry the heading twice back-to-back. For an *adjacent*
    pair the body belongs to the second heading, so the first (empty) one is
    dropped — unlike :func:`_dedupe_repeated_headings`, which removes the later
    occurrence and its body.
    """
    out: List[str] = []
    i = 0
    while i < len(lines):
        cur = _HEADING_RE.match(lines[i])
        if cur:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = _HEADING_RE.match(lines[j]) if j < len(lines) else None
            if (
                nxt
                and len(nxt.group(1)) == len(cur.group(1))
                and nxt.group(2).strip().lower() == cur.group(2).strip().lower()
            ):
                # Identical heading immediately follows: keep the later pair.
                i = j
                continue
        out.append(lines[i])
        i += 1
    return out


def _dedupe_repeated_headings(lines: List[str]) -> List[str]:
    """Drop a heading (and its body) that repeats an earlier heading verbatim."""
    seen: set = set()
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = _HEADING_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        level = len(m.group(1))
        key = (level, m.group(2).strip().lower())
        if key in seen:
            i += 1
            while i < len(lines):
                nxt = _HEADING_RE.match(lines[i])
                if nxt and len(nxt.group(1)) <= level:
                    break
                i += 1
            continue
        seen.add(key)
        out.append(lines[i])
        i += 1
    return out


def _split_table_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    out: List[str] = []
    cur = ""
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if ch == "|":
            out.append(cur.strip())
            cur = ""
            i += 1
            continue
        cur += ch
        i += 1
    out.append(cur.strip())
    return out


def _normalize_pipe_table(block_lines: List[str]) -> List[str]:
    """Turn a run of pipe-delimited lines into a clean, aligned GFM table."""
    rows = [_split_table_row(line) for line in block_lines]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return block_lines
    body = [r for r in rows if not all(_TABLE_SEP_CELL_RE.match(c or "") for c in r)]
    if not body:
        return block_lines
    header = body[0]
    data = body[1:]
    width = max(len(header), 2)
    if width < 2:
        return block_lines

    def fit(cells: List[str]) -> List[str]:
        out = list(cells[:width])
        while len(out) < width:
            out.append("")
        if len(cells) > width:
            out[width - 1] = f"{out[width - 1]} {' '.join(cells[width:])}".strip()
        return out

    lines = ["| " + " | ".join(fit(header)) + " |"]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in data:
        lines.append("| " + " | ".join(fit(row)) + " |")
    return lines


def _strip_placeholder_links(text: str) -> str:
    """Unwrap links whose target is a placeholder, not a URL.

    The model sometimes emits ``[some text](link)`` or ``[title](url)`` — a
    literal placeholder word where a real URL belongs. Left as-is these render
    as broken links. The link text is kept as plain prose; only the fake target
    is removed.
    """
    # [text](link) / [text](url) / [text](source) — a bare placeholder token.
    return re.sub(
        r"\[([^\]]+)\]\((?:link|url|source|website|link url|source url)\)",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )


# Filler an author slot the model could not fill. Left in, every reference
# reads "..., Author not specified in provided metadata [url]" — a visible
# defect. The phrase (and its variants) is removed from reference lines only,
# so the entry falls back to "Title (Year). Site. [url]", which is traceable.
_AUTHOR_PLACEHOLDER_RE = re.compile(
    r"\bAuthor not specified(?: in provided metadata)?\.?,?\s*",
    re.IGNORECASE,
)


def _strip_author_placeholders(text: str) -> str:
    """Remove literal "Author not specified..." filler from reference entries."""
    return _AUTHOR_PLACEHOLDER_RE.sub("", text)


def sanitize_report_markdown(markdown: str) -> str:
    """Clean report markdown for storage and rendering.

    Removes literal HTML artifacts, unwraps placeholder links, strips
    "Author not specified" reference filler, normalises ragged pipe tables into
    aligned GFM tables, drops template placeholders and repeated headings, and
    trims trailing whitespace. Content is never dropped: a table row with too
    many cells keeps its overflow in the last column.
    """
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_TAG_RE.sub("", text)
    text = _HTML_BREAK_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ")
    text = _strip_placeholder_links(text)
    text = _strip_author_placeholders(text)

    lines = text.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and line.count("|") >= 2:
            block: List[str] = []
            j = i
            while j < len(lines) and lines[j].strip() and "|" in lines[j]:
                block.append(lines[j])
                j += 1
            out.extend(_normalize_pipe_table(block))
            i = j
            continue
        out.append(line.rstrip())
        i += 1
    out = _remove_placeholder_lines(out)
    out = _collapse_adjacent_headings(out)
    out = _dedupe_repeated_headings(out)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
