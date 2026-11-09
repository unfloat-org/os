"""Findings of checks: errors block a commit, warnings are shown."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue:
    level: str  # "error" or "warning"
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    def error(self, where: object, message: str) -> None:
        self.issues.append(Issue("error", str(where), message))

    def warning(self, where: object, message: str) -> None:
        self.issues.append(Issue("warning", str(where), message))

    def add(self, level: str, where: object, message: str) -> None:
        self.issues.append(Issue(level, str(where), message))

    def extend(self, other: Report) -> None:
        self.issues.extend(other.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]
