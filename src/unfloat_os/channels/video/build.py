"""`uo video build`: a checked draft turned into a video and its companions (contracts C3, C5, C6).

The engine writes a build folder the renderer reads (`manifest.json`, `audio/<scene>.wav`, `media/<scene>-<n>.<ext>`),
calls the renderer for the video, the stills and the poster, and writes `captions.srt`, `transcript.md` and
`run_meta.json`. Everything lands in `<class>:updates/<draft id>/`, where the class is `restricted` when any media
is restricted and not released by the series. The renderer runs with `TMPDIR=<build>/tmp`, so its bundle and temp
files stay inside that folder too.

Each scene's WAV starts with `timing.LEAD` seconds of silence, so the renderer plays the audio from the first frame
of the scene and the caption times (seconds from the start of the scene) already include the lead.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from unfloat_os import provenance, schema
from unfloat_os.channels import ChannelError
from unfloat_os.channels.video import timing
from unfloat_os.channels.video.checks import MediaUse, Plan
from unfloat_os.channels.video.storyboard import CHAPTER_LABELS, Scene, screen_texts
from unfloat_os.channels.video.voice.audio import RATE as SAMPLE_RATE  # the voice's WAV format (C4)
from unfloat_os.workspace import DATA_CLASSES, Workspace, WorkspaceError

MANIFEST_VERSION = 1
WIDTH, HEIGHT = 1920, 1080
ENGINE_ONLY = ("kind", "sources", "numbers_ok", "seconds")
RENDERER_ENV = "UNFLOAT_RENDERER"
RENDERER_ENTRY = Path("bin") / "render.mjs"

Renderer = Callable[[Path, Path, Sequence[str]], None]


# the renderer -------------------------------------------------------------------


def renderer_dir() -> Path:
    """`$UNFLOAT_RENDERER`, else `renderer/` at the top of the source tree this module belongs to."""
    if os.environ.get(RENDERER_ENV):
        return Path(os.environ[RENDERER_ENV]).expanduser().resolve()
    return Path(__file__).resolve().parents[4] / "renderer"


def require_renderer(renderer: Path) -> None:
    """Refuse when the renderer or its packages are missing, naming `uo video setup`."""
    if not (renderer / "package.json").is_file() or not (renderer / RENDERER_ENTRY).is_file():
        raise ChannelError(f"no renderer at {renderer} (package.json and {RENDERER_ENTRY} missing); "
                           f"set {RENDERER_ENV} or install it, then run `uo video setup`")  # fmt: skip
    if not (renderer / "node_modules").is_dir():
        raise ChannelError(f"the renderer at {renderer} has no node_modules; run `uo video setup`")
    if shutil.which("node") is None:
        raise ChannelError("node not found; install Node.js to render videos")


def run_renderer(renderer: Path, build: Path, args: Sequence[str]) -> None:
    """`node bin/render.mjs --build <build> ...` in the renderer folder, with its temp files inside the build."""
    tmp = build / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not (k.startswith("REMOTION") and "LICENSE" in k)}
    env.update(TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp))
    command = ["node", str(RENDERER_ENTRY), "--build", str(build), *args]
    result = subprocess.run(command, cwd=renderer, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        reason = next((line for line in lines if line.startswith("error:")), lines[-1] if lines else "")
        raise ChannelError(f"renderer failed ({' '.join(args[:2])}): {reason.removeprefix('error:').strip()}")


def renderer_versions(renderer: Path) -> dict[str, str]:
    """Declared and installed versions of the renderer's packages, for `run_meta.json`."""
    try:
        declared = json.loads((renderer / "package.json").read_text(encoding="utf-8")).get("dependencies", {})
    except (OSError, ValueError):
        return {}
    versions = {}
    for name, wanted in sorted(declared.items()):
        try:
            installed = json.loads((renderer / "node_modules" / name / "package.json").read_text(encoding="utf-8"))
            versions[name] = installed.get("version", wanted)
        except (OSError, ValueError):
            versions[name] = wanted
    return versions


# voice --------------------------------------------------------------------------


def voice_package():
    """The voice package (C4); a function so tests can put a stand-in in its place."""
    from unfloat_os.channels.video import voice

    return voice


def settings_for(voice, plan: Plan, provider: str | None, voice_name: str | None):
    s = plan.series
    return voice.VoiceSettings(
        provider=provider or s.provider,
        voice=s.voice if voice_name is None else voice_name,
        speed=s.speed,
        lexicon=dict(s.lexicon),
        options=dict(s.options),
    )


def pad_wav(source: Path, target: Path, lead: float) -> float:
    """Copy a 48 kHz mono 16-bit WAV with `lead` seconds of silence in front; returns the new length."""
    with wave.open(str(source), "rb") as reader:
        params = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate())
        if params != (1, 2, SAMPLE_RATE):
            raise ChannelError(f"voice wrote {source.name} as {params}; expected mono 16-bit {SAMPLE_RATE} Hz")
        frames = reader.readframes(reader.getnframes())
    silence = b"\x00\x00" * round(lead * SAMPLE_RATE)
    with wave.open(str(target), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(silence + frames)
    return (len(silence) + len(frames)) / 2 / SAMPLE_RATE


# manifest -----------------------------------------------------------------------


@dataclass(frozen=True)
class Timed:
    scene: Scene
    seconds: float
    start: float  # in the video, on whole frames
    audio: dict | None
    captions: list[dict]


def output_dir(ws: Workspace, plan: Plan) -> Path:
    return ws.data_dir(plan.data_class) / "updates" / plan.record.id


def other_output(ws: Workspace, plan: Plan) -> Path | None:
    """The folder of an earlier build of this draft in the other output class, if there is one: a build whose class
    changed (a release added or withdrawn) leaves it as it was, so the user is told."""
    other = "private" if plan.data_class == "restricted" else "restricted"
    folder = ws.data_dir(other) / "updates" / plan.record.id
    return folder if folder.is_dir() else None


def default_meta(plan: Plan) -> str:
    number = schema.text(plan.record.meta, "number")
    return f"{plan.series.title} · Update {number} · {schema.text(plan.record.meta, 'created')}"


def scene_props(plan: Plan, scene: Scene) -> dict:
    props = {k: v for k, v in scene.props.items() if k not in ENGINE_ONLY}
    if scene.kind == "title" and "meta" not in props:
        props["meta"] = default_meta(plan)
    if scene.kind == "media":
        items = []
        for use, item in zip(plan.media_of(scene.id), props.get("items", []), strict=True):
            entry = {**item, "src": f"media/{use.build_name}", "type": use.info.type}
            entry.update(width=use.info.width, height=use.info.height)
            if use.info.type == "video":
                entry["seconds"] = use.info.seconds
            items.append(entry)
        props["items"] = items
    return props


def manifest(plan: Plan, timed: list[Timed]) -> dict:
    meta = plan.record.meta
    present = []
    for t in timed:
        if t.scene.chapter not in present:
            present.append(t.scene.chapter)
    return {
        "version": MANIFEST_VERSION,
        "draft": plan.record.id,
        "title": f"Update {schema.text(meta, 'number')}",
        "series": {"name": plan.series.name, "title": plan.series.title, "number": int(schema.text(meta, "number"))},
        "date": schema.text(meta, "created"),
        "fps": timing.FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "captions": plan.series.captions,
        "chapters": [{"id": c, "label": CHAPTER_LABELS[c]} for c in present],
        "roadmap": [{"id": s.id, "label": s.label} for s in plan.series.roadmap],
        "scenes": [
            {
                "id": t.scene.id,
                "chapter": t.scene.chapter,
                "kind": t.scene.kind,
                "props": scene_props(plan, t.scene),
                "audio": t.audio,
                "captions": t.captions,
                "seconds": t.seconds,
            }
            for t in timed
        ],
    }


def time_scenes(plan: Plan, speech: Mapping[str, object], audio_dir: Path) -> list[Timed]:
    timed, start = [], 0.0
    for scene in plan.board.scenes:
        spoken = speech.get(scene.id)
        audio, captions, speech_seconds = None, [], None
        if spoken is not None:
            target = audio_dir / f"{scene.id}.wav"
            padded = pad_wav(Path(spoken.path), target, timing.LEAD)
            speech_seconds = float(spoken.seconds)
            audio = {"src": f"audio/{scene.id}.wav", "seconds": round(padded, 3)}
        floor = scene.props.get("seconds") if isinstance(scene.props.get("seconds"), int | float) else None
        clips = [c for use in plan.media_of(scene.id) if (c := use.clip_floor()) is not None]
        seconds = timing.scene_seconds(speech_seconds, floor, clips)
        if spoken is not None:
            for segment in spoken.segments:
                begin = min(round(segment.start + timing.LEAD, 3), seconds)
                end = min(round(segment.end + timing.LEAD, 3), seconds)
                if end > begin and segment.text.strip():
                    captions.append({"text": segment.text, "start": begin, "end": end})
        timed.append(Timed(scene, seconds, start, audio, captions))
        start = round(start + seconds, 3)
    return timed


# companions ---------------------------------------------------------------------


def srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def captions_srt(timed: list[Timed]) -> str:
    blocks = []
    for t in timed:
        for caption in t.captions:
            start, end = t.start + caption["start"], t.start + caption["end"]
            blocks.append(f"{len(blocks) + 1}\n{srt_time(start)} --> {srt_time(end)}\n{caption['text']}\n")
    return "\n".join(blocks)


def travels(ws: Workspace, source: str) -> bool:
    """Whether a source may be named in a file that travels with the video: a data-class reference, or a file of a
    repository that is open or public. Records and private repositories stay in `run_meta.json` (S7)."""
    root = source.partition(":")[0]
    if root in DATA_CLASSES:
        return True
    try:
        return root in ws.members and ws.repo(root).is_open
    except WorkspaceError:
        return False


def transcript(ws: Workspace, plan: Plan, timed: list[Timed]) -> str:
    """The narration by chapter, the on-screen text, the media by scene and the shareable sources. It travels with the
    video, so it names no draft, no record and no media source file; `run_meta.json` keeps the full provenance."""
    meta = plan.record.meta
    total = sum(t.seconds for t in timed)
    out = [
        f"# {plan.series.title}: update {schema.text(meta, 'number')}",
        "",
        f"{schema.text(meta, 'title')}. Created {schema.text(meta, 'created')}, {timing.clock(total)}.",
        "",
    ]
    chapter = None
    for t in timed:
        scene = t.scene
        if scene.chapter != chapter:
            chapter = scene.chapter
            out += [f"## {CHAPTER_LABELS.get(chapter, chapter)}", ""]
        out += [f"### {scene.id} ({timing.clock(t.start)}, {scene.kind})", ""]
        screen = list(screen_texts(scene.props))
        if scene.kind == "title" and "meta" not in scene.props:
            screen.append(default_meta(plan))
        if screen:
            out += ["On screen:", "", *(f"- {text}" for text in screen), ""]
        out += ["Narration:", "", *(f"{p}\n" for p in scene.paragraphs)] if scene.narration else ["No narration.", ""]
        figures = plan.media_of(scene.id)
        if figures:
            out += ["Media:", ""]
            out += [f"- {scene.id}, item {u.n} ({u.info.type}){': ' + u.item['label'] if u.item.get('label') else ''}"
                    for u in figures]  # fmt: skip
            out.append("")
        sources = [s for s in scene.props.get("sources") or [] if isinstance(s, str)]
        shown = [s for s in sources if travels(ws, s)]
        if shown:
            out += ["Sources:", "", *(f"- `{s}`" for s in shown), ""]
        if len(shown) < len(sources):
            out += [f"{len(sources) - len(shown)} internal source(s) left out (listed in run_meta.json).", ""]
    return "\n".join(out).rstrip() + "\n"


# the build ----------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    out: Path
    written: tuple[Path, ...]
    restricted_by: tuple[MediaUse, ...]
    seconds: float
    voice: str = ""  # provider and voice used, for example "kokoro af_heart"
    voice_seconds: float = 0.0  # wall clock of the voice step
    voiced: int = 0  # sentences synthesized by this build (new files in the voice cache)
    other: Path | None = None  # an earlier build of this draft in the other output class, left as it was


def _fresh(folder: Path) -> Path:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    return folder


def build(
    ws: Workspace,
    plan: Plan,
    *,
    quality: str = "final",
    scene: str | None = None,
    provider: str | None = None,
    voice_name: str | None = None,
    voice=None,
    render: Renderer | None = None,
    renderer: Path | None = None,
) -> Result:
    """Build the video of a checked draft. `voice` is the voice package (C4) and `render` runs the renderer;
    tests pass a fake voice and a stub renderer."""
    if plan.report.errors:
        raise ChannelError(f"{plan.record.id} does not pass `uo video check` ({len(plan.report.errors)} error(s))")
    if scene is not None and plan.board.scene(scene) is None:
        raise ChannelError(f"{plan.record.id} has no scene '{scene}'")
    if any(use.info is None for use in plan.media):
        raise ChannelError("ffprobe not found; install ffmpeg (brew install ffmpeg) to build a video with clips")
    renderer = renderer or renderer_dir()
    if render is None:
        require_renderer(renderer)
        render = run_renderer
    voice = voice or voice_package()
    settings = settings_for(voice, plan, provider, voice_name)

    out = output_dir(ws, plan)
    build_dir = out / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    media_dir, audio_dir, voice_dir = (_fresh(build_dir / name) for name in ("media", "audio", "voice"))
    _fresh(build_dir / "tmp")
    for use in plan.media:
        shutil.copyfile(use.path, media_dir / use.build_name)

    cache = ws.data_dir("private") / "updates" / "cache" / "voice"
    cache.mkdir(parents=True, exist_ok=True)
    items = {s.id: s.narration for s in plan.board.scenes if s.narration}
    cached = set(cache.glob("*.wav"))
    started = time.monotonic()
    speech = voice.synthesize(items, settings, voice_dir, cache) if items else {}
    voice_seconds = round(time.monotonic() - started, 2)
    voiced = len(set(cache.glob("*.wav")) - cached)
    timed = time_scenes(plan, speech, audio_dir)
    shutil.rmtree(voice_dir)
    (build_dir / "manifest.json").write_text(
        json.dumps(manifest(plan, timed), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    written: list[Path] = []
    if scene is not None:
        target = out / f"scene-{scene}.mp4"
        render(renderer, build_dir, ["--out", str(target), "--quality", quality, "--scene", scene])
        written.append(target)
    else:
        video, stills, poster = out / "video.mp4", out / "stills", out / "poster.png"
        _fresh(stills)
        # one call: the renderer bundles and starts its browser once for the video, the stills and the poster
        render(renderer, build_dir, ["--out", str(video), "--quality", quality, "--stills", str(stills),
                                     "--poster", str(poster)])  # fmt: skip
        srt, text = out / "captions.srt", out / "transcript.md"
        srt.write_text(captions_srt(timed), encoding="utf-8")
        text.write_text(transcript(ws, plan, timed), encoding="utf-8")
        written += [video, srt, text, poster, *(stills / f"{t.scene.id}.png" for t in timed)]
    inputs = [("ops:" + plan.record.path.relative_to(ws.ops).as_posix(), plan.record.path)]
    inputs.append(("ops:" + plan.series.path.relative_to(ws.ops).as_posix(), plan.series.path))
    inputs += [(str(u.ref), u.path) for u in plan.media] + list(plan.sources)
    meta = provenance.run_meta(
        Path(__file__),
        inputs=[path for _, path in inputs],
        extra={
            "draft": plan.record.id,
            "series": plan.series.name,
            "number": int(schema.text(plan.record.meta, "number")),
            "data_class": plan.data_class,
            "restricted_by": [str(u.ref) for u in plan.restricted_by],
            "quality": quality,
            "scene": scene,
            "seconds": round(sum(t.seconds for t in timed), 3),
            "voice": {
                "provider": settings.provider,
                "voice": settings.voice,
                "speed": settings.speed,
                "synthesized": voiced,
                "seconds": voice_seconds,
            },
            "renderer": renderer_versions(renderer),
            "input_refs": [ref for ref, _ in inputs],
            "outputs": [p.relative_to(out).as_posix() for p in written],
        },
    )
    # a one-scene look gets its own record, so the full video's run_meta.json keeps describing video.mp4
    meta_path = out / ("run_meta.json" if scene is None else f"scene-{scene}.run_meta.json")
    provenance.write(meta_path, meta)
    written.append(meta_path)
    missing = [p for p in written if not p.exists()]
    if missing:
        raise ChannelError(f"the renderer did not write {', '.join(p.relative_to(out).as_posix() for p in missing)}")
    return Result(
        out,
        tuple(written),
        plan.restricted_by,
        sum(t.seconds for t in timed),
        f"{settings.provider} {settings.voice}".strip(),
        voice_seconds,
        voiced,
        other_output(ws, plan),
    )
