"""References to files (contract C5): `<root>:<relative path>`, resolved through the workspace.

A root is a data class (`public`, `private`, `restricted`: the workspace's data folders) or a workspace member
(`ops`, `os`, and the other repositories of the manifest). Media must be data-class references; sources for the
number check may be either. A reference never escapes its root: no absolute paths, no `..`, no symlink that
resolves outside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from unfloat_os.workspace import DATA_CLASSES, Workspace, WorkspaceError

REF_RE = re.compile(r"^(?P<root>[a-z][a-z0-9-]*):(?P<rel>.+)$")


class RefError(ValueError):
    """A reference that is malformed, names an unknown root or leaves its root."""


@dataclass(frozen=True)
class Ref:
    root: str
    rel: str

    def __str__(self) -> str:
        return f"{self.root}:{self.rel}"

    @property
    def data_class(self) -> str | None:
        """`public`, `private` or `restricted` for a data-class reference, else None."""
        return self.root if self.root in DATA_CLASSES else None

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.rel).suffix.lower()


def parse(text: object) -> Ref:
    if not isinstance(text, str):
        raise RefError(f"reference {text!r} must be a string '<root>:<path>'")
    match = REF_RE.match(text.strip())
    if not match:
        raise RefError(f"reference '{text}' must look like '<root>:<relative path>'")
    rel = match.group("rel")
    path = PurePosixPath(rel)
    if rel.startswith("/") or path.is_absolute() or "\\" in rel:
        raise RefError(f"reference '{text}' must use a relative path")
    if ".." in path.parts or "." in rel.split("/"):
        raise RefError(f"reference '{text}' must not contain '..' or '.'")
    return Ref(match.group("root"), path.as_posix())


def root_dir(ws: Workspace, root: str) -> Path:
    if root in DATA_CLASSES:
        try:
            return ws.data_dir(root)
        except WorkspaceError as exc:
            raise RefError(str(exc)) from exc
    if root in ws.members:
        return ws.members[root].path
    roots = ", ".join([*DATA_CLASSES, *ws.members])
    raise RefError(f"unknown root '{root}'; one of {roots}")


def resolve(ws: Workspace, ref: Ref) -> Path:
    """The file a reference names; refuses a path that resolves outside its root (symlinks included)."""
    base = root_dir(ws, ref.root)
    path = base / ref.rel
    if not path.resolve().is_relative_to(base.resolve()):
        raise RefError(f"reference '{ref}' resolves outside {ref.root}")
    return path


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """`**` matches across folders, `*` and `?` within one folder name."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(ref: Ref, pattern: str) -> bool:
    return bool(_glob_regex(pattern).match(str(ref)))
