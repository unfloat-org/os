"""The workspace: the folder holding the project's repositories and its data folder.

The member list lives in the private records repository at `ops/config/workspace.toml`.
Each repository describes itself in its own `.unfloat.toml` (visibility and data-guard
allowances), so a public repository can be checked on its own, without the workspace.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = Path("ops/config/workspace.toml")
REPO_CONFIG = ".unfloat.toml"
VISIBILITY = ("private", "open", "public")
DATA_CLASSES = ("public", "private", "restricted")
ENV = "UNFLOAT_WORKSPACE"


class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class RepoConfig:
    """What a repository says about itself in `.unfloat.toml`."""

    path: Path
    visibility: str
    guard_allow: tuple[str, ...] = ()
    guard_max_bytes: int = 2_000_000

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_open(self) -> bool:
        return self.visibility in ("open", "public")

    @classmethod
    def load(cls, path: Path) -> RepoConfig:
        config_path = path / REPO_CONFIG
        if not config_path.is_file():
            raise WorkspaceError(f"{config_path} is missing")
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise WorkspaceError(f"{config_path}: {exc}") from exc
        visibility = data.get("visibility")
        if visibility not in VISIBILITY:
            raise WorkspaceError(f"{config_path}: visibility must be one of {', '.join(VISIBILITY)}")
        guard = data.get("guard", {})
        return cls(
            path=path,
            visibility=visibility,
            guard_allow=tuple(guard.get("allow", ())),
            guard_max_bytes=int(guard.get("max_bytes", 2_000_000)),
        )


@dataclass(frozen=True)
class Member:
    name: str
    path: Path
    remote: str


@dataclass(frozen=True)
class Workspace:
    root: Path
    members: dict[str, Member]
    data: dict[str, Path] = field(default_factory=dict)

    @property
    def ops(self) -> Path:
        return self.members["ops"].path

    def repo(self, name: str) -> RepoConfig:
        if name not in self.members:
            raise WorkspaceError(f"no repository '{name}' in {self.root / MANIFEST}")
        return RepoConfig.load(self.members[name].path)

    def repos(self) -> list[RepoConfig]:
        return [self.repo(name) for name in self.members if self.members[name].path.is_dir()]

    def data_dir(self, access: str) -> Path:
        if access not in self.data:
            raise WorkspaceError(f"no data folder '{access}' in {self.root / MANIFEST}")
        return self.data[access]

    def member_of(self, path: Path) -> Member | None:
        resolved = path.resolve()
        for member in self.members.values():
            if resolved.is_relative_to(member.path.resolve()):
                return member
        return None

    @classmethod
    def load(cls, root: Path) -> Workspace:
        manifest = root / MANIFEST
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkspaceError(f"{manifest} not found") from exc
        except tomllib.TOMLDecodeError as exc:
            raise WorkspaceError(f"{manifest}: {exc}") from exc
        members = {
            name: Member(name=name, path=root / name, remote=str(entry.get("remote", "")))
            for name, entry in data.get("repos", {}).items()
        }
        if "ops" not in members:
            raise WorkspaceError(f"{manifest} must list the 'ops' repository")
        unknown = set(data.get("data", {})) - set(DATA_CLASSES)
        if unknown:
            raise WorkspaceError(f"{manifest}: unknown data classes {sorted(unknown)}")
        folders = {access: root / rel for access, rel in data.get("data", {}).items()}
        return cls(root=root, members=members, data=folders)

    @classmethod
    def find(cls, start: Path | None = None) -> Workspace:
        """$UNFLOAT_WORKSPACE, else the nearest folder at or above `start` that holds the manifest."""
        if os.environ.get(ENV):
            return cls.load(Path(os.environ[ENV]).expanduser().resolve())
        here = (start or Path.cwd()).resolve()
        for folder in (here, *here.parents):
            if (folder / MANIFEST).is_file():
                return cls.load(folder)
        raise WorkspaceError(f"no {MANIFEST} at or above {here}; set {ENV} or run inside the workspace")
