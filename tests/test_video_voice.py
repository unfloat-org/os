"""Behaviour of the video voice package (C4): text, segments, cache, provider refusals, the ElevenLabs adapter.

No network and no real synthesis except `silent`; tools that CI may lack (ffmpeg, afconvert) are skipped.
"""

from __future__ import annotations

import base64
import itertools
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from unfloat_os import http
from unfloat_os.channels.video.voice import (
    Speech,
    VoiceError,
    VoiceSettings,
    audio,
    engine,
    providers,
    synthesize,
)
from unfloat_os.channels.video.voice import text as textlib
from unfloat_os.channels.video.voice.elevenlabs import ElevenLabs
from unfloat_os.channels.video.voice.kokoro import Kokoro, lang_code
from unfloat_os.channels.video.voice.say import parse_voices

NARRATION = (
    "The tomato beds with mulch gave more fruit. We counted 41 percent more, e.g. 2.7 kilograms a bed.\n\n"
    "Dr. Rivera checked the scales twice. Was the soil the same? It was."
)


def read_wav(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as source:
        return source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getnframes()


class CountingProvider:
    """A provider that writes a 48 kHz tone per sentence and counts how it was called."""

    name = "counting"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def fingerprint(self, settings: VoiceSettings) -> str:
        return ""

    def check(self, settings: VoiceSettings) -> None:
        return None

    def render(self, texts, settings, work, keep):
        self.calls.append(list(texts))
        paths = []
        for index, text in enumerate(texts):
            samples = round(0.05 * len(text) * audio.RATE)
            pcm = b"".join(
                (8000 if (i // 40) % 2 else -8000).to_bytes(2, "little", signed=True) for i in range(samples)
            )
            path = work / f"{index}.wav"
            audio.write_wav(path, pcm)
            paths.append(path)
        return paths


@pytest.fixture
def counting(monkeypatch):
    provider = CountingProvider()
    monkeypatch.setitem(engine._PROVIDERS, "counting", provider)
    return provider


# --- text -------------------------------------------------------------------------------------------------


def sentences(text: str) -> list[str]:
    return [s.text for s in textlib.split_text(text)]


def test_decimals_do_not_end_a_sentence():
    assert sentences("The path is 0.8 metres wide. Two beds differ by up to 2.7 times.") == [
        "The path is 0.8 metres wide.",
        "Two beds differ by up to 2.7 times.",
    ]


def test_abbreviations_titles_and_initials_do_not_end_a_sentence():
    text = "Dr. Rivera, e.g. the second author, agreed with J. Smith. Fig. 2 shows it. No. 5 is the longest."
    assert sentences(text) == [
        "Dr. Rivera, e.g. the second author, agreed with J. Smith.",
        "Fig. 2 shows it.",
        "No. 5 is the longest.",
    ]


def test_questions_exclamations_and_quotes_end_sentences():
    assert sentences('Was it real? "Yes." Then say so! Done.') == ["Was it real?", '"Yes."', "Then say so!", "Done."]


def test_a_full_stop_before_lowercase_continues_the_sentence():
    assert sentences("It costs 5 USD approx. ten times less.") == ["It costs 5 USD approx. ten times less."]


def test_paragraphs_are_kept_and_white_space_is_collapsed():
    found = textlib.split_text("One   two.\nThree\nfour.\n\n\nFive.")
    assert [(s.text, s.paragraph) for s in found] == [("One two.", 0), ("Three four.", 0), ("Five.", 1)]


def test_lexicon_replaces_whole_words_in_the_case_given_longest_first():
    lexicon = {"NPK": "en pee kay", "compost": "com post", "compost heap": "the heap", "x": "ex"}
    assert textlib.apply_lexicon("NPK and compost heap, compost, npk, composting, x-ray, box", lexicon) == (
        "en pee kay and the heap, com post, npk, composting, ex-ray, box"
    )


def test_a_replacement_is_not_replaced_again():
    assert textlib.apply_lexicon("a b", {"a": "b", "b": "a"}) == "b a"


def test_captions_split_at_clause_boundaries_and_stay_short():
    sentence = (
        "Two weighings of the same bed, weeks apart, can differ by up to 2.7 times, "
        "so a smaller change in one bed cannot be called real without a second count."
    )
    parts = textlib.caption_parts(sentence)
    assert len(parts) >= 2
    assert all(len(p) <= textlib.CAPTION_LIMIT for p in parts)
    assert " ".join(parts) == sentence
    assert parts[0].endswith(",")


def test_a_short_sentence_is_one_caption_part():
    assert textlib.caption_parts("Short one.") == ["Short one."]


# --- audio and segments -----------------------------------------------------------------------------------


def test_silent_speech_has_the_standard_format_and_exact_length(tmp_path):
    settings = VoiceSettings("silent", options={"words_per_minute": 120})
    result = synthesize({"intro": "one two three four five six"}, settings, tmp_path / "out", tmp_path / "cache")
    speech = result["intro"]
    assert isinstance(speech, Speech)
    assert read_wav(speech.path) == (48000, 1, 2, round(speech.seconds * 48000))
    assert speech.seconds == pytest.approx(6 / 120 * 60)


def test_silent_speed_shortens_the_speech(tmp_path):
    text = {"a": "one two three four five six seven eight nine ten"}
    slow = synthesize(text, VoiceSettings("silent", speed=0.5), tmp_path / "s", tmp_path / "c")["a"]
    fast = synthesize(text, VoiceSettings("silent", speed=2.0), tmp_path / "f", tmp_path / "c")["a"]
    assert slow.seconds == pytest.approx(fast.seconds * 4, abs=0.01)


def test_segments_cover_the_audio_in_order_with_the_pauses_between(tmp_path):
    speech = synthesize({"s": NARRATION}, VoiceSettings("silent"), tmp_path / "out", tmp_path / "cache")["s"]
    segments = speech.segments
    assert segments[0].start == 0.0
    assert segments[-1].end == pytest.approx(speech.seconds)
    for before, after in itertools.pairwise(segments):
        assert before.start < before.end <= after.start
    gaps = [round(b.start - a.end, 2) for a, b in itertools.pairwise(segments)]
    assert set(gaps) <= {0.0, 0.35, 0.7}
    assert 0.7 in gaps  # the blank line
    assert 0.35 in gaps  # a sentence inside a paragraph
    sentence_ends = [t for t in (s.text for s in segments) if t.endswith((".", "?"))]
    assert len(sentence_ends) == 5


def test_segment_text_is_as_written_not_as_spoken(tmp_path):
    settings = VoiceSettings("silent", lexicon={"NPK": "en pee kay"})
    speech = synthesize({"s": "The NPK test is next."}, settings, tmp_path / "out", tmp_path / "cache")["s"]
    assert [s.text for s in speech.segments] == ["The NPK test is next."]


def test_long_sentences_are_timed_in_proportion_to_their_characters(tmp_path):
    sentence = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa, lambda mu nu xi omicron pi rho sigma tau upsilon."
    )
    speech = synthesize({"s": sentence}, VoiceSettings("silent"), tmp_path / "o", tmp_path / "c")["s"]
    first, second = speech.segments
    assert first.end == second.start
    share = (first.end - first.start) / (speech.seconds)
    assert share == pytest.approx(len(first.text) / (len(first.text) + len(second.text)), abs=0.01)


def test_an_empty_text_gives_no_entry(tmp_path):
    result = synthesize(
        {"a": "Hello there.", "b": "", "c": "  \n "}, VoiceSettings("silent"), tmp_path / "o", tmp_path / "c"
    )
    assert set(result) == {"a"}
    assert not (tmp_path / "o" / "b.wav").exists()


def test_trim_removes_edge_silence_and_keeps_a_pad_around_the_speech():
    tone = b"".join((9000).to_bytes(2, "little", signed=True) for _ in range(4800))
    clip = audio.silence(0.5) + tone + audio.silence(0.5)
    trimmed = audio.trim(clip)
    assert audio.seconds_of(trimmed) == pytest.approx(0.1 + 2 * audio.EDGE_PAD_S, abs=0.002)
    assert audio.peak_dbfs(trimmed) == pytest.approx(-11.2, abs=0.1)  # 9000 of 32768


def test_peak_of_silence_is_minus_infinity():
    assert audio.peak_dbfs(audio.silence(0.1)) == float("-inf")


@pytest.mark.skipif(not (shutil.which("ffmpeg") or shutil.which("afconvert")), reason="needs ffmpeg or afconvert")
def test_other_rates_are_converted_to_48_khz(tmp_path):
    source = tmp_path / "in.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(bytes(2 * 24000))  # one second
    pcm = audio.load_standard(source, tmp_path)
    assert audio.seconds_of(pcm) == pytest.approx(1.0, abs=0.01)


def test_conversion_without_a_tool_names_the_fix(tmp_path, monkeypatch):
    source = tmp_path / "in.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(bytes(480))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(VoiceError, match="install ffmpeg"):
        audio.load_standard(source, tmp_path)


# --- cache ------------------------------------------------------------------------------------------------


def test_a_second_call_with_the_same_texts_synthesizes_nothing(tmp_path, counting):
    settings = VoiceSettings("counting")
    items = {"one": NARRATION, "two": "A different scene. It has two sentences."}
    first = synthesize(items, settings, tmp_path / "a", tmp_path / "cache")
    assert len(counting.calls) == 1  # one call for the whole build: a local model starts once
    second = synthesize(items, settings, tmp_path / "b", tmp_path / "cache")
    assert len(counting.calls) == 1
    assert {k: v.seconds for k, v in first.items()} == {k: v.seconds for k, v in second.items()}
    assert first["one"].path.read_bytes() == second["one"].path.read_bytes()


def test_only_a_changed_sentence_is_synthesized_again(tmp_path, counting):
    settings = VoiceSettings("counting")
    synthesize({"s": "First sentence. Second sentence."}, settings, tmp_path / "a", tmp_path / "cache")
    synthesize({"s": "First sentence. A new second sentence."}, settings, tmp_path / "b", tmp_path / "cache")
    assert counting.calls[1] == ["A new second sentence."]


def test_a_sentence_used_twice_is_synthesized_once(tmp_path, counting):
    synthesize(
        {"a": "Same words here.", "b": "Same words here. And more."},
        VoiceSettings("counting"),
        tmp_path / "o",
        tmp_path / "c",
    )
    assert sorted(counting.calls[0]) == ["And more.", "Same words here."]


@pytest.mark.parametrize(
    "other",
    [
        VoiceSettings("counting", voice="v2"),
        VoiceSettings("counting", speed=0.9),
        VoiceSettings("counting", options={"k": 1}),
        VoiceSettings("counting", lexicon={"Hello": "Hullo"}),
    ],
)
def test_voice_speed_options_and_lexicon_each_change_the_cache(tmp_path, counting, other):
    items = {"s": "Hello there."}
    synthesize(items, VoiceSettings("counting"), tmp_path / "a", tmp_path / "cache")
    synthesize(items, other, tmp_path / "b", tmp_path / "cache")
    assert len(counting.calls) == 2


def test_a_cached_build_needs_neither_tool_nor_key(tmp_path, counting, monkeypatch):
    items = {"s": "Hello there."}
    synthesize(items, VoiceSettings("counting"), tmp_path / "a", tmp_path / "cache")
    monkeypatch.setattr(counting, "check", lambda settings: (_ for _ in ()).throw(VoiceError("no key")))
    synthesize(items, VoiceSettings("counting"), tmp_path / "b", tmp_path / "cache")


def test_no_scratch_files_stay_in_the_cache(tmp_path, counting):
    synthesize({"s": "Hello there."}, VoiceSettings("counting"), tmp_path / "o", tmp_path / "cache")
    assert [p.suffix for p in (tmp_path / "cache").iterdir()] == [".wav"]


def test_unknown_provider_speed_and_scene_names_are_refused(tmp_path):
    with pytest.raises(VoiceError, match="unknown voice provider 'nope'; use one of silent, say"):
        synthesize({"a": "x."}, VoiceSettings("nope"), tmp_path / "o", tmp_path / "c")
    with pytest.raises(VoiceError, match="speed must be above 0"):
        synthesize({"a": "x."}, VoiceSettings("silent", speed=0), tmp_path / "o", tmp_path / "c")
    with pytest.raises(VoiceError, match="cannot name an audio file"):
        synthesize({"../a": "x."}, VoiceSettings("silent"), tmp_path / "o", tmp_path / "c")


def test_providers_lists_the_five_of_c4():
    assert providers() == ("silent", "say", "kokoro", "elevenlabs", "file")


# --- providers without their tool or key ------------------------------------------------------------------


def test_say_without_the_command_names_the_alternatives(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(VoiceError, match=r"`say` command is not available.*kokoro"):
        synthesize({"a": "Hello."}, VoiceSettings("say"), tmp_path / "o", tmp_path / "c")


def test_say_voice_names_are_read_from_its_listing_with_spaces_and_brackets():
    listing = (
        "Daniel              en_GB    # Hello! My name is Daniel.\n"
        "Eddy (English (UK)) en_GB    # Hello! My name is Eddy.\n"
        "Bad News            en_US    # Hello! My name is Bad News.\n"
    )
    assert parse_voices(listing) == ["Daniel", "Eddy (English (UK))", "Bad News"]


def test_say_accepts_a_voice_by_its_short_name(tmp_path, monkeypatch):
    listing = "Daniel (English (UK)) en_GB    # Hello! My name is Daniel.\n"
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=listing, stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/say")
    monkeypatch.setattr(subprocess, "run", fake_run)
    from unfloat_os.channels.video.voice.say import Say

    Say().check(VoiceSettings("say", "daniel"))
    with pytest.raises(VoiceError, match="no voice named 'Danial'"):
        Say().check(VoiceSettings("say", "Danial"))


@pytest.mark.skipif(shutil.which("say") is None, reason="needs macOS say")
def test_an_unknown_say_voice_is_refused_not_replaced_by_the_default(tmp_path):
    with pytest.raises(VoiceError, match="no voice named 'NoSuchVoice'; list them with `say -v '\\?'`"):
        synthesize({"a": "Hello."}, VoiceSettings("say", "NoSuchVoice"), tmp_path / "o", tmp_path / "c")


def test_kokoro_without_uvx_names_the_install_command(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(VoiceError, match=r"`uvx` is not on PATH: install uv \(macOS: `brew install uv`"):
        synthesize({"a": "Hello."}, VoiceSettings("kokoro", "af_heart"), tmp_path / "o", tmp_path / "c")


def test_kokoro_language_follows_the_voice_name():
    assert lang_code("af_heart") == "a"
    assert lang_code("bm_george") == "b"
    assert lang_code("zf_xiaobei", {"lang_code": "z"}) == "z"
    with pytest.raises(VoiceError, match="cannot tell the language"):
        lang_code("george")


def test_kokoro_pins_are_part_of_the_cache_key():
    provider = Kokoro()
    one = provider.fingerprint(VoiceSettings("kokoro"))
    other = provider.fingerprint(VoiceSettings("kokoro", options={"model": "mlx-community/Kokoro-82M-4bit"}))
    assert "mlx-audio==" in one and one != other


def test_elevenlabs_without_a_key_refuses_with_one_line_and_sends_nothing(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setitem(
        engine._PROVIDERS, "elevenlabs", ElevenLabs(send=lambda *a, **k: sent.append(a), key=lambda: "")
    )
    with pytest.raises(VoiceError) as caught:
        synthesize({"a": "Hello."}, VoiceSettings("elevenlabs", "voice123"), tmp_path / "o", tmp_path / "c")
    assert "\n" not in str(caught.value)
    assert "~/.config/unfloat/elevenlabs/api_key" in str(caught.value) and "ELEVENLABS_API_KEY" in str(caught.value)
    assert sent == []


def test_the_real_key_lookup_finds_nothing_without_a_key_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("unfloat_os.credentials.BASE", tmp_path / ".config" / "unfloat")
    from unfloat_os.channels.video.voice.elevenlabs import read_key

    assert read_key() == ""


def test_elevenlabs_needs_a_voice_id(tmp_path, monkeypatch):
    monkeypatch.setitem(engine._PROVIDERS, "elevenlabs", ElevenLabs(send=lambda *a, **k: {}, key=lambda: "k"))
    with pytest.raises(VoiceError, match="set voice to a voice id"):
        synthesize({"a": "Hello."}, VoiceSettings("elevenlabs"), tmp_path / "o", tmp_path / "c")


# --- ElevenLabs against a fake transport ------------------------------------------------------------------


class FakeTransport:
    """Records every request and answers like the with-timestamps endpoint: base64 PCM and an alignment."""

    def __init__(self, rate: int = 48000, fail: Exception | None = None):
        self.rate, self.fail, self.requests = rate, fail, []

    def __call__(self, method, url, *, data=None, headers=None, redact=(), **kwargs):
        self.requests.append({"method": method, "url": url, "data": data, "headers": headers, "redact": list(redact)})
        if self.fail:
            raise self.fail
        samples = round(0.5 * self.rate)
        pcm = b"".join((6000 if (i // 50) % 2 else -6000).to_bytes(2, "little", signed=True) for i in range(samples))
        return {
            "audio_base64": base64.b64encode(pcm).decode(),
            "alignment": {
                "characters": ["H"],
                "character_start_times_seconds": [0],
                "character_end_times_seconds": [0.1],
            },
        }


def eleven(monkeypatch, transport: FakeTransport) -> None:
    monkeypatch.setitem(engine._PROVIDERS, "elevenlabs", ElevenLabs(send=transport, key=lambda: "secret-key"))


def test_elevenlabs_request_carries_only_the_spoken_sentence(tmp_path, monkeypatch):
    transport = FakeTransport()
    eleven(monkeypatch, transport)
    settings = VoiceSettings(
        "elevenlabs",
        "voice123",
        speed=0.9,
        lexicon={"NPK": "en pee kay"},
        options={"output_format": "pcm_48000", "model_id": "eleven_v4", "voice_settings": {"stability": 0.6}},
    )
    synthesize({"s": "The NPK test is next. It is 2.7 times."}, settings, tmp_path / "o", tmp_path / "c")
    assert len(transport.requests) == 2
    first = transport.requests[0]
    assert first["method"] == "POST"
    assert (
        first["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice123/with-timestamps?output_format=pcm_48000"
    )
    assert first["data"] == {
        "text": "The en pee kay test is next.",
        "model_id": "eleven_v4",
        "voice_settings": {"stability": 0.6, "speed": 0.9},
    }
    assert first["headers"] == {"xi-api-key": "secret-key"}
    assert first["redact"] == ["secret-key"]
    assert transport.requests[1]["data"]["text"] == "It is 2.7 times."


def test_elevenlabs_answer_is_decoded_into_the_standard_wav(tmp_path, monkeypatch):
    eleven(monkeypatch, FakeTransport(rate=48000))
    settings = VoiceSettings("elevenlabs", "v", options={"output_format": "pcm_48000"})
    speech = synthesize({"s": "One sentence. And another."}, settings, tmp_path / "o", tmp_path / "c")["s"]
    assert read_wav(speech.path)[:3] == (48000, 1, 2)
    # two half-second clips (edge pads kept) joined with the 0.35 s pause
    assert speech.seconds == pytest.approx(2 * 0.5 + 0.35, abs=0.02)
    assert audio.peak_dbfs(audio.load_standard(speech.path, tmp_path)) == pytest.approx(-14.7, abs=0.2)


@pytest.mark.skipif(not (shutil.which("ffmpeg") or shutil.which("afconvert")), reason="needs ffmpeg or afconvert")
def test_elevenlabs_default_format_is_resampled_to_48_khz(tmp_path, monkeypatch):
    eleven(monkeypatch, FakeTransport(rate=24000))
    speech = synthesize({"s": "One sentence."}, VoiceSettings("elevenlabs", "v"), tmp_path / "o", tmp_path / "c")["s"]
    assert read_wav(speech.path)[:3] == (48000, 1, 2)
    assert speech.seconds == pytest.approx(0.5, abs=0.02)


def test_elevenlabs_http_errors_become_voice_errors_without_the_key(tmp_path, monkeypatch):
    error = http.HttpError("POST", "https://api.elevenlabs.io/x?output_format=pcm_24000", 401, "invalid key")
    eleven(monkeypatch, FakeTransport(fail=error))
    with pytest.raises(VoiceError, match="HTTP 401: invalid key") as caught:
        synthesize({"s": "Hi there."}, VoiceSettings("elevenlabs", "v"), tmp_path / "o", tmp_path / "c")
    assert "secret-key" not in str(caught.value)
    eleven(monkeypatch, FakeTransport(fail=http.NetworkError("POST x: network failure (down)")))
    with pytest.raises(VoiceError, match="network failure"):
        synthesize({"s": "Hi there."}, VoiceSettings("elevenlabs", "v"), tmp_path / "o", tmp_path / "c")


def test_elevenlabs_answer_without_audio_is_refused(tmp_path, monkeypatch):
    monkeypatch.setitem(
        engine._PROVIDERS, "elevenlabs", ElevenLabs(send=lambda *a, **k: {"detail": "x"}, key=lambda: "k")
    )
    with pytest.raises(VoiceError, match="no audio_base64"):
        synthesize({"s": "Hi there."}, VoiceSettings("elevenlabs", "v"), tmp_path / "o", tmp_path / "c")


def test_elevenlabs_refuses_other_formats_and_plain_http(tmp_path, monkeypatch):
    transport = FakeTransport()
    eleven(monkeypatch, transport)
    with pytest.raises(VoiceError, match="output_format must be pcm_<rate>"):
        synthesize(
            {"s": "Hi."},
            VoiceSettings("elevenlabs", "v", options={"output_format": "mp3_44100_128"}),
            tmp_path / "o",
            tmp_path / "c",
        )
    with pytest.raises(VoiceError, match="base_url must start with https://"):
        synthesize(
            {"s": "Hi."},
            VoiceSettings("elevenlabs", "v", options={"base_url": "http://x.example"}),
            tmp_path / "o",
            tmp_path / "c",
        )
    assert transport.requests == []


def test_elevenlabs_results_are_cached_per_sentence(tmp_path, monkeypatch):
    transport = FakeTransport()
    eleven(monkeypatch, transport)
    settings = VoiceSettings("elevenlabs", "v", options={"output_format": "pcm_48000"})
    synthesize({"s": "One. Two."}, settings, tmp_path / "a", tmp_path / "c")
    synthesize({"s": "One. Two."}, settings, tmp_path / "b", tmp_path / "c")
    assert len(transport.requests) == 2


# --- the recorded-file provider ---------------------------------------------------------------------------


def make_recording(path: Path, seconds: float) -> None:
    audio.write_wav(
        path, b"".join((5000).to_bytes(2, "little", signed=True) for _ in range(round(seconds * audio.RATE)))
    )


def test_file_provider_uses_the_recording_and_times_segments_by_proportion(tmp_path):
    recording = tmp_path / "me.wav"
    make_recording(recording, 6.0)
    settings = VoiceSettings("file", options={"path": str(recording)})
    speech = synthesize(
        {"s": "Short one. A much longer second sentence follows."}, settings, tmp_path / "o", tmp_path / "c"
    )["s"]
    assert speech.seconds == pytest.approx(6.0)
    first, second = speech.segments
    assert first.start == 0.0 and first.end == second.start and second.end == pytest.approx(6.0)
    assert (first.end - first.start) / 6.0 == pytest.approx(
        len(first.text) / (len(first.text) + len(second.text)), abs=0.01
    )


def test_file_provider_needs_a_path_a_folder_for_several_scenes_and_an_existing_file(tmp_path):
    with pytest.raises(VoiceError, match="set options path"):
        synthesize({"s": "Hi."}, VoiceSettings("file"), tmp_path / "o", tmp_path / "c")
    recording = tmp_path / "me.wav"
    make_recording(recording, 1.0)
    with pytest.raises(VoiceError, match="2 narrations need a folder"):
        synthesize(
            {"a": "Hi.", "b": "Yo."},
            VoiceSettings("file", options={"path": str(recording)}),
            tmp_path / "o",
            tmp_path / "c",
        )
    with pytest.raises(VoiceError, match="no recording at"):
        synthesize(
            {"s": "Hi."},
            VoiceSettings("file", options={"path": str(tmp_path / "gone.wav")}),
            tmp_path / "o",
            tmp_path / "c",
        )
    folder = tmp_path / "rec"
    folder.mkdir()
    make_recording(folder / "a.wav", 1.0)
    make_recording(folder / "b.wav", 2.0)
    result = synthesize(
        {"a": "Hi.", "b": "Yo."}, VoiceSettings("file", options={"path": str(folder)}), tmp_path / "o", tmp_path / "c"
    )
    assert [result["a"].seconds, result["b"].seconds] == [pytest.approx(1.0), pytest.approx(2.0)]


class FailingOn(FakeTransport):
    """Answers like the service, but fails on request number `n` (1-based)."""

    def __init__(self, n: int):
        super().__init__()
        self.n = n

    def __call__(self, method, url, **kwargs):
        if len(self.requests) + 1 == self.n:
            self.requests.append({"url": url, "data": kwargs.get("data")})
            raise http.HttpError(method, url, 500, "server error")
        return super().__call__(method, url, **kwargs)


def test_elevenlabs_keeps_every_sentence_already_paid_for_when_a_request_fails(tmp_path, monkeypatch):
    text = "The beds were dug. The seeds went in. The rain came. The first shoots are up."
    settings = VoiceSettings("elevenlabs", "v", options={"output_format": "pcm_48000"})
    cache = tmp_path / "cache"
    failing = FailingOn(3)
    eleven(monkeypatch, failing)
    with pytest.raises(VoiceError, match="elevenlabs"):
        synthesize({"s": text}, settings, tmp_path / "out", cache)
    assert len(list(cache.glob("*.wav"))) == 2  # the two sentences answered before the failure
    retry = FakeTransport()
    eleven(monkeypatch, retry)
    synthesize({"s": text}, settings, tmp_path / "out", cache)
    assert [r["data"]["text"] for r in retry.requests] == ["The rain came.", "The first shoots are up."]


def test_elevenlabs_quotes_the_voice_id_so_it_cannot_change_the_request(tmp_path, monkeypatch):
    transport = FakeTransport()
    eleven(monkeypatch, transport)
    settings = VoiceSettings("elevenlabs", "a/b?x=1", options={"output_format": "pcm_48000"})
    synthesize({"s": "One line."}, settings, tmp_path / "o", tmp_path / "c")
    assert transport.requests[0]["url"] == (
        "https://api.elevenlabs.io/v1/text-to-speech/a%2Fb%3Fx%3D1/with-timestamps?output_format=pcm_48000"
    )
