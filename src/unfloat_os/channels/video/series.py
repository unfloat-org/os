"""Series profiles (contract C2): `config/video/<series>.toml` in the records repository.

A series says who a line of updates is for and what it may use: title, audience, the default task and people
of new drafts, captions, length bounds and speaking rate, required chapters, voice, the media it may show and
the restricted media it releases, and the roadmap steps. Unknown keys and bad values are errors naming the key.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from unfloat_os.channels.video import refs
from unfloat_os.schema import ID_RE, WORD_RE
from unfloat_os.workspace import DATA_CLASSES, Workspace

FOLDER = Path("config") / "video"  # relative to the records repository
CHAPTERS = ("title", "bottom-line", "goal", "done", "results", "limits", "next", "end")
DEFAULT_REQUIRED = ("bottom-line", "results", "next")
# The providers of contract C4, used when the voice package is not installed in this checkout.
TOP_KEYS = {"title", "audience", "task", "people", "captions", "length", "chapters", "voice", "data", "roadmap"}


class SeriesError(ValueError):
    """A profile that is missing or breaks a rule; the message names the file and the key."""


@dataclass(frozen=True)
class Step:
    id: str
    label: str


@dataclass(frozen=True)
class Release:
    path: str
    reason: str


@dataclass(frozen=True)
class Series:
    name: str
    path: Path
    title: str
    audience: str = ""
    task: str = ""
    people: tuple[str, ...] = ()
    captions: bool = True
    min_seconds: float = 90
    max_seconds: float = 300
    words_per_minute: float = 140
    required: tuple[str, ...] = DEFAULT_REQUIRED
    provider: str = "say"
    voice: str = ""
    speed: float = 1.0
    lexicon: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, object] = field(default_factory=dict)
    allow: tuple[str, ...] = ()
    release: tuple[Release, ...] = ()
    roadmap: tuple[Step, ...] = ()

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.roadmap if s.id == step_id), None)

    def released(self, ref: refs.Ref) -> Release | None:
        return next((r for r in self.release if r.path == str(ref)), None)

    def allows(self, ref: refs.Ref) -> bool:
        return any(refs.matches(ref, pattern) for pattern in self.allow)


def providers() -> tuple[str, ...]:
    from unfloat_os.channels.video import voice  # imported here: the voice package imports `channels`

    return tuple(voice.providers())


def folder(ws: Workspace) -> Path:
    return ws.ops / FOLDER


def names(ws: Workspace) -> list[str]:
    return sorted(p.stem for p in folder(ws).glob("*.toml"))


def _text(where: str, key: str, value: object, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or "\n" in value.strip():
        raise SeriesError(f"{where}: '{key}' must be {'a non-empty' if required else 'a'} one-line string")
    return value.strip()


def _number(where: str, key: str, value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise SeriesError(f"{where}: '{key}' must be a positive number")
    return float(value)


def _table(where: str, key: str, value: object, allowed: set[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SeriesError(f"{where}: '{key}' must be a table")
    unknown = set(value) - allowed
    if unknown:
        raise SeriesError(f"{where}: unknown key(s) in [{key}]: {', '.join(sorted(unknown))}")
    return value


def _strings(where: str, key: str, value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise SeriesError(f"{where}: '{key}' must be a list of strings")
    return tuple(v.strip() for v in value)


def parse(name: str, path: Path, source: str) -> Series:
    """Build a series from the text of its profile; raises SeriesError naming the key on the first problem."""
    where = f"{FOLDER.as_posix()}/{path.name}"
    if not WORD_RE.match(name):
        raise SeriesError(f"{where}: the series name must be lowercase letters, digits and '-'")
    try:
        raw = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        raise SeriesError(f"{where}: invalid TOML: {exc}") from exc
    unknown = set(raw) - TOP_KEYS
    if unknown:
        raise SeriesError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")

    title = _text(where, "title", raw.get("title"), required=True)
    audience = _text(where, "audience", raw.get("audience"))
    task = _text(where, "task", raw.get("task"))
    if task and not (ID_RE.match(task) and task.startswith("T-")):
        raise SeriesError(f"{where}: 'task' must be a task ID T-###")
    people = _strings(where, "people", raw.get("people"))
    for person in people:
        if not (ID_RE.match(person) and person.startswith("P-")):
            raise SeriesError(f"{where}: 'people' must list person IDs P-###, not '{person}'")
    captions = raw.get("captions", True)
    if not isinstance(captions, bool):
        raise SeriesError(f"{where}: 'captions' must be true or false")

    length = _table(where, "length", raw.get("length"), {"min_seconds", "max_seconds", "words_per_minute"})
    min_seconds = _number(where, "length.min_seconds", length.get("min_seconds"), 90)
    max_seconds = _number(where, "length.max_seconds", length.get("max_seconds"), 300)
    if min_seconds >= max_seconds:
        raise SeriesError(f"{where}: 'length.min_seconds' must be below 'length.max_seconds'")
    wpm = _number(where, "length.words_per_minute", length.get("words_per_minute"), 140)

    chapters = _table(where, "chapters", raw.get("chapters"), {"required"})
    required = _strings(where, "chapters.required", chapters.get("required")) or DEFAULT_REQUIRED
    for chapter in required:
        if chapter not in CHAPTERS:
            raise SeriesError(
                f"{where}: 'chapters.required' has unknown chapter '{chapter}'; one of {', '.join(CHAPTERS)}"
            )

    voice = _table(where, "voice", raw.get("voice"), {"provider", "voice", "speed", "lexicon", "options"})
    provider = _text(where, "voice.provider", voice.get("provider")) or "say"
    known = providers()
    if provider not in known:
        raise SeriesError(f"{where}: 'voice.provider' '{provider}' is not one of {', '.join(known)}")
    voice_name = _text(where, "voice.voice", voice.get("voice"))
    speed = _number(where, "voice.speed", voice.get("speed"), 1.0)
    lexicon = voice.get("lexicon", {})
    if not isinstance(lexicon, dict) or not all(
        isinstance(k, str) and k and isinstance(v, str) and v.strip() for k, v in lexicon.items()
    ):
        raise SeriesError(f"{where}: 'voice.lexicon' must map written words to spoken text")
    options = voice.get("options", {})
    if not isinstance(options, dict):
        raise SeriesError(f"{where}: 'voice.options' must be a table")

    data = _table(where, "data", raw.get("data"), {"allow", "release"})
    allow = _strings(where, "data.allow", data.get("allow"))
    for pattern in allow:
        root = pattern.partition(":")[0]
        if root not in DATA_CLASSES or not pattern.partition(":")[2]:
            raise SeriesError(
                f"{where}: 'data.allow' pattern '{pattern}' must start with public:, private: or restricted:"
            )
    releases = []
    raw_release = data.get("release", [])
    if not isinstance(raw_release, list):
        raise SeriesError(f"{where}: 'data.release' must be a list of tables {{ path, reason }}")
    for entry in raw_release:
        if not isinstance(entry, dict) or set(entry) != {"path", "reason"}:
            raise SeriesError(f"{where}: each 'data.release' entry must be a table with 'path' and 'reason'")
        path_text = _text(where, "data.release.path", entry["path"], required=True)
        reason = _text(where, "data.release.reason", entry["reason"], required=True)
        try:
            ref = refs.parse(path_text)
        except refs.RefError as exc:
            raise SeriesError(f"{where}: 'data.release.path': {exc}") from exc
        if ref.root != "restricted" or any(c in path_text for c in "*?["):
            raise SeriesError(
                f"{where}: 'data.release.path' '{path_text}' must name one restricted: file, no wildcards"
            )
        releases.append(Release(str(ref), reason))

    steps = []
    raw_roadmap = raw.get("roadmap", [])
    if not isinstance(raw_roadmap, list):
        raise SeriesError(f"{where}: 'roadmap' must be [[roadmap]] tables with 'id' and 'label'")
    for entry in raw_roadmap:
        if not isinstance(entry, dict) or set(entry) != {"id", "label"}:
            raise SeriesError(f"{where}: each [[roadmap]] entry must have exactly 'id' and 'label'")
        step_id = _text(where, "roadmap.id", entry["id"], required=True)
        if not WORD_RE.match(step_id):
            raise SeriesError(f"{where}: 'roadmap.id' '{step_id}' must be lowercase letters, digits and '-'")
        if any(s.id == step_id for s in steps):
            raise SeriesError(f"{where}: 'roadmap.id' '{step_id}' is listed twice")
        steps.append(Step(step_id, _text(where, "roadmap.label", entry["label"], required=True)))

    return Series(
        name=name,
        path=path,
        title=title,
        audience=audience,
        task=task,
        people=people,
        captions=captions,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        words_per_minute=wpm,
        required=required,
        provider=provider,
        voice=voice_name,
        speed=speed,
        lexicon=dict(lexicon),
        options=dict(options),
        allow=allow,
        release=tuple(releases),
        roadmap=tuple(steps),
    )


def load(ws: Workspace, name: str) -> Series:
    path = folder(ws) / f"{name}.toml"
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SeriesError(f"series '{name}' has no profile {FOLDER.as_posix()}/{name}.toml") from None
    return parse(name, path, source)
