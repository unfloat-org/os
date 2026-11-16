"""Storyboard rules as `uo check` and `uo video check` report them: one line naming the file, the scene and the rule."""

from __future__ import annotations

import pytest
from conftest import rid
from fixtures.video.helpers import CHOIR, GARDEN_BOARD, garden, video_draft, video_ws, write_series

from unfloat_os import check, cli
from unfloat_os.channels.video import storyboard


@pytest.fixture
def vws(ws):
    return video_ws(ws)


def errors(ws) -> list[str]:
    report, _ = check.check_records(ws)
    return [f"{i.where}: {i.message}" for i in report.errors]


def broken(vws, old: str, new: str) -> list[str]:
    assert old in GARDEN_BOARD
    garden(vws, body=GARDEN_BOARD.replace(old, new, 1))
    return errors(vws)


def where() -> str:
    return f"records/drafts/{rid('DR', 1)}-update-3.md"


def test_a_good_storyboard_passes_uo_check_and_uo_video_check(vws, monkeypatch, capsys):
    garden(vws)
    assert errors(vws) == []
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(vws.root))
    assert cli.main(["video", "check", rid("DR", 1)]) == 0
    out = capsys.readouterr().out
    assert f"ok: {rid('DR', 1)}: 6 scenes, estimated" in out
    assert "output class private" in out


def test_the_scene_id_is_the_slug_of_the_heading_and_notes_are_never_read():
    board = storyboard.parse(GARDEN_BOARD)
    assert [s.id for s in board.scenes] == [
        "title", "bottom-line", "goal-roadmap", "results-harvest", "results-count", "next-asks",
    ]  # fmt: skip
    assert board.scene("results-count").narration == ""
    assert board.scene("bottom-line").narration == "Beds with mulch gave more tomatoes. I need one decision from you."
    assert "99 bottles" in board.notes
    assert board.asks() == ["Mulch for every bed next spring", "Two more volunteers on Saturdays"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("## goal: roadmap", "## intro: roadmap", "scene intro-roadmap: chapter: unknown chapter 'intro'"),
        ("## results: count", "## goal: count", "scene goal-count: chapter order: 'goal' comes after 'results'"),
        ('kind = "number"', 'kind = "chart"', "scene results-count: kind: unknown kind 'chart'"),
        ('kind = "number"\n', "", "scene results-count: kind: missing 'kind'"),
        ('value = "24"\n', "", "scene results-count: property: kind 'number' needs 'value'"),
        ("seconds = 6", 'seconds = 6\ncolour = "red"', "scene results-count: property: unknown property 'colour'"),
        ("seconds = 6", "seconds = -1", "scene results-count: property: 'seconds' must be a positive number"),
        (
            'points = ["Decision needed: mulch every bed next year?"]',
            'points = ["a", "b", "c", "d"]',
            "scene bottom-line: property: 'points' has 4 entries; at most 3",
        ),
        (
            'headline = "Mulch gave more tomatoes"',
            f'headline = "{"x" * 121}"',
            "scene bottom-line: property: 'headline' has 121 characters; at most 120",
        ),
        (
            'rows = ["Mulch for every bed next spring", "Two more volunteers on Saturdays"]',
            'rows = ["a", "b", "c", "d", "e"]',
            "scene next-asks: property: 'rows' must be a list of 1 to 4 rows",
        ),
        ('style = "numbered"', 'style = "pairs"', "scene next-asks: property: row 1 of a 'pairs' list must be a table"),
        ('style = "numbered"', 'style = "table"', "scene next-asks: property: 'style' must be one of bullets"),
        (
            "highlight = [[0.55, 0.10, 0.35, 0.80]]",
            "highlight = [[0.55, 0.10, 0.65, 0.80]]",
            "scene results-harvest: property: 'items' item 1: 'highlight' box 1: must be a box [x, y, w, h] inside",
        ),
        (
            "highlight = [[0.55, 0.10, 0.35, 0.80]]",
            "highlight = [" + ", ".join(["[0.1, 0.1, 0.1, 0.1]"] * 9) + "]",
            "scene results-harvest: property: 'items' item 1: 'highlight' has 9 boxes; at most 8",
        ),
        ('current = "harvest"\n', "", "scene goal-roadmap: property: kind 'roadmap' needs 'current'"),
        ('done = ["plant"]\n', "", "scene goal-roadmap: property: kind 'roadmap' needs 'done'"),
        (
            'current = "harvest"',
            'current = "compost"',
            "scene goal-roadmap: roadmap: 'current' 'compost' is not a roadmap step of the series",
        ),
        ("I need one decision from you.", "I need one decision — from you.", "scene bottom-line: marks: forbidden '—'"),
        (
            'headline = "What I need from you"',
            'headline = "What I need [CONFIRM]"',
            "scene next-asks: marks: forbidden '[CONFIRM'",
        ),
        (
            "Planting is done.",
            "Planting is **done**.",
            "scene goal-roadmap: narration: narration contains markup character(s) *",
        ),
        (
            "Planting is done.",
            "Planting is done, see https://example.org.",
            "scene goal-roadmap: narration: narration contains a URL",
        ),
        (
            "Planting is done.",
            "- Planting is done.",
            "scene goal-roadmap: narration: narration is spoken text, not markup",
        ),
        (
            "## results: count\n\n```toml",
            "## results: count\n\nSome words first.\n\n```toml",
            "scene results-count: properties: a ```toml block must follow",
        ),
        (
            "## results: count",
            "## results: harvest",
            "scene results-harvest: scene id: 'results-harvest' is used by an earlier scene",
        ),
        (
            "private:updates/assets/garden/u3/harvest.png",
            "private:updates/elsewhere/harvest.png",
            "scene results-harvest: media: item 1: 'private:updates/elsewhere/harvest.png' is outside the allow-list",
        ),
        (
            "private:updates/assets/garden/u3/harvest.png",
            "research:figures/harvest.png",
            "scene results-harvest: media: item 1: 'research:figures/harvest.png' must be in a data folder",
        ),
        (
            "private:updates/assets/garden/u3/harvest.png",
            "private:updates/assets/garden/../../secret.png",
            "scene results-harvest: media: item 1: reference 'private:updates/assets/garden/../../secret.png'",
        ),
    ],
)
def test_a_broken_storyboard_is_refused_with_file_scene_and_rule(vws, old, new, message):
    found = broken(vws, old, new)
    assert any(line.startswith(f"{where()}: {message}") for line in found), found


def test_a_missing_required_chapter_is_named_with_the_series_that_requires_it(vws):
    start = GARDEN_BOARD.index("## next: asks")
    found = broken(vws, GARDEN_BOARD[start : GARDEN_BOARD.index("## notes")], "")
    assert found == [f"{where()}: required chapter: no 'next' scene (series 'garden' requires it)"]


def test_another_series_requires_other_chapters(vws):
    """A second invented series with its own required chapters and allow-list (AC7)."""
    write_series(vws, "choir", CHOIR)
    body = (
        '## title\n\n```toml\nkind = "title"\ntitle = "Choir"\n```\n\n'
        '## end\n\n```toml\nkind = "statement"\nheadline = "See you"\n```\n'
    )
    video_draft(vws, 2, "choir", 1, body)
    assert errors(vws) == [
        f"records/drafts/{rid('DR', 2)}-update-1.md: required chapter: no 'results' scene (series 'choir' requires it)"
    ]


def test_video_fields_are_required_for_video_drafts_and_refused_on_others(vws):
    garden(vws)
    path = garden(vws, draft=2, number=4)
    path.write_text(path.read_text().replace("series: garden\n", ""))
    from conftest import DRAFT, write_record

    write_record(vws.ops, "drafts", rid("DR", 3), "email", {**DRAFT, "channel": "document", "number": "2"})
    found = errors(vws)
    assert f"records/drafts/{rid('DR', 2)}-update-4.md: channel 'video' needs 'series'" in found
    assert f"records/drafts/{rid('DR', 3)}-email.md: 'number' only for channel 'video'" in found


def test_an_update_number_is_unique_in_its_series_unless_the_other_draft_is_dropped(vws):
    garden(vws)
    video_draft(vws, 2, "garden", 3, GARDEN_BOARD)
    assert (
        f"records/drafts/{rid('DR', 2)}-update-3.md: number 3 of series 'garden' is already used by {rid('DR', 1)}"
        in errors(vws)
    )
    video_draft(vws, 2, "garden", 3, GARDEN_BOARD, status="dropped")
    assert errors(vws) == []


def test_a_draft_naming_a_series_without_a_profile_is_an_error(vws):
    video_draft(vws, 1, "orchard", 1, GARDEN_BOARD)
    assert f"{rid('DR', 1)}-update-1.md: series 'orchard' has no profile config/video/orchard.toml" in "\n".join(
        errors(vws)
    )


def test_sent_and_dropped_video_drafts_are_not_checked_for_shape(vws):
    garden(vws, body="## intro\n\nwhatever\n", status="sent")
    assert errors(vws) == []
