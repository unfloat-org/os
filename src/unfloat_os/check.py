"""`uo check`: every rule that has an enforcer, over the records and each repository."""

from __future__ import annotations

import re
from collections.abc import Mapping

from unfloat_os import drafts, gitfiles, guard, health, integrity, links, schema, studies
from unfloat_os.records import Records, Store, to_date
from unfloat_os.report import Report
from unfloat_os.workspace import RepoConfig, Workspace

# Person and draft IDs never appear in open or public repositories (contract C5).
PRIVATE_ID = re.compile(r"\b(?:P|DR)-\d{3}\b")
TEXT_SUFFIXES = {".md", ".py", ".toml", ".txt", ".yml", ".yaml", ".json", ".cfg", ".ini", ".sh"}
SCHEMA_DOC = "SCHEMA.md"
BLOCK = re.compile(r"(<!-- uo:(?P<name>[a-z-]+) -->\n)(?P<body>.*?)(\n<!-- /uo -->)", re.S)


# records --------------------------------------------------------------------


def _check_field(f: schema.Field, value, records: Records, where: str, report: Report) -> None:
    if f.type == "date":
        if to_date(value) is None:
            report.error(where, f"{f.name} '{value}' is not a valid YYYY-MM-DD date")
    elif f.type in ("vocab", "vocab-list"):
        for problem in f.vocabulary_problems(value):
            report.error(where, problem)
    elif f.type == "ref":
        if value in f.vocab:
            return
        if not any(re.fullmatch(rf"{p}-\d{{3}}", value) for p in f.refs) or not records.exists(value):
            allowed = ", ".join([*f.vocab, *(f"an existing {p}-###" for p in f.refs)])
            report.error(where, f"{f.name} '{value}' must be {allowed}")
    elif f.type == "ref-list":
        for item in schema.items({f.name: value}, f.name):
            if not records.exists(item):
                report.error(where, f"{f.name}: {item} does not exist")
    elif f.type == "ref-or-text" and schema.ID_RE.match(value) and not records.exists(value):
        report.error(where, f"{f.name} {value} does not exist")


def validate(records: Records, store: Store) -> Report:
    report = Report()
    for kind in records.schema.kinds.values():
        names = {f.name for f in kind.fields}
        for record in records.by_kind[kind.name].values():
            where = record.path.relative_to(store.root).as_posix()
            unknown = set(record.meta) - names
            if unknown:
                report.error(where, f"unknown field(s): {', '.join(sorted(unknown))}")
            for f in kind.fields:
                value = record.meta.get(f.name)
                if not value:
                    if f.required:
                        report.error(where, f"missing '{f.name}'")
                    continue
                _check_field(f, value, records, where, report)
            for rule in kind.rules:
                message = rule.test(record.meta, records)
                if message:
                    report.add(rule.level, where, message)
    for event in records.events:
        for rid in event.refs:
            if schema.ID_RE.match(rid) and not records.exists(rid):
                report.error(f"records/log/{event.file}:{event.line}", f"{rid} does not exist")
    return report


def summary(records: Records) -> str:
    return (
        f"ok: {len(records.people)} people, {len(records.tasks)} tasks, {len(records.drafts)} drafts, "
        f"{len(records.decisions)} decisions, {len(records.events)} log events"
    )


def check_records(ws: Workspace) -> tuple[Report, str]:
    """Every rule over the records repository's content: schema, email drafts, append-only, CONTEXT freshness."""
    store = Store(ws.ops)
    records = store.load()
    report = Report()
    report.extend(records.report)
    report.extend(validate(records, store))
    report.extend(drafts.check_email_drafts(records))
    report.extend(integrity.check_append_only(ws.ops))
    report.extend(health.check_context_fresh(ws, records))
    return report, summary(records)


# schema documentation ---------------------------------------------------------


def fill_schema_blocks(text: str, blocks: Mapping[str, str]) -> str:
    """Rewrite each `<!-- uo:NAME -->` block of `text` that has a generated body in `blocks`."""

    def replace(match: re.Match) -> str:
        name = match.group("name")
        if name not in blocks:
            return match.group(0)
        return match.group(1) + blocks[name] + match.group(4)

    return BLOCK.sub(replace, text)


def check_schema_doc(ws: Workspace) -> Report:
    report = Report()
    path = ws.ops / SCHEMA_DOC
    text = path.read_text(encoding="utf-8")
    blocks = Store(ws.ops).schema.blocks
    present = {m.group("name") for m in BLOCK.finditer(text)}
    missing = set(blocks) - present
    if missing:
        report.error(f"ops/{SCHEMA_DOC}", f"no generated block for {', '.join(sorted(missing))}")
    if fill_schema_blocks(text, blocks) != text:
        report.error(f"ops/{SCHEMA_DOC}", "generated tables are out of date; run `uo schema`")
    return report


# repositories -----------------------------------------------------------------


def check_visibility(repo: RepoConfig) -> Report:
    report = Report()
    if not repo.is_open:
        return report
    for rel in gitfiles.tracked(repo.path):
        if rel.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = (repo.path / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in PRIVATE_ID.finditer(line):
                report.error(
                    f"{repo.name}/{rel.as_posix()}:{number}",
                    f"private record ID {match.group(0)} in an {repo.visibility} repository",
                )
    return report


def check_repo(repo: RepoConfig) -> Report:
    """Rules that hold for one repository on its own, as its CI runs them."""
    report = Report()
    report.extend(links.check_links(repo))
    report.extend(check_visibility(repo))
    report.extend(studies.check(repo))
    report.extend(guard.check_tree(repo))
    return report


def check_workspace(ws: Workspace) -> tuple[Report, str]:
    """The records, the generated docs, every repository, and the workspace's own state (hooks, remotes, sync)."""
    report, text = check_records(ws)
    report.extend(check_schema_doc(ws))
    for repo in ws.repos():
        report.extend(check_repo(repo))
    report.extend(health.check_hooks(ws))
    report.extend(health.check_remotes(ws))
    report.extend(health.check_sync(ws))
    return report, text
