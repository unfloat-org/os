from __future__ import annotations

from pathlib import Path

import pytest
from conftest import PERSON, TASK, rid, write_record, write_schema

from unfloat_os import check, cli, frontmatter, migrate
from unfloat_os.records import Store, StoreError


def snapshot(ops: Path) -> dict[str, str]:
    return {p.relative_to(ops).as_posix(): p.read_text() for p in sorted((ops / "records").rglob("*.md"))}


def messages(ws) -> list[str]:
    return [f"{i.level}: {i.message}" for i in check.check_records(ws)[0].issues]


def keys(path: Path) -> list[str]:
    return list(frontmatter.parse(path.read_text()))


def without(meta: dict, *names: str) -> dict:
    return {k: v for k, v in meta.items() if k not in names}


def test_a_migration_lists_what_it_would_change_and_writes_only_with_apply(in_workspace, capsys):
    ops = in_workspace.ops
    write_record(ops, "tasks", "T-001", "one", {**TASK, "milestone": "build"}, "Body stays.\n\n## Notes\n")
    write_record(ops, "tasks", "T-002", "two", {**TASK, "milestone": "ops"})
    before = snapshot(ops)
    command = ["migrate", "rename-value", "task", "milestone", "build", "launch"]

    assert cli.main(command) == 0
    assert capsys.readouterr().out == (
        "records/tasks/T-001-one.md: milestone: build -> launch\n"
        "1 file(s) would change; run again with --apply to write them\n"
    )
    assert snapshot(ops) == before

    assert cli.main([*command, "--apply"]) == 0
    assert capsys.readouterr().out == (
        "records/tasks/T-001-one.md: milestone: build -> launch\n1 file(s) changed; run `uo check` to confirm\n"
    )
    after = snapshot(ops)
    assert after["records/tasks/T-001-one.md"] == before["records/tasks/T-001-one.md"].replace(
        "milestone: build", "milestone: launch"
    )
    assert after["records/tasks/T-002-two.md"] == before["records/tasks/T-002-two.md"]

    assert cli.main([*command, "--apply"]) == 0
    assert capsys.readouterr().out == "nothing to change\n"


def test_renaming_a_milestone_is_a_config_edit_and_two_migrations(in_workspace, capsys):
    ops = in_workspace.ops
    write_record(ops, "tasks", "T-001", "one", {**TASK, "milestone": "build"})
    write_record(ops, "tasks", "T-002", "two", {**TASK, "milestone": "ops"})
    write_record(ops, "people", rid("P", 1), "someone", {**PERSON, "milestones": ["ops", "build"]})
    assert messages(in_workspace) == []

    write_schema(ops, milestones=["ops", "construct", "launch", "parked"])
    assert messages(in_workspace) == [
        "error: milestones 'build' not in ops, construct, launch, parked",
        "error: milestone 'build' not in ops, construct, launch, parked",
    ]

    assert cli.main(["migrate", "rename-value", "task", "milestone", "build", "construct", "--apply"]) == 0
    assert cli.main(["migrate", "rename-value", "person", "milestones", "build", "construct", "--apply"]) == 0
    capsys.readouterr()
    assert messages(in_workspace) == []
    assert frontmatter.parse((ops / "records/people" / f"{rid('P', 1)}-someone.md").read_text())["milestones"] == [
        "ops",
        "construct",
    ]


def test_rename_value_works_inside_lists_and_merges_a_word_that_is_already_there(ws):
    store = Store(ws.ops)
    for number, milestones in enumerate((["ops", "build", "launch"], ["build"], ["ops"]), start=1):
        meta = {**PERSON, "milestones": milestones, "tags": ["build"], "org": "build"}
        write_record(ws.ops, "people", rid("P", number), f"p{number}", meta)
    plan = migrate.rename_value(store, "person", "milestones", "build", "launch")
    assert [(c.path.name, c.what) for c in plan.changes] == [
        (f"{rid('P', 1)}-p1.md", "milestones: [ops, build, launch] -> [ops, launch]"),
        (f"{rid('P', 2)}-p2.md", "milestones: [build] -> [launch]"),
    ]
    migrate.apply(store, plan)
    people = {r.id: r.meta for r in store.load().people.values()}
    assert people[rid("P", 1)]["milestones"] == ["ops", "launch"]
    assert people[rid("P", 2)]["milestones"] == ["launch"]
    assert people[rid("P", 3)]["milestones"] == ["ops"]
    assert people[rid("P", 1)]["tags"] == ["build"]
    assert people[rid("P", 1)]["org"] == "build"


def test_rename_field_keeps_key_order_values_and_body(ws):
    store = Store(ws.ops)
    write_record(ws.ops, "tasks", "T-001", "one", {**TASK, "done_when": "Shipped"}, "Notes.\n\n- a\n")
    write_record(ws.ops, "tasks", "T-002", "two", without(TASK, "done_when"))
    plan = migrate.rename_field(store, "task", "done_when", "finished_when")
    assert [(c.path.name, c.what) for c in plan.changes] == [("T-001-one.md", "rename done_when -> finished_when")]
    assert plan.warnings == ["'finished_when' is not a field of task; uo check will report it as unknown"]
    migrate.apply(store, plan)
    path = ws.ops / "records/tasks/T-001-one.md"
    assert keys(path) == [k.replace("done_when", "finished_when") for k in ["id", *TASK]]
    assert frontmatter.parse(path.read_text())["finished_when"] == "Shipped"
    assert frontmatter.body(path.read_text()) == "Notes.\n\n- a\n"


def test_a_migration_that_cannot_rewrite_every_record_changes_none(ws, in_workspace, capsys):
    store = Store(ws.ops)
    write_record(ws.ops, "tasks", "T-001", "fine", {**TASK, "done_when": "Shipped"})
    write_record(ws.ops, "tasks", "T-002", "both", {**TASK, "finished_when": "Twice"})
    (ws.ops / "records/tasks/T-003-broken.md").write_text("no frontmatter here\n", encoding="utf-8")
    before = snapshot(ws.ops)
    with pytest.raises(StoreError) as refused:
        migrate.rename_field(store, "task", "done_when", "finished_when")
    assert str(refused.value) == (
        "migration refused, nothing was changed:\n"
        "records/tasks/T-002-both.md: has both 'done_when' and 'finished_when'; merge them by hand first\n"
        "records/tasks/T-003-broken.md: file must start with a '---' frontmatter block"
    )
    assert cli.main(["migrate", "rename-field", "task", "done_when", "finished_when", "--apply"]) == 1
    assert capsys.readouterr().err.startswith("error: migration refused, nothing was changed:\n")
    assert snapshot(ws.ops) == before


def test_add_field_goes_to_its_schema_position_and_skips_records_that_have_it(ws, capsys):
    store = Store(ws.ops)
    write_record(ws.ops, "tasks", "T-001", "one", without(TASK, "due", "people"), "Body.\n")
    write_record(ws.ops, "tasks", "T-002", "two", {**TASK, "due": "2026-10-01"})
    write_record(ws.ops, "tasks", "T-003", "three", without(TASK, "milestone", "due"))
    migrate.apply(store, migrate.add_field(store, "task", "due", "2026-12-01"))
    migrate.apply(store, migrate.add_field(store, "task", "people", f"[{rid('P', 1)}]"))
    tasks = ws.ops / "records/tasks"
    assert keys(tasks / "T-001-one.md") == [
        *["id", "title", "status", "owner", "milestone", "due", "people", "waiting_on"],
        *["done_when", "created", "closed"],
    ]
    assert keys(tasks / "T-003-three.md")[:6] == ["id", "title", "status", "owner", "due", "people"]
    two = frontmatter.parse((tasks / "T-002-two.md").read_text())
    assert (two["due"], two["people"]) == ("2026-10-01", [])
    assert frontmatter.parse((tasks / "T-001-one.md").read_text())["due"] == "2026-12-01"
    assert frontmatter.body((tasks / "T-001-one.md").read_text()) == "Body.\n"


def test_add_field_needs_a_field_of_the_schema_and_warns_about_values_it_cannot_fill(ws, in_workspace, capsys):
    store = Store(ws.ops)
    write_record(ws.ops, "tasks", "T-001", "one", without(TASK, "done_when", "milestone"))
    with pytest.raises(StoreError, match=r"task has no field 'colour' in the schema; add it to schema\.py first"):
        migrate.add_field(store, "task", "colour")
    assert cli.main(["migrate", "add-field", "task", "done_when"]) == 0
    assert capsys.readouterr().err == (
        "warning: task requires 'done_when'; records get an empty value that uo check reports (--value)\n"
    )
    assert cli.main(["migrate", "add-field", "task", "milestone", "--value", "moon"]) == 0
    assert capsys.readouterr().err == (
        "warning: task: milestone 'moon' not in ops, build, launch, parked; uo check will reject it\n"
    )


def test_drop_field_shows_the_value_it_removes(in_workspace, capsys):
    ops = in_workspace.ops
    write_record(ops, "tasks", "T-001", "one", {**TASK, "due": "2026-12-01"}, "Body.\n")
    write_record(ops, "tasks", "T-002", "two", TASK)
    write_record(ops, "tasks", "T-003", "three", without(TASK, "due"))
    assert cli.main(["migrate", "drop-field", "task", "due", "--apply"]) == 0
    captured = capsys.readouterr()
    assert captured.err == "warning: 'due' is still a field of task in the schema\n"
    assert captured.out == (
        "records/tasks/T-001-one.md: drop due (was: 2026-12-01)\n"
        "records/tasks/T-002-two.md: drop due (was: (empty))\n"
        "2 file(s) changed; run `uo check` to confirm\n"
    )
    for name in ("T-001-one", "T-002-two", "T-003-three"):
        assert "due" not in keys(ops / "records/tasks" / f"{name}.md")
    assert frontmatter.body((ops / "records/tasks/T-001-one.md").read_text()) == "Body.\n"


def test_the_log_and_the_decisions_are_never_rewritten(in_workspace, capsys):
    ops = in_workspace.ops
    write_record(ops, "tasks", "T-001", "one", {**TASK, "status": "todo"})
    log = ops / "records/log/2026-11.md"
    log.write_text(log.read_text() + "- 2026-11-02 | note | T-001 | status todo and milestone ops\n", encoding="utf-8")
    decisions = ops / "records/decisions.md"
    decisions.write_text(decisions.read_text() + "- Why: every task starts as todo\n", encoding="utf-8")
    before = snapshot(ops)
    assert cli.main(["migrate", "rename-value", "task", "status", "todo", "doing", "--apply"]) == 0
    after = snapshot(ops)
    assert after["records/tasks/T-001-one.md"] != before["records/tasks/T-001-one.md"]
    assert after["records/log/2026-11.md"] == before["records/log/2026-11.md"]
    assert after["records/decisions.md"] == before["records/decisions.md"]


def test_rename_value_warns_when_the_new_word_is_outside_the_vocabulary(in_workspace, capsys):
    write_record(in_workspace.ops, "tasks", "T-001", "one", {**TASK, "milestone": "build"})
    assert cli.main(["migrate", "rename-value", "task", "milestone", "build", "zzz"]) == 0
    captured = capsys.readouterr()
    assert captured.err == "warning: task: milestone 'zzz' not in ops, build, launch, parked; uo check will reject it\n"
    assert captured.out.startswith("records/tasks/T-001-one.md: milestone: build -> zzz\n")


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (["rename-value", "task", "nofield", "a", "b"], "task has no field 'nofield' in the schema"),
        (["rename-value", "task", "milestone", "ops", "ops"], "OLD and NEW are the same"),
        (["rename-value", "task", "milestone", "ops", ""], "OLD and NEW must not be empty"),
        (
            ["rename-value", "person", "milestones", "ops", "a,b"],
            "person.milestones is a list; a value cannot contain ','",
        ),
        (["rename-field", "task", "Bad", "x"], "field name 'Bad' must be lowercase letters, digits and '_'"),
        (["rename-field", "task", "a", "a"], "OLD and NEW are the same"),
        (["add-field", "task", "colour"], "task has no field 'colour' in the schema; add it to schema.py first"),
    ],
)
def test_migration_arguments_are_checked_before_any_record_is_read(in_workspace, capsys, command, message):
    write_record(in_workspace.ops, "tasks", "T-001", "one", TASK)
    before = snapshot(in_workspace.ops)
    assert cli.main(["migrate", *command, "--apply"]) == 1
    assert capsys.readouterr().err.startswith(f"error: {message}")
    assert snapshot(in_workspace.ops) == before
