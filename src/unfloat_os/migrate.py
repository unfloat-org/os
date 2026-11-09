"""`uo migrate`: carry every record of one kind through a schema change.

Rule enforced: when a field or a vocabulary word changes, the records change with it in one shown step,
never by hand across dozens of files. A migration is a dry run unless `--apply` is given, so the change
is listed before anything is written. It refuses to start when any record of the kind cannot be rewritten
safely (frontmatter that does not parse, a rename that would collide with an existing key), so a run never
leaves the records half migrated.

Only the frontmatter of people, tasks and drafts is edited, in place: key order, other lines and the body
stay as they are. The log and the decisions are append-only history and are never rewritten; an old event
keeps the words it was written with.

  uo migrate rename-field KIND OLD NEW
  uo migrate add-field KIND NAME [--value V]      at the field's position in the schema; the field must exist there
  uo migrate drop-field KIND NAME
  uo migrate rename-value KIND FIELD OLD NEW      also inside list fields; a repeated word is kept once
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from unfloat_os import frontmatter, schema
from unfloat_os.frontmatter import FrontmatterError, Meta, Value
from unfloat_os.records import Store, StoreError
from unfloat_os.workspace import Workspace

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# What one record's edit returns: the new file text and how it changed, or None when it is not affected.
Editor = Callable[[Meta, str], tuple[str, str] | None]


@dataclass(frozen=True)
class Change:
    path: Path  # relative to the records repository
    what: str
    text: str  # the file as it will be written


@dataclass(frozen=True)
class Plan:
    changes: list[Change]
    warnings: list[str]


def _kind(store: Store, name: str) -> schema.Kind:
    kind = store.schema.kinds.get(name)
    if kind is None:
        raise StoreError(f"unknown kind '{name}'; one of {', '.join(store.schema.kinds)}")
    return kind


def _key(name: str) -> str:
    if not KEY_RE.match(name):
        raise StoreError(f"field name '{name}' must be lowercase letters, digits and '_', starting with a letter")
    return name


def _show(value: Value) -> str:
    return frontmatter.format_value(value) or "(empty)"


def _plan(store: Store, kind: schema.Kind, edit: Editor) -> list[Change]:
    changes: list[Change] = []
    problems: list[str] = []
    for path in sorted(store.folder(kind).glob("*.md")):
        rel = path.relative_to(store.root)
        source = path.read_text(encoding="utf-8")
        try:
            outcome = edit(frontmatter.parse(source), source)
        except FrontmatterError as error:
            problems.append(f"{rel}: {error}")
            continue
        if outcome:
            changes.append(Change(rel, outcome[1], outcome[0]))
    if problems:
        raise StoreError("migration refused, nothing was changed:\n" + "\n".join(problems))
    return changes


def rename_field(store: Store, kind_name: str, old: str, new: str) -> Plan:
    kind = _kind(store, kind_name)
    if _key(old) == _key(new):
        raise StoreError("OLD and NEW are the same")

    def edit(meta: Meta, source: str) -> tuple[str, str] | None:
        if old not in meta:
            return None
        if new in meta:
            raise FrontmatterError(f"has both '{old}' and '{new}'; merge them by hand first")
        return frontmatter.rename(source, old, new), f"rename {old} -> {new}"

    warnings = [] if kind.field(new) else [f"'{new}' is not a field of {kind.name}; uo check will report it as unknown"]
    return Plan(_plan(store, kind, edit), warnings)


def add_field(store: Store, kind_name: str, name: str, value: str = "") -> Plan:
    kind = _kind(store, kind_name)
    field = kind.field(_key(name))
    if field is None:
        raise StoreError(f"{kind.name} has no field '{name}' in the schema; add it to schema.py first")
    if "\n" in value:
        raise StoreError("a value is one line")
    given = frontmatter.parse_value(value)
    if isinstance(field.empty(), list) and isinstance(given, str):
        given = [given] if given else []
    earlier = [f.name for f in kind.fields][: kind.fields.index(field)]

    def edit(meta: Meta, source: str) -> tuple[str, str] | None:
        if name in meta:
            return None
        present = [key for key in earlier if key in meta]
        inserted = frontmatter.insert(source, name, given, present[-1] if present else None)
        return inserted, f"add {name}: {_show(given)}"

    problems = field.vocabulary_problems(given) if given else []
    warnings = [f"{kind.name}: {problem}; uo check will reject it" for problem in problems]
    if field.required and not given:
        warnings.append(f"{kind.name} requires '{name}'; records get an empty value that uo check reports (--value)")
    return Plan(_plan(store, kind, edit), warnings)


def drop_field(store: Store, kind_name: str, name: str) -> Plan:
    kind = _kind(store, kind_name)
    _key(name)

    def edit(meta: Meta, source: str) -> tuple[str, str] | None:
        if name not in meta:
            return None
        return frontmatter.remove(source, name), f"drop {name} (was: {_show(meta[name])})"

    warnings = [f"'{name}' is still a field of {kind.name} in the schema"] if kind.field(name) else []
    return Plan(_plan(store, kind, edit), warnings)


def rename_value(store: Store, kind_name: str, field_name: str, old: str, new: str) -> Plan:
    kind = _kind(store, kind_name)
    field = kind.field(field_name)
    if field is None:
        raise StoreError(f"{kind.name} has no field '{field_name}' in the schema")
    if not old.strip() or not new.strip():
        raise StoreError("OLD and NEW must not be empty")
    if old == new:
        raise StoreError("OLD and NEW are the same")
    if "\n" in old or "\n" in new:
        raise StoreError("a value is one line")
    if isinstance(field.empty(), list) and "," in new:
        raise StoreError(f"{kind.name}.{field_name} is a list; a value cannot contain ','")

    def edit(meta: Meta, source: str) -> tuple[str, str] | None:
        value = meta.get(field_name)
        if isinstance(value, list):
            if old not in value:
                return None
            updated: Value = []
            for item in value:
                item = new if item == old else item
                if item not in updated:
                    updated.append(item)
        elif value == old:
            updated = new
        else:
            return None
        return frontmatter.update(source, {field_name: updated}), f"{field_name}: {_show(value)} -> {_show(updated)}"

    warnings = [f"{kind.name}: {problem}; uo check will reject it" for problem in field.vocabulary_problems(new)]
    return Plan(_plan(store, kind, edit), warnings)


def apply(store: Store, plan: Plan) -> None:
    for change in plan.changes:
        (store.root / change.path).write_text(change.text, encoding="utf-8")


# command ------------------------------------------------------------------------


def _plan_for(store: Store, args: argparse.Namespace) -> Plan:
    match args.migration:
        case "rename-field":
            return rename_field(store, args.kind, args.old, args.new)
        case "add-field":
            return add_field(store, args.kind, args.name, args.value)
        case "drop-field":
            return drop_field(store, args.kind, args.name)
        case _:
            return rename_value(store, args.kind, args.field, args.old, args.new)


def cmd_migrate(args: argparse.Namespace, ws: Workspace) -> int:
    store = Store(ws.ops)
    if args.apply:
        with store.lock():  # no `uo new` or `uo set` lands between reading the records and rewriting them
            plan = _plan_for(store, args)
            apply(store, plan)
    else:
        plan = _plan_for(store, args)
    if not plan.changes:
        print("nothing to change")
        return 0
    for warning in plan.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for change in plan.changes:
        print(f"{change.path}: {change.what}")
    if args.apply:
        print(f"{len(plan.changes)} file(s) changed; run `uo check` to confirm")
    else:
        print(f"{len(plan.changes)} file(s) would change; run again with --apply to write them")
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "migrate",
        help="rename, add or drop a field, or rename a value, in every record of a kind",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(handler=cmd_migrate)
    steps = p.add_subparsers(dest="migration", required=True)

    def step(name: str, help_text: str, *arguments: str) -> argparse.ArgumentParser:
        q = steps.add_parser(name, help=help_text)
        q.add_argument("kind", choices=schema.KIND_NAMES)
        for argument in arguments:
            q.add_argument(argument)
        q.add_argument("--apply", action="store_true", help="write the changes (default: only list them)")
        return q

    step("rename-field", "give a frontmatter key another name", "old", "new")
    q = step("add-field", "add a key at its schema position to records that lack it", "name")
    q.add_argument("--value", default="", help="value for the new key; lists as [a, b] (default: empty)")
    step("drop-field", "remove a key from every record", "name")
    step("rename-value", "replace a value of a field, also inside lists", "field", "old", "new")
