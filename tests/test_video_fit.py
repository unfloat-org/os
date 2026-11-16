"""Text that cannot fit is refused by `uo video check` and before anything is voiced (M2).

The renderer is the judge (`node bin/render.mjs --fit`; its real answers are tested in `test_video_render.py`).
Without a renderer, documented character limits apply, with a warning.
"""

from __future__ import annotations

import pytest
from conftest import rid
from fixtures.video.helpers import GARDEN_BOARD, FakeVoice, garden, video_ws

from unfloat_os import cli
from unfloat_os.channels.video import build, checks, fit

ROWS = 'rows = ["Mulch for every bed next spring", "Two more volunteers on Saturdays"]'
LONG = "Mulch for every bed in the garden next spring, " * 4  # 188 characters


@pytest.fixture
def vws(ws, monkeypatch, tmp_path):
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(ws.root))
    monkeypatch.setenv("UNFLOAT_RENDERER", str(tmp_path / "no-renderer"))
    return video_ws(ws)


def test_without_a_renderer_long_text_is_refused_by_character_limits_with_a_warning(vws, capsys):
    garden(vws, body=GARDEN_BOARD.replace(ROWS, f'rows = ["{LONG.strip()}", "Two more volunteers"]'))
    assert cli.main(["video", "check", rid("DR", 1)]) == 1
    out, err = capsys.readouterr()
    assert fit.FALLBACK_WARNING in out
    assert "scene next-asks: fit: 'rows[0]' has 187 characters; at most 120 fit" in err


def test_without_a_renderer_a_storyboard_within_the_limits_passes_with_the_warning(vws, capsys):
    garden(vws)
    assert cli.main(["video", "check", rid("DR", 1)]) == 0
    out = capsys.readouterr().out
    assert fit.FALLBACK_WARNING in out and "ok: " in out


def test_the_media_headline_and_the_line_under_a_single_item_have_their_own_limits(vws):
    body = GARDEN_BOARD.replace(
        'headline = "Beds with mulch gave 41% more tomatoes"',
        'headline = "Beds with mulch gave 41% more tomatoes than the beds without mulch in every row"',
    ).replace("]] }]", ']], label = "Harvest by bed, in kilograms, weighed every Saturday morning" }]')
    body = body.replace(
        "```\n\nThe twelve", 'caption = "Twelve beds with mulch and twelve without, side by side"\n```\n\nThe twelve'
    )
    garden(vws, body=body)
    plan = checks.prepare(vws, rid("DR", 1))
    fit.check(vws, plan)
    assert [i.message for i in plan.report.errors] == [
        "scene results-harvest: fit: 'headline' has 79 characters; at most 70 fit "
        "(character limit: the renderer is not installed)",
        "scene results-harvest: fit: 'caption' has 120 characters; at most 110 fit "
        "(character limit: the renderer is not installed)",
    ]


def test_the_renderer_is_asked_with_the_text_only_and_its_answer_names_scene_and_field(vws):
    garden(vws)
    plan = checks.prepare(vws, rid("DR", 1))
    asked = []

    def measure(data):
        asked.append(data)
        return [{"scene": 4, "id": "next-asks", "field": "rows[1]", "message": "does not fit 2 lines at 40 px"}]

    fit.check(vws, plan, measure)
    data = asked[0]
    assert data["version"] == 1 and data["captions"] is True
    assert [s["id"] for s in data["scenes"]] == ["bottom-line", "goal-roadmap", "results-harvest", "results-count",
                                                 "next-asks"]  # fmt: skip
    media = next(s for s in data["scenes"] if s["kind"] == "media")
    assert media["props"] == {"headline": "Beds with mulch gave 41% more tomatoes", "items": [{}]}
    assert [i.message for i in plan.report.errors] == ["scene next-asks: fit: 'rows[1]' does not fit 2 lines at 40 px"]


def test_a_build_refuses_text_that_cannot_fit_before_anything_is_voiced(vws, monkeypatch, capsys):
    garden(vws)
    voice = FakeVoice()
    monkeypatch.setattr(build, "voice_package", lambda: voice)
    answer = [{"id": "bottom-line", "field": "points[0]", "message": "does not fit 2 lines at 40 px: shorten it"}]
    monkeypatch.setattr(fit, "default_measure", lambda ws, plan: lambda data: answer)
    assert cli.main(["video", "build", rid("DR", 1)]) == 1
    err = capsys.readouterr().err
    assert "scene bottom-line: fit: 'points[0]' does not fit 2 lines at 40 px: shorten it" in err
    assert "1 error(s); nothing built" in err
    assert voice.calls == [] and not (vws.data_dir("private") / "updates" / rid("DR", 1)).exists()
