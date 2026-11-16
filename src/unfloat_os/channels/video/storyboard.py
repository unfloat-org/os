"""The storyboard (contract C1): the body of a draft record with `channel: video`, parsed into scenes.

A scene starts at `## <chapter>` or `## <chapter>: <label>`; its id is the slug of the heading. Right after the
heading comes one fenced `toml` block with the scene's properties (`kind` required), then the narration: plain
paragraphs up to the next level-2 heading. `## notes` ends the storyboard. Chapters carry the story (their order
and the required ones are checked); kinds carry the layout (their properties are checked here, from `KINDS`).

The rules that need only the text and the series run in `shape_problems` (part of `uo check`); the rules that
need files (sources, the number check, media, the length estimate) are in `checks`.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from unfloat_os import policy
from unfloat_os.channels.video import refs
from unfloat_os.channels.video.series import CHAPTERS, Series
from unfloat_os.records import slugify

CHAPTER_LABELS = {
    "title": "Title",
    "bottom-line": "Bottom line",
    "goal": "Goal",
    "done": "What was done",
    "results": "Results",
    "limits": "Limits",
    "next": "Next",
    "end": "End",
}
HEADLINE_MAX = 120
POINTS_MAX = 3
ROWS_MAX = 4
ITEMS_MAX = 2
HIGHLIGHTS_MAX = 8
HEADING_RE = re.compile(r"^## (?P<text>.+?)\s*$")
FENCE_OPEN = "```toml"
FENCE = "```"
# Spoken text only: anything a reader would see as Markdown, and links.
MARKUP_LINE_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|#|\|)")
MARKUP_CHARS = set("*_`[]<>|#~{}")
URL_RE = re.compile(r"https?://|www\.", re.I)
# Properties that are not on-screen text: never checked for marks or numbers.
NOT_SCREEN = {"kind", "sources", "numbers_ok", "seconds", "src", "style", "current", "done", "zoom", "highlight"}
PLACEHOLDER = "TODO"


@dataclass(frozen=True)
class Problem:
    scene: str | None
    rule: str
    message: str

    def __str__(self) -> str:
        where = f"scene {self.scene}: " if self.scene else ""
        return f"{where}{self.rule}: {self.message}"


@dataclass(frozen=True)
class Scene:
    id: str
    chapter: str
    label: str
    props: dict
    narration: str  # paragraphs joined by a blank line; empty when the scene is silent
    line: int

    @property
    def kind(self) -> str:
        value = self.props.get("kind")
        return value if isinstance(value, str) else ""

    @property
    def paragraphs(self) -> list[str]:
        return [p for p in self.narration.split("\n\n") if p]

    def words(self) -> int:
        return len(self.narration.split())


@dataclass(frozen=True)
class Storyboard:
    scenes: tuple[Scene, ...]
    notes: str
    problems: tuple[Problem, ...]  # what parsing found; the shape rules add the rest

    def scene(self, scene_id: str) -> Scene | None:
        return next((s for s in self.scenes if s.id == scene_id), None)

    def asks(self) -> list[str]:
        """The rows of every list scene marked `asks = true`, as one line each."""
        found = []
        for scene in self.scenes:
            if scene.kind == "list" and scene.props.get("asks") is True:
                for row in scene.props.get("rows") or []:
                    if isinstance(row, dict):
                        found.append(f"{row.get('left', '')}: {row.get('right', '')}")
                    elif isinstance(row, str):
                        found.append(row)
        return found

    def bottom_line(self) -> str:
        scene = next((s for s in self.scenes if s.chapter == "bottom-line"), None)
        value = scene.props.get("headline") if scene else ""
        return value if isinstance(value, str) else ""


def parse(body: str) -> Storyboard:
    lines = body.split("\n")
    problems: list[Problem] = []
    starts = [i for i, line in enumerate(lines) if HEADING_RE.match(line)]
    notes_at = next((i for i in starts if HEADING_RE.match(lines[i]).group("text").strip().lower() == "notes"), None)
    end = notes_at if notes_at is not None else len(lines)
    starts = [i for i in starts if i < end]
    if any(line.strip() for line in lines[: starts[0] if starts else end]):
        problems.append(Problem(None, "format", "text before the first scene heading"))
    scenes = []
    seen: set[str] = set()
    for n, start in enumerate(starts):
        stop = starts[n + 1] if n + 1 < len(starts) else end
        heading = HEADING_RE.match(lines[start]).group("text")
        chapter, colon, label = (part.strip() for part in heading.partition(":"))
        scene_id = slugify(heading, limit=80)
        if colon and not label:
            problems.append(Problem(scene_id, "heading", f"'## {heading}' has an empty label after ':'"))
        if scene_id in seen:
            problems.append(Problem(scene_id, "scene id", f"'{scene_id}' is used by an earlier scene"))
        seen.add(scene_id)
        props, narration = _scene_body(scene_id, lines[start + 1 : stop], problems)
        scenes.append(Scene(scene_id, chapter, label, props, narration, start + 1))
    notes = "\n".join(lines[notes_at + 1 :]) if notes_at is not None else ""
    return Storyboard(tuple(scenes), notes, tuple(problems))


def _scene_body(scene_id: str, lines: list[str], problems: list[Problem]) -> tuple[dict, str]:
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    props: dict = {}
    if i < len(lines) and lines[i].strip() == FENCE_OPEN:
        close = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == FENCE), None)
        if close is None:
            problems.append(Problem(scene_id, "properties", "the ```toml block is not closed"))
            return {}, ""
        try:
            props = tomllib.loads("\n".join(lines[i + 1 : close]))
        except tomllib.TOMLDecodeError as exc:
            problems.append(Problem(scene_id, "properties", f"invalid TOML: {exc}"))
        i = close + 1
    else:
        problems.append(Problem(scene_id, "properties", "a ```toml block must follow the heading"))
    paragraphs, current = [], []
    for line in lines[i:]:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return props, "\n\n".join(paragraphs)


# properties of each kind -----------------------------------------------------------

Check = Callable[[object], str | None]


def _line(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        return "must be a non-empty one-line string"
    return None


def _headline(value: object) -> str | None:
    if problem := _line(value):
        return problem
    if len(value) > HEADLINE_MAX:
        return f"has {len(value)} characters; at most {HEADLINE_MAX}"
    return None


def _strings(limit: int | None = None, minimum: int = 0) -> Check:
    def check(value: object) -> str | None:
        if not isinstance(value, list) or not all(_line(v) is None for v in value):
            return "must be a list of non-empty one-line strings"
        if len(value) < minimum:
            return f"needs at least {minimum}"
        if limit is not None and len(value) > limit:
            return f"has {len(value)} entries; at most {limit}"
        return None

    return check


def _positive(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return "must be a positive number of seconds"
    return None


def _non_negative(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return "must be a number of seconds, 0 or more"
    return None


def _bool(value: object) -> str | None:
    return None if isinstance(value, bool) else "must be true or false"


def _choice(*words: str) -> Check:
    return lambda value: None if value in words else f"must be one of {', '.join(words)}"


def _box(value: object) -> str | None:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v <= 1 for v in value)
    ):
        return "must be [x, y, w, h] as fractions between 0 and 1"
    x, y, w, h = value
    if w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        return "must be a box [x, y, w, h] inside the frame, with w and h above 0"
    return None


def _boxes(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return "must be a list of boxes [x, y, w, h]"
    if len(value) > HIGHLIGHTS_MAX:
        return f"has {len(value)} boxes; at most {HIGHLIGHTS_MAX}"
    return next((f"box {n}: {p}" for n, box in enumerate(value, 1) if (p := _box(box))), None)


@dataclass(frozen=True)
class Prop:
    check: Check
    required: bool = False


ITEM = {
    "src": Prop(_line, required=True),
    "label": Prop(_line),
    "zoom": Prop(_box),
    "highlight": Prop(_boxes),
    "start": Prop(_non_negative),
    "end": Prop(_positive),
    "loop": Prop(_bool),
}


def _items(value: object) -> str | None:
    if not isinstance(value, list) or not 1 <= len(value) <= ITEMS_MAX:
        return f"must be a list of 1 to {ITEMS_MAX} tables"
    for n, item in enumerate(value, 1):
        if not isinstance(item, dict):
            return f"item {n} must be a table with 'src'"
        if problem := _table_problem(item, ITEM):
            return f"item {n}: {problem}"
        start, end = item.get("start", 0), item.get("end")
        if isinstance(end, int | float) and isinstance(start, int | float) and end <= start:
            return f"item {n}: 'end' must be after 'start'"
    return None


def _table_problem(table: dict, spec: dict[str, Prop]) -> str | None:
    unknown = sorted(set(table) - set(spec))
    if unknown:
        return f"unknown property {', '.join(repr(k) for k in unknown)}"
    for key, prop in spec.items():
        if key not in table:
            if prop.required:
                return f"missing '{key}'"
            continue
        if problem := prop.check(table[key]):
            return f"'{key}' {problem}"
    return None


COMMON = {
    "kind": Prop(_line, required=True),
    "sources": Prop(_strings()),
    "numbers_ok": Prop(_strings()),
    "seconds": Prop(_positive),
}
# One entry per kind: its own properties besides COMMON. A new kind is an entry here and a component in the renderer.
KINDS: dict[str, dict[str, Prop]] = {
    "title": {"title": Prop(_line, required=True), "subtitle": Prop(_line), "meta": Prop(_line)},
    "statement": {"headline": Prop(_headline, required=True), "points": Prop(_strings(POINTS_MAX))},
    "media": {"headline": Prop(_headline, required=True), "items": Prop(_items, required=True), "caption": Prop(_line)},
    "number": {
        "value": Prop(_line, required=True),
        "label": Prop(_line, required=True),
        "denominator": Prop(_line),
        "headline": Prop(_headline),
    },
    "list": {
        "headline": Prop(_headline, required=True),
        "style": Prop(_choice("bullets", "numbered", "pairs"), required=True),
        "rows": Prop(lambda v: None, required=True),  # shape depends on the style: `_rows`
        "left_title": Prop(_line),
        "right_title": Prop(_line),
        "asks": Prop(_bool),
    },
    "roadmap": {
        "headline": Prop(_headline),
        "current": Prop(_line, required=True),
        "done": Prop(_strings(), required=True),
    },
}


def _rows(scene: Scene) -> Iterator[str]:
    rows = scene.props.get("rows")
    style = scene.props.get("style")
    if not isinstance(rows, list) or not 1 <= len(rows) <= ROWS_MAX:
        yield f"'rows' must be a list of 1 to {ROWS_MAX} rows"
        return
    if style == "pairs":
        for n, row in enumerate(rows, 1):
            if not isinstance(row, dict) or set(row) != {"left", "right"} or _line(row["left"]) or _line(row["right"]):
                yield f'row {n} of a \'pairs\' list must be a table {{ left = "...", right = "..." }}'
    else:
        if any(_line(row) for row in rows):
            yield f"rows of a '{style}' list must be non-empty one-line strings"
        for key in ("left_title", "right_title"):
            if key in scene.props:
                yield f"'{key}' is only for style 'pairs'"


def _roadmap(scene: Scene, series: Series) -> Iterator[str]:
    if not series.roadmap:
        yield f"series '{series.name}' has no [[roadmap]] steps"
        return
    ids = ", ".join(s.id for s in series.roadmap)
    current = scene.props.get("current")
    if isinstance(current, str) and series.step(current) is None:
        yield f"'current' '{current}' is not a roadmap step of the series ({ids})"
    done = scene.props.get("done")
    for step in done if isinstance(done, list) else []:
        if isinstance(step, str) and series.step(step) is None:
            yield f"'done' step '{step}' is not a roadmap step of the series ({ids})"
    if isinstance(done, list) and current in done:
        yield f"'current' '{current}' is also listed as done"


def screen_texts(value: object, key: str = "") -> Iterator[str]:
    """Every on-screen string of a scene's properties (headline, points, rows, labels, captions, ...)."""
    if key in NOT_SCREEN:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from screen_texts(item)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from screen_texts(v, k)


def narration_problems(text: str) -> Iterator[str]:
    for paragraph in text.split("\n\n"):
        if MARKUP_LINE_RE.match(paragraph):
            yield f"narration is spoken text, not markup: '{paragraph[:40]}'"
        found = sorted(MARKUP_CHARS & set(paragraph))
        if found:
            yield f"narration contains markup character(s) {' '.join(found)}"
        if URL_RE.search(paragraph):
            yield "narration contains a URL"


def shape_problems(board: Storyboard, series: Series | None) -> list[Problem]:
    """Every rule that needs only the storyboard text and the series: chapters, kinds, properties, marks."""
    problems = list(board.problems)
    order = {chapter: n for n, chapter in enumerate(CHAPTERS)}
    highest = -1
    last = ""
    for scene in board.scenes:
        if scene.chapter not in order:
            problems.append(
                Problem(scene.id, "chapter", f"unknown chapter '{scene.chapter}'; one of {', '.join(CHAPTERS)}")
            )
        elif order[scene.chapter] < highest:
            problems.append(Problem(scene.id, "chapter order", f"'{scene.chapter}' comes after '{last}'"))
        else:
            highest, last = order[scene.chapter], scene.chapter
        problems.extend(Problem(scene.id, rule, message) for rule, message in _scene_problems(scene, series))
    if series is not None:
        present = {s.chapter for s in board.scenes}
        for chapter in series.required:
            if chapter not in present:
                problems.append(
                    Problem(None, "required chapter", f"no '{chapter}' scene (series '{series.name}' requires it)")
                )
    if not board.scenes:
        problems.append(Problem(None, "format", "no scenes: a scene starts at '## <chapter>'"))
    return problems


def _scene_problems(scene: Scene, series: Series | None) -> Iterator[tuple[str, str]]:
    kind = scene.props.get("kind")
    if "kind" not in scene.props:
        yield "kind", f"missing 'kind'; one of {', '.join(KINDS)}"
        return
    if kind not in KINDS:
        yield "kind", f"unknown kind {kind!r}; one of {', '.join(KINDS)}"
        return
    spec = {**COMMON, **KINDS[kind]}
    for key in sorted(set(scene.props) - set(spec)):
        yield "property", f"unknown property '{key}' for kind '{kind}'"
    for key, prop in spec.items():
        if key not in scene.props:
            if prop.required:
                yield "property", f"kind '{kind}' needs '{key}'"
            continue
        if problem := prop.check(scene.props[key]):
            yield "property", f"'{key}' {problem}"
    for source in scene.props.get("sources") or []:
        if isinstance(source, str):
            try:
                refs.parse(source)
            except refs.RefError as exc:
                yield "source", str(exc)
    if kind == "list":
        for message in _rows(scene):
            yield "property", message
    if kind == "roadmap" and series is not None:
        for message in _roadmap(scene, series):
            yield "roadmap", message
    if kind == "media":
        for n, item in enumerate(scene.props.get("items") or [], 1):
            if isinstance(item, dict) and isinstance(item.get("src"), str):
                yield from _media_ref(n, item["src"], series)
    for message in narration_problems(scene.narration):
        yield "narration", message
    for text in [scene.narration, *screen_texts(scene.props)]:
        marks = policy.problems(text, "video")
        if marks:
            yield "marks", f"forbidden {', '.join(map(repr, marks))} in '{text[:50]}'"


def _media_ref(n: int, src: str, series: Series | None) -> Iterator[tuple[str, str]]:
    try:
        ref = refs.parse(src)
    except refs.RefError as exc:
        yield "media", f"item {n}: {exc}"
        return
    if ref.data_class is None:
        yield "media", f"item {n}: '{src}' must be in a data folder (public:, private: or restricted:)"
    elif ref.suffix not in (".png", ".jpg", ".jpeg", ".mp4"):
        yield "media", f"item {n}: '{src}' must be a PNG, JPG or MP4 file"
    elif series is not None and not series.allows(ref):
        yield "media", f"item {n}: '{src}' is outside the allow-list of series '{series.name}' (data.allow)"


def placeholders(board: Storyboard) -> list[Problem]:
    """Scenes that still hold the placeholder text `uo video new` writes."""
    return [
        Problem(s.id, "placeholder", f"'{PLACEHOLDER}' left in the scene")
        for s in board.scenes
        if any(PLACEHOLDER in t for t in [s.narration, *screen_texts(s.props)])
    ]
