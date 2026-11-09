"""Append-only log and decisions: what may change in a pushed file, and which version counts as pushed."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import commit, git

from unfloat_os.integrity import check_append_only
from unfloat_os.workspace import Workspace

LOG = "records/log/2026-11.md"
DECISIONS = "records/decisions.md"
LOG_TEXT = (
    "# Log 2026-11\n\n"
    "- 2026-11-02 | note | - | first\n"
    "- 2026-11-03 | note | - | second\n"
    "- 2026-11-04 | note | - | third\n"
)


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


@pytest.fixture
def ops(ws: Workspace) -> Path:
    """A records repository with a three-line log and one decision, committed; no remote."""
    (ws.ops / LOG).write_text(LOG_TEXT, encoding="utf-8")
    commit(ws.ops, "init")
    return ws.ops


@pytest.fixture
def clone_of_remote(tmp_path: Path):
    def make(name: str, remote: Path) -> Path:
        path = tmp_path / name
        git(tmp_path, "clone", "-q", str(remote), str(path))
        git(path, "config", "user.email", "test@example.org")
        git(path, "config", "user.name", "Test")
        git(path, "config", "commit.gpgsign", "false")
        return path

    return make


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(path))
    return path


@pytest.fixture
def published(ops: Path, remote: Path) -> Path:
    """`ops` with its first commit pushed to a local bare remote, tracking origin/main."""
    git(ops, "remote", "add", "origin", str(remote))
    git(ops, "push", "-q", "-u", "origin", "main")
    return ops


def test_nothing_to_compare_before_the_first_commit(ws: Workspace):
    assert check_append_only(ws.ops).issues == []


def test_appended_and_inserted_lines_are_accepted(ops: Path):
    replace(
        ops / LOG,
        "- 2026-11-03 | note | - | second\n",
        "- 2026-11-03 | note | - | between\n- 2026-11-03 | note | - | second\n",
    )
    append(ops / LOG, "- 2026-11-05 | note | - | last\n")
    replace(ops / DECISIONS, "- Decision: x\n", "- Decision: x\n- Replaced by D-002\n")
    (ops / "records" / "log" / "2026-12.md").write_text("# Log 2026-12\n\n", encoding="utf-8")
    assert check_append_only(ops).issues == []


def test_an_edited_line_is_an_error_naming_file_and_line(ops: Path):
    replace(ops / LOG, "second", "2nd")
    report = check_append_only(ops)
    assert [(i.where, i.level) for i in report.issues] == [(f"{LOG}:4", "error")]
    assert "'- 2026-11-03 | note | - | second'" in report.errors[0].message
    assert "append only" in report.errors[0].message


def test_a_deleted_line_is_an_error_at_the_first_lost_line(ops: Path):
    replace(ops / LOG, "- 2026-11-02 | note | - | first\n", "")
    replace(ops / LOG, "- 2026-11-04 | note | - | third\n", "")
    report = check_append_only(ops)
    assert [i.where for i in report.errors] == [f"{LOG}:3"]
    assert "(and 1 more)" in report.errors[0].message


def test_a_reordered_line_counts_as_changed(ops: Path):
    replace(
        ops / LOG,
        "- 2026-11-03 | note | - | second\n- 2026-11-04 | note | - | third\n",
        "- 2026-11-04 | note | - | third\n- 2026-11-03 | note | - | second\n",
    )
    assert [i.where for i in check_append_only(ops).errors] == [f"{LOG}:5"]


def test_a_deleted_file_is_an_error(ops: Path):
    (ops / LOG).unlink()
    report = check_append_only(ops)
    assert [(i.where, i.level) for i in report.issues] == [(LOG, "error")]
    assert "deleted" in report.errors[0].message


def test_an_old_decision_cannot_be_edited(ops: Path):
    replace(ops / DECISIONS, "| First", "| First, reworded")
    assert [i.where for i in check_append_only(ops).errors] == [f"{DECISIONS}:3"]


def test_unpushed_lines_may_be_corrected_but_pushed_ones_may_not(published: Path):
    append(published / LOG, "- 2026-11-05 | note | - | typo\n")
    commit(published, "log a line")
    replace(published / LOG, "typo", "fixed")
    assert check_append_only(published).issues == []  # committed but not pushed: the upstream version is the baseline

    git(published, "push", "-q")
    replace(published / LOG, "fixed", "changed again")
    assert [i.where for i in check_append_only(published).errors] == [f"{LOG}:6"]


def test_without_an_upstream_committed_lines_are_the_baseline(ops: Path):
    append(ops / LOG, "- 2026-11-05 | note | - | typo\n")
    commit(ops, "log a line")
    replace(ops / LOG, "typo", "fixed")
    assert [i.where for i in check_append_only(ops).errors] == [f"{LOG}:6"]


def test_lines_another_clone_pushed_are_not_missing_here(published: Path, remote: Path, clone_of_remote):
    other = clone_of_remote("other", remote)
    append(other / LOG, "- 2026-11-06 | note | - | from the other clone\n")
    commit(other, "log elsewhere")
    git(other, "push", "-q")
    git(published, "fetch", "-q")
    assert check_append_only(published).issues == []

    replace(published / LOG, "first", "1st")
    assert [i.where for i in check_append_only(published).errors] == [f"{LOG}:3"]
