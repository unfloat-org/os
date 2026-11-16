"""The engine joined to the real renderer (contract C3), on invented series and a stand-in voice.

These tests run only where the renderer is installed (`uo video setup`): the first needs its packages, the second
also its browser and ffmpeg, and renders a whole video (about a minute on a laptop). CI installs no renderer, so
they are skipped there; the renderer's own checks run in the CI job `renderer`. Set UO_SKIP_RENDER=1 to skip them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import rid
from fixtures.video.helpers import BAKERY_CLIP, StubRenderer, ToneVoice, bakery, garden, video_ws

from unfloat_os import cli
from unfloat_os.channels.video import build, checks, timing

RENDERER = build.renderer_dir()
SKIP = os.environ.get("UO_SKIP_RENDER") == "1"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAS_PACKAGES = not SKIP and shutil.which("node") is not None and (RENDERER / "node_modules").is_dir()
HAS_BROWSER = HAS_PACKAGES and (RENDERER / "node_modules/.remotion/chrome-headless-shell").is_dir()

needs_packages = pytest.mark.skipif(not (HAS_PACKAGES and HAS_FFMPEG), reason="needs `uo video setup` and ffmpeg")
needs_browser = pytest.mark.skipif(not (HAS_BROWSER and HAS_FFMPEG), reason="needs `uo video setup` and ffmpeg")

VALIDATE = """
import { readFileSync } from "node:fs";
import { checkFiles, parseManifest } from "./src/validate.ts";
const build = process.argv[1];
checkFiles(parseManifest(JSON.parse(readFileSync(build + "/manifest.json", "utf8"))), build);
"""


@pytest.fixture
def vws(ws, monkeypatch):
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(ws.root))
    return video_ws(ws)


def renderer_accepts(build_dir: Path) -> subprocess.CompletedProcess:
    """The renderer's own validation (schema and files) of a build folder, without a browser."""
    command = ["node", "--input-type=module", "-e", VALIDATE, str(build_dir)]
    return subprocess.run(command, cwd=RENDERER, capture_output=True, text=True)


@needs_packages
def test_the_renderer_accepts_every_manifest_the_engine_writes(vws):
    garden(vws)
    bakery(vws, draft=2)
    for n in (1, 2):
        plan = checks.prepare(vws, rid("DR", n))
        assert plan.report.errors == []
        result = build.build(vws, plan, voice=ToneVoice(), render=StubRenderer(), renderer=RENDERER)
        done = renderer_accepts(result.out / "build")
        assert done.returncode == 0, done.stderr


def probe(path: Path) -> dict:
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-count_frames", "-of", "json", str(path)]
    return json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)


def sound_starts(path: Path) -> list[float]:
    """Times at which sound starts after at least 0.3 s of silence (ffmpeg silencedetect)."""
    command = ["ffmpeg", "-v", "info", "-i", str(path), "-af", "silencedetect=n=-50dB:d=0.3", "-f", "null", "-"]
    err = subprocess.run(command, capture_output=True, text=True, check=True).stderr
    return [float(t) for t in re.findall(r"silence_end: ([\d.]+)", err)]


def temp_entries() -> tuple[list[set[str]], list[str]]:
    """The names in the user temp folder and in /tmp, and the two folders."""
    folders = [os.environ.get("TMPDIR", "/tmp"), "/tmp"]
    if shutil.which("getconf"):
        darwin = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True).stdout.strip()
        folders[0] = darwin or folders[0]
    return [set(os.listdir(f)) if os.path.isdir(f) else set() for f in folders], folders


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@needs_browser
def test_a_storyboard_becomes_a_video_with_the_real_renderer(vws, monkeypatch, capsys):
    draft = bakery(vws)
    voice = ToneVoice()
    monkeypatch.setattr(build, "voice_package", lambda: voice)
    clip = vws.data_dir("restricted") / "bakery/oven/cam-0412.mp4"
    before, folders = temp_entries()

    assert cli.main(["video", "build", rid("DR", 1)]) == 0
    out = vws.data_dir("restricted") / "updates" / rid("DR", 1)
    assert f"restricted by: scene results-rise, item 2 ({BAKERY_CLIP})" in capsys.readouterr().out
    manifest = json.loads((out / "build/manifest.json").read_text())
    scenes = manifest["scenes"]
    assert {s["kind"] for s in scenes} == {"title", "statement", "media", "number", "list", "roadmap"}

    # C6 outputs; the video: 1920x1080 H.264 yuv420p at 30 fps with AAC, as long as its scenes
    for name in ("video.mp4", "captions.srt", "transcript.md", "poster.png", "run_meta.json"):
        assert (out / name).is_file(), name
    assert sorted(p.stem for p in (out / "stills").iterdir()) == sorted(s["id"] for s in scenes)
    info = probe(out / "video.mp4")
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["codec_name"], video["pix_fmt"], video["width"], video["height"]) == ("h264", "yuv420p", 1920, 1080)
    assert video["r_frame_rate"] == "30/1" and audio["codec_name"] == "aac"
    frames = sum(round(s["seconds"] * timing.FPS) for s in scenes)
    assert int(video["nb_read_frames"]) == frames
    assert abs(float(info["format"]["duration"]) - sum(s["seconds"] for s in scenes)) <= 1 / timing.FPS + 0.01

    # D17: each narrated scene's sound starts one lead after the scene's first frame
    starts, t = [], 0.0
    for s in scenes:
        if s["audio"]:
            starts.append(t + timing.LEAD)
        t += s["seconds"]
    heard = sound_starts(out / "video.mp4")
    assert heard[0] == pytest.approx(timing.LEAD, abs=0.05)
    for expected in starts[1:]:
        assert any(abs(h - expected) <= 0.05 for h in heard), (expected, heard)

    # captions.srt has one block per caption of the manifest, in order, with the scene offset
    srt = (out / "captions.srt").read_text().strip().split("\n\n")
    offsets = zip(scenes, _starts(scenes), strict=True)
    captions = [(c["text"], t0 + c["start"]) for s, t0 in offsets for c in s["captions"]]
    assert [block.split("\n")[2] for block in srt] == [text for text, _ in captions]
    first = srt[1].split("\n")[1].split(" --> ")[0]
    assert first == build.srt_time(captions[1][1])

    # AC5: the restricted clip and the bundle stay inside the restricted folder
    after, _ = temp_entries()
    restricted = vws.data_dir("restricted")
    copies = [p for p in vws.root.rglob("*") if p.is_file() and p.stat().st_size == clip.stat().st_size]
    assert all(restricted in p.parents for p in copies if sha(p) == sha(clip))
    for folder, old, new in zip(folders, before, after, strict=True):
        for entry in new - old:
            for p in Path(folder, entry).rglob("*") if Path(folder, entry).is_dir() else [Path(folder, entry)]:
                assert not (p.is_file() and p.stat().st_size == clip.stat().st_size and sha(p) == sha(clip)), p
                assert p.name != "bundle", p
    assert [p for p in (out / "build/tmp").rglob("*") if p.is_file()] == []
    # the source file name reaches no output: not a file name, not the transcript, captions or manifest
    assert not [p for p in out.rglob("*cam-0412*")]
    for name in ("transcript.md", "captions.srt", "build/manifest.json"):
        assert "cam-0412" not in (out / name).read_text(), name

    # a second build reuses the voice cache; --scene and --quality draft render one scene at half size
    synthesized = voice.synthesized
    assert cli.main(["video", "build", rid("DR", 1), "--scene", "results-rise", "--quality", "draft"]) == 0
    assert voice.synthesized == synthesized
    one = probe(out / "scene-results-rise.mp4")
    small = next(s for s in one["streams"] if s["codec_type"] == "video")
    assert (small["width"], small["height"]) == (960, 540)
    rise = next(s for s in scenes if s["id"] == "results-rise")
    assert int(small["nb_read_frames"]) == round(rise["seconds"] * timing.FPS)
    assert json.loads((out / "run_meta.json").read_text())["outputs"][0] == "video.mp4"
    assert json.loads((out / "scene-results-rise.run_meta.json").read_text())["quality"] == "draft"
    assert draft.is_file()


def _starts(scenes: list[dict]) -> list[float]:
    out, t = [], 0.0
    for s in scenes:
        out.append(t)
        t = round(t + s["seconds"], 3)
    return out
