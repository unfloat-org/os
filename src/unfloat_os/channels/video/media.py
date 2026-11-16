"""Sizes and lengths of media: PNG and JPG headers read with the standard library, clips through `ffprobe`."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from unfloat_os.channels import ChannelError

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
CLIP_SUFFIXES = (".mp4",)
SUFFIXES = IMAGE_SUFFIXES + CLIP_SUFFIXES
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# JPEG start-of-frame markers carry the size; C4, C8 and CC are other segments.
SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


@dataclass(frozen=True)
class Media:
    type: str  # "image" or "video"
    width: int
    height: int
    seconds: float | None = None


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or not head.startswith(PNG_SIGNATURE) or head[12:16] != b"IHDR":
        raise ChannelError(f"{path.name} is not a PNG file")
    return struct.unpack(">II", head[16:24])


def jpg_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise ChannelError(f"{path.name} is not a JPG file")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in SOF_MARKERS:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise ChannelError(f"{path.name}: no frame size in the JPG header")


def has_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def clip_info(path: Path) -> Media:
    """Width, height and length of a clip; needs `ffprobe` (part of ffmpeg)."""
    if not has_ffprobe():
        raise ChannelError("ffprobe not found; install ffmpeg (brew install ffmpeg)")
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip
    if result.returncode != 0:
        raise ChannelError(f"ffprobe cannot read {path.name}: {result.stderr.strip().splitlines()[-1:]}")
    info = json.loads(result.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise ChannelError(f"{path.name} has no video stream")
    duration = float((info.get("format") or {}).get("duration") or 0)
    return Media("video", int(streams[0]["width"]), int(streams[0]["height"]), round(duration, 3))


def probe(path: Path) -> Media:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return Media("image", *png_size(path))
    if suffix in (".jpg", ".jpeg"):
        return Media("image", *jpg_size(path))
    if suffix in CLIP_SUFFIXES:
        return clip_info(path)
    raise ChannelError(f"{path.name}: media must be PNG, JPG or MP4")
