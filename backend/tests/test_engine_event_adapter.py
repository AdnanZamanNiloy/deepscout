"""Engine event adapter: no misleading "sources" counts.

Regression: the adapter mapped the browser stage's initial-research CHARACTER
count (and the researcher stage's SECTION count) onto `search_progress.snippets`,
which the UI renders as "N sources". A run showed "24229 sources" — actually a
24 KB character count — while the real source total was in the tens. The adapter
now surfaces those as honest progress messages and reports the real source
count only in the terminal final_report frame.
"""
from __future__ import annotations

from app.engine.event_adapter import EngineEventAdapter, final_report_frame


def test_browser_stage_emits_progress_not_a_source_count():
    adapter = EngineEventAdapter("q")
    frames = adapter.to_frames({
        "engine_node": "browser",
        "stage": "search",
        "initial_research_chars": 24229,
    })
    # No search_progress frame claiming 24229 sources.
    assert not any(f["type"] == "search_progress" for f in frames)
    progress = [f for f in frames if f["type"] == "progress"]
    assert len(progress) == 1
    assert "characters" in progress[0]["message"]
    assert "24229" in progress[0]["message"].replace(",", "")


def test_researcher_stage_reports_sections_not_sources():
    adapter = EngineEventAdapter("q")
    frames = adapter.to_frames({
        "engine_node": "researcher",
        "stage": "search",
        "research_sections": 5,
    })
    assert not any(f["type"] == "search_progress" for f in frames)
    progress = [f for f in frames if f["type"] == "progress"]
    assert progress and "5 section" in progress[0]["message"]


def test_final_report_frame_carries_the_real_source_count():
    fr = final_report_frame(report="# R", sources=["http://a", "http://b", "http://c"])
    assert fr["type"] == "final_report"
    assert fr["source_count"] == 3


def test_final_report_source_count_is_zero_when_no_sources():
    fr = final_report_frame(report="# R", sources=[])
    assert fr["source_count"] == 0
