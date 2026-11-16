"""`uo video new` and `uo video list`, and the tracker's video section."""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import TASK, rid, write_record
from fixtures.video.helpers import CHOIR, GARDEN, GARDEN_BOARD, garden, video_draft, video_ws, write_series

from unfloat_os import check, cli, frontmatter, render
from unfloat_os.channels.video import storyboard
from unfloat_os.records import Store


@pytest.fixture
def vws(ws, monkeypatch):
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(ws.root))
    return video_ws(ws)


def draft_file(ws, n: int):
    return next((ws.ops / "records/drafts").glob(f"{rid('DR', n)}-*.md"))


def test_new_creates_the_first_update_with_the_skeleton_of_the_series(vws, capsys):
    write_record(vws.ops, "tasks", "T-001", "garden", TASK)
    write_series(vws, "garden", GARDEN.replace('task = ""', 'task = "T-001"'))
    assert cli.main(["video", "new", "garden", "Spring planting"]) == 0
    assert capsys.readouterr().out.startswith(f"{rid('DR', 1)} ")
    text = draft_file(vws, 1).read_text()
    meta = frontmatter.parse(text)
    assert {k: meta[k] for k in ("status", "channel", "series", "number", "task")} == {
        "status": "draft", "channel": "video", "series": "garden", "number": "1", "task": "T-001",
    }  # fmt: skip
    board = storyboard.parse(frontmatter.body(text))
    assert [s.id for s in board.scenes] == ["title", "bottom-line", "goal-roadmap", "results", "next"]
    assert board.scene("title").props == {
        "kind": "title",
        "title": "Community garden trial",
        "subtitle": "Spring planting",
    }
    assert board.scene("goal-roadmap").props["current"] == "plant"
    assert "The first update of this series." in board.notes
    # The skeleton passes `uo check`: placeholders are warnings there, errors in `uo video check`.
    report, _ = check.check_records(vws)
    assert report.errors == [] and len(report.warnings) == 3


def test_the_next_update_takes_the_next_number_and_carries_the_asks_forward(vws):
    garden(vws, number=3)
    video_draft(vws, 2, "garden", 4, GARDEN_BOARD, status="dropped")
    assert cli.main(["video", "new", "garden", "Autumn"]) == 0
    text = draft_file(vws, 3).read_text()
    assert frontmatter.parse(text)["number"] == "4"  # the dropped update 4 does not hold its number
    board = storyboard.parse(frontmatter.body(text))
    assert board.scene("goal-roadmap").props == {
        "kind": "roadmap", "headline": "Where we are", "current": "harvest", "done": ["plant"],
    }  # fmt: skip
    assert f"Asks of update 3 ({rid('DR', 1)}), to follow up in this one:" in board.notes
    assert "- Mulch for every bed next spring\n- Two more volunteers on Saturdays" in board.notes


def test_new_refuses_a_series_without_a_profile(vws, capsys):
    assert cli.main(["video", "new", "orchard", "First"]) == 1
    assert capsys.readouterr().err == "error: series 'orchard' has no profile config/video/orchard.toml\n"
    assert list((vws.ops / "records/drafts").iterdir()) == []


def test_list_prints_each_series_with_its_updates(vws, capsys):
    garden(vws, number=3)
    write_series(vws, "choir", CHOIR)
    assert cli.main(["video", "list"]) == 0
    assert capsys.readouterr().out == (
        "choir  Choir rehearsal notes\n"
        "  no updates yet\n"
        "garden  Community garden trial\n"
        f"    3  {rid('DR', 1)}  draft     2026-11-17  Update 3\n"
    )


def test_the_tracker_lists_the_updates_of_each_series_with_bottom_line_and_asks(vws):
    garden(vws, number=3)
    write_series(vws, "choir", CHOIR)
    text = render.render(vws, dt.date(2026, 11, 18))
    section = text[text.index("## Video: Community garden trial") :]
    draft = f"[{rid('DR', 1)}](records/drafts/{rid('DR', 1)}-update-3.md)"
    asks = "Mulch for every bed next spring; Two more volunteers on Saturdays"
    assert section.splitlines()[2:5] == [
        "| Update | Draft | Status | Created | Bottom line | Asks |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| 3 | {draft} | draft | 2026-11-17 | Mulch gave more tomatoes | {asks} |",
    ]
    assert "## Video: Choir rehearsal notes\n\nNone." in text


def test_the_tracker_has_no_video_section_without_series(ws):
    text = render.render_records(Store(ws.ops).load(), dt.date(2026, 11, 18))
    assert "## Video" not in text
