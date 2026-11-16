"""One audio format for every provider: 48 kHz mono 16-bit PCM, joined and measured with the standard library.

Other formats and rates are converted by `ffmpeg` (or `afconvert` on macOS) as a subprocess.
"""

from __future__ import annotations

import array
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from unfloat_os.channels.video.voice.model import VoiceError

RATE = 48000
WIDTH = 2  # bytes per sample
SILENCE_LEVEL = 100  # |sample| at or below this (about -50 dBFS) is silence at the edges of a clip
EDGE_PAD_S = 0.03  # kept around the speech when a clip is trimmed
FADE_S = 0.005

FFMPEG_HINT = "install ffmpeg (macOS: `brew install ffmpeg`; Debian or Ubuntu: `sudo apt install ffmpeg`)"


def seconds_of(pcm: bytes) -> float:
    return len(pcm) // WIDTH / RATE


def silence(seconds: float) -> bytes:
    return bytes(round(seconds * RATE) * WIDTH)


def _samples(pcm: bytes) -> array.array:
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":  # pragma: no cover
        samples.byteswap()
    return samples


def _bytes(samples: array.array) -> bytes:
    if sys.byteorder == "big":  # pragma: no cover
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


def peak_dbfs(pcm: bytes) -> float:
    """Peak level in dB below full scale; -inf for digital silence."""
    samples = _samples(pcm)
    peak = max((abs(s) for s in samples), default=0)
    return 20 * math.log10(peak / 32768) if peak else float("-inf")


def trim(pcm: bytes) -> bytes:
    """Cut silence from both ends, keep a short pad and fade the edges. All-silent audio is returned as is."""
    samples = _samples(pcm)
    first = next((i for i, s in enumerate(samples) if abs(s) > SILENCE_LEVEL), None)
    if first is None:
        return pcm
    last = len(samples) - 1 - next(i for i, s in enumerate(reversed(samples)) if abs(s) > SILENCE_LEVEL)
    pad = round(EDGE_PAD_S * RATE)
    clip = array.array("h", samples[max(0, first - pad) : min(len(samples), last + pad + 1)])
    fade = min(round(FADE_S * RATE), len(clip) // 2)
    for i in range(fade):
        gain = i / fade
        clip[i] = round(clip[i] * gain)
        clip[len(clip) - 1 - i] = round(clip[len(clip) - 1 - i] * gain)
    return _bytes(clip)


def write_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    """Write a mono 16-bit WAV, atomically (a reader or a parallel build never sees half a file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle, wave.open(handle, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(WIDTH)
            out.setframerate(rate)
            out.writeframes(pcm)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_standard(path: Path) -> bytes | None:
    """The frames of a WAV that already is 48 kHz mono 16-bit PCM; None for any other file."""
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (RATE, 1, WIDTH):
                return None
            return source.readframes(source.getnframes())
    except (wave.Error, EOFError):
        return None


def convert(src: Path, dst: Path) -> None:
    """`src` (any format the tool reads) to a 48 kHz mono 16-bit WAV at `dst`."""
    ffmpeg, afconvert = shutil.which("ffmpeg"), shutil.which("afconvert")
    if ffmpeg:
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
        command += ["-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    elif afconvert:
        command = [afconvert, "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(src), str(dst)]
    else:
        raise VoiceError(f"cannot convert {src.suffix or 'this audio'} to 48 kHz WAV: {FFMPEG_HINT}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not dst.exists():
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise VoiceError(f"{Path(command[0]).name} could not convert {src.name}: {detail[0]}")


def load_standard(path: Path, work: Path) -> bytes:
    """The samples of any audio file as 48 kHz mono 16-bit PCM (converted through a tool when needed)."""
    pcm = _read_standard(path)
    if pcm is not None:
        return pcm
    converted = work / f"{path.stem}.48k.wav"
    convert(path, converted)
    pcm = _read_standard(converted)
    if pcm is None:
        raise VoiceError(f"conversion of {path.name} did not give 48 kHz mono 16-bit WAV")
    return pcm
