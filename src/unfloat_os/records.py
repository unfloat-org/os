"""Records in the private repository: people, tasks and drafts as Markdown notes, decisions and the log.

Layout under the records repository root:

    records/people/P-###-slug.md   records/tasks/T-###-slug.md   records/drafts/DR-###-slug.md
    records/decisions.md           records/log/YYYY-MM.md        records/inbox.md

New IDs are taken under an exclusive lock on `records/.lock`, so parallel sessions sharing one
working tree never receive the same ID.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import functools
import os
import re
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from unfloat_os import frontmatter, schema
from unfloat_os.frontmatter import Meta, Value
from unfloat_os.report import Report
from unfloat_os.schema import Schema, SchemaError

RECORDS = "records"


@dataclass
class Record:
    kind: schema.Kind
    meta: Meta
    path: Path

    @property
    def id(self) -> str:
        return schema.text(self.meta, "id")

    @property
    def title(self) -> str:
        return schema.text(self.meta, self.kind.title_key)


@dataclass(frozen=True)
class Decision:
    id: str
    date: str
    title: str
    line: int


@dataclass(frozen=True)
class Event:
    date: str
    type: str
    refs: tuple[str, ...]
    summary: str
    file: str
    line: int

    def format(self) -> str:
        return f"- {self.date} | {self.type} | {', '.join(self.refs) or '-'} | {self.summary}"


@dataclass
class Records:
    """A loaded snapshot of every record, with the problems found while reading."""

    schema: Schema
    by_kind: dict[str, dict[str, Record]] = field(init=False)
    decisions: dict[str, Decision] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    report: Report = field(default_factory=Report)

    def __post_init__(self) -> None:
        self.by_kind = {name: {} for name in self.schema.kinds}

    @property
    def people(self) -> dict[str, Record]:
        return self.by_kind["person"]

    @property
    def tasks(self) -> dict[str, Record]:
        return self.by_kind["task"]

    @property
    def drafts(self) -> dict[str, Record]:
        return self.by_kind["draft"]

    def record(self, rid: str) -> Record | None:
        kind = self.schema.by_prefix.get(rid.split("-")[0])
        return self.by_kind[kind.name].get(rid) if kind else None

    def exists(self, rid: str) -> bool:
        return rid in self.decisions or self.record(rid) is not None

    def meta(self, rid: str) -> Meta | None:
        found = self.record(rid)
        return found.meta if found else None


def to_date(value: object) -> dt.date | None:
    if not isinstance(value, str) or not schema.DATE_RE.match(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def slugify(title: str, limit: int = 50) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_title.lower())
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > limit:
            break
        slug = candidate
    return slug or "untitled"


class StoreError(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.dir = root / RECORDS

    @functools.cached_property
    def schema(self) -> Schema:
        """The kinds and vocabularies of `config/schema.toml`, read on first use."""
        try:
            return schema.load(self.root)
        except SchemaError as error:
            raise StoreError(str(error)) from error

    def folder(self, kind: schema.Kind) -> Path:
        return self.dir / kind.folder

    @property
    def decisions_path(self) -> Path:
        return self.dir / "decisions.md"

    @property
    def log_dir(self) -> Path:
        return self.dir / "log"

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive across processes on this machine; held while an ID is chosen and its file created."""
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / ".lock", os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    # reading ---------------------------------------------------------------

    def path_of(self, rid: str) -> Path:
        kind = self.schema.by_prefix.get(rid.split("-")[0])
        if kind is None or not schema.ID_RE.match(rid):
            raise StoreError(f"'{rid}' is not a record ID")
        files = sorted(self.folder(kind).glob(f"{rid}-*.md"))
        if len(files) != 1:
            raise StoreError(
                f"{rid}: expected one file in {self.folder(kind).relative_to(self.root)}, found {len(files)}"
            )
        return files[0]

    def load(self) -> Records:
        records = Records(self.schema)
        report = records.report
        if not self.dir.is_dir():
            report.error(self.dir, "records folder not found")
            return records
        for kind in self.schema.kinds.values():
            for path in sorted(self.folder(kind).glob("*.md")):
                where = path.relative_to(self.root)
                try:
                    meta = frontmatter.parse(path.read_text(encoding="utf-8"))
                except frontmatter.FrontmatterError as error:
                    report.error(where, str(error))
                    continue
                rid = schema.text(meta, "id")
                if not rid:
                    report.error(where, "missing 'id'")
                    continue
                if not re.fullmatch(rf"{kind.prefix}-\d{{3}}", rid):
                    report.error(where, f"id '{rid}' must look like {kind.prefix}-001")
                if not path.name.startswith(f"{rid}-"):
                    report.error(where, f"file name must start with '{rid}-'")
                if rid in records.by_kind[kind.name]:
                    report.error(where, f"duplicate id {rid}")
                records.by_kind[kind.name][rid] = Record(kind, meta, path)
        self._load_decisions(records)
        self._load_log(records)
        return records

    def _load_decisions(self, records: Records) -> None:
        path = self.decisions_path
        where_file = path.relative_to(self.root)
        if not path.exists():
            records.report.error(where_file, "missing")
            return
        previous = 0
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.startswith("## "):
                continue
            where = f"{where_file}:{number}"
            match = schema.DECISION_RE.match(line)
            if not match:
                records.report.error(where, "heading must be '## D-### | YYYY-MM-DD | Title'")
                continue
            did, date, title = match.groups()
            if did in records.decisions:
                records.report.error(where, f"duplicate {did}")
            if int(did[2:]) <= previous:
                records.report.error(where, f"{did} is out of order")
            previous = max(previous, int(did[2:]))
            if to_date(date) is None:
                records.report.error(where, f"invalid date {date}")
            records.decisions[did] = Decision(did, date, title, number)

    def _load_log(self, records: Records) -> None:
        for path in sorted(self.log_dir.glob("*.md")):
            rel = path.relative_to(self.root)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.startswith("- "):
                    continue
                where = f"{rel}:{number}"
                match = schema.LOG_RE.match(line)
                if not match:
                    records.report.error(where, "expected '- YYYY-MM-DD | type | refs | summary'")
                    continue
                date, kind, refs, summary = match.groups()
                if to_date(date) is None:
                    records.report.error(where, f"invalid date {date}")
                elif path.stem != date[:7]:
                    records.report.warning(where, f"dated {date} but filed in {path.name}")
                if kind not in self.schema.log_types:
                    records.report.error(where, f"unknown type '{kind}'")
                ids = () if refs.strip() == "-" else tuple(r.strip() for r in refs.split(",") if r.strip())
                for rid in ids:
                    if not schema.ID_RE.match(rid):
                        records.report.error(where, f"'{rid}' is not an ID")
                records.events.append(Event(date, kind, ids, summary.strip(), path.name, number))

    # writing ---------------------------------------------------------------

    def _upstream_highest(self, prefix: str) -> int:
        """Highest number already pushed by any clone (as of the last fetch); 0 without an upstream."""
        from unfloat_os import ids  # ids imports records

        return ids.highest_upstream(self.root, prefix)

    def next_id(self, kind: schema.Kind) -> str:
        """One above every number here and every number already pushed, so clones rarely collide."""
        numbers = [
            int(m.group(1))
            for p in self.folder(kind).glob(f"{kind.prefix}-*.md")
            if (m := re.match(rf"{kind.prefix}-(\d{{3}})-", p.name))
        ]
        highest = max(max(numbers, default=0), self._upstream_highest(kind.prefix))
        return f"{kind.prefix}-{highest + 1:03d}"

    def _refuse(self, kind: schema.Kind, subject: str, values: Mapping[str, Value]) -> None:
        """Reject fields outside the kind and words outside a vocabulary, before anything is written."""
        unknown = set(values) - {f.name for f in kind.fields}
        if unknown:
            raise StoreError(f"unknown field(s) for {kind.name}: {', '.join(sorted(unknown))}")
        problems = [p for f in kind.fields if values.get(f.name) for p in f.vocabulary_problems(values[f.name])]
        if problems:
            raise StoreError(f"{subject}: {'; '.join(problems)}")

    def create(self, kind_name: str, title: str, values: Mapping[str, Value], body: str = "") -> Record:
        """Create a record with the next free ID; refuses values outside the schema or missing required ones."""
        kind = self.schema.kinds.get(kind_name)
        if kind is None:
            raise StoreError(f"unknown kind '{kind_name}'; one of {', '.join(self.schema.kinds)}")
        self._refuse(kind, kind_name, values)
        if not title.strip():
            raise StoreError(f"{kind.name} needs a {kind.title_key}")
        with self.lock():
            rid = self.next_id(kind)
            meta: Meta = {}
            for f in kind.fields:
                meta[f.name] = values.get(f.name, f.empty())
            meta["id"] = rid
            meta[kind.title_key] = title.strip()
            missing = [f.name for f in kind.fields if f.required and not meta.get(f.name)]
            if missing:
                raise StoreError(f"missing required field(s): {', '.join(missing)}")
            path = self.folder(kind) / f"{rid}-{slugify(title)}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(frontmatter.render(meta, body))
        return Record(kind, meta, path)

    def update(self, rid: str, values: Mapping[str, Value]) -> Record:
        path = self.path_of(rid)
        kind = self.schema.by_prefix[rid.split("-")[0]]
        self._refuse(kind, rid, values)
        if "id" in values:
            raise StoreError("IDs never change")
        text = frontmatter.update(path.read_text(encoding="utf-8"), values)
        path.write_text(text, encoding="utf-8")
        return Record(kind, frontmatter.parse(text), path)

    def add_decision(self, title: str, date: dt.date, bullets: Mapping[str, str]) -> str:
        """Append `## D-### | date | title` with Decision/Why/Replaces/Source bullets under the lock."""
        if "\n" in title or "|" in title:
            raise StoreError("a decision title is one line without '|'")
        with self.lock():
            text = self.decisions_path.read_text(encoding="utf-8")
            numbers = [int(m.group(1)) for m in re.finditer(r"^## D-(\d{3}) \|", text, re.M)]
            highest = max(max(numbers, default=0), self._upstream_highest("D"))
            did = f"D-{highest + 1:03d}"
            lines = [f"## {did} | {date.isoformat()} | {title.strip()}", ""]
            lines += [f"- {key}: {value}" for key, value in bullets.items() if value]
            self.decisions_path.write_text(text.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return did

    def append_event(self, records: Records, date: dt.date, kind: str, refs: list[str], summary: str) -> Event:
        """Append one log line after validating it against the loaded records."""
        if kind not in self.schema.log_types:
            raise StoreError(f"unknown log type '{kind}'; one of {', '.join(self.schema.log_types)}")
        summary = summary.strip()
        if not summary or "\n" in summary:
            raise StoreError("the summary is one non-empty line")
        for rid in refs:
            if not schema.ID_RE.match(rid):
                raise StoreError(f"'{rid}' is not an ID")
            if not records.exists(rid):
                raise StoreError(f"{rid} does not exist")
        month = date.strftime("%Y-%m")
        path = self.log_dir / f"{month}.md"
        event = Event(date.isoformat(), kind, tuple(refs), summary, path.name, 0)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
        with os.fdopen(fd, "r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            current = handle.read()
            prefix = f"# Log {month}\n\n" if not current else ("" if current.endswith("\n") else "\n")
            handle.write(prefix + event.format() + "\n")
        return event
