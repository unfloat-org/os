"""Analyses are frozen: the pre-registration never changes, and later changes need a dated amendment."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import commit, git

from unfloat_os import frontmatter, studies
from unfloat_os.workspace import RepoConfig

FOLDER = "analysis/a1_frozen"


def messages(research: Path) -> list[str]:
    report = studies.check(RepoConfig.load(research))
    return [f"{i.level}: {i.message}" for i in report.issues]


def short(research: Path) -> str:
    return git(research, "rev-parse", "HEAD").strip()[:7]


def put(research: Path, name: str, text: str) -> Path:
    path = research / FOLDER / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def study_index(research: Path) -> None:
    meta = {
        "id": "A1",
        "title": "Q",
        "status": "result",
        "task": "",
        "prereg": "PREREG.md",
        "result": "RESULT.md",
        "review": "",
        "verdict": "X",
    }
    put(research, "STUDY.md", frontmatter.render(meta, "\nWhy.\n"))


@pytest.fixture
def research(ws) -> Path:
    """An analysis with STUDY.md and PREREG.md in one commit, RESULT.md, a script and out/ in the next."""
    repo = ws.members["research"].path
    study_index(repo)
    put(repo, "PREREG.md", "plan\n")
    commit(repo, "study", ".")
    put(repo, "RESULT.md", "line one\nline two\n")
    put(repo, "run.py", "print(1)\n")
    put(repo, "out/table.csv", "a,b\n")
    commit(repo, "result", ".")
    return repo


def test_a_clean_analysis_passes(research):
    assert messages(research) == []


def test_committed_preregistration_edit_is_rejected_even_with_an_amendment(research):
    put(research, "PREREG.md", "plan, quietly improved\n")
    put(research, "AMENDMENTS.md", "## 2026-11-09 fix\n")
    commit(research, "edit prereg", ".")
    assert messages(research) == [f"error: {FOLDER}/PREREG.md is a pre-registration and changed in {short(research)}"]


def test_uncommitted_preregistration_edit_is_rejected(research):
    put(research, "PREREG.md", "plan, quietly improved\n")
    assert messages(research) == [f"error: {FOLDER}/PREREG.md is a pre-registration and changed uncommitted"]
    git(research, "add", "-A")
    assert messages(research) == [f"error: {FOLDER}/PREREG.md is a pre-registration and changed uncommitted"]


def test_script_edit_after_result_needs_a_dated_amendment_in_the_same_commit(research):
    put(research, "run.py", "print(2)\n")
    commit(research, "tweak", ".")
    rejected = short(research)
    assert messages(research) == [
        f"error: {FOLDER}/run.py changed in {rejected} without a dated entry in {FOLDER}/AMENDMENTS.md"
    ]

    put(research, "run.py", "print(3)\n")
    put(research, "AMENDMENTS.md", "## 2026-11-10 changed the script\n\nBecause.\n")
    commit(research, "tweak with amendment", ".")
    assert messages(research) == [
        f"error: {FOLDER}/run.py changed in {rejected} without a dated entry in {FOLDER}/AMENDMENTS.md"
    ]


def test_amendment_without_a_dated_heading_does_not_count(research):
    put(research, "run.py", "print(2)\n")
    put(research, "AMENDMENTS.md", "changed the script\n")
    commit(research, "tweak", ".")
    assert messages(research) == [
        f"error: {FOLDER}/run.py changed in {short(research)} without a dated entry in {FOLDER}/AMENDMENTS.md"
    ]


def test_amended_edit_is_accepted_in_a_clean_history(research):
    put(research, "run.py", "print(2)\n")
    put(research, "out/table.csv", "a,b\n1,2\n")
    put(research, "AMENDMENTS.md", "## 2026-11-10 rerun\n")
    commit(research, "amended", ".")
    put(research, "run.py", "print(3)\n")
    put(research, "AMENDMENTS.md", "## 2026-11-10 rerun\n\n## 2026-11-11 again\n")
    commit(research, "amended twice", ".")
    assert messages(research) == []


def test_uncommitted_edits_need_an_uncommitted_dated_amendment(research):
    put(research, "out/table.csv", "a,b\n1,2\n")
    put(research, "out/new.csv", "x\n")
    assert messages(research) == [
        f"error: {FOLDER}/out/new.csv changed uncommitted without a dated entry in {FOLDER}/AMENDMENTS.md",
        f"error: {FOLDER}/out/table.csv changed uncommitted without a dated entry in {FOLDER}/AMENDMENTS.md",
    ]
    put(research, "AMENDMENTS.md", "## 2026-11-10 rerun\n")
    assert messages(research) == []


def test_result_may_grow_but_not_change_existing_lines(research):
    put(research, "RESULT.md", "line one\nline two\nline three\n")
    commit(research, "append", ".")
    assert messages(research) == []

    put(research, "RESULT.md", "line one\nline TWO\nline three\n")
    commit(research, "change")
    assert messages(research) == [
        f"error: {FOLDER}/RESULT.md changed in {short(research)} without a dated entry in {FOLDER}/AMENDMENTS.md"
    ]


def test_uncommitted_result_edits_follow_the_same_rule(research):
    put(research, "RESULT.md", "line one\nline two\nline three\n")
    assert messages(research) == []
    put(research, "RESULT.md", "line one\n")
    assert messages(research) == [
        f"error: {FOLDER}/RESULT.md changed uncommitted without a dated entry in {FOLDER}/AMENDMENTS.md"
    ]
    put(research, "AMENDMENTS.md", "## 2026-11-10 trimmed\n")
    assert messages(research) == []


def test_changes_before_the_study_index_existed_are_grandfathered(ws):
    repo = ws.members["research"].path
    put(repo, "PREREG.md", "plan\n")
    put(repo, "run.py", "print(1)\n")
    commit(repo, "early", ".")
    put(repo, "PREREG.md", "plan v2\n")
    put(repo, "RESULT.md", "r\n")
    commit(repo, "early edit", ".")
    put(repo, "run.py", "print(2)\n")
    put(repo, "RESULT.md", "r2\n")
    commit(repo, "early result edit", ".")
    study_index(repo)
    commit(repo, "enter the contract", ".")
    assert messages(repo) == []

    put(repo, "run.py", "print(3)\n")
    commit(repo, "after the contract")
    assert messages(repo) == [
        f"error: {FOLDER}/run.py changed in {short(repo)} without a dated entry in {FOLDER}/AMENDMENTS.md"
    ]


def test_files_changed_before_the_result_was_committed_are_free(ws):
    repo = ws.members["research"].path
    study_index(repo)
    put(repo, "PREREG.md", "plan\n")
    put(repo, "run.py", "print(1)\n")
    commit(repo, "study", ".")
    put(repo, "run.py", "print(2)\n")
    commit(repo, "script work")
    put(repo, "run.py", "print(3)\n")
    put(repo, "RESULT.md", "r\n")
    commit(repo, "result")
    assert messages(repo) == []


def test_review_and_study_index_edits_are_always_accepted(research):
    put(research, "REVIEW.md", "ok\n")
    commit(research, "review", ".")
    put(research, "REVIEW.md", "ok, with notes\n")
    study_index(research)
    (research / FOLDER / "STUDY.md").write_text(
        (research / FOLDER / "STUDY.md").read_text().replace("Why.", "Why, better.")
    )
    commit(research, "edit both", ".")
    put(research, "REVIEW.md", "changed again\n")
    assert messages(research) == []


def test_a_study_without_a_preregistration_still_freezes_after_the_result(ws):
    repo = ws.members["research"].path
    study_index(repo)
    (repo / FOLDER / "STUDY.md").write_text(
        (repo / FOLDER / "STUDY.md").read_text().replace("prereg: PREREG.md", "prereg: none")
    )
    put(repo, "RESULT.md", "r\n")
    put(repo, "run.py", "print(1)\n")
    commit(repo, "all at once", ".")
    assert messages(repo) == []
    put(repo, "run.py", "print(2)\n")
    assert messages(repo) == [
        f"error: {FOLDER}/run.py changed uncommitted without a dated entry in {FOLDER}/AMENDMENTS.md"
    ]
