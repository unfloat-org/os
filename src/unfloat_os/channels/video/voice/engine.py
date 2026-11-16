"""`synthesize`: sentences in, one WAV per item out, with a cache of one file per sentence."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from unfloat_os.channels.video.voice import audio
from unfloat_os.channels.video.voice import text as textlib
from unfloat_os.channels.video.voice.elevenlabs import ElevenLabs
from unfloat_os.channels.video.voice.file import speech_from_files
from unfloat_os.channels.video.voice.kokoro import Kokoro
from unfloat_os.channels.video.voice.model import Provider, Segment, Speech, VoiceError, VoiceSettings
from unfloat_os.channels.video.voice.say import Say
from unfloat_os.channels.video.voice.silent import Silent

SENTENCE_PAUSE_S = 0.35
PARAGRAPH_PAUSE_S = 0.70

_PROVIDERS: dict[str, Provider] = {
    "silent": Silent(),
    "say": Say(),
    "kokoro": Kokoro(),
    "elevenlabs": ElevenLabs(),
}
FILE_PROVIDER = "file"


def providers() -> tuple[str, ...]:
    return (*_PROVIDERS, FILE_PROVIDER)


@dataclass(frozen=True)
class _Planned:
    written: str
    spoken: str
    paragraph: int
    cache_key: str


def cache_key(provider: str, settings: VoiceSettings, fingerprint: str, spoken: str) -> str:
    """A hash of provider, voice, speed, options (and the engine pins) and the spoken text."""
    payload = [provider, settings.voice, settings.speed, dict(settings.options), fingerprint, spoken]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _plan(text: str, settings: VoiceSettings, provider: Provider) -> list[_Planned]:
    fingerprint = provider.fingerprint(settings)
    planned = []
    for sentence in textlib.split_text(text):
        spoken = textlib.apply_lexicon(sentence.text, settings.lexicon)
        key = cache_key(settings.provider, settings, fingerprint, spoken)
        planned.append(_Planned(sentence.text, spoken, sentence.paragraph, key))
    return planned


def _cache_path(cache: Path, settings: VoiceSettings, key: str) -> Path:
    return cache / f"{settings.provider}-{key[:32]}.wav"


def _validate(items: Mapping[str, str], settings: VoiceSettings) -> None:
    if settings.provider not in providers():
        raise VoiceError(f"unknown voice provider {settings.provider!r}; use one of {', '.join(providers())}")
    if not settings.speed > 0:
        raise VoiceError(f"voice speed must be above 0, got {settings.speed}")
    for key in items:
        if not key or "/" in key or "\\" in key or key.startswith("."):
            raise VoiceError(f"{key!r} cannot name an audio file; scene ids are plain names")


def _render_missing(
    missing: dict[str, str], provider: Provider, settings: VoiceSettings, cache: Path, wanted: dict[str, Path]
) -> None:
    """One provider call for every sentence that is not cached; each result is normalized into the cache."""
    provider.check(settings)
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=cache, prefix=".work-"))
    try:
        keys = list(missing)
        rendered = provider.render([missing[key] for key in keys], settings, work)
        if len(rendered) != len(keys):
            raise VoiceError(f"{provider.name} returned {len(rendered)} audio files for {len(keys)} sentences")
        for key, path in zip(keys, rendered, strict=True):
            pcm = audio.trim(audio.load_standard(path, work))
            if not pcm:
                raise VoiceError(f"{provider.name} returned no audio for: {missing[key][:60]}")
            audio.write_wav(wanted[key], pcm)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _assemble(planned: list[_Planned], clips: dict[str, bytes], path: Path) -> Speech:
    parts: list[bytes] = []
    segments: list[Segment] = []
    samples = 0
    for index, sentence in enumerate(planned):
        if index:
            pause = PARAGRAPH_PAUSE_S if sentence.paragraph != planned[index - 1].paragraph else SENTENCE_PAUSE_S
            gap = audio.silence(pause)
            parts.append(gap)
            samples += len(gap) // audio.WIDTH
        pcm = clips[sentence.cache_key]
        length = len(pcm) // audio.WIDTH
        pieces = textlib.caption_parts(sentence.written)
        total_chars = sum(len(piece) for piece in pieces)
        used = 0
        for piece in pieces:
            before, used = used, used + len(piece)
            start = samples + round(length * before / total_chars)
            end = samples + round(length * used / total_chars)
            segments.append(Segment(piece, start / audio.RATE, end / audio.RATE))
        parts.append(pcm)
        samples += length
    data = b"".join(parts)
    audio.write_wav(path, data)
    return Speech(path, samples / audio.RATE, tuple(segments))


def synthesize(items: Mapping[str, str], settings: VoiceSettings, out_dir: Path, cache: Path) -> dict[str, Speech]:
    """The narrations of one build, keyed by scene id, as `out_dir/<key>.wav` and a `Speech` each.

    An empty text gives no entry. Every sentence is cached on its own under `cache`, so a second call with the
    same texts synthesizes nothing, and one call starts a local model at most once.
    """
    _validate(items, settings)
    items = {key: text for key, text in items.items() if text.strip()}
    out_dir.mkdir(parents=True, exist_ok=True)
    if settings.provider == FILE_PROVIDER:
        return speech_from_files(items, settings, out_dir)
    provider = _PROVIDERS[settings.provider]
    plans = {key: _plan(text, settings, provider) for key, text in items.items()}
    wanted = {s.cache_key: _cache_path(cache, settings, s.cache_key) for plan in plans.values() for s in plan}
    missing = {}
    for plan in plans.values():
        for sentence in plan:
            if not wanted[sentence.cache_key].exists():
                missing.setdefault(sentence.cache_key, sentence.spoken)
    if missing:
        _render_missing(missing, provider, settings, cache, wanted)
    clips: dict[str, bytes] = {}
    for key, path in wanted.items():
        pcm = audio.load_standard(path, cache)
        clips[key] = pcm
    return {key: _assemble(plan, clips, out_dir / f"{key}.wav") for key, plan in plans.items()}
