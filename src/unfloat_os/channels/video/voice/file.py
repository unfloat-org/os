"""`file`: a recorded WAV (options["path"]) used as the narration; segments follow the characters.

`path` is a file when the call has one item, or a folder holding `<key>.wav` for each item.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path

from unfloat_os.channels.video.voice import audio
from unfloat_os.channels.video.voice import text as textlib
from unfloat_os.channels.video.voice.model import Segment, Speech, VoiceError, VoiceSettings


def _recording(key: str, settings: VoiceSettings, count: int) -> Path:
    raw = settings.options.get("path")
    if not raw:
        raise VoiceError("file: set options path to a recorded WAV (a folder of <scene id>.wav for several scenes)")
    path = Path(str(raw)).expanduser()
    if path.is_dir():
        path = path / f"{key}.wav"
    elif count > 1:
        raise VoiceError(f"file: {count} narrations need a folder of <scene id>.wav files in options path, got a file")
    if not path.is_file():
        raise VoiceError(f"file: no recording at {path}")
    return path


def proportional_segments(text: str, seconds: float) -> tuple[Segment, ...]:
    """Caption parts of the whole narration, timed by their share of its characters."""
    parts = [part for sentence in textlib.split_text(text) for part in textlib.caption_parts(sentence.text)]
    total = sum(len(part) for part in parts)
    samples = round(seconds * audio.RATE)
    segments, used = [], 0
    for part in parts:
        used_before = used
        used += len(part)
        start = round(samples * used_before / total) / audio.RATE
        end = round(samples * used / total) / audio.RATE
        segments.append(Segment(part, start, end))
    return tuple(segments)


def speech_from_files(items: Mapping[str, str], settings: VoiceSettings, out_dir: Path) -> dict[str, Speech]:
    result: dict[str, Speech] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".convert-") as work:
        for key, text in items.items():
            pcm = audio.load_standard(_recording(key, settings, len(items)), Path(work))
            path = out_dir / f"{key}.wav"
            audio.write_wav(path, pcm)
            seconds = audio.seconds_of(pcm)
            result[key] = Speech(path, seconds, proportional_segments(text, seconds))
    return result
