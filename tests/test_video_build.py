"""`uo video build` with a fake voice and a stub renderer: the outputs of C6, scene lengths, captions, data rules."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from conftest import rid
from fixtures.video.helpers import WORD_SECONDS, FakeVoice, StubRenderer, garden, png, video_ws
from test_video_check import SCAN, with_scan

from unfloat_os import cli
from unfloat_os.channels import ChannelError
from unfloat_os.channels.video import build, checks, media, timing


@pytest.fixture
def vws(ws):
    return video_ws(ws)


def run_build(ws, **options):
    plan = checks.prepare(ws, rid("DR", 1))
    voice, renderer = FakeVoice(), StubRenderer()
    result = build.build(ws, plan, voice=voice, render=renderer, renderer=ws.root / "renderer", **options)
    return plan, result, voice, renderer


def test_a_build_writes_every_output_into_the_private_updates_folder(vws):
    garden(vws)
    plan, result, _, renderer = run_build(vws)
    out = vws.data_dir("private") / "updates" / rid("DR", 1)
    assert result.out == out
    names = sorted(p.relative_to(out).as_posix() for p in result.written)
    assert names == sorted(
        ["video.mp4", "captions.srt", "transcript.md", "poster.png", "run_meta.json"]
        + [f"stills/{s.id}.png" for s in plan.board.scenes]
    )
    assert (out / "build/manifest.json").is_file()
    assert sorted(p.name for p in (out / "build/audio").iterdir()) == [
        "bottom-line.wav", "goal-roadmap.wav", "next-asks.wav", "results-harvest.wav", "title.wav",
    ]  # fmt: skip
    assert [p.name for p in (out / "build/media").iterdir()] == ["results-harvest-1.png"]
    assert renderer.calls == [
        ["--out", str(out / "video.mp4"), "--quality", "final", "--stills", str(out / "stills"),
         "--poster", str(out / "poster.png")]
    ]  # fmt: skip


def test_all_narration_is_voiced_in_one_call_with_the_series_voice(vws):
    garden(vws)
    _, _, voice, _ = run_build(vws, provider="say", voice_name="Daniel")
    assert len(voice.calls) == 1
    items, settings = voice.calls[0]
    assert sorted(items) == ["bottom-line", "goal-roadmap", "next-asks", "results-harvest", "title"]
    assert items["goal-roadmap"] == "Planting is done. We are at the harvest."
    assert (settings.provider, settings.voice, dict(settings.lexicon)) == ("say", "Daniel", {"mulch": "mulsh"})


def test_scene_lengths_and_caption_offsets(vws):
    garden(vws)
    plan, result, _, _ = run_build(vws)
    manifest = json.loads((result.out / "build/manifest.json").read_text())
    scenes = {s["id"]: s for s in manifest["scenes"]}
    # Narrated: lead + speech + tail. Silent with `seconds`: that length.
    bottom_words = len(plan.board.scene("bottom-line").narration.split())
    assert scenes["bottom-line"]["seconds"] == timing.on_frames(timing.LEAD + bottom_words * WORD_SECONDS + timing.TAIL)
    assert scenes["results-count"]["seconds"] == 6 and scenes["results-count"]["audio"] is None
    # The WAV carries the lead, so captions start at the lead.
    first = scenes["bottom-line"]["captions"][0]
    assert first == {"text": "Beds with mulch gave more tomatoes.", "start": timing.LEAD, "end": first["end"]}
    assert first["end"] == round(timing.LEAD + 6 * WORD_SECONDS, 3)
    with wave.open(str(result.out / "build/audio/bottom-line.wav")) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (48_000, 1, 2)
        assert abs(wav.getnframes() / 48_000 - scenes["bottom-line"]["audio"]["seconds"]) < 0.001
    assert scenes["bottom-line"]["audio"]["seconds"] == round(timing.LEAD + bottom_words * WORD_SECONDS, 3)
    # captions.srt: each caption offset by the start of its scene in the video.
    start_of_bottom = scenes["title"]["seconds"]
    srt = (result.out / "captions.srt").read_text().split("\n\n")
    title_caption, bottom_caption = srt[0].splitlines(), srt[1].splitlines()
    assert title_caption[1] == f"00:00:00,400 --> {build.srt_time(timing.LEAD + 9 * WORD_SECONDS)}"
    assert bottom_caption[1].startswith(build.srt_time(start_of_bottom + timing.LEAD) + " --> ")
    assert bottom_caption[2] == "Beds with mulch gave more tomatoes."
    assert result.seconds == pytest.approx(sum(s["seconds"] for s in manifest["scenes"]))


def test_the_manifest_follows_c3(vws):
    garden(vws)
    _, result, _, _ = run_build(vws)
    manifest = json.loads((result.out / "build/manifest.json").read_text())
    assert {k: manifest[k] for k in ("version", "draft", "title", "series", "date", "fps", "width", "height")} == {
        "version": 1, "draft": rid("DR", 1), "title": "Update 3",
        "series": {"name": "garden", "title": "Community garden trial", "number": 3},
        "date": "2026-11-17", "fps": 30, "width": 1920, "height": 1080,
    }  # fmt: skip
    assert [(c["id"], c["label"]) for c in manifest["chapters"]] == [
        ("title", "Title"), ("bottom-line", "Bottom line"), ("goal", "Goal"), ("results", "Results"), ("next", "Next"),
    ]  # fmt: skip
    assert [s["id"] for s in manifest["roadmap"]] == ["plant", "harvest", "report"]
    scenes = {s["id"]: s for s in manifest["scenes"]}
    assert scenes["results-harvest"]["props"] == {
        "headline": "Beds with mulch gave 41% more tomatoes",
        "items": [{"src": "media/results-harvest-1.png", "type": "image", "width": 240, "height": 135,
                   "highlight": [[0.55, 0.10, 0.35, 0.80]]}],
    }  # fmt: skip
    assert scenes["title"]["props"]["meta"] == "Community garden trial · Update 3 · 2026-11-17"
    assert "sources" not in scenes["bottom-line"]["props"] and "seconds" not in scenes["results-count"]["props"]


def test_the_transcript_has_narration_by_chapter_screen_text_figures_and_sources(vws):
    garden(vws)
    _, result, _, _ = run_build(vws)
    text = (result.out / "transcript.md").read_text()
    assert "## Results\n\n### results-harvest (" in text
    assert "- Beds with mulch gave 41% more tomatoes" in text
    assert "The twelve beds with mulch gave 41 percent more tomatoes than the twelve without." in text
    assert "- media/results-harvest-1.png (image)" in text
    assert "- `research:results/harvest.md`" in text


def test_run_meta_records_the_voice_the_inputs_and_the_commit(vws):
    garden(vws)
    _, result, _, _ = run_build(vws)
    meta = json.loads((result.out / "run_meta.json").read_text())
    assert meta["voice"] == {"provider": "silent", "voice": "", "speed": 1.0}
    assert meta["input_refs"][:2] == [f"ops:records/drafts/{rid('DR', 1)}-update-3.md", "ops:config/video/garden.toml"]
    assert "private:updates/assets/garden/u3/harvest.png" in meta["input_refs"]
    assert len(meta["input_files"]) == len(meta["input_refs"]) and all(
        len(f["sha256"]) == 64 for f in meta["input_files"]
    )
    assert meta["data_class"] == "private" and meta["commit"]


def test_a_restricted_build_stays_in_the_restricted_folder_and_names_no_source_file(vws, capsys):
    garden(vws, body=with_scan(vws))
    _, result, _, _ = run_build(vws)
    restricted = vws.data_dir("restricted")
    assert result.out == restricted / "updates" / rid("DR", 1)
    assert [str(u.ref) for u in result.restricted_by] == [SCAN]
    assert not (vws.data_dir("private") / "updates" / rid("DR", 1)).exists()
    copies = [
        p
        for p in vws.root.rglob("*.png")
        if p.read_bytes() == (restricted / "garden/plots/plot-07/scan.png").read_bytes()
    ]
    assert all(p.is_relative_to(restricted) for p in copies)
    for path in result.out.rglob("*"):
        assert "scan" not in path.name and "plot-07" not in path.name
    assert "scan.png" not in (result.out / "build/manifest.json").read_text()
    assert "scan.png" not in (result.out / "captions.srt").read_text() + (result.out / "transcript.md").read_text()


def test_one_scene_can_be_built_for_a_quick_look(vws):
    garden(vws)
    _, result, _, renderer = run_build(vws, scene="results-harvest", quality="draft")
    assert [p.name for p in result.written] == ["scene-results-harvest.mp4", "scene-results-harvest.run_meta.json"]
    assert renderer.calls == [
        ["--out", str(result.out / "scene-results-harvest.mp4"), "--quality", "draft", "--scene", "results-harvest"]
    ]
    with pytest.raises(ChannelError, match="has no scene 'compost'"):
        run_build(vws, scene="compost")


def test_a_draft_that_fails_the_check_is_not_built(vws, monkeypatch, capsys):
    garden(vws)
    path = vws.ops / f"records/drafts/{rid('DR', 1)}-update-3.md"
    path.write_text(path.read_text().replace("41 percent", "43 percent"))
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(vws.root))
    assert cli.main(["video", "build", rid("DR", 1)]) == 1
    assert "1 error(s); nothing built" in capsys.readouterr().err
    assert not (vws.data_dir("private") / "updates" / rid("DR", 1)).exists()


def test_without_a_renderer_build_and_setup_name_uo_video_setup(vws, monkeypatch, capsys, tmp_path):
    garden(vws)
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(vws.root))
    monkeypatch.setenv("UNFLOAT_RENDERER", str(tmp_path / "renderer"))
    assert cli.main(["video", "build", rid("DR", 1)]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"error: no renderer at {tmp_path / 'renderer'}") and "uo video setup" in err
    (tmp_path / "renderer/bin").mkdir(parents=True)
    (tmp_path / "renderer/package.json").write_text("{}")
    (tmp_path / "renderer/bin/render.mjs").write_text("")
    assert cli.main(["video", "build", rid("DR", 1)]) == 1
    assert "has no node_modules; run `uo video setup`" in capsys.readouterr().err
    monkeypatch.setenv("UNFLOAT_RENDERER", str(tmp_path / "nothing"))
    assert cli.main(["video", "setup"]) == 1
    assert "error: no renderer at" in capsys.readouterr().err


def test_without_the_voice_package_a_build_says_so(vws, monkeypatch, capsys, tmp_path):
    try:
        from unfloat_os.channels.video import voice  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("the voice package is installed")
    garden(vws)
    renderer = tmp_path / "renderer"
    (renderer / "bin").mkdir(parents=True)
    (renderer / "node_modules").mkdir()
    (renderer / "package.json").write_text("{}")
    (renderer / "bin/render.mjs").write_text("")
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(vws.root))
    monkeypatch.setenv("UNFLOAT_RENDERER", str(renderer))
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    assert cli.main(["video", "build", rid("DR", 1)]) == 1
    assert "error: the voice package unfloat_os.channels.video.voice is not in this checkout" in capsys.readouterr().err


def fake_node(folder: Path, script: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    node = folder / "node"
    node.write_text("#!/bin/sh\n" + script)
    node.chmod(0o755)


def test_the_renderer_runs_in_its_folder_with_temp_files_inside_the_build(tmp_path, monkeypatch):
    renderer, build_dir = tmp_path / "renderer", tmp_path / "out" / "build"
    renderer.mkdir()
    build_dir.mkdir(parents=True)
    fake_node(tmp_path / "bin", f'echo "$PWD|$TMPDIR|$*" > {tmp_path / "seen"}\n')
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("REMOTION_LICENSE_KEY", "should-not-pass")
    build.run_renderer(renderer, build_dir, ["--poster", "p.png"])
    cwd, tmpdir, args = (tmp_path / "seen").read_text().strip().split("|")
    assert Path(cwd).resolve() == renderer.resolve()
    assert Path(tmpdir) == build_dir / "tmp" and (build_dir / "tmp").is_dir()
    assert args == f"bin/render.mjs --build {build_dir} --poster p.png"


def test_a_renderer_failure_is_reported_by_its_error_line(tmp_path, monkeypatch):
    fake_node(
        tmp_path / "bin",
        'echo "bundling" >&2\necho "error: manifest.scenes[2].seconds: must be positive" >&2\nexit 3\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    (tmp_path / "build").mkdir()
    with pytest.raises(
        ChannelError, match=r"renderer failed \(--stills x\): manifest.scenes\[2\].seconds: must be positive"
    ):
        build.run_renderer(tmp_path, tmp_path / "build", ["--stills", "x"])


def test_image_sizes_are_read_from_png_and_jpg_headers(tmp_path):
    assert media.probe(png(tmp_path / "a.png", 2400, 1350)) == media.Media("image", 2400, 1350)
    jpg = tmp_path / "b.jpg"
    sof = b"\xff\xc0\x00\x11\x08" + (1080).to_bytes(2, "big") + (1920).to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    jpg.write_bytes(b"\xff\xd8" + b"\xff\xe0\x00\x10" + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9")
    assert media.probe(jpg) == media.Media("image", 1920, 1080)
    (tmp_path / "c.png").write_bytes(b"not a png at all, really not")
    with pytest.raises(ChannelError, match=r"c\.png is not a PNG file"):
        media.probe(tmp_path / "c.png")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg and ffprobe"
)
def test_clip_size_and_length_come_from_ffprobe_and_are_a_floor_of_the_scene(vws, tmp_path):
    clip = vws.data_dir("private") / "updates/assets/garden/u3/walk.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=12:r=30",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    info = media.probe(clip)
    assert (info.type, info.width, info.height) == ("video", 320, 180) and info.seconds == pytest.approx(12, abs=0.1)
    from fixtures.video.helpers import GARDEN_BOARD

    garden(vws, body=GARDEN_BOARD.replace("u3/harvest.png", "u3/walk.mp4"))
    plan = checks.prepare(vws, rid("DR", 1))
    assert plan.report.errors == []
    assert plan.estimates["results-harvest"] == pytest.approx(12, abs=0.1)
    _, result, _, _ = run_build(vws)
    item = json.loads((result.out / "build/manifest.json").read_text())["scenes"][3]["props"]["items"][0]
    assert (
        item["src"] == "media/results-harvest-1.mp4"
        and item["type"] == "video"
        and item["seconds"] == pytest.approx(12, abs=0.1)
    )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg and ffprobe"
)
def test_a_clip_cut_past_its_own_length_is_refused_before_rendering(vws):
    clip = vws.data_dir("private") / "updates/assets/garden/u3/walk.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    lavfi = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=4:r=30", "-pix_fmt", "yuv420p"]
    subprocess.run([*lavfi, str(clip)], check=True)
    from fixtures.video.helpers import GARDEN_BOARD

    for cut, message in [
        ("end = 9", "item 1: 'end' 9 s is past the end of the clip (4.00 s)"),
        ("start = 5", "item 1: 'start' 5 s is not before the end of the clip (4.00 s)"),
    ]:
        body = GARDEN_BOARD.replace("u3/harvest.png", "u3/walk.mp4").replace(
            "highlight = [[0.55, 0.10, 0.35, 0.80]]", cut
        )
        garden(vws, body=body)
        errors = checks.prepare(vws, rid("DR", 1)).report.errors
        assert [e.message for e in errors] == [f"scene results-harvest: media: {message}"]
