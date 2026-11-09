"""`uo status`: what a session needs to know in one screen before it starts work.

It reports, in order: the records check, each repository's branch against its upstream (as of the
last fetch), inbox entries waiting to be filed, tasks that are overdue or due within three days,
waiting tasks whose due date has passed or is near, drafts waiting for Daniil, and whether CONTEXT.md
lags the log. Every section is a pure function of the loaded records and a date, so the selection
rules are testable and the same on every machine. The command exits 1 only when the check has errors.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
from pathlib import Path

from unfloat_os import gitfiles, health, schema
from unfloat_os.records import Record, Records, Store, StoreError, to_date
from unfloat_os.workspace import Member, Workspace

SOON_DAYS = 3
INBOX = Path("records/inbox.md")
INBOX_INTRO = "Quick capture."
BULLET = re.compile(r"^[-*]\s+")
HEADING = re.compile(r"^#{1,6}\s")
ACTIVE = ("doing", "todo")
AWAITING_DANIIL = ("draft", "approved")


# sections: pure functions over records and a date ---------------------------------------------


def _due(record: Record) -> dt.date | None:
    return to_date(record.meta.get("due"))


def _by_due(records: list[Record]) -> list[Record]:
    return sorted(records, key=lambda r: (_due(r) or dt.date.max, r.id))


def overdue_tasks(records: Records, today: dt.date) -> list[Record]:
    """Doing or todo tasks whose due date is before today."""
    found = [
        t
        for t in records.tasks.values()
        if t.meta.get("status") in ACTIVE and (due := _due(t)) is not None and due < today
    ]
    return _by_due(found)


def due_soon_tasks(records: Records, today: dt.date, days: int = SOON_DAYS) -> list[Record]:
    """Doing or todo tasks due from today through `days` days ahead."""
    last = today + dt.timedelta(days=days)
    found = [
        t
        for t in records.tasks.values()
        if t.meta.get("status") in ACTIVE and (due := _due(t)) is not None and today <= due <= last
    ]
    return _by_due(found)


def waiting_due_tasks(records: Records, today: dt.date, days: int = SOON_DAYS) -> list[Record]:
    """Waiting tasks whose due date has passed or falls within `days` days: time to chase."""
    last = today + dt.timedelta(days=days)
    found = [
        t
        for t in records.tasks.values()
        if t.meta.get("status") == "waiting" and (due := _due(t)) is not None and due <= last
    ]
    return _by_due(found)


def drafts_awaiting_daniil(records: Records) -> list[Record]:
    """Drafts he has not sent yet: still a draft, or approved but not sent."""
    found = [d for d in records.drafts.values() if d.meta.get("status") in AWAITING_DANIIL]
    return sorted(found, key=lambda d: d.id)


def inbox_entries(text: str) -> list[str]:
    """Items in `records/inbox.md`: every line after the `# Inbox` heading and its one-line intro.

    The file is a heading, one intro line starting "Quick capture." and then one line per item;
    a leading list bullet is dropped. Blank lines and other headings are not items.
    """
    entries: list[str] = []
    first_content = True
    for raw in text.splitlines():
        line = raw.strip()
        if not line or HEADING.match(line):
            continue
        if first_content:
            first_content = False
            if line.startswith(INBOX_INTRO):
                continue
        entries.append(BULLET.sub("", line))
    return entries


# repositories ------------------------------------------------------------------------------


def branch_of(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True, check=False
    )
    name = result.stdout.strip()
    return name or "(detached HEAD)"


def describe_sync(counts: tuple[int, int] | None, has_commits: bool = True) -> str:
    if not has_commits:
        return "no commits yet"
    if counts is None:
        return "no upstream"
    ahead, behind = counts
    if not ahead and not behind:
        return "up to date"
    parts = []
    if ahead:
        parts.append(f"ahead {ahead} (not pushed)")
    if behind:
        parts.append(f"behind {behind} (pull)")
    return ", ".join(parts)


def repo_line(member: Member) -> str:
    has_commits = gitfiles.head(member.path) is not None
    state = describe_sync(gitfiles.ahead_behind(member.path) if has_commits else None, has_commits)
    return f"{member.name}: {branch_of(member.path)}, {state}"


# rendering ---------------------------------------------------------------------------------


def _when(due: dt.date | None, today: dt.date) -> str:
    if due is None:
        return ""
    days = (due - today).days
    if days < 0:
        return f" due {due} (overdue {-days}d)"
    return f" due {due} ({'today' if days == 0 else f'in {days}d'})"


def _task_line(task: Record, today: dt.date) -> str:
    when = _when(_due(task), today)
    waiting = schema.text(task.meta, "waiting_on")
    note = f" [waiting on {waiting}]" if task.meta.get("status") == "waiting" and waiting else ""
    owner = schema.text(task.meta, "owner")
    return f"{task.id} {task.title}{when}{note} ({owner})"


def _draft_line(draft: Record) -> str:
    status = schema.text(draft.meta, "status")
    return f"{draft.id} {draft.title} [{status}, {schema.text(draft.meta, 'channel')}]"


def _section(title: str, lines: list[str], count: bool = True) -> list[str]:
    heading = f"{title} ({len(lines)})" if count else title
    return [heading, *(f"  {line}" for line in lines)]


def render(ws: Workspace, records: Records, today: dt.date, inbox: list[str], check_lines: list[str]) -> list[str]:
    """The status screen as lines; `check_lines` is the check's headline followed by its errors."""
    context = health.check_context_fresh(ws, records)
    sections = [
        [f"uo status {today}"],
        _section("Records check", check_lines, count=False),
        _section("Repositories", [repo_line(m) for m in health.member_repos(ws)], count=False),
        _section("Inbox", inbox),
        _section("Overdue tasks", [_task_line(t, today) for t in overdue_tasks(records, today)]),
        _section(f"Due within {SOON_DAYS} days", [_task_line(t, today) for t in due_soon_tasks(records, today)]),
        _section("Waiting, due or near", [_task_line(t, today) for t in waiting_due_tasks(records, today)]),
        _section("Drafts awaiting Daniil", [_draft_line(d) for d in drafts_awaiting_daniil(records)]),
        _section("CONTEXT.md", [i.message for i in context.issues] or ["up to date"], count=False),
    ]
    out: list[str] = []
    for section in sections:
        out += [*section, ""]
    return out[:-1]


# command -----------------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace, ws: Workspace) -> int:
    from unfloat_os import check  # deferred: the full check pulls in every rule module

    today = dt.date.today()
    if args.today:
        parsed = to_date(args.today)
        if parsed is None:
            raise StoreError(f"'{args.today}' is not a valid YYYY-MM-DD date")
        today = parsed
    report, summary = check.check_workspace(ws)
    headline = (
        f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
        if report.errors
        else f"{summary}; {len(report.warnings)} warning(s)"
    )
    check_lines = [headline, *(str(issue) for issue in report.errors)]
    inbox_path = ws.ops / INBOX
    inbox = inbox_entries(inbox_path.read_text(encoding="utf-8")) if inbox_path.is_file() else []
    records = Store(ws.ops).load()
    print("\n".join(render(ws, records, today, inbox, check_lines)))
    return 1 if report.errors else 0


def register(sub: argparse._SubParsersAction) -> None:
    """Add `status` to the top-level parser."""
    status = sub.add_parser("status", help="one screen for the start of a session", description=__doc__)
    status.add_argument("--today", metavar="YYYY-MM-DD", help="treat this date as today (default: the real date)")
    status.set_defaults(handler=cmd_status)
