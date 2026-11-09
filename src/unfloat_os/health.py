"""Workspace health: hooks on, remotes as the manifest says, work pushed, CONTEXT current.

The rules, each decided from git config, git refs or files, never from memory of what was done:

- A repository that ships `.githooks/` has them switched on (`core.hooksPath = .githooks`); a clone
  without that setting commits without `uo guard` and `uo check`, which is how private data leaks.
- `origin` is the remote the manifest lists, so a push goes where the project expects.
- Commits not pushed are a warning: unpushed work exists on one machine only. Everything is judged
  as of the last fetch; these checks never touch the network. Commits behind are for `uo status`.
- `ops/CONTEXT.md` says `Updated YYYY-MM-DD.` near the top, and that date is not far behind the newest
  log event, so the brief other sessions read first does not lag the records.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from unfloat_os import gitfiles
from unfloat_os.records import Records, to_date
from unfloat_os.report import Report
from unfloat_os.workspace import Member, Workspace

HOOKS_DIR = ".githooks"
CONTEXT = "CONTEXT.md"
CONTEXT_HEAD_LINES = 10
DEFAULT_STALE_DAYS = 3
UPDATED = re.compile(r"^Updated\s+(.+?)\.(?:\s|$)")


def member_repos(ws: Workspace) -> list[Member]:
    """Members whose folder exists and is a git work tree."""
    return [m for m in ws.members.values() if m.path.is_dir() and gitfiles.is_repo(m.path)]


def check_hooks(ws: Workspace) -> Report:
    report = Report()
    for member in member_repos(ws):
        if not (member.path / HOOKS_DIR).is_dir():
            continue
        value = gitfiles.config_value(member.path, "core.hooksPath")
        if value != HOOKS_DIR:
            shown = f"'{value}'" if value else "not set"
            report.error(member.name, f"core.hooksPath is {shown}, expected '{HOOKS_DIR}'; run `uo setup`")
    return report


def normalise_remote(url: str) -> str:
    """A remote URL without trailing slash or `.git`, so equivalent spellings compare equal."""
    url = url.strip().rstrip("/")
    url = url.removesuffix(".git")
    return url.rstrip("/")


def check_remotes(ws: Workspace) -> Report:
    report = Report()
    for member in member_repos(ws):
        if not member.remote:
            continue
        actual = gitfiles.remote_url(member.path, "origin")
        if actual is None:
            report.error(member.name, f"no remote 'origin'; the manifest says {member.remote}")
        elif normalise_remote(actual) != normalise_remote(member.remote):
            report.error(member.name, f"remote 'origin' is {actual}, the manifest says {member.remote}")
    return report


def check_sync(ws: Workspace) -> Report:
    report = Report()
    for member in member_repos(ws):
        if gitfiles.head(member.path) is None:
            continue
        counts = gitfiles.ahead_behind(member.path)
        if counts is None:
            report.warning(member.name, "no upstream branch; commits are not pushed anywhere")
        elif counts[0]:
            report.warning(member.name, f"{counts[0]} commit(s) not pushed")
    return report


def context_date(text: str) -> tuple[str, dt.date | None] | None:
    """The `Updated ...` value within the first lines and its date, or None if there is no such line."""
    for line in text.splitlines()[:CONTEXT_HEAD_LINES]:
        match = UPDATED.match(line)
        if match:
            return match.group(1), to_date(match.group(1))
    return None


def newest_event(records: Records) -> dt.date | None:
    dates = [d for d in (to_date(event.date) for event in records.events) if d is not None]
    return max(dates, default=None)


def stale_days(ws: Workspace) -> int:
    value = ws.settings.get("context_stale_days", DEFAULT_STALE_DAYS)
    return value if isinstance(value, int) and not isinstance(value, bool) else DEFAULT_STALE_DAYS


def check_context_fresh(ws: Workspace, records: Records) -> Report:
    report = Report()
    path: Path = ws.ops / CONTEXT
    where = f"ops/{CONTEXT}"
    if not path.is_file():
        report.error(where, "missing")
        return report
    found = context_date(path.read_text(encoding="utf-8"))
    if found is None:
        report.error(where, f"no 'Updated YYYY-MM-DD.' line in the first {CONTEXT_HEAD_LINES} lines")
        return report
    raw, updated = found
    if updated is None:
        report.error(where, f"'Updated {raw}.' is not a valid YYYY-MM-DD date")
        return report
    newest = newest_event(records)
    limit = stale_days(ws)
    if newest is not None and (newest - updated).days > limit:
        report.warning(
            where, f"updated {updated}, but the log has events up to {newest} (more than {limit} days later)"
        )
    return report
