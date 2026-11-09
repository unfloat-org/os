"""Health checks: hooks enabled, remotes as the manifest says, work pushed, CONTEXT current."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from conftest import commit, git

from unfloat_os import health
from unfloat_os.records import Store
from unfloat_os.workspace import Workspace


def with_remote(ws: Workspace, name: str, url: str) -> Workspace:
    members = dict(ws.members)
    members[name] = dataclasses.replace(members[name], remote=url)
    return dataclasses.replace(ws, members=members)


def with_settings(ws: Workspace, **settings: object) -> Workspace:
    return dataclasses.replace(ws, settings=settings)


def messages(report) -> list[str]:
    return [str(issue) for issue in report.issues]


# hooks ------------------------------------------------------------------------------------------


def test_hooks_not_set_is_an_error_naming_the_repo(ws):
    (ws.root / "research" / ".githooks").mkdir()
    report = health.check_hooks(ws)
    assert messages(report) == ["error: research: core.hooksPath is not set, expected '.githooks'; run `uo setup`"]


def test_hooks_pointing_elsewhere_is_an_error(ws):
    research = ws.root / "research"
    (research / ".githooks").mkdir()
    git(research, "config", "core.hooksPath", "other-hooks")
    report = health.check_hooks(ws)
    assert messages(report) == [
        "error: research: core.hooksPath is 'other-hooks', expected '.githooks'; run `uo setup`"
    ]


def test_hooks_enabled_is_clean(ws):
    research = ws.root / "research"
    (research / ".githooks").mkdir()
    git(research, "config", "core.hooksPath", ".githooks")
    assert not health.check_hooks(ws).issues


def test_repo_without_a_hooks_folder_needs_no_setting(ws):
    assert not health.check_hooks(ws).issues


# remotes ----------------------------------------------------------------------------------------


def test_missing_origin_is_an_error(ws):
    ws = with_remote(ws, "research", "https://example.org/org/research.git")
    report = health.check_remotes(ws)
    assert messages(report) == [
        "error: research: no remote 'origin'; the manifest says https://example.org/org/research.git"
    ]


def test_origin_different_from_manifest_is_an_error(ws):
    git(ws.root / "research", "remote", "add", "origin", "https://example.org/org/other.git")
    ws = with_remote(ws, "research", "https://example.org/org/research.git")
    report = health.check_remotes(ws)
    assert messages(report) == [
        "error: research: remote 'origin' is https://example.org/org/other.git, "
        "the manifest says https://example.org/org/research.git"
    ]


@pytest.mark.parametrize(
    "actual",
    [
        "https://example.org/org/research",
        "https://example.org/org/research.git",
        "https://example.org/org/research/",
        "https://example.org/org/research.git/",
    ],
)
def test_git_suffix_and_trailing_slash_are_ignored(ws, actual):
    git(ws.root / "research", "remote", "add", "origin", actual)
    ws = with_remote(ws, "research", "https://example.org/org/research.git")
    assert not health.check_remotes(ws).issues


def test_member_without_manifest_remote_is_not_compared(ws):
    assert not health.check_remotes(ws).issues


# sync -------------------------------------------------------------------------------------------


@pytest.fixture
def pushed(ws, tmp_path) -> Path:
    """The research repository with one pushed commit and a local bare origin."""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    git(bare, "init", "-q", "--bare", "-b", "main")
    research = ws.root / "research"
    (research / "a.md").write_text("a\n", encoding="utf-8")
    commit(research, "first")
    git(research, "remote", "add", "origin", str(bare))
    git(research, "push", "-q", "-u", "origin", "main")
    return research


def test_fully_pushed_repository_is_clean(ws, pushed):
    assert not health.check_sync(ws).issues


def test_unpushed_commits_are_counted(ws, pushed):
    for name in ("b", "c"):
        (pushed / f"{name}.md").write_text(name, encoding="utf-8")
        commit(pushed, f"add {name}")
    report = health.check_sync(ws)
    assert messages(report) == ["warning: research: 2 commit(s) not pushed"]
    assert not report.errors


def test_commits_behind_are_not_a_warning(ws, pushed, tmp_path):
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    git(other, "config", "user.email", "t@example.org")
    git(other, "config", "user.name", "T")
    git(other, "config", "commit.gpgsign", "false")
    (other / "n.md").write_text("n", encoding="utf-8")
    commit(other, "elsewhere")
    git(other, "push", "-q", "origin", "main")
    git(pushed, "fetch", "-q", "origin")
    assert not health.check_sync(ws).issues


def test_branch_without_upstream_is_a_warning(ws):
    research = ws.root / "research"
    (research / "a.md").write_text("a\n", encoding="utf-8")
    commit(research, "first")
    assert messages(health.check_sync(ws)) == ["warning: research: no upstream branch; commits are not pushed anywhere"]


def test_repository_without_commits_has_nothing_to_push(ws):
    assert not health.check_sync(ws).issues


# CONTEXT ----------------------------------------------------------------------------------------


def write_context(ws: Workspace, text: str) -> None:
    (ws.ops / "CONTEXT.md").write_text(text, encoding="utf-8")


def write_log(ws: Workspace, *dates: str) -> None:
    lines = "".join(f"- {date} | note | - | something happened\n" for date in dates)
    (ws.ops / "records" / "log" / "2026-11.md").write_text("# Log 2026-11\n\n" + lines, encoding="utf-8")


def check_context(ws: Workspace):
    return health.check_context_fresh(ws, Store(ws.ops).load())


def test_missing_context_file_is_an_error(ws):
    (ws.ops / "CONTEXT.md").unlink()
    assert messages(check_context(ws)) == ["error: ops/CONTEXT.md: missing"]


def test_context_without_updated_line_is_an_error(ws):
    write_context(ws, "# Context brief\n\nRead this first.\n")
    assert messages(check_context(ws)) == ["error: ops/CONTEXT.md: no 'Updated YYYY-MM-DD.' line in the first 10 lines"]


def test_updated_line_below_the_top_is_not_found(ws):
    write_context(ws, "# Context brief\n" + "filler\n" * 12 + "Updated 2026-11-09.\n")
    assert check_context(ws).errors


def test_context_with_invalid_date_is_an_error(ws):
    write_context(ws, "# Context brief\n\nUpdated 2026-13-45. Read this first.\n")
    assert messages(check_context(ws)) == [
        "error: ops/CONTEXT.md: 'Updated 2026-13-45.' is not a valid YYYY-MM-DD date"
    ]


def test_context_with_text_instead_of_date_is_an_error(ws):
    write_context(ws, "# Context brief\n\nUpdated yesterday. Read this first.\n")
    assert check_context(ws).errors


def test_context_current_with_the_log_is_clean(ws):
    write_context(ws, "# Context brief\n\nUpdated 2026-11-09. Read this first.\n")
    write_log(ws, "2026-11-05", "2026-11-09")
    assert not check_context(ws).issues


def test_context_exactly_at_the_limit_is_clean(ws):
    write_context(ws, "# Context brief\n\nUpdated 2026-11-06. Read this first.\n")
    write_log(ws, "2026-11-09")
    assert not check_context(ws).issues


def test_context_older_than_the_limit_is_a_warning(ws):
    write_context(ws, "# Context brief\n\nUpdated 2026-11-05. Read this first.\n")
    write_log(ws, "2026-11-02", "2026-11-09")
    report = check_context(ws)
    assert messages(report) == [
        "warning: ops/CONTEXT.md: updated 2026-11-05, but the log has events up to 2026-11-09 (more than 3 days later)"
    ]
    assert not report.errors


def test_stale_threshold_comes_from_the_manifest_settings(ws):
    write_context(ws, "# Context brief\n\nUpdated 2026-11-05. Read this first.\n")
    write_log(ws, "2026-11-09")
    assert not health.check_context_fresh(with_settings(ws, context_stale_days=5), Store(ws.ops).load()).issues
    strict = health.check_context_fresh(with_settings(ws, context_stale_days=1), Store(ws.ops).load())
    assert len(strict.warnings) == 1


def test_context_newer_than_the_log_is_clean(ws):
    write_context(ws, "# Context brief\n\nUpdated 2026-11-09. Read this first.\n")
    write_log(ws, "2026-11-01")
    assert not check_context(ws).issues
