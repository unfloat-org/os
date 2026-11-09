"""`uo`: the unfloat operating tools.

  uo check [--repo PATH]              records, schema docs, links, visibility and analyses; exit 1 on errors
  uo guard                            data guard for the staged files of the current repository (pre-commit)
  uo render [--today YYYY-MM-DD]      check the records, then write TRACKER.md in the records repository
  uo new task|person|draft TITLE [--set key=value ...] [--body TEXT]
  uo new decision TITLE --decision TEXT --why TEXT [--replaces TEXT] [--source TEXT]
  uo set ID key=value [key=value ...]
  uo log TYPE REFS SUMMARY [--date YYYY-MM-DD]   REFS: comma-separated IDs, or -
  uo schema [--check]                 write (or verify) the generated tables in SCHEMA.md
  uo paths                            the workspace, its repositories and data folders
  uo setup                            clone missing repositories, enable hooks, sync environments
  uo gmail ...                        Gmail drafts and replies (uo gmail -h)
  uo discord ...                      Discord server setup and reading (uo discord -h)

Run inside the workspace, or set UNFLOAT_WORKSPACE.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

from unfloat_os import schema
from unfloat_os.channels import ChannelError
from unfloat_os.http import HttpError, NetworkError
from unfloat_os.records import Store, StoreError, to_date
from unfloat_os.report import Report
from unfloat_os.workspace import RepoConfig, Workspace, WorkspaceError


def _date(value: str | None) -> dt.date:
    if value is None:
        return dt.date.today()
    parsed = to_date(value)
    if parsed is None:
        raise StoreError(f"'{value}' is not a valid YYYY-MM-DD date")
    return parsed


def _pairs(items: list[str]) -> dict[str, str | list[str]]:
    values: dict[str, str | list[str]] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise StoreError(f"expected key=value, got '{item}'")
        value = value.strip()
        values[key.strip()] = (
            [v.strip() for v in value[1:-1].split(",") if v.strip()]
            if value.startswith("[") and value.endswith("]")
            else value
        )
    return values


def _print_report(report: Report) -> int:
    for issue in report.warnings:
        print(issue)
    for issue in report.errors:
        print(issue, file=sys.stderr)
    if report.errors:
        print(f"{len(report.errors)} error(s)", file=sys.stderr)
        return 1
    return 0


def cmd_check(args, ws: Workspace | None) -> int:
    from unfloat_os import check

    if args.repo:
        path = Path(args.repo).resolve()
        report = check.check_repo(RepoConfig.load(path))
        if ws is not None and path == ws.ops.resolve():
            records_report, summary = check.check_records(ws)
            report.extend(records_report)
            report.extend(check.check_schema_doc(ws))
            if not report.errors:
                print(summary)
    else:
        if ws is None:
            raise WorkspaceError("uo check without --repo needs the workspace")
        report, summary = check.check_workspace(ws)
        if not report.errors:
            print(summary)
    return _print_report(report)


def cmd_guard(args, ws: Workspace | None) -> int:
    from unfloat_os import guard

    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    return _print_report(guard.check_staged(RepoConfig.load(Path(top.stdout.strip()))))


def cmd_render(args, ws: Workspace) -> int:
    from unfloat_os import check, render

    report, summary = check.check_records(ws)
    status = _print_report(report)
    if status:
        print("TRACKER.md not written", file=sys.stderr)
        return status
    print(summary)
    target = ws.ops / "TRACKER.md"
    target.write_text(render.render(ws, _date(args.today)), encoding="utf-8")
    print(f"wrote {target}")
    return 0


def cmd_new(args, ws: Workspace) -> int:
    store = Store(ws.ops)
    if args.kind == "decision":
        if not args.decision or not args.why:
            raise StoreError("a decision needs --decision and --why")
        did = store.add_decision(
            args.title,
            dt.date.today(),
            {
                "Decision": args.decision,
                "Why": args.why,
                "Replaces": args.replaces or "",
                "Source": args.source or "",
            },
        )
        print(f"{did} {store.decisions_path}")
        return 0
    kind = schema.KINDS[args.kind]
    values = _pairs(args.set or [])
    today = dt.date.today().isoformat()
    defaults = {
        "task": {"status": "todo", "created": today},
        "person": {"status": "to-contact", "kind": "person"},
        "draft": {"status": "draft", "created": today},
    }[kind.name]
    for key, value in defaults.items():
        values.setdefault(key, value)
    body = (
        args.body
        if args.body is not None
        else ("## Who\n\n## Our ask\n\n## History\n\n## Notes\n" if kind.name == "person" else "")
    )
    record = store.create(kind.name, args.title, values, body)
    print(f"{record.id} {record.path}")
    return 0


def cmd_set(args, ws: Workspace) -> int:
    record = Store(ws.ops).update(args.id, _pairs(args.pairs))
    print(f"{record.id} {record.path}")
    return 0


def cmd_log(args, ws: Workspace) -> int:
    store = Store(ws.ops)
    refs = [] if args.refs.strip() == "-" else [r.strip() for r in args.refs.split(",") if r.strip()]
    event = store.append_event(store.load(), _date(args.date), args.type, refs, args.summary)
    print(event.format())
    return 0


def cmd_schema(args, ws: Workspace) -> int:
    from unfloat_os import check

    path = ws.ops / "SCHEMA.md"
    current = path.read_text(encoding="utf-8")
    updated = check.fill_schema_blocks(current)
    if args.check:
        if updated != current:
            print(f"error: {path} is out of date; run `uo schema`", file=sys.stderr)
            return 1
        print(f"ok: {path} matches the schema")
        return 0
    path.write_text(updated, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_paths(args, ws: Workspace) -> int:
    print(f"{'workspace':<17}{ws.root}")
    for name, member in ws.members.items():
        state = "" if member.path.is_dir() else "  (missing: uo setup)"
        print(f"{name:<17}{member.path}{state}")
    for access, folder in ws.data.items():
        print(f"{'data/' + access:<17}{folder}")
    return 0


ROUTER = """# unfloat workspace

Written by `uo setup`; not part of any repository, do not edit.

This folder holds the unfloat repositories and the data folder; it is not a repository itself.
Read `ops/AGENTS.md` now and follow it: it is the session routine and the map of the workspace.
Make every change inside one repository (`ops/`, `research/`, `vitreometry/`, `os/`, `site/`)
and commit there. Never create files at this level, and never write into `data/` except through
the project's tools.
"""


def write_router(ws: Workspace) -> None:
    """The workspace folder routes agents launched there to the records repository."""
    (ws.root / "AGENTS.md").write_text(ROUTER, encoding="utf-8")
    (ws.root / "CLAUDE.md").write_text("Instructions for agents are in AGENTS.md.\n\n@AGENTS.md\n", encoding="utf-8")


def cmd_setup(args, ws: Workspace) -> int:
    for name, member in ws.members.items():
        if not member.path.is_dir():
            if not member.remote:
                print(f"{name}: missing and no remote in the manifest", file=sys.stderr)
                continue
            subprocess.run(["git", "clone", member.remote, str(member.path)], check=True)
        if (member.path / ".githooks").is_dir():
            subprocess.run(["git", "-C", str(member.path), "config", "core.hooksPath", ".githooks"], check=True)
        if (member.path / "pyproject.toml").is_file():
            subprocess.run(["uv", "sync", "--project", str(member.path)], check=True)
        print(f"{name}: ready")
    for folder in ws.data.values():
        folder.mkdir(parents=True, exist_ok=True)
    write_router(ws)
    print(f"workspace: {ws.root / 'AGENTS.md'} routes sessions to ops/AGENTS.md")
    return 0


def build_parser() -> argparse.ArgumentParser:
    from unfloat_os.channels import discord, gmail

    parser = argparse.ArgumentParser(
        prog="uo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="check records, schema docs, links, visibility and analyses")
    p.add_argument("--repo", help="check one repository on its own (as CI does)")
    p.set_defaults(handler=cmd_check, needs_workspace=False)

    p = sub.add_parser("guard", help="data guard for staged files (pre-commit)")
    p.set_defaults(handler=cmd_guard, needs_workspace=False)

    p = sub.add_parser("render", help="write TRACKER.md")
    p.add_argument("--today", help="date to render for, YYYY-MM-DD (default: today)")
    p.set_defaults(handler=cmd_render)

    p = sub.add_parser("new", help="create a record with the next free ID")
    p.add_argument("kind", choices=(*schema.KINDS, "decision"))
    p.add_argument("title")
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="frontmatter value; lists as [a, b]")
    p.add_argument("--body", help="note body (default: the kind's section template)")
    p.add_argument("--decision")
    p.add_argument("--why")
    p.add_argument("--replaces")
    p.add_argument("--source")
    p.set_defaults(handler=cmd_new)

    p = sub.add_parser("set", help="change frontmatter values of a record")
    p.add_argument("id")
    p.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    p.set_defaults(handler=cmd_set)

    p = sub.add_parser("log", help="append one event to the monthly log")
    p.add_argument("type", choices=schema.LOG_TYPES)
    p.add_argument("refs", help="comma-separated IDs, or -")
    p.add_argument("summary")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.set_defaults(handler=cmd_log)

    p = sub.add_parser("schema", help="write the generated tables in SCHEMA.md")
    p.add_argument("--check", action="store_true", help="only verify, exit 1 if out of date")
    p.set_defaults(handler=cmd_schema)

    p = sub.add_parser("paths", help="show the workspace layout")
    p.set_defaults(handler=cmd_paths)

    p = sub.add_parser("setup", help="clone missing repositories, enable hooks, sync environments")
    p.set_defaults(handler=cmd_setup)

    gmail.register(sub)
    discord.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        try:
            ws = Workspace.find()
        except WorkspaceError:
            if getattr(args, "needs_workspace", True):
                raise
            ws = None
        return args.handler(args, ws)
    except (WorkspaceError, StoreError, ChannelError, HttpError, NetworkError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
