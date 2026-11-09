"""`uo status`: which tasks, drafts and inbox lines each section picks, and the exit code."""

from __future__ import annotations

import argparse
import datetime as dt

import pytest
from conftest import DRAFT, TASK, rid, write_record

from unfloat_os import check, status
from unfloat_os.records import Store, StoreError
from unfloat_os.report import Report

TODAY = dt.date(2026, 11, 9)


def add_task(ws, number: int, **fields) -> str:
    task_id = rid("T", number)
    write_record(ws.ops, "tasks", task_id, "task", {**TASK, "title": f"Task {number}", **fields})
    return task_id


def add_draft(ws, number: int, **fields) -> str:
    draft_id = rid("DR", number)
    write_record(ws.ops, "drafts", draft_id, "draft", {**DRAFT, "title": f"Draft {number}", **fields})
    return draft_id


def ids(records) -> list[str]:
    return [r.id for r in records]


def load(ws):
    return Store(ws.ops).load()


# overdue and due soon -----------------------------------------------------------------------------


def test_overdue_means_due_before_today_and_still_active(ws):
    late = add_task(ws, 1, status="todo", due="2026-11-08")
    doing = add_task(ws, 2, status="doing", due="2026-10-01")
    add_task(ws, 3, status="todo", due="2026-11-09")  # due today is not overdue
    add_task(ws, 4, status="done", due="2026-10-01", closed="2026-10-02")
    add_task(ws, 5, status="dropped", due="2026-10-01", closed="2026-10-02")
    add_task(ws, 6, status="waiting", waiting_on="a reply", due="2026-10-01")  # has its own section
    add_task(ws, 7, status="todo", due="")
    assert ids(status.overdue_tasks(load(ws), TODAY)) == [doing, late]  # most overdue first


def test_due_soon_covers_today_through_three_days_ahead(ws):
    today = add_task(ws, 1, status="todo", due="2026-11-09")
    edge = add_task(ws, 2, status="doing", due="2026-11-12")
    add_task(ws, 3, status="todo", due="2026-11-13")  # four days away
    add_task(ws, 4, status="todo", due="2026-11-08")  # overdue, not due soon
    add_task(ws, 5, status="waiting", waiting_on="x", due="2026-11-10")
    add_task(ws, 6, status="done", due="2026-11-10", closed="2026-11-10")
    assert ids(status.due_soon_tasks(load(ws), TODAY)) == [today, edge]


def test_tasks_with_an_invalid_due_date_are_skipped(ws):
    add_task(ws, 1, status="todo", due="soon")
    add_task(ws, 2, status="todo", due="2026-02-30")
    records = load(ws)
    assert not status.overdue_tasks(records, TODAY)
    assert not status.due_soon_tasks(records, TODAY)


def test_ties_are_ordered_by_id(ws):
    second = add_task(ws, 2, status="todo", due="2026-11-01")
    first = add_task(ws, 1, status="todo", due="2026-11-01")
    assert ids(status.overdue_tasks(load(ws), TODAY)) == [first, second]


# waiting ------------------------------------------------------------------------------------------


def test_waiting_tasks_appear_when_due_passed_or_within_three_days(ws):
    passed = add_task(ws, 1, status="waiting", waiting_on="a reply", due="2026-11-01")
    today = add_task(ws, 2, status="waiting", waiting_on="a reply", due="2026-11-09")
    edge = add_task(ws, 3, status="waiting", waiting_on="a reply", due="2026-11-12")
    add_task(ws, 4, status="waiting", waiting_on="a reply", due="2026-11-13")
    add_task(ws, 5, status="waiting", waiting_on="a reply", due="")
    add_task(ws, 6, status="todo", due="2026-11-01")
    assert ids(status.waiting_due_tasks(load(ws), TODAY)) == [passed, today, edge]


# drafts -------------------------------------------------------------------------------------------


def test_drafts_awaiting_daniil_are_drafts_and_approved_ones(ws):
    first = add_draft(ws, 1, status="draft")
    third = add_draft(ws, 3, status="approved")
    add_draft(ws, 2, status="sent")
    add_draft(ws, 4, status="dropped")
    assert ids(status.drafts_awaiting_daniil(load(ws))) == [first, third]


# inbox --------------------------------------------------------------------------------------------

INBOX_HEADER = (
    "# Inbox\n\nQuick capture. Write one line per item: news, a contact, an idea, a reply you got. "
    "The next agent session files each line into the right record and deletes it here.\n"
)


def test_empty_inbox_has_no_entries():
    assert status.inbox_entries(INBOX_HEADER) == []


def test_each_line_after_the_header_is_an_entry():
    text = INBOX_HEADER + "\nSomeone replied about the grant\n- a bulleted idea\n\n* another one\n"
    assert status.inbox_entries(text) == ["Someone replied about the grant", "a bulleted idea", "another one"]


def test_entry_directly_below_the_intro_is_kept():
    text = INBOX_HEADER + "First item\n"
    assert status.inbox_entries(text) == ["First item"]


def test_inbox_without_the_intro_paragraph_keeps_all_lines():
    assert status.inbox_entries("# Inbox\n\nfirst\nsecond\n") == ["first", "second"]


# repositories -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counts", "text"),
    [
        ((0, 0), "up to date"),
        ((2, 0), "ahead 2 (not pushed)"),
        ((0, 3), "behind 3 (pull)"),
        ((1, 4), "ahead 1 (not pushed), behind 4 (pull)"),
        (None, "no upstream"),
    ],
)
def test_sync_state_is_described(counts, text):
    assert status.describe_sync(counts) == text


def test_repository_line_names_branch_and_state(ws):
    member = ws.members["ops"]
    assert status.repo_line(member) == "ops: main, no commits yet"


# the screen and the exit code ---------------------------------------------------------------------


def write_context(ws, updated: str) -> None:
    (ws.ops / "CONTEXT.md").write_text(f"# Context brief\n\nUpdated {updated}. Read this first.\n", encoding="utf-8")


def test_screen_lists_sections_in_session_order(ws):
    add_task(ws, 1, status="todo", due="2026-11-01")
    add_task(ws, 2, status="todo", due="2026-11-11")
    add_task(ws, 3, status="waiting", waiting_on="a reply", due="2026-11-05")
    add_draft(ws, 1, status="approved", channel="email")
    write_context(ws, "2026-11-09")
    lines = status.render(ws, load(ws), TODAY, ["call the lab"], ["ok: 1 tasks; 0 warning(s)"])
    text = "\n".join(lines)
    headings = [
        "uo status 2026-11-09",
        "Records check",
        "Repositories",
        "Inbox (1)",
        "Overdue tasks (1)",
        "Due within 3 days (1)",
        "Waiting, due or near (1)",
        "Drafts awaiting Daniil (1)",
        "CONTEXT.md",
    ]
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)
    assert f"{rid('T', 1)} Task 1 due 2026-11-01 (overdue 8d) (claude)" in text
    assert f"{rid('T', 2)} Task 2 due 2026-11-11 (in 2d) (claude)" in text
    assert f"{rid('T', 3)} Task 3 due 2026-11-05 (overdue 4d) [waiting on a reply] (claude)" in text
    assert f"{rid('DR', 1)} Draft 1 [approved, email]" in text
    assert "  call the lab" in text
    assert "  up to date" in text


def test_screen_shows_stale_context(ws):
    (ws.ops / "records" / "log" / "2026-11.md").write_text(
        "# Log 2026-11\n\n- 2026-11-09 | note | - | something\n", encoding="utf-8"
    )
    write_context(ws, "2026-11-01")
    text = "\n".join(status.render(ws, load(ws), TODAY, [], ["ok"]))
    assert "updated 2026-11-01, but the log has events up to 2026-11-09" in text


def run_status(ws, monkeypatch, report: Report, today: str | None = None) -> int:
    monkeypatch.setattr(check, "check_workspace", lambda _ws: (report, "ok: summary"))
    return status.cmd_status(argparse.Namespace(today=today), ws)


def test_exit_code_is_one_with_check_errors_and_lists_them(ws, monkeypatch, capsys):
    report = Report()
    report.error("records/tasks/x.md", "something is wrong")
    report.warning("records/y.md", "minor")
    write_context(ws, "2026-11-09")
    assert run_status(ws, monkeypatch, report, "2026-11-09") == 1
    out = capsys.readouterr().out
    assert "1 error(s), 1 warning(s)" in out
    assert "error: records/tasks/x.md: something is wrong" in out


def test_exit_code_is_zero_without_check_errors(ws, monkeypatch, capsys):
    report = Report()
    report.warning("records/y.md", "minor")
    write_context(ws, "2026-11-09")
    (ws.ops / "records" / "inbox.md").write_text(INBOX_HEADER + "ring the clinic\n", encoding="utf-8")
    assert run_status(ws, monkeypatch, report, "2026-11-09") == 0
    out = capsys.readouterr().out
    assert "ok: summary; 1 warning(s)" in out
    assert "Inbox (1)\n  ring the clinic" in out


def test_invalid_today_is_refused(ws, monkeypatch):
    with pytest.raises(StoreError, match="'tomorrow' is not a valid YYYY-MM-DD date"):
        run_status(ws, monkeypatch, Report(), "tomorrow")
