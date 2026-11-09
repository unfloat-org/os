"""Sequential IDs with two clones of the records repository: collisions, renumbering and the push check."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import DRAFT, PERSON, TASK, commit, git, git_init, rid, write_record

from unfloat_os import frontmatter, ids
from unfloat_os.integrity import check_append_only
from unfloat_os.links import check_links
from unfloat_os.records import Store, StoreError
from unfloat_os.workspace import RepoConfig, Workspace

LOG = "records/log/2026-11.md"
DECISIONS = "records/decisions.md"
SEED_LOG = "# Log 2026-11\n\n- 2026-11-02 | task | {task} | seed\n"


@dataclass
class Pair:
    a: Path  # the `ws` records repository, pushed
    b: Path  # a second clone
    remote: Path


@pytest.fixture
def pair(ws: Workspace, tmp_path: Path) -> Pair:
    a = ws.ops
    write_record(a, "tasks", rid("T", 1), "seed", TASK)
    write_record(a, "people", rid("P", 1), "seed", PERSON)
    (a / LOG).write_text(SEED_LOG.format(task=rid("T", 1)), encoding="utf-8")
    commit(a, "seed")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(a, "remote", "add", "origin", str(remote))
    git(a, "push", "-q", "-u", "origin", "main")
    b = tmp_path / "clone-b" / "ops"
    git(tmp_path, "clone", "-q", str(remote), str(b))
    for key, value in (("user.email", "b@example.org"), ("user.name", "B"), ("commit.gpgsign", "false")):
        git(b, "config", key, value)
    return Pair(a, b, remote)


def task(ops: Path, number: int, slug: str, **meta: str) -> Path:
    return write_record(ops, "tasks", rid("T", number), slug, {**TASK, "title": slug.title(), **meta})


def push(repo: Path, message: str = "work") -> None:
    commit(repo, message)
    git(repo, "push", "-q")


def fetch(repo: Path) -> None:
    git(repo, "fetch", "-q")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# collisions ---------------------------------------------------------------


def test_collision_after_the_other_clone_pushed_the_same_task_id(pair: Pair):
    task(pair.a, 2, "alpha")
    push(pair.a)
    task(pair.b, 2, "beta")
    commit(pair.b, "beta")
    assert ids.collisions(pair.b) == []  # nothing fetched yet: the check never touches the network

    fetch(pair.b)
    assert ids.collisions(pair.b) == [
        ids.Collision(rid("T", 2), "records/tasks/T-002-beta.md", "records/tasks/T-002-alpha.md")
    ]


def test_different_ids_do_not_collide(pair: Pair):
    task(pair.a, 2, "alpha")
    push(pair.a)
    task(pair.b, 3, "beta")
    commit(pair.b, "beta")
    fetch(pair.b)
    assert ids.collisions(pair.b) == []


def test_an_id_already_pulled_is_not_a_collision(pair: Pair):
    task(pair.a, 2, "alpha")
    push(pair.a)
    git(pair.b, "pull", "-q")
    assert ids.collisions(pair.b) == []


def test_a_record_not_yet_committed_counts_as_added_here(pair: Pair):
    task(pair.a, 2, "alpha")
    push(pair.a)
    task(pair.b, 2, "beta")
    fetch(pair.b)
    assert [c.id for c in ids.collisions(pair.b)] == [rid("T", 2)]


def test_kinds_have_separate_number_ranges(pair: Pair):
    write_record(pair.a, "drafts", rid("DR", 2), "alpha", DRAFT)
    push(pair.a)
    write_record(pair.b, "people", rid("P", 2), "beta", PERSON)
    commit(pair.b, "beta")
    fetch(pair.b)
    assert ids.collisions(pair.b) == []


def test_decisions_collide_by_heading(pair: Pair):
    append = "\n## D-002 | 2026-11-05 | {title}\n\n- Decision: x\n"
    with (pair.a / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write(append.format(title="Alpha"))
    push(pair.a)
    with (pair.b / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write(append.format(title="Beta"))
    commit(pair.b, "beta")
    fetch(pair.b)
    assert ids.collisions(pair.b) == [
        ids.Collision("D-002", f"{DECISIONS}: D-002 | 2026-11-05 | Beta", f"{DECISIONS}: D-002 | 2026-11-05 | Alpha")
    ]


def test_no_upstream_means_no_collisions(ws: Workspace):
    task(ws.ops, 1, "alpha")
    commit(ws.ops, "alpha")
    assert ids.collisions(ws.ops) == []


# highest_upstream ---------------------------------------------------------


def test_highest_upstream_sees_the_other_clones_ids_after_fetch(pair: Pair):
    task(pair.a, 5, "alpha")
    write_record(pair.a, "drafts", rid("DR", 4), "alpha", DRAFT)
    with (pair.a / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write("\n## D-003 | 2026-11-05 | Alpha\n\n- Decision: x\n")
    push(pair.a)
    task(pair.b, 9, "local-only")
    assert [ids.highest_upstream(pair.b, p) for p in ("T", "P", "DR", "D")] == [1, 1, 0, 1]

    fetch(pair.b)
    assert [ids.highest_upstream(pair.b, p) for p in ("T", "P", "DR", "D")] == [5, 1, 4, 3]


def test_highest_upstream_is_zero_without_an_upstream(ws: Workspace):
    task(ws.ops, 4, "alpha")
    commit(ws.ops, "alpha")
    assert ids.highest_upstream(ws.ops, "T") == 0


def test_highest_upstream_rejects_an_unknown_prefix(pair: Pair):
    with pytest.raises(ValueError, match="unknown ID prefix 'X'"):
        ids.highest_upstream(pair.a, "X")


def test_replacement_ids_are_distinct_and_above_both_sides(pair: Pair):
    task(pair.a, 2, "alpha")
    task(pair.a, 3, "alpha-two")
    push(pair.a)
    task(pair.b, 2, "beta")
    task(pair.b, 3, "beta-two")
    commit(pair.b, "beta")
    fetch(pair.b)
    found = ids.collisions(pair.b)
    assert ids.new_ids(pair.b, found) == {rid("T", 2): rid("T", 4), rid("T", 3): rid("T", 5)}


# renumber -----------------------------------------------------------------


def collide(pair: Pair) -> None:
    """Both clones took T-002; clone b references its own T-002 everywhere, and has fetched."""
    task(pair.a, 2, "alpha")
    push(pair.a)
    old = rid("T", 2)
    task(pair.b, 2, "beta")
    person = pair.b / "records" / "people" / f"{rid('P', 1)}-seed.md"
    person.write_text(frontmatter.update(read(person), {"next_task": old}), encoding="utf-8")
    write_record(
        pair.b,
        "drafts",
        rid("DR", 2),
        "mail",
        {**DRAFT, "task": old},
        f"See [{old}](../tasks/{old}-beta.md), also {old}, but not {old}0 or {rid('DR', 2)}.\n",
    )
    with (pair.b / LOG).open("a", encoding="utf-8") as handle:
        handle.write(f"- 2026-11-09 | task | {old} | created beta\n")
    with (pair.b / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write(f"\n## D-002 | 2026-11-09 | Use beta\n\n- Source: {old}\n")
    (pair.b / "notes.md").write_text(f"Working on {old} today.\n", encoding="utf-8")
    (pair.b / "config").mkdir(exist_ok=True)
    (pair.b / "config" / "hook.toml").write_text(f"# {old}\n", encoding="utf-8")
    fetch(pair.b)


def test_renumber_rewrites_file_references_links_and_unpushed_lines(pair: Pair):
    collide(pair)
    old, new = rid("T", 2), rid("T", 3)
    log_before = read(pair.b / LOG)

    result = ids.renumber(pair.b, old)

    assert (result.old, result.new) == (old, new)
    assert result.moved == (Path("records/tasks/T-002-beta.md"), Path("records/tasks/T-003-beta.md"))
    assert not (pair.b / result.moved[0]).exists()
    moved = pair.b / result.moved[1]
    assert frontmatter.parse(read(moved))["id"] == new
    assert frontmatter.parse(read(pair.b / f"records/people/{rid('P', 1)}-seed.md"))["next_task"] == new
    draft = read(pair.b / f"records/drafts/{rid('DR', 2)}-mail.md")
    assert frontmatter.parse(draft)["task"] == new
    assert f"[{new}](../tasks/{new}-beta.md), also {new}, but not {old}0 or {rid('DR', 2)}." in draft
    assert read(pair.b / "notes.md") == f"Working on {new} today.\n"
    assert read(pair.b / LOG) == log_before.replace(f"| {old} |", f"| {new} |")
    assert read(pair.b / LOG).count(f"| {rid('T', 1)} |") == 1  # a pushed line about another task is untouched
    assert f"- Source: {new}\n" in read(pair.b / DECISIONS)
    assert read(pair.b / "config" / "hook.toml") == f"# {old}\n"
    assert [str(m) for m in result.elsewhere] == ["ops/config/hook.toml:1"]
    assert set(result.rewritten) == {
        Path("records/tasks/T-003-beta.md"),
        Path(f"records/people/{rid('P', 1)}-seed.md"),
        Path(f"records/drafts/{rid('DR', 2)}-mail.md"),
        Path(LOG),
        Path(DECISIONS),
        Path("notes.md"),
    }

    assert ids.collisions(pair.b) == []
    assert check_append_only(pair.b).issues == []
    assert Store(pair.b).load().report.errors == []
    assert check_links(RepoConfig.load(pair.b)).errors == []


def test_after_renumbering_pull_and_push_go_through(pair: Pair):
    collide(pair)
    ids.renumber(pair.b, rid("T", 2))
    commit(pair.b, "renumber")
    git(pair.b, "pull", "-q", "--rebase")
    git(pair.b, "push", "-q")
    git(pair.a, "pull", "-q")
    assert sorted(Store(pair.a).load().tasks) == [rid("T", 1), rid("T", 2), rid("T", 3)]
    assert Store(pair.a).load().report.errors == []
    assert ids.collisions(pair.b) == []


def test_renumber_takes_the_next_number_free_here_and_upstream(pair: Pair):
    task(pair.a, 5, "alpha")
    push(pair.a)
    task(pair.b, 2, "beta")
    fetch(pair.b)
    assert ids.renumber(pair.b, rid("T", 2)).new == rid("T", 6)


def test_renumber_to_a_chosen_id(pair: Pair):
    task(pair.b, 2, "beta")
    result = ids.renumber(pair.b, rid("T", 2), rid("T", 8))
    assert (pair.b / "records/tasks/T-008-beta.md").is_file()
    assert result.new == rid("T", 8)


def test_renumber_refuses_a_pushed_id_and_changes_nothing(pair: Pair):
    task(pair.a, 2, "alpha")
    push(pair.a)
    before = read(pair.a / "records/tasks/T-002-alpha.md")
    for pushed in (rid("T", 1), rid("T", 2)):
        with pytest.raises(StoreError, match=f"{pushed} is already pushed to origin/main"):
            ids.renumber(pair.a, pushed)
    assert read(pair.a / "records/tasks/T-002-alpha.md") == before
    assert not list((pair.a / "records/tasks").glob("T-003-*"))


@pytest.mark.parametrize(
    ("file", "line", "where"),
    [
        (LOG, "- 2026-11-05 | task | {old} | mentioned before the task was pushed\n", f"{LOG}:4"),
        (DECISIONS, "\n## D-002 | 2026-11-05 | Pushed\n\n- Source: {old}\n", f"{DECISIONS}:9"),
    ],
)
def test_renumber_refuses_when_a_pushed_line_mentions_the_id(pair: Pair, file: str, line: str, where: str):
    old = rid("T", 4)
    task(pair.a, 4, "late")  # the file itself is never committed
    with (pair.a / file).open("a", encoding="utf-8") as handle:
        handle.write(line.format(old=old))
    commit(pair.a, "mention", file)
    git(pair.a, "push", "-q")
    with pytest.raises(StoreError, match=f"{old} appears in {where}, which is already pushed to origin/main"):
        ids.renumber(pair.a, old)
    assert (pair.a / "records/tasks/T-004-late.md").is_file()


def test_renumber_refuses_bad_requests(pair: Pair):
    task(pair.a, 2, "alpha")
    push(pair.a)
    task(pair.b, 3, "beta")
    task(pair.b, 4, "gamma")
    fetch(pair.b)
    with pytest.raises(StoreError, match="T-002 does not exist in this clone; it exists upstream, pull it first"):
        ids.renumber(pair.b, rid("T", 2))
    with pytest.raises(StoreError, match=r"T-009 does not exist in this clone$"):
        ids.renumber(pair.b, rid("T", 9))
    with pytest.raises(StoreError, match="T-002 is already taken upstream"):
        ids.renumber(pair.b, rid("T", 3), rid("T", 2))
    with pytest.raises(StoreError, match="T-004 is already taken here"):
        ids.renumber(pair.b, rid("T", 3), rid("T", 4))
    with pytest.raises(StoreError, match="is not a T-### ID"):
        ids.renumber(pair.b, rid("T", 3), rid("D", 9))
    with pytest.raises(StoreError, match="'T-3' is not a record ID"):
        ids.renumber(pair.b, "T-3")
    assert (pair.b / "records/tasks/T-003-beta.md").is_file()


def test_renumber_a_decision_inside_decisions_md(pair: Pair):
    block = "\n## D-002 | 2026-11-05 | {title}\n\n- Decision: x\n"
    with (pair.a / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write(block.format(title="Alpha"))
    push(pair.a)
    old, new = "D-002", "D-003"
    with (pair.b / DECISIONS).open("a", encoding="utf-8") as handle:
        handle.write(block.format(title="Beta") + f"- Why: follows {old}.\n")
    task(pair.b, 2, "uses-beta", waiting_on=old, status="waiting")
    fetch(pair.b)
    assert [c.id for c in ids.collisions(pair.b)] == [old]

    result = ids.renumber(pair.b, old)

    assert result.new == new and result.moved is None
    text = read(pair.b / DECISIONS)
    assert f"## {new} | 2026-11-05 | Beta" in text
    assert f"follows {new}." in text
    assert old not in text
    assert "## D-001 | 2026-11-01 | First" in text
    assert frontmatter.parse(read(pair.b / "records/tasks/T-002-uses-beta.md"))["waiting_on"] == new
    assert ids.collisions(pair.b) == []


def test_renumber_reports_mentions_in_other_repositories_without_editing_them(pair: Pair, tmp_path: Path):
    old = rid("T", 2)
    task(pair.b, 2, "beta")
    research = tmp_path / "research"
    (research / "analysis").mkdir(parents=True)
    git_init(research)
    study = research / "analysis" / "STUDY.md"
    study.write_text(f"# Study\n\ntask: {old}\n", encoding="utf-8")
    (research / "data.bin").write_bytes(b"\xff\xfe" + old.encode())
    result = ids.renumber(pair.b, old, others=[research])
    assert [str(m) for m in result.elsewhere] == ["research/analysis/STUDY.md:3"]
    assert read(study) == f"# Study\n\ntask: {old}\n"


# commands -----------------------------------------------------------------


def run(argv: list[str], ws: Workspace) -> int:
    parser = argparse.ArgumentParser(prog="uo")
    ids.register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return args.handler(args, ws)


def test_push_check_passes_without_a_collision(pair: Pair, capsys):
    task(pair.a, 2, "alpha")
    push(pair.a)
    task(pair.b, 3, "beta")
    commit(pair.b, "beta")
    assert run(["push-check"], Workspace.load(pair.b.parent)) == 0
    assert "no ID collisions with origin/main" in capsys.readouterr().out


def test_push_check_is_quiet_without_an_upstream(ws: Workspace, capsys):
    assert run(["push-check"], ws) == 0
    assert "no upstream branch" in capsys.readouterr().out


def test_push_check_fetches_and_names_the_commands_that_fix_the_collision(pair: Pair, capsys):
    task(pair.b, 2, "beta")
    task(pair.b, 3, "beta-two")
    commit(pair.b, "beta")
    task(pair.a, 2, "alpha")
    task(pair.a, 3, "alpha-two")
    push(pair.a)  # b has not fetched: the command does it

    b = Workspace.load(pair.b.parent)
    assert run(["push-check"], b) == 1
    err = capsys.readouterr().err
    assert "T-002: here records/tasks/T-002-beta.md; upstream records/tasks/T-002-alpha.md" in err
    assert "T-003: here records/tasks/T-003-beta-two.md; upstream records/tasks/T-003-alpha-two.md" in err
    commands = [line.strip() for line in err.splitlines() if line.strip().startswith("uo renumber")]
    assert commands == ["uo renumber T-002 T-004", "uo renumber T-003 T-005"]

    for command in commands:
        assert run(command.split()[1:], b) == 0
    capsys.readouterr()
    assert run(["push-check"], b) == 0


def test_renumber_command_prints_what_it_did(pair: Pair, capsys):
    task(pair.b, 2, "beta")
    assert run(["renumber", rid("T", 2), rid("T", 7)], Workspace.load(pair.b.parent)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "T-002 -> T-007"
    assert "renamed records/tasks/T-002-beta.md -> records/tasks/T-007-beta.md" in out
    assert "rewrote records/tasks/T-007-beta.md" in out
