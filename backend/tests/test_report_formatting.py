"""Report markdown sanitisation: strip HTML artifacts, normalise tables.

The engine's writer/LLM occasionally emits literal `<br>` breaks and ragged
pipe tables that render badly. `sanitize_report_markdown` cleans them at the
single point where the report is assembled, so the persisted report, the
trace, replay and every export share one clean form.
"""
from __future__ import annotations

from app.engine.orchestrator import sanitize_report_markdown


def test_literal_br_tags_are_removed_not_printed():
    out = sanitize_report_markdown("line one<br/>line two<br>line three")
    assert "<br" not in out.lower()
    assert "line one line two line three" in out


def test_stray_inline_html_tags_are_stripped():
    out = sanitize_report_markdown("This is <b>bold</b> and <p>a para</p>.")
    assert "<b>" not in out and "<p>" not in out
    assert "This is bold and a para." in out


def test_wellformed_table_is_preserved_with_separator():
    md = "\n".join([
        "| Indicator | Status |",
        "| --- | --- |",
        "| A | ok |",
        "| B | warn |",
    ])
    out = sanitize_report_markdown(md)
    lines = out.splitlines()
    assert lines[0] == "| Indicator | Status |"
    assert lines[1] == "| --- | --- |"
    assert "| A | ok |" in out
    assert "| B | warn |" in out


def test_ragged_table_gets_aligned_and_padded():
    md = "\n".join([
        "| A | B | C |",
        "| --- | --- | --- |",
        "| one |",
        "| x | y | z | overflow |",
    ])
    out = sanitize_report_markdown(md)
    rows = [l for l in out.splitlines() if l.startswith("|")]
    # Every row now has exactly 3 columns.
    for row in rows:
        assert row.count("|") == 4, row
    # Overflow content is preserved, not dropped.
    assert "overflow" in out


def test_separatorless_table_gains_a_separator_row():
    md = "\n".join(["| H1 | H2 |", "| v1 | v2 |", "| v3 | v4 |"])
    out = sanitize_report_markdown(md)
    lines = out.splitlines()
    assert lines[1] == "| --- | --- |"


def test_single_pipe_line_is_left_as_prose():
    out = sanitize_report_markdown("The ratio is a | b here.")
    assert "The ratio is a | b here." in out


def test_three_plus_blank_lines_collapse():
    out = sanitize_report_markdown("a\n\n\n\n\nb")
    assert "\n\n\n" not in out
    assert "a" in out and "b" in out


def test_content_is_never_dropped():
    md = "## Heading\n\nA paragraph with **bold** and [a link](https://x.org).\n\n- one\n- two"
    out = sanitize_report_markdown(md)
    for token in ("## Heading", "A paragraph with **bold**", "https://x.org", "- one", "- two"):
        assert token in out
