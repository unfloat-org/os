"""The few git questions the checks ask, answered with the git command line."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def is_repo(path: Path) -> bool:
    return _git(path, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def tracked(repo: Path) -> list[Path]:
    """Tracked files plus untracked ones that are not ignored, relative to the repository root."""
    result = _git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode:
        return sorted(p.relative_to(repo) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts)
    return sorted({Path(name) for name in result.stdout.split("\0") if name and (repo / name).is_file()})


def staged(repo: Path) -> list[Path]:
    result = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [Path(name) for name in result.stdout.split("\0") if name]


def staged_size(repo: Path, path: Path) -> int:
    result = _git(repo, "cat-file", "-s", f":{path.as_posix()}")
    return int(result.stdout.strip() or 0)


def staged_added_lines(repo: Path) -> list[str]:
    result = _git(repo, "diff", "--cached", "-U0")
    return [line[1:] for line in result.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")]


def first_added(repo: Path, path: Path) -> str | None:
    """The oldest commit that added `path`, or None if it was never committed.

    No `--follow`: it attributes a new file to any identical file added earlier. After a move,
    the move commit counts as the addition, which the analysis check reports as unprovable order.
    """
    result = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", path.as_posix())
    commits = result.stdout.split()
    return commits[-1] if commits else None


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", older, newer).returncode == 0


def head(repo: Path) -> str | None:
    result = _git(repo, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def upstream(repo: Path) -> str | None:
    """The tracking branch of the current branch (for example `origin/main`), or None."""
    result = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return result.stdout.strip() if result.returncode == 0 else None


def ahead_behind(repo: Path) -> tuple[int, int] | None:
    """(commits not pushed, commits not pulled) against the tracking branch, as of the last fetch."""
    result = _git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    if result.returncode:
        return None
    ahead, behind = result.stdout.split()
    return int(ahead), int(behind)


def remote_url(repo: Path, remote: str = "origin") -> str | None:
    result = _git(repo, "remote", "get-url", remote)
    return result.stdout.strip() if result.returncode == 0 else None


def config_value(repo: Path, key: str) -> str | None:
    result = _git(repo, "config", "--get", key)
    return result.stdout.strip() if result.returncode == 0 else None


def show(repo: Path, ref: str, path: Path) -> str | None:
    """Content of `path` at `ref` (use ref "" for the index), or None if it does not exist there."""
    result = _git(repo, "show", f"{ref}:{path.as_posix()}")
    return result.stdout if result.returncode == 0 else None


def commits_touching(repo: Path, path: Path) -> list[str]:
    """Commits that changed `path` (a file or folder), oldest first."""
    result = _git(repo, "log", "--reverse", "--format=%H", "--", path.as_posix())
    return result.stdout.split()


def files_in_commit(repo: Path, commit: str) -> list[Path]:
    """Paths a commit added, changed or deleted, relative to the repository root."""
    result = _git(repo, "show", "--name-only", "--format=", "--no-renames", commit)
    return [Path(name) for name in result.stdout.splitlines() if name]


def changed_since_head(repo: Path, path: Path) -> bool:
    """Whether the working tree or index differs from HEAD at `path` (untracked files count)."""
    if _git(repo, "diff", "HEAD", "--quiet", "--", path.as_posix()).returncode:
        return True
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "--", path.as_posix())
    return bool(untracked.stdout.strip())


def fetch(repo: Path, remote: str = "origin") -> bool:
    """Update remote-tracking refs (network). False if the fetch failed."""
    return _git(repo, "fetch", "--quiet", remote).returncode == 0
