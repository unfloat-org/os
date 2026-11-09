from __future__ import annotations

import datetime as dt
import multiprocessing
from pathlib import Path

import pytest
from conftest import LOG_TYPES, MILESTONES, PERSON, TASK, rid, schema_toml, write_record, write_schema

from unfloat_os import cli, frontmatter, schema
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


def test_frontmatter_remove_rename_and_insert_keep_order_other_lines_and_body():
    text = "---\nid: T-001\nstatus: todo\npeople: [a, b]\n---\nBody line\n\n## Notes\n"
    assert frontmatter.remove(text, "status") == "---\nid: T-001\npeople: [a, b]\n---\nBody line\n\n## Notes\n"
    assert (
        frontmatter.rename(text, "people", "who")
        == "---\nid: T-001\nstatus: todo\nwho: [a, b]\n---\nBody line\n\n## Notes\n"
    )
    assert frontmatter.insert(text, "owner", "claude", after="id") == (
        "---\nid: T-001\nowner: claude\nstatus: todo\npeople: [a, b]\n---\nBody line\n\n## Notes\n"
    )
    assert frontmatter.insert(text, "owner", ["x", "y"]).startswith("---\nowner: [x, y]\nid: T-001\n")
    with pytest.raises(frontmatter.FrontmatterError, match="no key 'gone'"):
        frontmatter.remove(text, "gone")
    with pytest.raises(frontmatter.FrontmatterError, match="key 'id' already exists"):
        frontmatter.rename(text, "status", "id")
    with pytest.raises(frontmatter.FrontmatterError, match="key 'status' already exists"):
        frontmatter.insert(text, "status", "x")
    with pytest.raises(frontmatter.FrontmatterError, match="must start with"):
        frontmatter.remove("status: todo\n", "status")


def test_the_schema_comes_from_the_config_of_the_records_repository(ws):
    spec = Store(ws.ops).schema
    assert tuple(spec.kinds) == schema.KIND_NAMES
    assert spec.by_prefix["T"] is spec.kinds["task"]
    assert spec.milestones == MILESTONES
    assert spec.kinds["task"].field("milestone").vocab == MILESTONES
    assert spec.kinds["person"].field("milestones").vocab == MILESTONES
    assert spec.kinds["task"].field("owner").vocab == ("founder", "claude")
    assert [(o.id, o.name, o.heading) for o in spec.owners] == [
        ("founder", "Daniil", "Daniil: next actions"),
        ("claude", "Claude", "Claude: queue"),
    ]
    assert spec.log_types == LOG_TYPES
    assert schema.STUDY.field("status").vocab == schema.STUDY_STATUS


def test_the_store_needs_the_config_only_when_it_reads_vocabularies(ws, tmp_path):
    bare = Store(tmp_path / "no-ops")
    assert bare.folder(schema.DRAFT) == tmp_path / "no-ops" / "records" / "drafts"
    with pytest.raises(StoreError, match=r"schema\.toml not found: milestones, task owners and log types"):
        bare.load()
    (ws.ops / "config" / "schema.toml").unlink()
    with pytest.raises(StoreError, match=r"schema\.toml not found"):
        Store(ws.ops).create("person", "Someone", PERSON)


OWNER = '[owners.boss]\nname = "Boss"\nheading = "Boss: today"\n'


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("milestones = [\n", "invalid TOML"),
        ("colour = 1\n" + schema_toml(), "unknown key\\(s\\): colour"),
        (schema_toml(milestones=[]), "'milestones' must be a non-empty list of words"),
        (schema_toml(milestones=["Not Valid"]), "milestones 'Not Valid' must be lowercase letters, digits and '-'"),
        (schema_toml(milestones=["a", "b", "a"]), "milestones lists a more than once"),
        (schema_toml(log_types=["call", "note2"]), "log_types 'note2' must be lowercase words joined by single '-'"),
        (schema_toml(owners=""), "'owners' must be a table"),
        (schema_toml(owners=OWNER + "colour = 'x'\n"), "owners.boss has unknown key\\(s\\): colour"),
        (schema_toml(owners='[owners.boss]\nname = "Boss"\n'), "owners.boss.heading must be one line of text"),
        (schema_toml(owners='[owners.Boss]\nname = "B"\nheading = "B"\n'), "owner 'Boss' must be lowercase"),
        (schema_toml(owners=OWNER.replace("Boss: today", "Boss | today")), "owners.boss.heading must be one line"),
    ],
)
def test_an_unusable_config_is_refused_with_the_reason(text, message):
    with pytest.raises(schema.SchemaError, match=message):
        schema.parse(text)


def test_create_and_update_refuse_words_outside_the_vocabulary(ws):
    store = Store(ws.ops)
    values = {"owner": "claude", "milestone": "ops", "done_when": "x", "status": "todo", "created": "2026-11-09"}
    with pytest.raises(StoreError, match="task: milestone 'moon' not in ops, build, launch, parked"):
        store.create("task", "Bad", {**values, "milestone": "moon"})
    with pytest.raises(StoreError, match="person: milestones 'moon' not in ops, build, launch, parked"):
        store.create("person", "Someone", {**PERSON, "milestones": ["ops", "moon"]})
    assert not list((ws.ops / "records" / "tasks").glob("*.md"))
    record = store.create("task", "Good", values)
    with pytest.raises(StoreError, match=f"{record.id}: status 'finished' not in doing, todo, waiting, done, dropped"):
        store.update(record.id, {"status": "finished"})
    assert store.update(record.id, {"milestone": "build"}).meta["milestone"] == "build"


def test_log_types_are_validated_against_the_config(ws):
    write_schema(ws.ops, log_types=["call", "note"])
    store = Store(ws.ops)
    with pytest.raises(StoreError, match="unknown log type 'dm'; one of call, note"):
        store.append_event(store.load(), dt.date(2026, 11, 9), "dm", [], "x")
    assert store.append_event(store.load(), dt.date(2026, 11, 9), "call", [], "x").type == "call"


def test_uo_log_checks_the_type_at_runtime_and_uo_new_keeps_its_defaults(in_workspace, capsys):
    assert cli.main(["log", "gossip", "-", "x"]) == 1
    assert capsys.readouterr().err == f"error: unknown log type 'gossip'; one of {', '.join(LOG_TYPES)}\n"
    assert cli.main(["log", "call", "-", "Talked"]) == 0
    assert "| call | - | Talked" in capsys.readouterr().out
    assert (
        cli.main(["new", "task", "Plan", "--set", "owner=claude", "--set", "milestone=ops", "--set", "done_when=x"])
        == 0
    )
    created = next((in_workspace.ops / "records" / "tasks").glob("T-001-*.md"))
    assert frontmatter.parse(created.read_text())["status"] == "todo"
    assert (
        cli.main(["new", "task", "Bad", "--set", "owner=claude", "--set", "milestone=moon", "--set", "done_when=x"])
        == 1
    )
    assert "error: task: milestone 'moon' not in ops, build, launch, parked" in capsys.readouterr().err


def test_a_missing_config_is_a_plain_error_for_every_command(in_workspace, capsys):
    (in_workspace.ops / "SCHEMA.md").write_text("<!-- uo:log-types -->\nold\n<!-- /uo -->\n", encoding="utf-8")
    (in_workspace.ops / "config" / "schema.toml").unlink()
    for command in (["check"], ["render"], ["schema"], ["log", "note", "-", "x"]):
        assert cli.main(command) == 1
        assert "schema.toml not found" in capsys.readouterr().err
