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
