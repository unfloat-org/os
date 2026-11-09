"""The analysis contract (C4), the same in every repository with an `analysis/` folder.

Each `analysis/<code>_<slug>/` holds a STUDY.md index (fields in `schema.STUDY`). When the study has a
pre-registration, git history must show it committed before the result and before anything in `out/`.

Analyses are frozen records. The contract starts at the commit that first added the folder's STUDY.md
(earlier history is grandfathered). From then on:

1. The pre-registration never changes once committed. No amendment path.
2. Once the result file is committed, every other file in the folder except STUDY.md, REVIEW.md and
   AMENDMENTS.md (scripts, config, outputs) changes only in a commit that also adds a dated `## YYYY-MM-DD`
   entry to the folder's AMENDMENTS.md. The result file may grow by added lines; changing or deleting
   existing lines needs the entry too. Uncommitted changes against HEAD are held to the same rule.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from unfloat_os import frontmatter, gitfiles, schema
from unfloat_os.frontmatter import Meta
from unfloat_os.report import Report
from unfloat_os.workspace import RepoConfig

FOLDER = "analysis"
INDEX = "STUDY.md"
AMENDMENTS = "AMENDMENTS.md"
EXEMPT = (INDEX, "REVIEW.md", AMENDMENTS)
DATED = re.compile(r"^## \d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Study:
    repo: str
    folder: Path
    meta: Meta

    @property
    def id(self) -> str:
        return schema.text(self.meta, "id")


def folders(repo: RepoConfig) -> list[Path]:
    root = repo.path / FOLDER
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


def load(repo: RepoConfig) -> tuple[list[Study], Report]:
    report = Report()
    found = []
    for folder in folders(repo):
        where = f"{repo.name}/{folder.relative_to(repo.path).as_posix()}"
        index = folder / INDEX
        if not index.is_file():
            report.error(where, f"missing {INDEX}")
            continue
        try:
            meta = frontmatter.parse(index.read_text(encoding="utf-8"))
        except frontmatter.FrontmatterError as exc:
            report.error(f"{where}/{INDEX}", str(exc))
            continue
        found.append(Study(repo.name, folder, meta))
    return found, report


def _order(repo: Path, study_where: str, prereg: Path, later: Path, report: Report) -> None:
    """The pre-registration must reach git before `later` does."""
    before = gitfiles.first_added(repo, prereg.relative_to(repo))
    after = gitfiles.first_added(repo, later.relative_to(repo))
    if after is None:
        return
    name = later.relative_to(repo).as_posix()
    if before is None:
        report.error(study_where, f"{name} is committed but the pre-registration is not")
    elif before == after:
        report.warning(
            study_where, f"{name} and the pre-registration were added in the same commit; order not provable"
        )
    elif not gitfiles.is_ancestor(repo, before, after):
        report.error(study_where, f"{name} was committed before the pre-registration")


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


def _statuses(raw: str) -> dict[str, str]:
    """Parse `--name-status -z` output into {path: status letter}."""
    parts = raw.split("\0")
    return {path: status for status, path in zip(parts[0::2], parts[1::2], strict=False) if status and path}


def _commits(repo: Path, folder: str) -> list[str]:
    """Non-merge commits that touched the folder, oldest first."""
    return _run(repo, "log", "--reverse", "--no-merges", "--format=%H", "--", folder).split()


def _changes_in(repo: Path, commit: str, folder: str) -> dict[str, str]:
    raw = _run(
        repo, "diff-tree", "-r", "--root", "--no-commit-id", "--no-renames", "--name-status", "-z", commit, "--", folder
    )
    return _statuses(raw)


def _changes_uncommitted(repo: Path, folder: str) -> dict[str, str]:
    found = _statuses(_run(repo, "diff", "HEAD", "--no-renames", "--name-status", "-z", "--", folder))
    for name in _run(repo, "ls-files", "-z", "--others", "--exclude-standard", "--", folder).split("\0"):
        if name:
            found[name] = "A"
    return found


def _removes_lines(repo: Path, path: str, commit: str | None) -> bool:
    """Whether the change to `path` (in `commit`, or uncommitted) deletes or alters existing lines."""
    if commit is None:
        raw = _run(repo, "diff", "HEAD", "--no-renames", "--numstat", "--", path)
    else:
        raw = _run(repo, "diff-tree", "-r", "--root", "--no-commit-id", "--no-renames", "--numstat", commit, "--", path)
    for line in raw.splitlines():
        deleted = line.split("\t")[1]
        if deleted != "0":
            return True
    return False


def _dated_entries(text: str | None) -> Counter[str]:
    return Counter(line for line in (text or "").splitlines() if DATED.match(line))


def _amends(repo: Path, path: str, commit: str | None) -> bool:
    """Whether the change to the AMENDMENTS file `path` adds a dated entry."""
    if commit is None:
        file = repo / path
        before = gitfiles.show(repo, "HEAD", Path(path))
        after = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""
    else:
        before = gitfiles.show(repo, f"{commit}^", Path(path))
        after = gitfiles.show(repo, commit, Path(path))
    return bool(_dated_entries(after) - _dated_entries(before))


def _frozen(repo: Path, where: str, folder: Path, prereg: str, result: str, report: Report) -> None:
    """Enforce rules 1 and 2 of the module docstring for one analysis folder."""
    rel = folder.relative_to(repo).as_posix()
    index = f"{rel}/{INDEX}"
    started = gitfiles.first_added(repo, Path(index))
    if started is None:
        return
    commits = _commits(repo, rel)
    if started not in commits:
        return
    amendments = f"{rel}/{AMENDMENTS}"
    exempt = {f"{rel}/{name}" for name in EXEMPT}
    prereg_path = f"{rel}/{prereg}" if prereg and prereg != "none" else None
    result_path = f"{rel}/{result}" if result else None

    def after(path: str | None) -> list[str]:
        """Commits strictly after the later of the study start and the first commit of `path`."""
        first = gitfiles.first_added(repo, Path(path)) if path else None
        if first is None or first not in commits:
            return []
        return commits[max(commits.index(first), commits.index(started)) + 1 :]

    if prereg_path:
        for commit in after(prereg_path):
            if prereg_path in _changes_in(repo, commit, rel):
                report.error(where, f"{prereg_path} is a pre-registration and changed in {commit[:7]}")
        if gitfiles.first_added(repo, Path(prereg_path)) and gitfiles.changed_since_head(repo, Path(prereg_path)):
            report.error(where, f"{prereg_path} is a pre-registration and changed uncommitted")

    if not result_path or gitfiles.first_added(repo, Path(result_path)) is None:
        return
    for commit in after(result_path):
        changes = _changes_in(repo, commit, rel)
        if amendments in changes and _amends(repo, amendments, commit):
            continue
        for path in sorted(changes):
            if path in exempt or path == prereg_path:
                continue
            if path == result_path and changes[path] == "M" and not _removes_lines(repo, path, commit):
                continue
            if path == result_path and changes[path] == "A":
                continue
            report.error(where, f"{path} changed in {commit[:7]} without a dated entry in {amendments}")
    changes = _changes_uncommitted(repo, rel)
    if amendments in changes and _amends(repo, amendments, None):
        return
    for path in sorted(changes):
        if path in exempt or path == prereg_path:
            continue
        if path == result_path and changes[path] == "M" and not _removes_lines(repo, path, None):
            continue
        report.error(where, f"{path} changed uncommitted without a dated entry in {amendments}")


def check(repo: RepoConfig) -> Report:
    studies, report = load(repo)
    for study in studies:
        where = f"{repo.name}/{study.folder.relative_to(repo.path).as_posix()}/{INDEX}"
        meta = study.meta
        for f in schema.STUDY.fields:
            if f.required and not meta.get(f.name):
                report.error(where, f"missing '{f.name}'")
        unknown = set(meta) - {f.name for f in schema.STUDY.fields}
        if unknown:
            report.error(where, f"unknown field(s): {', '.join(sorted(unknown))}")
        status = schema.text(meta, "status")
        if status and status not in schema.STUDY_STATUS:
            report.error(where, f"status '{status}' not in {', '.join(schema.STUDY_STATUS)}")
        files = {key: schema.text(meta, key) for key in ("prereg", "result", "review")}
        for key, name in files.items():
            if name and name != "none" and not (study.folder / name).is_file():
                report.error(where, f"{key} file '{name}' does not exist")
        if status == "preregistered" and files["prereg"] in ("", "none"):
            report.error(where, "status 'preregistered' needs a prereg file")
        if status in ("result", "reviewed", "published") and not files["result"]:
            report.error(where, f"status '{status}' needs a result file")
        if status in ("reviewed", "published") and not files["review"]:
            report.error(where, f"status '{status}' needs a review file")
        prereg = files["prereg"]
        if prereg and prereg != "none" and (study.folder / prereg).is_file():
            later = [study.folder / files["result"]] if files["result"] else []
            out = study.folder / "out"
            if out.is_dir():
                later += sorted(p for p in out.rglob("*") if p.is_file())
            for path in later:
                if path.is_file():
                    _order(repo.path, where, study.folder / prereg, path, report)
        _frozen(repo.path, where, study.folder, prereg, files["result"], report)
    return report
