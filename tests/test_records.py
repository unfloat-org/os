from __future__ import annotations

import datetime as dt
import multiprocessing
from pathlib import Path

import pytest
from conftest import PERSON, TASK, rid, write_record

from unfloat_os import frontmatter
from unfloat_os.records import Store, StoreError


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("id: x\n---\n", "must start with"),
        ("---\nid: x\n", "not closed"),
        ("---\nno colon here\n---\n", "line 2: expected 'key: value'"),
        ("---\nid: a\nid: b\n---\n", "line 3: duplicate key 'id'"),
    ],
)
def test_frontmatter_rejects_malformed_blocks(text, message):
    with pytest.raises(frontmatter.FrontmatterError, match=message):
        frontmatter.parse(text)


def test_frontmatter_update_keeps_order_body_and_unrelated_lines():
    text = "---\nid: T-001\nstatus: todo\npeople: [a, b]\n---\nBody line\n\n## Notes\n"
    updated = frontmatter.update(text, {"status": "done", "closed": "2026-11-09"})
    assert updated == "---\nid: T-001\nstatus: done\npeople: [a, b]\nclosed: 2026-11-09\n---\nBody line\n\n## Notes\n"
    assert frontmatter.parse(updated)["people"] == ["a", "b"]
    assert frontmatter.body(updated) == "Body line\n\n## Notes\n"


def test_create_takes_the_number_after_the_highest_existing_file(ws):
    write_record(ws.ops, "tasks", "T-007", "old", {**TASK, "status": "done", "closed": "2026-11-02"})
    record = Store(ws.ops).create(
        "task",
        "Write the plan",
        {"owner": "claude", "milestone": "ops", "done_when": "Plan exists", "status": "todo", "created": "2026-11-09"},
    )
    assert record.id == "T-008"
    assert record.path.name == "T-008-write-the-plan.md"
    meta = frontmatter.parse(record.path.read_text())
    assert list(meta) == [
        "id",
        "title",
        "status",
        "owner",
        "milestone",
        "due",
        "people",
        "waiting_on",
        "done_when",
        "created",
        "closed",
    ]
    assert meta["people"] == []


def test_create_refuses_missing_required_and_unknown_fields(ws):
    store = Store(ws.ops)
    with pytest.raises(StoreError, match="missing required field\\(s\\): owner, milestone, done_when"):
        store.create("task", "Incomplete", {"status": "todo", "created": "2026-11-09"})
    with pytest.raises(StoreError, match="unknown field"):
        store.create("person", "Someone", {**PERSON, "colour": "blue"})
    assert not list((ws.ops / "records" / "tasks").glob("*.md"))


def _create_many(args: tuple[str, int]) -> list[str]:
    root, count = args
    store = Store(Path(root))
    return [
        store.create(
            "task",
            f"Parallel {i}",
            {"owner": "claude", "milestone": "ops", "done_when": "x", "status": "todo", "created": "2026-11-09"},
        ).id
        for i in range(count)
    ]


def test_parallel_processes_never_receive_the_same_id(ws):
    with multiprocessing.get_context("spawn").Pool(6) as pool:
        ids = [i for batch in pool.map(_create_many, [(str(ws.ops), 5)] * 6) for i in batch]
    assert len(ids) == 30
    assert sorted(ids) == [f"T-{n:03d}" for n in range(1, 31)]
    assert len(list((ws.ops / "records" / "tasks").glob("T-*.md"))) == 30


def test_update_rewrites_values_and_never_changes_ids(ws):
    write_record(ws.ops, "tasks", "T-001", "thing", TASK, "Notes stay.\n")
    store = Store(ws.ops)
    store.update("T-001", {"status": "done", "closed": "2026-11-09"})
    text = (ws.ops / "records/tasks/T-001-thing.md").read_text()
    assert frontmatter.parse(text)["status"] == "done"
    assert text.endswith("Notes stay.\n")
    with pytest.raises(StoreError, match="IDs never change"):
        store.update("T-001", {"id": "T-002"})


def test_append_event_validates_type_and_references(ws):
    write_record(ws.ops, "people", rid("P", 1), "someone", PERSON)
    store = Store(ws.ops)
    records = store.load()
    with pytest.raises(StoreError, match="unknown log type"):
        store.append_event(records, dt.date(2026, 11, 9), "gossip", [], "x")
    with pytest.raises(StoreError, match=f"{rid('P', 2)} does not exist"):
        store.append_event(records, dt.date(2026, 11, 9), "note", [rid("P", 2)], "x")
    with pytest.raises(StoreError, match="one non-empty line"):
        store.append_event(records, dt.date(2026, 11, 9), "note", [], "two\nlines")
    event = store.append_event(records, dt.date(2026, 12, 1), "note", [rid("P", 1)], "Met")
    new_month = (ws.ops / "records/log/2026-12.md").read_text()
    assert new_month == f"# Log 2026-12\n\n- 2026-12-01 | note | {rid('P', 1)} | Met\n"
    assert store.load().events[-1] == event.__class__(event.date, "note", (rid("P", 1),), "Met", "2026-12.md", 3)


def test_add_decision_appends_the_next_number(ws):
    store = Store(ws.ops)
    did = store.add_decision("Second", dt.date(2026, 11, 9), {"Decision": "y", "Why": "z", "Replaces": ""})
    assert did == "D-002"
    text = store.decisions_path.read_text()
    assert text.endswith("## D-002 | 2026-11-09 | Second\n\n- Decision: y\n- Why: z\n")
    assert list(store.load().decisions) == ["D-001", "D-002"]
