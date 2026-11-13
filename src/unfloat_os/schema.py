"""The record schema: kinds, fields, statuses and rules in code; the project's vocabularies in the records repository.

`uo check` enforces it and `uo schema` writes the field tables into the records repository's
SCHEMA.md, so the prose documentation cannot drift from what is checked.

Two sources, one per kind of rule:
- This module holds what drives logic: kinds, fields, statuses and every rule over them. Those change with
  the code, in review, together with their tests.
- `config/schema.toml` of the records repository holds what a project edits as it grows and that logic only
  reads: the ordered milestones, the task owners with the names and tracker headings they render as, and
  the log types. A public repository cannot carry them, and a milestone renamed there is one config edit
  plus `uo migrate rename-value`, with `uo check` failing until both are done.

`load()` reads the config and builds the kinds that use it. The study kind (`STUDY`) never depends on
the config, so a public repository checks its analyses without the records repository.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from unfloat_os.frontmatter import Meta

CONFIG = Path("config") / "schema.toml"  # relative to the records repository

PERSON_KIND = ("person", "group", "org")
PERSON_STATUS = ("active", "call-booked", "replied", "contacted", "to-contact", "paused", "declined")
CONTACTED_STATUS = ("contacted", "replied", "call-booked", "active")
TASK_STATUS = ("doing", "todo", "waiting", "done", "dropped")
OPEN_STATUS = ("doing", "todo", "waiting")
CLOSED_STATUS = ("done", "dropped")
DRAFT_STATUS = ("draft", "approved", "sent", "dropped")
STUDY_STATUS = ("planned", "preregistered", "result", "reviewed", "published", "dropped")
KIND_NAMES = ("person", "task", "draft")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^(P|T|D|DR)-\d{3}$")
DECISION_RE = re.compile(r"^## (D-\d{3}) \| (\d{4}-\d{2}-\d{2}) \| (.+)$")
LOG_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) \| ([a-z-]+) \| ([^|]+?) \| (.+)$")
# What a milestone or an owner key may look like: one frontmatter word that survives `[a, b]` lists.
WORD_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# What a log type may look like: the type column of LOG_RE, without stray dashes.
LOG_TYPE_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*$")


class SchemaError(Exception):
    """`config/schema.toml` is missing or does not describe a usable vocabulary."""


class Index(Protocol):
    def exists(self, rid: str) -> bool: ...

    def meta(self, rid: str) -> Meta | None: ...


def text(meta: Meta, key: str) -> str:
    value = meta.get(key) or ""
    return value if isinstance(value, str) else ", ".join(value)


def items(meta: Meta, key: str) -> list[str]:
    value = meta.get(key)
    if isinstance(value, list):
        return value
    return [value] if value else []


@dataclass(frozen=True)
class Field:
    """One frontmatter key.

    type: text | list | date | vocab | vocab-list | ref | ref-list | ref-or-text
    refs: the ID prefixes a ref may point to; vocab: allowed words (for `ref`, words allowed instead of an ID).
    """

    name: str
    doc: str
    type: str = "text"
    required: bool = False
    vocab: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()

    def describe(self) -> str:
        if self.type == "date":
            return "`YYYY-MM-DD` or empty"
        if self.type in ("vocab", "vocab-list"):
            words = ", ".join(f"`{w}`" for w in self.vocab)
            return f"One of {words}" if self.type == "vocab" else f"Subset of {words}"
        if self.type == "ref":
            alternatives = [f"`{w}`" for w in self.vocab] + [f"`{p}-###`" for p in self.refs]
            return " or ".join(alternatives) + (" or empty" if not self.required else "")
        if self.type == "ref-list":
            return "List of " + " or ".join(f"`{p}-###`" for p in self.refs)
        if self.type == "ref-or-text":
            return " or ".join(f"`{p}-###`" for p in self.refs) + " or free text"
        return self.doc

    def checked(self) -> str:
        parts = ["required"] if self.required else []
        if self.type == "date":
            parts.append("date")
        if self.type in ("vocab", "vocab-list"):
            parts.append("vocabulary")
        if self.type in ("ref", "ref-list", "ref-or-text"):
            parts.append("IDs must exist")
        return "; ".join(parts)

    def empty(self) -> str | list[str]:
        """The value of an unset field: `[]` for list types, an empty string otherwise."""
        return [] if self.type in ("list", "vocab-list", "ref-list") else ""

    def vocabulary_problems(self, value: str | list[str]) -> list[str]:
        """Why `value` is outside the vocabulary of a `vocab` or `vocab-list` field; other types: nothing."""
        words = ", ".join(self.vocab)
        if self.type == "vocab":
            return [] if value in self.vocab else [f"{self.name} '{value}' not in {words}"]
        if self.type == "vocab-list":
            return [
                f"{self.name} '{w}' not in {words}" for w in items({self.name: value}, self.name) if w not in self.vocab
            ]
        return []


@dataclass(frozen=True)
class Rule:
    doc: str
    level: str  # "error" or "warning"
    test: Callable[[Meta, Index], str | None]


@dataclass(frozen=True)
class Kind:
    name: str
    prefix: str
    folder: str
    title_key: str
    fields: tuple[Field, ...]
    rules: tuple[Rule, ...] = ()
    body: str = ""

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)


def _waiting_needs_reason(meta: Meta, _: Index) -> str | None:
    if meta.get("status") == "waiting" and not meta.get("waiting_on"):
        return "status 'waiting' needs 'waiting_on'"
    return None


def _closed_needs_date(meta: Meta, _: Index) -> str | None:
    if meta.get("status") in CLOSED_STATUS and not meta.get("closed"):
        return f"status '{meta.get('status')}' needs a 'closed' date"
    return None


def _open_without_date(meta: Meta, _: Index) -> str | None:
    if meta.get("status") in OPEN_STATUS and meta.get("closed"):
        return "open task has a 'closed' date"
    return None


def _contact_needs_date(meta: Meta, _: Index) -> str | None:
    if meta.get("status") in CONTACTED_STATUS and not meta.get("last_contact"):
        return "status implies contact but last_contact is empty"
    return None


def _next_task_open(meta: Meta, index: Index) -> str | None:
    task = text(meta, "next_task")
    found = index.meta(task) if task else None
    if found and found.get("status") in CLOSED_STATUS:
        return f"next_task {task} is already closed"
    return None


def _person(milestones: tuple[str, ...]) -> Kind:
    return Kind(
        name="person",
        prefix="P",
        folder="people",
        title_key="name",
        fields=(
            Field("id", "`P-###`", required=True),
            Field("name", "Name, or the group's members", required=True),
            Field("kind", "", "vocab", required=True, vocab=PERSON_KIND),
            Field("org", "Free text"),
            Field("role", "Free text"),
            Field("location", "Free text"),
            Field("via", "", "ref-or-text", refs=("P",)),
            Field("milestones", "", "vocab-list", required=True, vocab=milestones),
            Field("tags", "Free list", "list"),
            Field("status", "", "vocab", required=True, vocab=PERSON_STATUS),
            Field("last_contact", "", "date"),
            Field("next_task", "", "ref", refs=("T",)),
        ),
        rules=(
            Rule("`next_task` is still open", "warning", _next_task_open),
            Rule("a contacted status has a `last_contact` date", "warning", _contact_needs_date),
        ),
        body="`## Who` (sourced facts), `## Our ask`, `## History` (short; the log has the detail), `## Notes`.",
    )


def _task(milestones: tuple[str, ...], owners: tuple[str, ...]) -> Kind:
    return Kind(
        name="task",
        prefix="T",
        folder="tasks",
        title_key="title",
        fields=(
            Field("id", "`T-###`", required=True),
            Field("title", "Short imperative", required=True),
            Field("status", "", "vocab", required=True, vocab=TASK_STATUS),
            Field("owner", "", "ref", required=True, vocab=owners, refs=("P",)),
            Field("milestone", "", "vocab", required=True, vocab=milestones),
            Field("due", "", "date"),
            Field("people", "", "ref-list", refs=("P",)),
            Field("waiting_on", "", "ref-or-text", refs=("T", "P", "D", "DR")),
            Field("done_when", "Observable end state", required=True),
            Field("created", "", "date", required=True),
            Field("closed", "", "date"),
        ),
        rules=(
            Rule("`waiting_on` is required when status is `waiting`", "error", _waiting_needs_reason),
            Rule("`closed` is required when status is `done` or `dropped`", "error", _closed_needs_date),
            Rule("an open task has no `closed` date", "warning", _open_without_date),
        ),
        body="Notes, links to drafts and files.",
    )


# Nothing in a draft comes from the config, so the kind is a constant (the Gmail adapter needs it without one).
DRAFT = Kind(
    name="draft",
    prefix="DR",
    folder="drafts",
    title_key="title",
    fields=(
        Field("id", "`DR-###`", required=True),
        Field("title", "What it is", required=True),
        Field("status", "", "vocab", required=True, vocab=DRAFT_STATUS),
        Field("channel", "`email`, `reddit`, `linkedin`, `discord`, `document`, ...", required=True),
        Field("task", "", "ref", refs=("T",)),
        Field("people", "", "ref-list", refs=("P",)),
        Field("created", "", "date", required=True),
        Field("reply_to", "Sent email draft this one answers (optional)", "ref", refs=("DR",)),
        Field("reply_thread", "Gmail thread ID of a thread someone else started (optional; instead of `reply_to`)"),
        Field("attachments", "Files `uo gmail push` attaches, relative to the private data folder (optional)", "list"),
    ),
    body="The text, ready to copy. Only Daniil sets `approved` or `sent`.",
)

# A pure code constant: public repositories check their analyses without the records repository.
STUDY = Kind(
    name="study",
    prefix="",
    folder="analysis",
    title_key="title",
    fields=(
        Field("id", "Short code, for example `A2b`", required=True),
        Field("title", "The question", required=True),
        Field("status", "", "vocab", required=True, vocab=STUDY_STATUS),
        Field("task", "`T-###` of the project task, as plain text"),
        Field("prereg", "File committed before any outcome was read, or `none` for descriptive work", required=True),
        Field("result", "Result file, once there is one"),
        Field("review", "Independent review file, once there is one"),
        Field("verdict", "The pre-registered verdict label, unchanged"),
    ),
    body="One paragraph: what was asked and where the caveats are.",
)


def markdown_table(kind: Kind) -> str:
    rows = ["| Key | Value | Checked |", "| --- | --- | --- |"]
    for f in kind.fields:
        rows.append(f"| `{f.name}` | {f.describe()} | {f.checked()} |")
    for rule in kind.rules:
        rows.append(f"| (rule) | {rule.doc} | {rule.level} |")
    return "\n".join(rows)


# the loaded schema ---------------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    """A task owner that is not a person record: the name it shows as and the tracker section for its tasks."""

    id: str
    name: str
    heading: str


@dataclass(frozen=True)
class Schema:
    """The kinds of the records repository with its vocabularies applied, and the SCHEMA.md blocks they generate."""

    kinds: dict[str, Kind]
    by_prefix: dict[str, Kind]
    milestones: tuple[str, ...]
    owners: tuple[Owner, ...]
    log_types: tuple[str, ...]
    blocks: dict[str, str]

    def owner(self, owner_id: str) -> Owner | None:
        return next((o for o in self.owners if o.id == owner_id), None)


def _words(raw: dict, key: str, pattern: re.Pattern[str], shape: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(word, str) for word in value):
        raise SchemaError(f"{CONFIG.as_posix()}: '{key}' must be a non-empty list of words")
    for word in value:
        if not pattern.match(word):
            raise SchemaError(f"{CONFIG.as_posix()}: {key} '{word}' must be {shape}")
    repeated = sorted({word for word in value if value.count(word) > 1})
    if repeated:
        raise SchemaError(f"{CONFIG.as_posix()}: {key} lists {', '.join(repeated)} more than once")
    return tuple(value)


def _owners(raw: dict) -> tuple[Owner, ...]:
    table = raw.get("owners")
    where = CONFIG.as_posix()
    if not isinstance(table, dict) or not table:
        raise SchemaError(f"{where}: 'owners' must be a table with one [owners.<id>] entry per task owner")
    owners = []
    for owner_id, entry in table.items():
        if not WORD_RE.match(owner_id):
            raise SchemaError(f"{where}: owner '{owner_id}' must be lowercase letters, digits and '-'")
        if not isinstance(entry, dict):
            raise SchemaError(f"{where}: owners.{owner_id} must be a table with 'name' and 'heading'")
        unknown = set(entry) - {"name", "heading"}
        if unknown:
            raise SchemaError(f"{where}: owners.{owner_id} has unknown key(s): {', '.join(sorted(unknown))}")
        for key in ("name", "heading"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip() or "\n" in value or "|" in value:
                raise SchemaError(f"{where}: owners.{owner_id}.{key} must be one line of text without '|'")
        owners.append(Owner(owner_id, entry["name"].strip(), entry["heading"].strip()))
    return tuple(owners)


def parse(source: str) -> Schema:
    """Build the schema from the text of `config/schema.toml`."""
    where = CONFIG.as_posix()
    try:
        raw = tomllib.loads(source)
    except tomllib.TOMLDecodeError as error:
        raise SchemaError(f"{where}: invalid TOML: {error}") from error
    unknown = set(raw) - {"milestones", "log_types", "owners"}
    if unknown:
        raise SchemaError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    milestones = _words(raw, "milestones", WORD_RE, "lowercase letters, digits and '-', starting with a letter")
    log_types = _words(raw, "log_types", LOG_TYPE_RE, "lowercase words joined by single '-'")
    owners = _owners(raw)
    kinds = {kind.name: kind for kind in (_person(milestones), _task(milestones, tuple(o.id for o in owners)), DRAFT)}
    blocks = {kind.name: markdown_table(kind) for kind in (*kinds.values(), STUDY)}
    blocks["log-types"] = ", ".join(f"`{t}`" for t in log_types)
    return Schema(
        kinds=kinds,
        by_prefix={kind.prefix: kind for kind in kinds.values()},
        milestones=milestones,
        owners=owners,
        log_types=log_types,
        blocks=blocks,
    )


def load(ops_root: Path) -> Schema:
    """Read `config/schema.toml` of the records repository at `ops_root`; raises SchemaError when unusable."""
    path = ops_root / CONFIG
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SchemaError(f"{path} not found: milestones, task owners and log types are defined there") from None
    except (OSError, UnicodeDecodeError) as error:
        raise SchemaError(f"cannot read {path}: {error}") from error
    return parse(source)
