"""The analysis contract (C4), the same in every repository with an `analysis/` folder.

Each `analysis/<code>_<slug>/` holds a STUDY.md index (fields in `schema.STUDY`). When the study has a
pre-registration, git history must show it committed before the result and before anything in `out/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unfloat_os import frontmatter, gitfiles, schema
from unfloat_os.frontmatter import Meta
from unfloat_os.report import Report
from unfloat_os.workspace import RepoConfig

FOLDER = "analysis"
INDEX = "STUDY.md"


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
    return report
