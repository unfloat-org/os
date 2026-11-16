"""The checks of video drafts and series profiles.

`check_records` runs inside `uo check`: every series profile, and every video draft not `sent` or `dropped`
for shape and marks (as email drafts are), plus the uniqueness of update numbers in a series. `prepare` runs
for `uo video check` and before every build: it adds what needs files (sources exist, the number check, media
exist and are allowed, the length estimate) and works out the data class of the output (contract C5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from unfloat_os import frontmatter, policy, schema
from unfloat_os.channels import ChannelError
from unfloat_os.channels.video import media, refs, storyboard, timing
from unfloat_os.channels.video.series import FOLDER, Series, SeriesError, load, names
from unfloat_os.channels.video.storyboard import Problem, Scene, Storyboard
from unfloat_os.records import Record, Records, Store
from unfloat_os.report import Report
from unfloat_os.workspace import Workspace

DONE = ("sent", "dropped")
# A numeral to check: two or more digits, a decimal point, or a percent sign (contract C1).
NUMERAL_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+(?!\d)|\d+)(?:\.(\d+))?(\s?%|\s+percent\b)?", re.I)


def where(record: Record) -> str:
    return "/".join(record.path.parts[-3:])


def series_where(name: str) -> str:
    return f"{FOLDER.as_posix()}/{name}.toml"


def board_of(record: Record) -> Storyboard:
    return storyboard.parse(frontmatter.body(record.path.read_text(encoding="utf-8")))


# uo check -------------------------------------------------------------------------


def load_all(ws: Workspace, records: Records, report: Report) -> dict[str, Series]:
    """Every series profile that loads; problems of the others (and of task, people, on-screen text) go to `report`."""
    found = {}
    for name in names(ws):
        try:
            loaded = load(ws, name)
        except SeriesError as exc:
            report.error(series_where(name), str(exc).removeprefix(f"{series_where(name)}: "))
            continue
        if loaded.task and not records.exists(loaded.task):
            report.error(series_where(name), f"'task' {loaded.task} does not exist")
        for person in loaded.people:
            if not records.exists(person):
                report.error(series_where(name), f"'people': {person} does not exist")
        for key, text in [("title", loaded.title), *(("roadmap.label", s.label) for s in loaded.roadmap)]:
            marks = policy.problems(text, "video")
            if marks:
                report.error(series_where(name), f"'{key}' contains {', '.join(map(repr, marks))}")
        found[name] = loaded
    return found


def check_records(ws: Workspace, records: Records) -> Report:
    report = Report()
    known = load_all(ws, records, report)
    numbers: dict[tuple[str, str], str] = {}
    for draft_id, record in sorted(records.drafts.items()):
        meta = record.meta
        if meta.get("channel") != "video":
            continue
        name, number, status = schema.text(meta, "series"), schema.text(meta, "number"), schema.text(meta, "status")
        if status != "dropped" and name and number:
            first = numbers.setdefault((name, number), draft_id)
            if first != draft_id:
                report.error(where(record), f"number {number} of series '{name}' is already used by {first}")
        if status in DONE:
            continue
        series = known.get(name)
        if name and series is None and not (ws.ops / FOLDER / f"{name}.toml").is_file():
            report.error(where(record), f"series '{name}' has no profile {series_where(name)}")
        board = board_of(record)
        for problem in storyboard.shape_problems(board, series):
            report.error(where(record), str(problem))
        for problem in storyboard.placeholders(board):
            report.warning(where(record), str(problem))
    return report


# uo video check -------------------------------------------------------------------


@dataclass(frozen=True)
class MediaUse:
    scene: str
    n: int
    ref: refs.Ref
    path: Path
    info: media.Media | None
    item: dict

    @property
    def build_name(self) -> str:
        """The copy's name in the build folder: scene-based, never the source file name (C3, C5)."""
        suffix = ".jpg" if self.ref.suffix == ".jpeg" else self.ref.suffix
        return f"{self.scene}-{self.n}{suffix}"

    def clip_floor(self) -> float | None:
        start = float(self.item.get("start", 0))
        if isinstance(self.item.get("end"), int | float):
            return float(self.item["end"]) - start
        if self.info is not None and self.info.seconds is not None:
            return max(self.info.seconds - start, 0.0)
        return None


@dataclass(frozen=True)
class Plan:
    """A checked draft: what `uo video build` works from."""

    record: Record
    series: Series
    board: Storyboard
    report: Report
    media: tuple[MediaUse, ...] = ()
    sources: tuple[tuple[str, Path], ...] = ()
    restricted_by: tuple[MediaUse, ...] = ()
    estimates: dict[str, float] = field(default_factory=dict)

    @property
    def data_class(self) -> str:
        return "restricted" if self.restricted_by else "private"

    @property
    def estimate(self) -> float:
        return sum(self.estimates.values())

    def media_of(self, scene_id: str) -> list[MediaUse]:
        return [m for m in self.media if m.scene == scene_id]


def numerals(text: str) -> list[tuple[str, str]]:
    """(as written, normalized) for every numeral the number check applies to; `94 percent` normalizes to `94%`."""
    found = []
    for match in NUMERAL_RE.finditer(text):
        whole, frac, percent = match.group(1).replace(",", ""), match.group(2), match.group(3)
        if len(whole + (frac or "")) < 2 and not frac and not percent:
            continue
        found.append((match.group(0).strip(), whole + (f".{frac}" if frac else "") + ("%" if percent else "")))
    return found


def _in_sources(value: str, source_values: set[str]) -> bool:
    if value.endswith("%"):
        return value in source_values
    return value in source_values or f"{value}%" in source_values


def number_problems(scene: Scene, texts: dict[str, str]) -> list[Problem]:
    """Numerals of the scene's narration and on-screen text that none of its readable sources contain."""
    exempt = {norm for entry in scene.props.get("numbers_ok") or [] for _, norm in numerals(str(entry))}
    exempt |= {value.removesuffix("%") for value in exempt}  # `94%` listed also exempts `94 percent`, and back
    in_sources = {norm for text in texts.values() for _, norm in numerals(text)}
    problems = []
    seen = set()
    for text in [scene.narration, *storyboard.screen_texts(scene.props)]:
        for written, value in numerals(text):
            if value in seen or value.removesuffix("%") in exempt:
                continue
            seen.add(value)
            if not _in_sources(value, in_sources):
                where = "occurs in none of its sources" if texts else "has no source: the scene cites none"
                problems.append(Problem(scene.id, "number", f"'{written}' {where}; cite one or list it in numbers_ok"))
    return problems


def find_draft(records: Records, draft_id: str) -> Record:
    record = records.drafts.get(draft_id)
    if record is None:
        raise ChannelError(f"no draft {draft_id} in records/drafts")
    if record.meta.get("channel") != "video":
        raise ChannelError(f"{draft_id} has channel {schema.text(record.meta, 'channel')!r}, not 'video'")
    return record


def prepare(ws: Workspace, draft_id: str, records: Records | None = None) -> Plan:
    """Every check of one video draft, including those that read files; errors go to the plan's report."""
    records = records if records is not None else Store(ws.ops).load()
    record = find_draft(records, draft_id)
    name = schema.text(record.meta, "series")
    if not name:
        raise ChannelError(f"{draft_id} has no 'series'")
    try:
        series = load(ws, name)
    except SeriesError as exc:
        raise ChannelError(str(exc)) from exc
    report = Report()
    here = where(record)
    for rule in schema.DRAFT.rules:
        if message := rule.test(record.meta, records):
            report.add(rule.level, here, message)
    records_report = check_records(ws, records)
    report.issues.extend(
        i for i in records_report.issues if i.where in (here, series_where(name)) and i.level == "error"
    )
    board = board_of(record)
    for problem in storyboard.placeholders(board):
        report.error(here, str(problem))

    uses, sources, restricted = [], [], []
    estimates = {}
    clips_unknown = False
    for scene in board.scenes:
        texts = {}
        for source in scene.props.get("sources") or []:
            try:
                ref = refs.parse(source)
                path = refs.resolve(ws, ref)
            except (refs.RefError, TypeError):
                continue  # reported by the shape rules
            if not path.is_file():
                report.error(here, str(Problem(scene.id, "source", f"'{source}' does not exist")))
                continue
            try:
                texts[str(ref)] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                report.error(here, str(Problem(scene.id, "source", f"'{source}' is not a text file")))
                continue
            if all(p != path for _, p in sources):
                sources.append((str(ref), path))
        for problem in number_problems(scene, texts):
            report.error(here, str(problem))

        items = scene.props.get("items") if scene.kind == "media" else None
        for n, item in enumerate(items if isinstance(items, list) else [], 1):
            if not isinstance(item, dict) or not isinstance(item.get("src"), str):
                continue
            try:
                ref = refs.parse(item["src"])
                path = refs.resolve(ws, ref)
            except refs.RefError as exc:
                report.error(here, str(Problem(scene.id, "media", f"item {n}: {exc}")))
                continue
            if ref.data_class is None or not path.is_file():
                if ref.data_class is not None:
                    report.error(here, str(Problem(scene.id, "media", f"item {n}: '{ref}' does not exist")))
                continue
            info = None
            if ref.suffix in media.IMAGE_SUFFIXES or media.has_ffprobe():
                try:
                    info = media.probe(path)
                except ChannelError as exc:
                    report.error(here, str(Problem(scene.id, "media", f"item {n}: {exc}")))
            else:
                clips_unknown = True
            use = MediaUse(scene.id, n, ref, path, info, item)
            uses.append(use)
            if ref.data_class == "restricted" and series.released(ref) is None:
                restricted.append(use)

        speech = timing.spoken_estimate(scene.words(), len(scene.paragraphs), series.words_per_minute, series.speed)
        floor = scene.props.get("seconds") if isinstance(scene.props.get("seconds"), int | float) else None
        clips = [c for u in uses if u.scene == scene.id and (c := u.clip_floor()) is not None]
        estimates[scene.id] = timing.scene_seconds(speech, floor, clips)

    if clips_unknown:
        report.warning(here, "ffprobe not found: clip lengths are left out of the length estimate")
    total = sum(estimates.values())
    if board.scenes and not series.min_seconds <= total <= series.max_seconds:
        report.error(
            here,
            str(
                Problem(
                    None,
                    "length",
                    f"estimated {total:.0f} s is outside {series.min_seconds:.0f} to {series.max_seconds:.0f} s "
                    f"(series '{series.name}')",
                )
            ),
        )
    return Plan(record, series, board, report, tuple(uses), tuple(sources), tuple(restricted), estimates)
