"""`uo video check`: sources, numbers, media, length, placeholders; series profiles; data classes (AC2, AC5)."""

from __future__ import annotations

import os

import pytest
from conftest import rid
from fixtures.video.helpers import GARDEN, GARDEN_BOARD, garden, png, video_ws, write_series

from unfloat_os import check, cli
from unfloat_os.channels.video import checks, refs, storyboard
from unfloat_os.channels.video.series import SeriesError, load

CITE = "cite one or list it in numbers_ok"


@pytest.fixture
def vws(ws):
    return video_ws(ws)


def video_errors(ws, body: str = GARDEN_BOARD) -> list[str]:
    garden(ws, body=body)
    plan = checks.prepare(ws, rid("DR", 1))
    return [i.message for i in plan.report.errors]


def test_a_numeral_no_cited_source_contains_is_refused(vws):
    found = video_errors(vws, GARDEN_BOARD.replace("gave 41 percent more", "gave 43 percent more"))
    assert found == ["scene results-harvest: number: '43 percent' occurs in none of its sources; " + CITE]


def test_percent_written_three_ways_counts_as_the_same_number(vws):
    for spelling in ("41%", "41 %", "41 percent"):
        assert video_errors(vws, GARDEN_BOARD.replace("41 percent", spelling)) == []


def test_a_scene_without_sources_may_not_state_numbers_unless_exempt(vws):
    body = GARDEN_BOARD.replace("And two more volunteers on Saturdays.", "And 15 more volunteers.")
    assert video_errors(vws, body) == ["scene next-asks: number: '15' has no source: the scene cites none; " + CITE]
    exempt = body.replace("asks = true", 'asks = true\nnumbers_ok = ["15"]')
    assert video_errors(vws, exempt) == []


def test_single_digits_are_not_checked_but_decimals_are(vws):
    assert video_errors(vws, GARDEN_BOARD.replace("Two things.", "2 things.")) == []
    found = video_errors(vws, GARDEN_BOARD.replace("Two things.", "About 0.5 of them."))
    assert found == ["scene next-asks: number: '0.5' has no source: the scene cites none; " + CITE]


def test_a_number_must_match_whole_not_inside_a_longer_one(vws):
    found = video_errors(vws, GARDEN_BOARD.replace('value = "24"', 'value = "202"'))
    assert found == ["scene results-count: number: '202' occurs in none of its sources; " + CITE]


def test_missing_sources_and_media_are_errors(vws):
    body = GARDEN_BOARD.replace(
        'sources = ["research:results/harvest.md"]\n```\n\nBeds', 'sources = ["research:results/gone.md"]\n```\n\nBeds'
    )
    body = body.replace("u3/harvest.png", "u3/missing.png")
    found = video_errors(vws, body)
    assert "scene bottom-line: source: 'research:results/gone.md' does not exist" in found
    assert (
        "scene results-harvest: media: item 1: 'private:updates/assets/garden/u3/missing.png' does not exist" in found
    )


def test_the_length_estimate_must_fall_inside_the_series_bounds(vws):
    found = video_errors(vws, GARDEN_BOARD.replace("seconds = 6", "seconds = 200"))
    assert len(found) == 1 and found[0].startswith("length: estimated 23")
    assert found[0].endswith("s is outside 10 to 120 s (series 'garden')")


def test_placeholders_warn_in_uo_check_and_block_uo_video_check(vws):
    body = GARDEN_BOARD.replace('headline = "Mulch gave more tomatoes"', 'headline = "TODO: the result"')
    assert video_errors(vws, body) == ["scene bottom-line: placeholder: 'TODO' left in the scene"]
    report, _ = check.check_records(vws)
    assert [i.message for i in report.errors] == []
    assert [i.message for i in report.warnings] == ["scene bottom-line: placeholder: 'TODO' left in the scene"]


# data classes (C5) ----------------------------------------------------------------

SCAN = "restricted:garden/plots/plot-07/scan.png"


def with_scan(ws, body: str = GARDEN_BOARD) -> str:
    png(ws.data_dir("restricted") / "garden/plots/plot-07/scan.png", 50, 50)
    return body.replace(
        "highlight = [[0.55, 0.10, 0.35, 0.80]] }]", f'highlight = [[0.55, 0.10, 0.35, 0.80]] }}, {{ src = "{SCAN}" }}]'
    )


def test_restricted_media_make_the_video_restricted_unless_released(vws):
    garden(vws, body=with_scan(vws))
    plan = checks.prepare(vws, rid("DR", 1))
    assert plan.report.errors == []
    assert plan.data_class == "restricted"
    assert [(u.scene, u.n, str(u.ref)) for u in plan.restricted_by] == [("results-harvest", 2, SCAN)]

    released = GARDEN.replace("release = []", f'release = [{{ path = "{SCAN}", reason = "the plot owner agreed" }}]')
    write_series(vws, "garden", released)
    plan = checks.prepare(vws, rid("DR", 1))
    assert plan.data_class == "private" and plan.restricted_by == ()


def test_public_media_still_give_a_private_video(vws):
    png(vws.data_dir("public") / "garden/sky.png")
    body = GARDEN_BOARD.replace("private:updates/assets/garden/u3/harvest.png", "public:garden/sky.png")
    garden(vws, body=body)
    write_series(vws, "garden", GARDEN.replace('allow = ["', 'allow = ["public:garden/**", "'))
    assert checks.prepare(vws, rid("DR", 1)).data_class == "private"


def test_a_reference_never_leaves_its_root(vws, tmp_path):
    with pytest.raises(refs.RefError, match="must use a relative path"):
        refs.parse("private:/etc/passwd")
    with pytest.raises(refs.RefError, match="must not contain"):
        refs.parse("private:a/../../b.png")
    with pytest.raises(refs.RefError, match="unknown root 'garden'"):
        refs.resolve(vws, refs.parse("garden:x.png"))
    outside = png(tmp_path / "outside.png")
    link = vws.data_dir("private") / "updates/assets/garden/u3/link.png"
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, link)
    with pytest.raises(refs.RefError, match="resolves outside private"):
        refs.resolve(vws, refs.parse("private:updates/assets/garden/u3/link.png"))


def test_allow_globs_match_folders_with_double_star_and_names_with_star():
    ref = refs.parse("private:updates/assets/garden/u3/harvest.png")
    assert refs.matches(ref, "private:updates/assets/garden/**")
    assert refs.matches(ref, "private:updates/assets/garden/*/harvest.png")
    assert not refs.matches(ref, "private:updates/assets/*.png")
    assert not refs.matches(ref, "restricted:updates/assets/garden/**")


# series profiles (C2) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("captions = true", 'captions = true\ncolour = "green"', "unknown key(s): colour"),
        ('provider = "silent"', 'provider = "robot"', "'voice.provider' 'robot' is not one of"),
        ("min_seconds = 10", "min_seconds = 500", "'length.min_seconds' must be below 'length.max_seconds'"),
        ("words_per_minute = 150", "words_per_minute = 0", "'length.words_per_minute' must be a positive number"),
        (
            'required = ["bottom-line", "results", "next"]',
            'required = ["summary"]',
            "'chapters.required' has unknown chapter 'summary'",
        ),
        (
            "release = []",
            'release = [{ path = "restricted:garden/**", reason = "x" }]',
            "'data.release.path' 'restricted:garden/**' must name one restricted: file",
        ),
        (
            'allow = ["private',
            'allow = ["garden:x", "private',
            "'data.allow' pattern 'garden:x' must start with public:, private: or restricted:",
        ),
        ('id = "harvest"', 'id = "plant"', "'roadmap.id' 'plant' is listed twice"),
        ('title = "Community garden trial"', 'title = ""', "'title' must be a non-empty one-line string"),
        ("[length]", "[length]\nfps = 25", "unknown key(s) in [length]: fps"),
    ],
)
def test_a_broken_series_profile_names_the_key(vws, old, new, message):
    write_series(vws, "garden", GARDEN.replace(old, new, 1))
    with pytest.raises(SeriesError, match=r"^config/video/garden\.toml: ") as raised:
        load(vws, "garden")
    assert message in str(raised.value)
    report, _ = check.check_records(vws)
    assert [f"{i.where}: {i.message}" for i in report.errors] == [
        f"config/video/garden.toml: {str(raised.value).split(': ', 1)[1]}"
    ]


def test_uo_check_refuses_a_series_whose_task_or_people_do_not_exist_or_whose_title_has_marks(vws):
    text = GARDEN.replace('task = ""', 'task = "T-009"').replace("people = []", f'people = ["{rid("P", 9)}"]')
    write_series(vws, "garden", text.replace('label = "Harvest"', 'label = "Harvest \u2013 autumn"'))
    report, _ = check.check_records(vws)
    assert [i.message for i in report.errors] == [
        "'task' T-009 does not exist",
        f"'people': {rid('P', 9)} does not exist",
        "'roadmap.label' contains '\u2013'",
    ]


def test_screen_text_is_every_string_a_viewer_sees():
    board = storyboard.parse(GARDEN_BOARD)
    assert list(storyboard.screen_texts(board.scene("results-harvest").props)) == [
        "Beds with mulch gave 41% more tomatoes"
    ]
    assert list(storyboard.screen_texts(board.scene("goal-roadmap").props)) == ["Where we are"]


# every check applies whatever the draft's status (H1), and the record's own fields are checked too (M1) -------------


def test_a_sent_draft_gets_every_storyboard_rule_in_uo_video_check(vws, monkeypatch, capsys):
    outside = "private:other/secret.png"
    png(vws.data_dir("private") / "other/secret.png")
    body = GARDEN_BOARD.replace("private:updates/assets/garden/u3/harvest.png", outside)
    body = body.replace('kind = "number"', 'kind = "bogus"')
    garden(vws, body=body, status="sent")
    report, _ = check.check_records(vws)
    assert report.errors == []  # `uo check` leaves a sent draft alone
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(vws.root))
    assert cli.main(["video", "check", rid("DR", 1)]) == 1
    err = capsys.readouterr().err
    assert f"scene results-harvest: media: item 1: '{outside}' is outside the allow-list of series 'garden'" in err
    assert "scene results-count: kind: unknown kind 'bogus'" in err


def test_media_on_a_repository_root_is_one_error_line_not_a_traceback(vws, monkeypatch, capsys):
    body = GARDEN_BOARD.replace("private:updates/assets/garden/u3/harvest.png", "research:results/harvest.png")
    garden(vws, body=body, status="dropped")
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(vws.root))
    assert cli.main(["video", "build", rid("DR", 1)]) == 1
    err = capsys.readouterr().err
    assert "item 1: 'research:results/harvest.png' must be in a data folder" in err
    assert err.strip().endswith("error(s); nothing built") and "Traceback" not in err


def test_uo_video_check_refuses_what_uo_check_refuses_in_the_record_fields(vws):
    path = garden(vws)
    path.write_text(path.read_text().replace("created: 2026-11-17\n", ""))
    plan = checks.prepare(vws, rid("DR", 1))
    assert [i.message for i in plan.report.errors] == ["missing 'created'"]


# number check edges (L1) -------------------------------------------------------------------------------------------


def test_a_leading_decimal_point_and_odd_comma_groups_are_numerals():
    assert checks.numerals("a .25 cm gap") == [(".25", "0.25")]
    assert checks.numerals("3,1500 seeds") == [("1500", "1500")]
    assert checks.numerals("1,500 seeds and 12,000 more") == [("1,500", "1500"), ("12,000", "12000")]
    assert checks.numerals("in 2,3 and 4") == []


def test_an_on_screen_value_with_a_leading_point_is_checked(vws):
    found = video_errors(vws, GARDEN_BOARD.replace('value = "24"', 'value = ".93"'))
    assert found == ["scene results-count: number: '.93' occurs in none of its sources; " + CITE]
