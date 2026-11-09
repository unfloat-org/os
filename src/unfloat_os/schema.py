"""The record schema: the single source for kinds, fields, vocabularies and rules.

`uo check` enforces it and `uo schema` writes the field tables into the records repository's
SCHEMA.md, so the prose documentation cannot drift from what is checked.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from unfloat_os.frontmatter import Meta

MILESTONES = (
    "map",
    "composition",
    "clearance",
    "rebuild",
    "animal",
    "measurement",
    "community",
    "funding",
    "ops",
    "parked",
)
PERSON_KIND = ("person", "group", "org")
PERSON_STATUS = ("active", "call-booked", "replied", "contacted", "to-contact", "paused", "declined")
CONTACTED_STATUS = ("contacted", "replied", "call-booked", "active")
TASK_STATUS = ("doing", "todo", "waiting", "done", "dropped")
OPEN_STATUS = ("doing", "todo", "waiting")
CLOSED_STATUS = ("done", "dropped")
TASK_OWNERS = ("founder", "claude")
DRAFT_STATUS = ("draft", "approved", "sent", "dropped")
LOG_TYPES = ("email-in", "email-out", "call", "meeting", "post", "comment", "dm", "result", "decision", "note", "task")
STUDY_STATUS = ("planned", "preregistered", "result", "reviewed", "published", "dropped")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^(P|T|D|DR)-\d{3}$")
DECISION_RE = re.compile(r"^## (D-\d{3}) \| (\d{4}-\d{2}-\d{2}) \| (.+)$")
LOG_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) \| ([a-z-]+) \| ([^|]+?) \| (.+)$")


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


PERSON = Kind(
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
        Field("milestones", "", "vocab-list", required=True, vocab=MILESTONES),
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

TASK = Kind(
    name="task",
    prefix="T",
    folder="tasks",
    title_key="title",
    fields=(
        Field("id", "`T-###`", required=True),
        Field("title", "Short imperative", required=True),
        Field("status", "", "vocab", required=True, vocab=TASK_STATUS),
        Field("owner", "", "ref", required=True, vocab=TASK_OWNERS, refs=("P",)),
        Field("milestone", "", "vocab", required=True, vocab=MILESTONES),
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
    ),
    body="The text, ready to copy. Only Daniil sets `approved` or `sent`.",
)

KINDS: dict[str, Kind] = {kind.name: kind for kind in (PERSON, TASK, DRAFT)}
BY_PREFIX: dict[str, Kind] = {kind.prefix: kind for kind in KINDS.values()}

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


def generated_blocks() -> dict[str, str]:
    """Named blocks that `uo schema` writes into SCHEMA.md between `<!-- uo:NAME -->` markers."""
    blocks = {kind.name: markdown_table(kind) for kind in (*KINDS.values(), STUDY)}
    blocks["log-types"] = ", ".join(f"`{t}`" for t in LOG_TYPES)
    return blocks
