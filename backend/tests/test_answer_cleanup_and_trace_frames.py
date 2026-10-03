"""Presentation cleanup of the delivered answer, and trace replay frames.

Two independent defects this locks down:

1. The writer emits inline citation markers (`[4]`, `[1][6]`) and thematic
   breaks (`---`) that read as machine scaffolding. Both are load-bearing
   DURING a run — citation density is scored from the markers, and the evidence
   ledger enumerates by the same numbers — so they must be stripped only after
   scoring, and only from the writer's prose. The machine sections keep their
   own numbering and their own rules.

2. The pipeline trace renders the emitted NDJSON frames. `agent_events` records
   which node ran, not what the user saw, so a restored session replayed from
   it showed "0 steps" — the run's history looked erased on navigation. The
   frames are now persisted verbatim and replayed as-is.
"""

import json

import pytest

from app.agents.sources import clean_writer_prose
from app.db.sqlite import load_trace_frames, save_trace_frames


# --------------------------------------------------------------------------
# Writer-prose cleanup
# --------------------------------------------------------------------------

WRITER = """## Capability Is Accelerating

---

Growth accelerated [4] and capex scaled faster [1][6]. Adoption rose [8].

## Where It Goes Next

Costs fell [12, 14] while quality held.
"""


def test_inline_citation_markers_are_removed():
    out = clean_writer_prose(WRITER)
    for marker in ("[4]", "[1][6]", "[8]", "[12, 14]"):
        assert marker not in out, f"{marker} survived into the delivered answer"
    # The sentences themselves must remain intact — only the marker goes.
    assert "Growth accelerated" in out
    assert "capex scaled faster" in out
    assert "Adoption rose." in out


def test_multi_marker_runs_collapse_without_leaving_gaps():
    # "[1][6]" is two markers written adjacently; each is removed with any
    # whitespace it introduced, so the sentence does not gain a double space.
    out = clean_writer_prose("Values rose [1][6] sharply.")
    assert out == "Values rose sharply."


def test_thematic_breaks_are_removed_but_headings_survive():
    out = clean_writer_prose(WRITER)
    assert "---" not in out
    assert "## Capability Is Accelerating" in out
    assert "## Where It Goes Next" in out
    # Paragraph breaks between sections must still exist.
    assert "\n\n" in out


def test_machine_sections_are_left_untouched():
    # The audit layer legitimately numbers its ledger and rules off its own
    # sections. Cleaning must stop at the first machine heading.
    doc = WRITER + "\n\n## Source ledger\n\n[1] Some claim about batteries.\n"
    out = clean_writer_prose(doc)
    assert "## Source ledger" not in out, "machine section must not reach the reader"
    assert "[1] Some claim about batteries." not in out


def test_cleaning_never_returns_empty_for_real_prose():
    out = clean_writer_prose(WRITER)
    assert out.strip(), "cleanup must not eat the whole answer"
    assert len(out) > 40


def test_cleanup_is_idempotent():
    once = clean_writer_prose(WRITER)
    assert clean_writer_prose(once) == once


def test_text_without_noise_passes_through():
    plain = "## Heading\n\nA single clear paragraph of prose."
    assert clean_writer_prose(plain) == plain


def test_empty_input_is_safe():
    assert clean_writer_prose("") == ""
    assert clean_writer_prose(None) == ""


# --------------------------------------------------------------------------
# Trace frame persistence
# --------------------------------------------------------------------------

FRAMES = [
    {"type": "progress", "message": "Query received"},
    {"type": "search_query", "query": "battery storage", "results": [
        {"title": "IEA", "url": "https://iea.org/ger", "source": "tavily"},
    ]},
    {"type": "final_report", "report": "# Answer\n\nStorage grew."},
]


@pytest.mark.asyncio
async def test_trace_frames_round_trip_in_order(tmp_path):
    db = str(tmp_path / "frames.db")
    # research_runs must exist for the foreign key to resolve.
    import aiosqlite

    from app.db.sqlite import CREATE_TABLE_SQL

    async with aiosqlite.connect(db) as conn:
        await conn.executescript(CREATE_TABLE_SQL)
        await conn.execute(
            "INSERT INTO research_runs (id, query, status, created_at) VALUES (?, ?, ?, ?)",
            ("run-1", "battery", "completed", "2026-01-01T00:00:00+00:00"),
        )
        await conn.commit()

    await save_trace_frames(db, "run-1", FRAMES)
    loaded = await load_trace_frames(db, "run-1")

    assert [f["type"] for f in loaded] == ["progress", "search_query", "final_report"]
    # The search frame must survive intact — it is what the trace renders.
    assert loaded[1]["query"] == "battery storage"
    assert loaded[1]["results"][0]["url"] == "https://iea.org/ger"


@pytest.mark.asyncio
async def test_no_frames_is_not_an_error(tmp_path):
    db = str(tmp_path / "empty.db")
    await save_trace_frames(db, "run-x", [])
    assert await load_trace_frames(db, "run-x") == []


@pytest.mark.asyncio
async def test_a_corrupt_row_does_not_break_the_whole_replay(tmp_path):
    db = str(tmp_path / "corrupt.db")
    import aiosqlite

    from app.db.sqlite import CREATE_TABLE_SQL

    async with aiosqlite.connect(db) as conn:
        await conn.executescript(CREATE_TABLE_SQL)
        await conn.execute(
            "INSERT INTO research_runs (id, query, status, created_at) VALUES (?, ?, ?, ?)",
            ("run-2", "battery", "completed", "2026-01-01T00:00:00+00:00"),
        )
        await conn.execute(
            "INSERT INTO run_trace_frames (run_id, seq, frame) VALUES (?, ?, ?)",
            ("run-2", 0, "{not json"),
        )
        await conn.commit()

    await save_trace_frames(db, "run-2", FRAMES)
    loaded = await load_trace_frames(db, "run-2")
    # The unreadable row is skipped; the good frames still replay.
    assert [f["type"] for f in loaded] == ["progress", "search_query", "final_report"]


@pytest.mark.asyncio
async def test_frames_are_stored_as_json_not_python_repr(tmp_path):
    db = str(tmp_path / "json.db")
    import aiosqlite

    from app.db.sqlite import CREATE_TABLE_SQL

    async with aiosqlite.connect(db) as conn:
        await conn.executescript(CREATE_TABLE_SQL)
        await conn.execute(
            "INSERT INTO research_runs (id, query, status, created_at) VALUES (?, ?, ?, ?)",
            ("run-3", "battery", "completed", "2026-01-01T00:00:00+00:00"),
        )
        await conn.execute(
            "INSERT INTO run_trace_frames (run_id, seq, frame) VALUES (?, ?, ?)",
            ("run-3", 0, json.dumps({"type": "progress", "message": "hi"})),
        )
        await conn.commit()

    loaded = await load_trace_frames(db, "run-3")
    assert loaded == [{"type": "progress", "message": "hi"}]