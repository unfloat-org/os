"""`elevenlabs`: text to speech with timestamps over `http.send`. Only the spoken sentence leaves the machine.

API pages read for this adapter (2026-11-16):
https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps  (endpoint, body, answer)
https://elevenlabs.io/docs/overview/models  (model ids, character limits)
https://elevenlabs.io/docs/api-reference/authentication  (the `xi-api-key` header)
https://elevenlabs.io/pricing/api  (price per 1,000 characters)
The plain endpoint answers with binary audio, which `http.send` cannot decode; the `with-timestamps` endpoint
answers with JSON (`audio_base64`). The character alignment in the answer is not used: sentence times are
exact from the audio, parts of a sentence are timed by characters (D8).
"""

from __future__ import annotations

import base64
import urllib.parse
import wave
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from unfloat_os import credentials, http
from unfloat_os.channels.video.voice.model import Keep, VoiceError, VoiceSettings

DEFAULT_MODEL = "eleven_multilingual_v2"  # long-form narration; 10,000 characters per request
DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_FORMAT = "pcm_24000"  # raw 16-bit mono PCM, allowed on every plan
KEY_ENV = "ELEVENLABS_API_KEY"

Send = Callable[..., "dict | list"]


def read_key() -> str:
    return credentials.read_secret(credentials.channel_dir("elevenlabs") / "api_key", KEY_ENV)


class ElevenLabs:
    name = "elevenlabs"

    def __init__(self, send: Send | None = None, key: Callable[[], str] | None = None):
        self._send = send or http.send
        self._key = key or read_key

    def fingerprint(self, settings: VoiceSettings) -> str:
        return ""

    def check(self, settings: VoiceSettings) -> None:
        if not settings.voice:
            raise VoiceError("elevenlabs: set voice to a voice id (GET /v1/voices lists them)")
        if not self._key():
            raise VoiceError(
                f"elevenlabs: no API key: put it in ~/.config/unfloat/elevenlabs/api_key (chmod 600) or set {KEY_ENV}"
            )
        self._format(settings.options)  # validated before any request
        self._base_url(settings.options)

    @staticmethod
    def _base_url(options: Mapping[str, object]) -> str:
        base = str(options.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        if not base.startswith("https://"):
            raise VoiceError(f"elevenlabs: base_url must start with https://, got {base!r}")
        return base

    @staticmethod
    def _format(options: Mapping[str, object]) -> tuple[str, int]:
        name = str(options.get("output_format", DEFAULT_FORMAT))
        prefix, _, rate = name.partition("_")
        if prefix != "pcm" or not rate.isdigit():
            raise VoiceError(f"elevenlabs: output_format must be pcm_<rate>, like {DEFAULT_FORMAT}; got {name!r}")
        return name, int(rate)

    def request(self, text: str, settings: VoiceSettings) -> tuple[str, dict]:
        """The URL and JSON body of one sentence: nothing but the spoken text, the model and the voice settings."""
        output_format, _ = self._format(settings.options)
        voice = urllib.parse.quote(settings.voice, safe="")  # a voice id never changes the path or the query
        url = f"{self._base_url(settings.options)}/v1/text-to-speech/{voice}/with-timestamps"
        voice_settings = dict(settings.options.get("voice_settings", {}))  # type: ignore[call-overload]
        voice_settings["speed"] = settings.speed
        body = {
            "text": text,
            "model_id": str(settings.options.get("model_id", DEFAULT_MODEL)),
            "voice_settings": voice_settings,
        }
        return f"{url}?output_format={output_format}", body

    def render(self, texts: Sequence[str], settings: VoiceSettings, work: Path, keep: Keep) -> list[Path]:
        key = self._key()
        _, rate = self._format(settings.options)
        paths = []
        for index, text in enumerate(texts):
            url, body = self.request(text, settings)
            try:
                answer = self._send("POST", url, data=body, headers={"xi-api-key": key}, redact=[key])
            except http.HttpError as exc:
                raise VoiceError(f"elevenlabs: {exc}") from exc
            except http.NetworkError as exc:
                raise VoiceError(f"elevenlabs: {exc}") from exc
            encoded = answer.get("audio_base64") if isinstance(answer, dict) else None
            if not encoded:
                raise VoiceError("elevenlabs: the answer has no audio_base64")
            try:
                pcm = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise VoiceError("elevenlabs: audio_base64 is not valid base64") from exc
            path = work / f"{index}.wav"
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                out.writeframes(pcm)
            paths.append(path)
            keep(index, path)
        return paths
