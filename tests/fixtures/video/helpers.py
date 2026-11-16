"""Invented video series for the tests (a community garden, a choir), a fake voice and a stub renderer.

Nothing here has to do with unfloat's own work (AC7). Draft IDs are built at runtime (`rid`).
"""

from __future__ import annotations

import json
import re
import struct
import wave
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from conftest import DRAFT, rid, write_record

from unfloat_os.workspace import Workspace

GARDEN = """\
title = "Community garden trial"
audience = "The garden committee."
task = ""
people = []
captions = true

[length]
min_seconds = 10
max_seconds = 120
words_per_minute = 150

[chapters]
required = ["bottom-line", "results", "next"]

[voice]
provider = "silent"
voice = ""
speed = 1.0

[voice.lexicon]
"mulch" = "mulsh"

[data]
allow = ["private:updates/assets/garden/**", "restricted:garden/plots/**"]
release = []

[[roadmap]]
id = "plant"
label = "Planting"

[[roadmap]]
id = "harvest"
label = "Harvest"

[[roadmap]]
id = "report"
label = "Report"
"""

CHOIR = """\
title = "Choir rehearsal notes"
[chapters]
required = ["results"]
[voice]
provider = "say"
[data]
allow = ["public:choir/**"]
"""

# A storyboard of the garden series that passes every check.
GARDEN_BOARD = """\
## title

```toml
kind = "title"
title = "Community garden trial"
subtitle = "Third update"
```

This is the third update on the garden trial.

## bottom-line

```toml
kind = "statement"
headline = "Mulch gave more tomatoes"
points = ["Decision needed: mulch every bed next year?"]
sources = ["research:results/harvest.md"]
```

Beds with mulch gave more tomatoes. I need one decision from you.

## goal: roadmap

```toml
kind = "roadmap"
headline = "Where we are"
current = "harvest"
done = ["plant"]
```

Planting is done. We are at the harvest.

## results: harvest

```toml
kind = "media"
headline = "Beds with mulch gave 41% more tomatoes"
items = [{ src = "private:updates/assets/garden/u3/harvest.png", highlight = [[0.55, 0.10, 0.35, 0.80]] }]
sources = ["research:results/harvest.md"]
```

The twelve beds with mulch gave 41 percent more tomatoes than the twelve without.

## results: count

```toml
kind = "number"
value = "24"
label = "beds in the trial"
sources = ["research:results/harvest.md"]
seconds = 6
```

## next: asks

```toml
kind = "list"
headline = "What I need from you"
style = "numbered"
asks = true
rows = ["Mulch for every bed next spring", "Two more volunteers on Saturdays"]
```

Two things. Mulch for every bed next spring. And two more volunteers on Saturdays.

## notes

Not checked: 99 bottles, a URL https://example.org and an em dash — here.
"""

HARVEST = "Harvest 2026: 24 beds, 12 with mulch and 12 without. Beds with mulch gave 41% more tomatoes.\n"


def video_ws(ws: Workspace) -> Workspace:
    """The test workspace with a restricted data folder, as the real one has."""
    manifest = ws.ops / "config" / "workspace.toml"
    manifest.write_text(manifest.read_text() + 'restricted = "data/restricted"\n', encoding="utf-8")
    return Workspace.load(ws.root)


def write_series(ws: Workspace, name: str, text: str) -> Path:
    path = ws.ops / "config" / "video" / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def png(path: Path, width: int = 64, height: int = 36) -> Path:
    """A small valid PNG written with the standard library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x20\x10\x1a" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )
    return path


def garden(ws: Workspace, number: int = 3, body: str = GARDEN_BOARD, status: str = "draft", draft: int = 1) -> Path:
    """The garden series with its assets and source, and one storyboard draft; returns the draft file."""
    write_series(ws, "garden", GARDEN)
    png(ws.data_dir("private") / "updates/assets/garden/u3/harvest.png", 240, 135)
    source = ws.members["research"].path / "results" / "harvest.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(HARVEST, encoding="utf-8")
    return video_draft(ws, draft, "garden", number, body, status)


def video_draft(ws: Workspace, n: int, series: str, number: int, body: str, status: str = "draft") -> Path:
    meta = {**DRAFT, "title": f"Update {number}", "channel": "video", "status": status}
    meta.update(series=series, number=str(number), created="2026-11-17")
    return write_record(ws.ops, "drafts", rid("DR", n), f"update-{number}", meta, body)


# the fake voice (contract C4) -------------------------------------------------------

WORD_SECONDS = 0.3


@dataclass(frozen=True)
class VoiceSettings:
    provider: str
    voice: str = ""
    speed: float = 1.0
    lexicon: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Segment:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Speech:
    path: Path
    seconds: float
    segments: tuple[Segment, ...]


def silence(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(48_000)
        writer.writeframes(b"\x00\x00" * round(seconds * 48_000))


class FakeVoice:
    """Speaks every word in WORD_SECONDS of silence, one caption segment per sentence; records each call."""

    VoiceSettings = VoiceSettings

    def __init__(self) -> None:
        self.calls: list[tuple[dict, VoiceSettings]] = []

    def synthesize(self, items, settings, out_dir: Path, cache: Path) -> dict[str, Speech]:
        self.calls.append((dict(items), settings))
        out = {}
        for key, text in items.items():
            if not text:
                continue
            segments, t = [], 0.0
            for sentence in re.split(r"(?<=[.?!])\s+", text.replace("\n\n", " ")):
                length = len(sentence.split()) * WORD_SECONDS
                segments.append(Segment(sentence, round(t, 3), round(t + length, 3)))
                t += length
            path = out_dir / f"{key}.wav"
            silence(path, t)
            out[key] = Speech(path, round(t, 3), tuple(segments))
        return out


class StubRenderer:
    """Writes the files the renderer would (C3), from the manifest, and records the commands."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, renderer: Path, build: Path, args) -> None:
        args = list(args)
        self.calls.append(args)
        manifest = json.loads((build / "manifest.json").read_text())
        if "--out" in args:
            Path(args[args.index("--out") + 1]).write_bytes(b"stub video")
        if "--stills" in args:
            folder = Path(args[args.index("--stills") + 1])
            for scene in manifest["scenes"]:
                png(folder / f"{scene['id']}.png")
        if "--poster" in args:
            png(Path(args[args.index("--poster") + 1]))
