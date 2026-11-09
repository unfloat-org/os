"""Sequential record IDs stay unique when several clones write the records repository.

`uo new` takes the next free number from the files in this clone, so two clones that both create a task
before they sync pick the same ID. Rule: an ID that this clone added since the merge base with the upstream
branch must not also be added upstream. `collisions()` finds such IDs from the last fetch (no network);
`uo push-check` fetches first and fails with the exact `uo renumber` commands, so a pre-push hook keeps the
duplicate off the remote. `renumber()` gives a record that was never pushed a free number and rewrites every
reference to it inside the repository; a pushed ID, and an ID that a pushed log or decision line mentions,
never change.

Not covered: after a pull has already brought both records into one tree there is no collision "since the merge
base" any more; `uo check` reports it as a duplicate ID.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from unfloat_os import frontmatter, gitfiles, schema
from unfloat_os.integrity import DECISIONS, Baseline, baseline, ls_tree, published_texts
from unfloat_os.records import RECORDS, Store, StoreError
from unfloat_os.workspace import Workspace

DECISION_PREFIX = "D"
HEADING = re.compile(r"^## (D-\d{3}) \|.*$", re.M)
SCAN_LIMIT = 2_000_000


@dataclass(frozen=True)
class Collision:
    """An ID that this clone and the upstream branch both added since their merge base."""

    id: str
    here: str  # the file (or decision heading) of this clone
    upstream: str  # the file (or decision heading) upstream holds under the same ID


@dataclass(frozen=True)
class Mention:
    """A line that mentions a renumbered ID in a file that was not rewritten."""

    repo: str
    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.repo}/{self.path}:{self.line}"


@dataclass(frozen=True)
class Renumbered:
    old: str
    new: str
    moved: tuple[Path, Path] | None  # (old, new) file of a record; None for a decision
    rewritten: tuple[Path, ...]  # files whose text changed, relative to the records repository
    elsewhere: tuple[Mention, ...]  # mentions in files that are not rewritten (other repositories, non-Markdown)


def _prefix(rid: str) -> str:
    if not schema.ID_RE.match(rid):
        raise StoreError(f"'{rid}' is not a record ID (P-###, T-###, D-### or DR-###)")
    return rid.rsplit("-", 1)[0]


def _number(rid: str) -> int:
    return int(rid.rsplit("-", 1)[1])


def _highest(ids: Iterable[str], prefix: str) -> int:
    return max((_number(rid) for rid in ids if rid.rsplit("-", 1)[0] == prefix), default=0)


def _file_id(kind: schema.Kind, name: str) -> str | None:
    match = re.match(rf"({kind.prefix}-\d{{3}})-.+\.md$", name)
    return match.group(1) if match else None


def _decision_ids(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in HEADING.finditer(text):
        found.setdefault(match.group(1), match.group(0)[3:])
    return found


def _ids_at(store: Store, ref: str) -> dict[str, str]:
    """Every record and decision ID in the tree at `ref`, with the file or heading that holds it."""
    found: dict[str, str] = {}
    for kind in store.schema.kinds.values():
        for rel in ls_tree(store.root, ref, Path(RECORDS) / kind.folder):
            rid = _file_id(kind, rel.name)
            if rid:
                found.setdefault(rid, rel.as_posix())
    for rid, heading in _decision_ids(gitfiles.show(store.root, ref, DECISIONS) or "").items():
        found.setdefault(rid, f"{DECISIONS.as_posix()}: {heading}")
    return found


def _ids_here(store: Store) -> dict[str, str]:
    """Every record and decision ID in the working tree, with the file or heading that holds it."""
    found: dict[str, str] = {}
    for kind in store.schema.kinds.values():
        for path in sorted(store.folder(kind).glob("*.md")):
            rid = _file_id(kind, path.name)
            if rid:
                found.setdefault(rid, path.relative_to(store.root).as_posix())
    if store.decisions_path.is_file():
        text = store.decisions_path.read_text(encoding="utf-8", errors="replace")
        for rid, heading in _decision_ids(text).items():
            found.setdefault(rid, f"{DECISIONS.as_posix()}: {heading}")
    return found


def _order(rid: str) -> tuple[str, int]:
    return rid.rsplit("-", 1)[0], _number(rid)


def collisions(ops_root: Path) -> list[Collision]:
    """IDs added here and upstream since the merge base, as of the last fetch; [] without an upstream."""
    tip = gitfiles.upstream(ops_root)
    base = baseline(ops_root)
    if tip is None or base is None:
        return []
    store = Store(ops_root)
    known = _ids_at(store, base.ref)
    upstream = _ids_at(store, tip)
    here = _ids_here(store)
    return [
        Collision(rid, here[rid], upstream[rid])
        for rid in sorted(here, key=_order)
        if rid not in known and rid in upstream
    ]


def highest_upstream(ops_root: Path, prefix: str) -> int:
    """The highest number taken for `prefix` (a record prefix, or D for decisions) upstream; 0 without an upstream."""
    store = Store(ops_root)
    prefixes = (*store.schema.by_prefix, DECISION_PREFIX)
    if prefix not in prefixes:
        raise ValueError(f"unknown ID prefix '{prefix}'; one of {', '.join(prefixes)}")
    tip = gitfiles.upstream(ops_root)
    return 0 if tip is None else _highest(_ids_at(store, tip), prefix)


def new_ids(ops_root: Path, found: Sequence[Collision]) -> dict[str, str]:
    """A distinct free replacement ID for each collision: above every number taken here and upstream."""
    store = Store(ops_root)
    tip = gitfiles.upstream(ops_root)
    taken = set(_ids_here(store)) | (set(_ids_at(store, tip)) if tip else set())
    top: dict[str, int] = {}
    replacements = {}
    for collision in found:
        prefix = _prefix(collision.id)
        top[prefix] = top.get(prefix, _highest(taken, prefix)) + 1
        replacements[collision.id] = f"{prefix}-{top[prefix]:03d}"
    return replacements


# renumber ---------------------------------------------------------------


def _word(rid: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(rid)}\b")


def _read(path: Path) -> str | None:
    """Text of a file, or None if it is binary or too large to be a note."""
    try:
        if path.stat().st_size > SCAN_LIMIT:
            return None
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _hits(repo: str, rel: Path, text: str, pattern: re.Pattern[str]) -> list[Mention]:
    return [
        Mention(repo, rel.as_posix(), number)
        for number, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def _scan(repo: Path, pattern: re.Pattern[str]) -> list[Mention]:
    found = []
    for rel in gitfiles.tracked(repo):
        text = _read(repo / rel)
        if text is not None:
            found += _hits(repo.name, rel, text, pattern)
    return found


def _chosen_id(old: str, new: str | None, taken: dict[str, str], upstream: dict[str, str]) -> str:
    prefix = _prefix(old)
    if new is None:
        number = _highest(taken.keys() | upstream.keys(), prefix) + 1
        if number > 999:
            raise StoreError(f"no free {prefix}-### number left")
        return f"{prefix}-{number:03d}"
    if _prefix(new) != prefix:
        raise StoreError(f"{new} is not a {prefix}-### ID; {old} cannot change its kind")
    if new in taken or new in upstream:
        raise StoreError(f"{new} is already taken {'here' if new in taken else 'upstream'}")
    return new


def _refuse_published(ops_root: Path, base: Baseline, old: str, pattern: re.Pattern[str]) -> None:
    for rel, text in sorted(published_texts(ops_root, base).items()):
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                raise StoreError(
                    f"{old} appears in {rel.as_posix()}:{number}, which is already {base.label}; "
                    "pushed log and decision lines are never rewritten"
                )


def _rewritten_record(ops_root: Path, rel: Path, old: str, new: str, pattern: re.Pattern[str]) -> str:
    text = _read(ops_root / rel)
    if text is None:
        raise StoreError(f"{rel.as_posix()} cannot be read as text")
    try:
        found = schema.text(frontmatter.parse(text), "id")
    except frontmatter.FrontmatterError as error:
        raise StoreError(f"{rel.as_posix()}: {error}") from error
    if found != old:
        raise StoreError(f"{rel.as_posix()}: its frontmatter id is '{found}', not {old}; fix it first (uo check)")
    return pattern.sub(new, text)


def renumber(ops_root: Path, old: str, new: str | None = None, others: Sequence[Path] = ()) -> Renumbered:
    """Give the record or decision `old`, which exists only in this clone, the ID `new`.

    `new` defaults to the next number free here and upstream. Rewrites the file name and `id`, and every mention
    of `old` (word boundary, so `T-0010` is not `T-001`) in the Markdown files of the records repository:
    frontmatter values and lists, links to the file, notes, and the log and decision lines that are not pushed
    yet. Refuses when `old` is already pushed or a pushed log or decision line mentions it. Mentions in other
    files and in the repositories `others` are reported, not edited.
    """
    prefix = _prefix(old)
    pattern = _word(old)
    store = Store(ops_root)
    with store.lock():
        here = _ids_here(store)
        tip = gitfiles.upstream(ops_root)
        upstream = _ids_at(store, tip) if tip else {}
        base = baseline(ops_root)
        if old not in here:
            hint = "; it exists upstream, pull it first" if old in upstream else ""
            raise StoreError(f"{old} does not exist in this clone{hint}")
        if tip and base and old in _ids_at(store, base.ref):
            raise StoreError(f"{old} is already pushed to {tip}; a pushed ID never changes")
        if base:
            _refuse_published(ops_root, base, old, pattern)
        new = _chosen_id(old, new, here, upstream)
        own = store.path_of(old).relative_to(ops_root) if prefix != DECISION_PREFIX else None
        changes: dict[Path, str] = {}
        elsewhere: list[Mention] = []
        if own is not None:
            changes[own] = _rewritten_record(ops_root, own, old, new, pattern)
        for rel in gitfiles.tracked(ops_root):
            text = _read(ops_root / rel)
            if text is None or rel == own:
                continue
            if rel.suffix != ".md":
                elsewhere += _hits(ops_root.name, rel, text, pattern)
            elif pattern.search(text):
                changes[rel] = pattern.sub(new, text)
        moved = None
        if own is not None:
            moved = (own, own.with_name(new + own.name[len(old) :]))
            if (ops_root / moved[1]).exists():
                raise StoreError(f"{moved[1].as_posix()} already exists")
            (ops_root / own).rename(ops_root / moved[1])
            changes[moved[1]] = changes.pop(own)
        for rel, text in changes.items():
            (ops_root / rel).write_bytes(text.encode("utf-8"))
    for other in others:
        elsewhere += _scan(other, pattern)
    return Renumbered(old, new, moved, tuple(sorted(changes)), tuple(elsewhere))


# commands ---------------------------------------------------------------


def _ops_root(ws: Workspace | None) -> Path:
    if ws is not None:
        return ws.ops
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode:
        raise StoreError("not inside the workspace or a git repository")
    return Path(top.stdout.strip())


def cmd_renumber(args: argparse.Namespace, ws: Workspace | None) -> int:
    ops = _ops_root(ws)
    others = [m.path for name, m in ws.members.items() if name != "ops" and m.path.is_dir()] if ws else []
    result = renumber(ops, args.old, args.new, others)
    print(f"{result.old} -> {result.new}")
    if result.moved:
        print(f"renamed {result.moved[0].as_posix()} -> {result.moved[1].as_posix()}")
    for path in result.rewritten:
        print(f"rewrote {path.as_posix()}")
    if result.elsewhere:
        print(f"not rewritten, check by hand: {len(result.elsewhere)} mention(s) of {result.old} elsewhere")
        for mention in result.elsewhere:
            print(f"  {mention}")
    return 0


def cmd_push_check(args: argparse.Namespace, ws: Workspace | None) -> int:
    ops = _ops_root(ws)
    tip = gitfiles.upstream(ops)
    if tip is None:
        print("push-check: no upstream branch, nothing to compare")
        return 0
    remote = tip.split("/", 1)[0]
    if not gitfiles.fetch(ops, remote):
        print(f"error: could not fetch {remote}; ID collisions cannot be ruled out", file=sys.stderr)
        return 1
    found = collisions(ops)
    if not found:
        print(f"push-check: no ID collisions with {tip}")
        return 0
    replacements = new_ids(ops, found)
    print(f"error: record IDs taken here and on {tip}:", file=sys.stderr)
    for collision in found:
        print(f"  {collision.id}: here {collision.here}; upstream {collision.upstream}", file=sys.stderr)
    print("Give the ones created here new IDs, then pull and push again:", file=sys.stderr)
    for collision in found:
        print(f"  uo renumber {collision.id} {replacements[collision.id]}", file=sys.stderr)
    return 1


def register(sub: argparse._SubParsersAction) -> None:
    """Add `renumber` and `push-check` to the top-level parser."""
    p = sub.add_parser("renumber", help="give a record that was never pushed a new ID, rewriting its references")
    p.add_argument("old", metavar="ID", help="the ID to replace, for example one reported by push-check")
    p.add_argument("new", nargs="?", metavar="NEW", help="the new ID (default: the next number free here and upstream)")
    p.set_defaults(handler=cmd_renumber, needs_workspace=False)

    p = sub.add_parser("push-check", help="fetch the upstream and fail when it already took an ID created here")
    p.set_defaults(handler=cmd_push_check, needs_workspace=False)
