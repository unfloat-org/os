"""Relative Markdown links must resolve. In open and public repositories they must also stay inside
the repository, so they work for anyone reading it on its own."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Iterator

from unfloat_os import gitfiles
from unfloat_os.report import Report
from unfloat_os.workspace import RepoConfig

INLINE = re.compile(r"!?\[(?:[^\]\n]|\][^(\n])*\]\((<[^>\n]+>|[^)\s]+)(?:\s+\"[^\"\n]*\")?\)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s+(<[^>\n]+>|\S+)")
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
FENCE = re.compile(r"^\s{0,3}(```|~~~)")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
# Agent run records are frozen history and may point at files as they were at the time.
SKIP_PREFIXES = ("runs/",)


def links(text: str) -> Iterator[tuple[int, str]]:
    """(line number, target) for every link outside code blocks and code spans."""
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        plain = CODE_SPAN.sub("", line)
        for match in INLINE.finditer(plain):
            yield number, match.group(1)
        ref = REFDEF.match(plain)
        if ref:
            yield number, ref.group(1)


def local_target(target: str) -> str | None:
    """The file part of a relative link, or None for URLs, anchors and absolute paths."""
    target = target.strip("<>")
    if SCHEME.match(target) or target.startswith(("#", "/")):
        return None
    path = target.split("#", 1)[0].split("?", 1)[0]
    return urllib.parse.unquote(path) or None


def check_links(repo: RepoConfig) -> Report:
    report = Report()
    root = repo.path.resolve()
    for rel in gitfiles.tracked(repo.path):
        if rel.suffix != ".md" or rel.as_posix().startswith(SKIP_PREFIXES):
            continue
        source = repo.path / rel
        for number, target in links(source.read_text(encoding="utf-8")):
            path = local_target(target)
            if path is None:
                continue
            resolved = (source.parent / path).resolve()
            where = f"{repo.name}/{rel.as_posix()}:{number}"
            if repo.is_open and not resolved.is_relative_to(root):
                report.error(where, f"link '{target}' leaves the repository; use a URL")
            elif not resolved.exists():
                report.error(where, f"broken link '{target}'")
    return report
