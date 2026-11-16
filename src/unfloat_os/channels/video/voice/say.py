"""`say`: the macOS speech synthesizer. `voice` is a `say` voice name; `speed` scales its rate."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from unfloat_os.channels.video.voice import audio
from unfloat_os.channels.video.voice.model import VoiceError, VoiceSettings

# `say` takes a rate in words per minute but renders it in steps that depend on the voice (measured on this
# laptop: for Daniel every rate from 155 to 180 gives the same audio as no -r at all), so a small `speed`
# change such as 0.95 can do nothing; use 0.8 or below for an audible slow-down.
BASE_WORDS_PER_MINUTE = 175
HINT = (
    "`say` is part of macOS; on another system use the kokoro provider "
    "(needs `uvx`: https://docs.astral.sh/uv/) or the elevenlabs provider"
)


class Say:
    name = "say"

    def fingerprint(self, settings: VoiceSettings) -> str:
        return ""

    def check(self, settings: VoiceSettings) -> None:
        if shutil.which("say") is None:
            raise VoiceError(f"say: the `say` command is not available: {HINT}")

    def render(self, texts: Sequence[str], settings: VoiceSettings, work: Path) -> list[Path]:
        paths = []
        for index, text in enumerate(texts):
            source, path = work / f"{index}.txt", work / f"{index}.wav"
            source.write_text(text, encoding="utf-8")  # a file, so text starting with "-" is not an option
            command = ["say", "--file-format=WAVE", f"--data-format=LEI16@{audio.RATE}", "-o", str(path)]
            if settings.voice:
                command += ["-v", settings.voice]
            if settings.speed != 1.0:
                command += ["-r", str(round(BASE_WORDS_PER_MINUTE * settings.speed))]
            result = subprocess.run([*command, "-f", str(source)], capture_output=True, text=True)
            if result.returncode != 0 or not path.exists():
                detail = (result.stderr or result.stdout).strip() or "no output"
                hint = " (list voices with `say -v '?'`)" if settings.voice else ""
                raise VoiceError(f"say failed for voice {settings.voice or 'default'!r}: {detail}{hint}")
            paths.append(path)
        return paths
