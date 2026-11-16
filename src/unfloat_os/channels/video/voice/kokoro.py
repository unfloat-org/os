"""`kokoro`: Kokoro-82M through mlx-audio, in a subprocess with its own environment made by `uvx`.

The runtime Python of `uo` is standard library only, so the model never loads here. `kokoro_runner.py` (next to
this file) is executed by that subprocess, loads the model once and writes one WAV per sentence; `uo` never
imports it. Docs read 2026-11-16: https://github.com/Blaizzy/mlx-audio (README, Kokoro section),
https://huggingface.co/hexgrad/Kokoro-82M (card, VOICES.md), https://github.com/hexgrad/misaki (README).
Licences: mlx-audio MIT, misaki Apache-2.0, Kokoro-82M Apache-2.0.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from unfloat_os.channels.video.voice.model import Keep, VoiceError, VoiceSettings

# One place for every pin; a change invalidates the cached sentences (see `fingerprint`).
PYTHON = "3.12"
MLX_AUDIO = "mlx-audio==0.6.6"
MISAKI = "misaki[en]==0.9.4"
MODEL = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "af_heart"
TIMEOUT_S = 3600
UVX_HINT = "install uv (macOS: `brew install uv`; others: https://docs.astral.sh/uv/getting-started/installation/)"
RUNNER = Path(__file__).with_name("kokoro_runner.py")


def lang_code(voice: str, options: dict | None = None) -> str:
    """Kokoro's language code: from options, else the voice prefix (`a` American: af_/am_, `b` British: bf_/bm_)."""
    explicit = (options or {}).get("lang_code")
    if explicit:
        return str(explicit)
    if voice[:1] in ("a", "b"):
        return voice[:1]
    raise VoiceError(f"kokoro: cannot tell the language of voice {voice!r}; use a name like af_heart or bm_george")


class Kokoro:
    name = "kokoro"

    def __init__(self) -> None:
        self.last_stats: dict = {}  # timings the model process reported in the last `render` (for measurements)

    def fingerprint(self, settings: VoiceSettings) -> str:
        return f"{PYTHON} {MLX_AUDIO} {MISAKI} {settings.options.get('model', MODEL)}"

    def check(self, settings: VoiceSettings) -> None:
        if shutil.which("uvx") is None:
            raise VoiceError(f"kokoro: `uvx` is not on PATH: {UVX_HINT}")
        lang_code(settings.voice or DEFAULT_VOICE, dict(settings.options))

    def command(self, request: Path, out: Path) -> list[str]:
        return [
            "uvx", "--python", PYTHON, "--from", MLX_AUDIO, "--with", MISAKI,
            "python", str(RUNNER), "--request", str(request), "--out", str(out),
        ]  # fmt: skip

    def render(self, texts: Sequence[str], settings: VoiceSettings, work: Path, keep: Keep) -> list[Path]:
        # one model process makes every file at once; the engine caches them when it returns
        voice = settings.voice or DEFAULT_VOICE
        request = work / "request.json"
        request.write_text(
            json.dumps(
                {
                    "model": str(settings.options.get("model", MODEL)),
                    "voice": voice,
                    "lang_code": lang_code(voice, dict(settings.options)),
                    "speed": settings.speed,
                    "texts": list(texts),
                }
            ),
            encoding="utf-8",
        )
        out = work / "kokoro"
        out.mkdir()
        try:
            result = subprocess.run(self.command(request, out), capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise VoiceError(f"kokoro: no answer after {TIMEOUT_S} s; the model process was stopped") from exc
        if result.returncode != 0:
            lines = [line for line in (result.stderr or result.stdout).strip().splitlines() if line.strip()]
            error = next((line for line in reversed(lines) if line.startswith("error:")), None)
            raise VoiceError(f"kokoro: {error or ' | '.join(lines[-3:]) or 'the model process failed'}")
        try:
            self.last_stats = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            self.last_stats = {}
        paths = [out / f"{index}.wav" for index in range(len(texts))]
        missing = [path.name for path in paths if not path.exists()]
        if missing:
            raise VoiceError(f"kokoro: the model process wrote no audio for {', '.join(missing[:3])}")
        return paths
