"""Report markdown sanitisation: strip HTML artifacts, normalise tables.

The engine's writer/LLM occasionally emits literal `<br>` breaks and ragged
pipe tables that render badly. `sanitize_report_markdown` cleans them at the
single point where the report is assembled, so the persisted report, the
trace, replay and every export share one clean form.
"""
from __future__ import annotations

from app.report.formatting import sanitize_report_markdown


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


def test_duplicate_introduction_heading_is_removed():
    md = (
        "# Report\n\n"
        "## Introduction\nFirst intro.\n\n"
        "## Body\nBody text.\n\n"
        "## Introduction\nA second intro block.\n\n"
        "## Conclusion\nDone."
    )
    out = sanitize_report_markdown(md)
    assert out.count("## Introduction") == 1
    assert "First intro." in out
    assert "A second intro block." not in out
    assert "## Conclusion" in out and "Done." in out


def test_placeholder_new_header_block_is_removed():
    md = (
        "# Report\n\n"
        "## Body\n\n"
        "### New header (similar to existing header)\n\n"
        "While the previous section discussed A, this explores B.\n\n"
        "### Real Point\nReal content."
    )
    out = sanitize_report_markdown(md)
    assert "New header" not in out
    assert "While the previous section" not in out
    assert "### Real Point" in out and "Real content." in out


def test_bare_date_heading_is_removed_but_value_kept():
    md = "# Report\n\n## Date:\n2026-06-01\n\n## Body\nx"
    out = sanitize_report_markdown(md)
    assert "## Date:" not in out
    assert "2026-06-01" in out


def test_bare_none_title_is_removed():
    out = sanitize_report_markdown("# Report\n# None\nreal body")
    assert "# None" not in out


def test_adjacent_duplicate_heading_is_collapsed_body_kept():
    """Publisher template + writer model both emit the heading back-to-back.

    The body belongs to the second heading, so collapsing must keep it (unlike
    the spaced-apart dedup, which drops the later heading with its body).
    """
    md = (
        "## Introduction\n"
        "## Introduction\n\n"
        "The body text that must survive.\n\n"
        "## Conclusion\n"
        "## Conclusion\n\n"
        "The closing text that must survive."
    )
    out = sanitize_report_markdown(md)
    assert out.count("## Introduction") == 1
    assert out.count("## Conclusion") == 1
    assert "The body text that must survive." in out
    assert "The closing text that must survive." in out


def test_placeholder_link_targets_are_unwrapped():
    """`[text](link)` (a literal placeholder for a URL) must not render broken."""
    out = sanitize_report_markdown("See [the 2027 report](link) and [x](url).")
    assert "(link)" not in out and "(url)" not in out
    assert "the 2027 report" in out and "x" in out
    # A real URL target is untouched.
    kept = sanitize_report_markdown("Real [paper](https://a.test/x) stays.")
    assert "[paper](https://a.test/x)" in kept
