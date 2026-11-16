"""Scene lengths, shared by the estimate of `uo video check` and by the build.

A scene with narration lasts its speech plus a short lead and tail; `seconds` and the length of its clips are
floors. A scene without narration lasts `seconds`, or `SILENT_SECONDS`, or its longest clip. Lengths are rounded
to whole frames here, so the renderer (which rounds each scene to the nearest frame) gets the same frames.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

FPS = 30
LEAD = 0.4  # silence before the narration starts (written into the scene's WAV)
TAIL = 1.0  # silence after it, before the next scene
SILENT_SECONDS = 4.0
PARAGRAPH_PAUSE = 0.7  # the voice's pause between paragraphs (contract C4)


def to_frames(seconds: float, fps: int = FPS) -> int:
    return math.floor(seconds * fps + 0.5)


def on_frames(seconds: float, fps: int = FPS) -> float:
    return round(to_frames(seconds, fps) / fps, 3)


def spoken_estimate(words: int, paragraphs: int, words_per_minute: float, speed: float = 1.0) -> float:
    if not words:
        return 0.0
    return words / (words_per_minute * speed) * 60 + PARAGRAPH_PAUSE * max(paragraphs - 1, 0)


def scene_seconds(speech: float | None, floor: float | None, clips: Iterable[float] = ()) -> float:
    base = speech + LEAD + TAIL if speech else (floor or SILENT_SECONDS)
    return on_frames(max([base, floor or 0.0, *clips]))


def clock(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"
