from __future__ import annotations

import datetime as dt

import pytest
from conftest import PERSON, TASK, rid, write_record, write_schema

from unfloat_os import policy, render
from unfloat_os.records import Store
from unfloat_os.workspace import RepoConfig, Workspace, WorkspaceError


def test_tracker_places_tasks_by_due_date_owner_and_status(ws):
    write_record(ws.ops, "tasks", "T-001", "late", {**TASK, "title": "Late", "due": "2026-11-08", "owner": "founder"})
    write_record(
        ws.ops, "tasks", "T-002", "wait", {**TASK, "title": "Wait", "status": "waiting", "waiting_on": "a reply"}
    )
    write_record(ws.ops, "tasks", "T-003", "old", {**TASK, "title": "Old", "status": "done", "closed": "2026-10-01"})
    write_record(ws.ops, "tasks", "T-004", "new", {**TASK, "title": "New", "status": "done", "closed": "2026-11-05"})
    text = render.render_records(Store(ws.ops).load(), dt.date(2026, 11, 9))
    sections = {part.split("\n", 1)[0]: part for part in text.split("\n## ")}
    assert "Open tasks: 2 (Daniil 1, Claude 0, others 0, waiting 1). Overdue: 1." in text
    assert "[T-001](records/tasks/T-001-late.md) Late | 2026-11-08 | Daniil | todo" in sections["Overdue"]
    assert "T-002" in sections["Waiting"] and "a reply" in sections["Waiting"]
    assert "T-004" in sections["Closed in the last 14 days"]
    assert "T-003" not in sections["Closed in the last 14 days"]


def test_tracker_sections_and_counts_follow_the_owners_and_milestones_of_the_config(ws):
    owners = (
        '[owners.boss]\nname = "Boss"\nheading = "Boss: today"\n\n[owners.bot]\nname = "Bot"\nheading = "Bot: queue"\n'
    )
    write_schema(ws.ops, milestones=["parked", "ops", "build"], owners=owners)
    write_record(ws.ops, "people", rid("P", 1), "someone", {**PERSON, "name": "Some One"})
    write_record(ws.ops, "tasks", "T-001", "a", {**TASK, "title": "A", "owner": "boss", "milestone": "ops"})
    write_record(ws.ops, "tasks", "T-002", "b", {**TASK, "title": "B", "owner": "bot", "milestone": "parked"})
    write_record(ws.ops, "tasks", "T-003", "c", {**TASK, "title": "C", "owner": rid("P", 1), "milestone": "ops"})
    write_record(
        ws.ops,
        "tasks",
        "T-004",
        "d",
        {**TASK, "title": "D", "owner": "boss", "milestone": "ops", "status": "done", "closed": "2026-11-05"},
    )
    text = render.render_records(Store(ws.ops).load(), dt.date(2026, 11, 9))
    sections = {part.split("\n", 1)[0]: part for part in text.split("\n## ")}
    assert "Open tasks: 3 (Boss 1, Bot 1, others 1, waiting 0). Overdue: 0." in text
    assert list(sections)[2:5] == ["Boss: today", "Bot: queue", "Others"]
    assert "T-001" in sections["Boss: today"] and "T-002" not in sections["Boss: today"]
    assert "T-002" in sections["Bot: queue"]
    assert "Some One" in sections["Others"]
    assert "## Daniil" not in text
    rows = [
        line for line in sections["Milestones and workstreams"].splitlines() if line[2:].startswith(("parked", "ops"))
    ]
    assert rows == ["| parked | 1 | 0 |", "| ops | 2 | 1 |"]


def test_policy_is_one_rule_set_with_channel_additions():
    text = "Range 3\u20135 \u2014 see [CONFIRM] @everyone"
    assert policy.problems(text, "email") == ["[CONFIRM", "\u2014"]
    assert policy.problems(text, "discord") == ["[CONFIRM", "\u2014", "\u2013", "@everyone"]
    assert policy.problems("Plain words, hyphen-ated.", "discord") == []


def test_workspace_is_found_from_any_folder_inside_it(ws, monkeypatch):
    monkeypatch.delenv("UNFLOAT_WORKSPACE", raising=False)
    deep = ws.members["research"].path / "analysis" / "x"
    deep.mkdir(parents=True)
    found = Workspace.find(deep)
    assert found.root == ws.root
    assert [r.name for r in found.repos()] == ["ops", "research"]
    assert found.data_dir("private") == ws.root / "data" / "private"
    with pytest.raises(WorkspaceError, match="no data folder 'restricted'"):
        found.data_dir("restricted")


def test_workspace_errors_name_the_problem(tmp_path, monkeypatch):
    monkeypatch.delenv("UNFLOAT_WORKSPACE", raising=False)
    with pytest.raises(WorkspaceError, match=r"no ops/config/workspace\.toml"):
        Workspace.find(tmp_path)
    (tmp_path / ".unfloat.toml").write_text('visibility = "secret"\n')
    with pytest.raises(WorkspaceError, match="visibility must be one of private, open, public"):
        RepoConfig.load(tmp_path)
