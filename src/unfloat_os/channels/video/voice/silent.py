"""`silent`: deterministic silence whose length follows the text at a stated rate (tests and timing drafts)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from unfloat_os.channels.video.voice import audio
from unfloat_os.channels.video.voice.model import VoiceSettings

DEFAULT_WORDS_PER_MINUTE = 140.0
MIN_SECONDS = 0.4


class Silent:
    name = "silent"

    def fingerprint(self, settings: VoiceSettings) -> str:
        return ""

    def check(self, settings: VoiceSettings) -> None:
        return None

    def seconds_for(self, text: str, settings: VoiceSettings) -> float:
        """options["words_per_minute"] (default 140) times `speed`; a sentence is never shorter than 0.4 s."""
        wpm = float(settings.options.get("words_per_minute", DEFAULT_WORDS_PER_MINUTE))  # type: ignore[arg-type]
        return max(MIN_SECONDS, len(text.split()) / (wpm * settings.speed / 60))

    def render(self, texts: Sequence[str], settings: VoiceSettings, work: Path) -> list[Path]:
        paths = []
        for index, text in enumerate(texts):
            path = work / f"{index}.wav"
            audio.write_wav(path, audio.silence(self.seconds_for(text, settings)))
            paths.append(path)
        return paths
