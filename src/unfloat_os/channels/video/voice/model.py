"""The data types of the voice package (contract C4) and the provider protocol."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from unfloat_os.channels import ChannelError


class VoiceError(ChannelError):
    """A refusal or failure of the voice step; the message names the fix."""


@dataclass(frozen=True)
class VoiceSettings:
    provider: str  # silent | say | kokoro | elevenlabs | file
    voice: str = ""
    speed: float = 1.0
    lexicon: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Segment:
    text: str  # caption text as written (lexicon not applied)
    start: float  # seconds from the start of this item's audio
    end: float


@dataclass(frozen=True)
class Speech:
    path: Path  # WAV, 48 kHz mono 16-bit PCM
    seconds: float
    segments: tuple[Segment, ...]


Keep = Callable[[int, Path], None]


class Provider(Protocol):
    """One speech engine. It turns sentences into audio files; the engine does the rest.

    `render` is called at most once per `synthesize` call, with every sentence that is not cached yet, so
    a local model is started once per build.
    """

    name: str

    def fingerprint(self, settings: VoiceSettings) -> str:
        """Extra text for the cache key, for example the pinned versions of the engine."""

    def check(self, settings: VoiceSettings) -> None:
        """Raise `VoiceError` naming the fix when the tool or the key is missing."""

    def render(self, texts: Sequence[str], settings: VoiceSettings, work: Path, keep: Keep) -> list[Path]:
        """One audio file (WAV, any rate, PCM) per text, in order, written under `work`.

        A provider that makes the files one by one calls `keep(index, path)` as each is ready, so the engine caches
        it at once and a failure later in the batch loses nothing already made."""
