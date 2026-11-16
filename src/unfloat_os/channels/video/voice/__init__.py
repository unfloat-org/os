"""Narration for videos (contract C4): sentences become speech, cached one file per sentence.

    from unfloat_os.channels.video.voice import VoiceSettings, synthesize
    speech = synthesize({"results": "Text."}, VoiceSettings("kokoro", "af_heart"), out_dir, cache)

Providers: silent, say, kokoro, elevenlabs, file. Standard library only; speech engines run as subprocesses
(`uvx` for Kokoro) or over `http.send` (ElevenLabs).
"""

from unfloat_os.channels.video.voice.engine import providers, synthesize
from unfloat_os.channels.video.voice.model import Segment, Speech, VoiceError, VoiceSettings

__all__ = ["Segment", "Speech", "VoiceError", "VoiceSettings", "providers", "synthesize"]
