"""Invented video series for the tests (a community garden, a choir), a fake voice and a stub renderer.

Nothing here has to do with unfloat's own work (AC7). Draft IDs are built at runtime (`rid`).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import subprocess
import wave
import zlib
from pathlib import Path

from conftest import DRAFT, rid, write_record

from unfloat_os.channels.video.voice import Segment, Speech, VoiceSettings
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


# a second invented series for the end-to-end build with the real renderer -----------

BAKERY = """\
title = "Sourdough starter trial"
audience = "The bakery team."
task = ""
people = []
captions = true

[length]
min_seconds = 10
max_seconds = 300
words_per_minute = 150

[voice]
provider = "silent"

[data]
allow = ["private:updates/assets/bakery/**", "restricted:bakery/oven/**"]
release = []

[[roadmap]]
id = "mix"
label = "Mixing"

[[roadmap]]
id = "bake"
label = "Test bakes"

[[roadmap]]
id = "taste"
label = "Tasting"
"""

BAKERY_IMAGE = "private:updates/assets/bakery/u2/rise.png"
BAKERY_CLIP = "restricted:bakery/oven/cam-0412.mp4"
BAKERY_SOURCE = "Trial 2026: 3 starters, 18 loaves each. Rye rose 35% higher than wheat. 16 of 18 rye loaves passed.\n"

# Every kind; a media scene with an image and a restricted clip; a silent scene.
BAKERY_BOARD = f"""\
## title

```toml
kind = "title"
title = "Sourdough starter trial"
subtitle = "Second update"
```

This is the second update on the sourdough starter trial.

## bottom-line

```toml
kind = "statement"
headline = "The rye starter rose highest"
points = ["Decision needed: switch the morning bake to rye?"]
```

The rye starter gave the best rise. I need one decision from you.

## goal: roadmap

```toml
kind = "roadmap"
headline = "Where we are"
current = "bake"
done = ["mix"]
```

Mixing is done. We are baking the test loaves.

## done

```toml
kind = "list"
headline = "What we did"
style = "bullets"
rows = ["Fed three starters for two weeks", "Baked 18 loaves with each"]
sources = ["research:results/starters.md"]
```

We fed three starters for two weeks. Then we baked 18 loaves with each one.

## results: rise

```toml
kind = "media"
headline = "Rye rose 35% higher than wheat"
caption = "Rise measured after four hours"
sources = ["research:results/starters.md"]
items = [
  {{ src = "{BAKERY_IMAGE}", label = "Rise by starter", highlight = [[0.08, 0.15, 0.26, 0.75]] }},
  {{ src = "{BAKERY_CLIP}", label = "Oven camera", loop = false }},
]
```

On the left, the rise of each starter. The rye starter rose 35 percent higher.

On the right, a rye loaf in the oven.

## results: crumb

```toml
kind = "number"
headline = "Crumb test"
value = "16"
denominator = "of 18"
label = "rye loaves passed the crumb test"
sources = ["research:results/starters.md"]
```

16 of the 18 rye loaves passed the crumb test.

## limits

```toml
kind = "list"
headline = "What we cannot say yet"
style = "pairs"
left_title = "Open"
right_title = "What would settle it"
rows = [{{ left = "Taste", right = "A blind tasting" }}, {{ left = "Shelf life", right = "A week of storage" }}]
```

We have not tested taste or shelf life. A blind tasting and a week of storage would settle both.

## next: asks

```toml
kind = "list"
headline = "What I need from you"
style = "numbered"
asks = true
rows = ["Approve rye for the morning bake", "Book the tasting panel"]
```

Two asks. Approve rye for the morning bake. And book the tasting panel.

## end

```toml
kind = "statement"
headline = "Questions to the bakery team"
seconds = 3
```
"""


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def bakery(ws: Workspace, draft: int = 1, number: int = 2) -> Path:
    """The bakery series, its media made with ffmpeg (a bar chart and a 6 s clip), its source and its draft."""
    write_series(ws, "bakery", BAKERY)
    image = ws.data_dir("private") / "updates/assets/bakery/u2/rise.png"
    clip = ws.data_dir("restricted") / "bakery/oven/cam-0412.mp4"
    for path in (image, clip):
        path.parent.mkdir(parents=True, exist_ok=True)
    bars = ",".join(
        f"drawbox=x={300 + i * 650}:y={1250 - h}:w=420:h={h}:color={colour}:t=fill"
        for i, (h, colour) in enumerate([(950, "0xFF8A4C"), (700, "0x6CC5F0"), (560, "0xFFB454")])
    )
    axis = "drawbox=x=150:y=1250:w=2100:h=6:color=0x8E8598:t=fill"
    ffmpeg("-f", "lavfi", "-i", f"color=c=0x14101A:s=2400x1350,{bars},{axis}", "-frames:v", "1", str(image))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
           str(clip))  # fmt: skip
    source = ws.members["research"].path / "results" / "starters.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(BAKERY_SOURCE, encoding="utf-8")
    return video_draft(ws, draft, "bakery", number, BAKERY_BOARD)


class ToneVoice:
    """A stand-in for the voice package (C4): a quiet 220 Hz tone per sentence (0.25 s a word), 0.35 s between
    sentences and 0.7 s between paragraphs, 48 kHz mono 16-bit, each sentence cached under `cache`."""

    VoiceSettings = VoiceSettings
    WORD = 0.25
    RATE = 48_000

    def __init__(self) -> None:
        self.synthesized = 0

    def _sentence(self, text: str, cache: Path) -> bytes:
        path = cache / f"tone-{hashlib.sha256(text.encode()).hexdigest()[:16]}.pcm"
        if path.exists():
            return path.read_bytes()
        n = round(len(text.split()) * self.WORD * self.RATE)
        wave_at = (round(6000 * math.sin(2 * math.pi * 220 * i / self.RATE)) for i in range(n))
        frames = struct.pack(f"<{n}h", *wave_at)
        path.write_bytes(frames)
        self.synthesized += 1
        return frames

    def synthesize(self, items, settings, out_dir: Path, cache: Path) -> dict[str, Speech]:
        out = {}
        for key, text in items.items():
            if not text:
                continue
            pcm, segments, t = b"", [], 0.0
            for p, paragraph in enumerate(text.split("\n\n")):
                for s, sentence in enumerate(re.split(r"(?<=[.?!])\s+", paragraph)):
                    if p or s:
                        gap = 0.7 if s == 0 else 0.35
                        pcm += b"\x00\x00" * round(gap * self.RATE)
                        t += gap
                    frames = self._sentence(sentence, cache)
                    seconds = len(frames) / 2 / self.RATE
                    segments.append(Segment(sentence, round(t, 3), round(t + seconds, 3)))
                    pcm += frames
                    t += seconds
            path = out_dir / f"{key}.wav"
            with wave.open(str(path), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(self.RATE)
                writer.writeframes(pcm)
            out[key] = Speech(path, round(len(pcm) / 2 / self.RATE, 3), tuple(segments))
        return out
