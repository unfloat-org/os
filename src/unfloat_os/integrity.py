"""Append-only records: lines of the log and of `decisions.md` that were pushed never change.

Rule: the event log (`records/log/*.md`) and the decisions file (`records/decisions.md`) only grow. A line that
is already published must stay in its file, unchanged and in order; new lines may be added anywhere (for
example "Replaced by D-###" inside an old decision). A published file is never deleted. Lines that exist only
in this clone (not yet pushed) may still be corrected: the history of what was agreed is only worth
something once someone else could have read it.

Which lines count as published is decided by `baseline()`: the version at the merge base of HEAD and the
upstream branch (as of the last fetch), which is the upstream version itself unless the upstream has moved on
since; without an upstream, HEAD. The merge base, not the upstream tip, so that lines another clone appended
and this clone has not pulled yet never look like deletions. Forward-looking only: earlier edits to
history are not audited.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from unfloat_os import gitfiles
from unfloat_os.records import RECORDS
from unfloat_os.report import Report

DECISIONS = Path(RECORDS) / "decisions.md"
LOG_DIR = Path(RECORDS) / "log"
EXCERPT = 60


@dataclass(frozen=True)
class Baseline:
    """The commit whose log and decision lines count as published."""

    ref: str
    label: str  # "pushed to origin/main", or "committed" when there is no upstream


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def baseline(ops_root: Path) -> Baseline | None:
    """The published state of the records repository, or None before the first commit."""
    head = gitfiles.head(ops_root)
    if head is None:
        return None
    tip = gitfiles.upstream(ops_root)
    if tip is None:
        return Baseline(head, "committed")
    merged = _git(ops_root, "merge-base", "HEAD", tip)
    return Baseline(merged.stdout.strip() if merged.returncode == 0 else tip, f"pushed to {tip}")


def ls_tree(ops_root: Path, ref: str, folder: Path) -> list[Path]:
    """Paths directly inside `folder` at `ref` (empty if it does not exist there)."""
    result = _git(ops_root, "ls-tree", "-z", "--name-only", ref, f"{folder.as_posix()}/")
    return [Path(name) for name in result.stdout.split("\0") if name]


def published_texts(ops_root: Path, base: Baseline) -> dict[Path, str]:
    """The log files and the decisions file as they were at the baseline, by path in the repository."""
    paths = [rel for rel in ls_tree(ops_root, base.ref, LOG_DIR) if rel.suffix == ".md"] + [DECISIONS]
    texts = {}
    for rel in paths:
        text = gitfiles.show(ops_root, base.ref, rel)
        if text is not None:
            texts[rel] = text
    return texts


def lost_lines(old: list[str], new: list[str]) -> list[int]:
    """1-based numbers of the lines of `old` that `new` does not keep, in order, as a subsequence.

    New lines anywhere are fine; an edited, deleted or moved line is lost.
    """
    lost = []
    position = 0
    for number, line in enumerate(old, start=1):
        try:
            position = new.index(line, position) + 1
        except ValueError:
            lost.append(number)
    return lost


def check_append_only(ops_root: Path) -> Report:
    report = Report()
    base = baseline(ops_root)
    if base is None:
        return report
    for rel, old_text in sorted(published_texts(ops_root, base).items()):
        path = ops_root / rel
        if not path.is_file():
            report.error(
                rel.as_posix(), f"deleted, but the version {base.label} has it and these files are append only"
            )
            continue
        old = old_text.splitlines()
        lost = lost_lines(old, path.read_text(encoding="utf-8", errors="replace").splitlines())
        if not lost:
            continue
        first = lost[0]
        excerpt = old[first - 1]
        if len(excerpt) > EXCERPT:
            excerpt = excerpt[: EXCERPT - 3] + "..."
        more = f" (and {len(lost) - 1} more)" if len(lost) > 1 else ""
        report.error(
            f"{rel.as_posix()}:{first}",
            f"line {first} ('{excerpt}') of the version {base.label} was changed or deleted{more}; "
            "such lines are append only, add a new line instead",
        )
    return report
