"""Restricted frontmatter: `key: value` lines between `---` markers, lists written inline as `[a, b]`.

No nesting, no comments, no quoting: what a person types in Obsidian is what a program reads.
"""

from __future__ import annotations

from collections.abc import Mapping

Value = str | list[str]
Meta = dict[str, Value]


class FrontmatterError(ValueError):
    pass


def _bounds(text: str) -> tuple[int, int]:
    """Start and end offsets of the frontmatter lines (between the markers)."""
    if not text.startswith("---\n"):
        raise FrontmatterError("file must start with a '---' frontmatter block")
    end = text.find("\n---", 3)
    if end < 0:
        raise FrontmatterError("frontmatter block is not closed with '---'")
    return 4, end


def parse_value(raw: str) -> Value:
    """One value as the block writes it: `[a, b]` is a list, anything else a string (stripped)."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [item.strip() for item in raw[1:-1].split(",") if item.strip()]
    return raw


def parse(text: str) -> Meta:
    start, end = _bounds(text)
    meta: Meta = {}
    for number, line in enumerate(text[start:end].splitlines(), start=2):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not key or " " in key:
            raise FrontmatterError(f"frontmatter line {number}: expected 'key: value'")
        if key in meta:
            raise FrontmatterError(f"frontmatter line {number}: duplicate key '{key}'")
        meta[key] = parse_value(value)
    return meta


def body(text: str) -> str:
    """Everything after the closing marker line."""
    _, end = _bounds(text)
    after = text.find("\n", end + 1)
    return "" if after < 0 else text[after + 1 :]


def format_value(value: Value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(value) + "]"
    if "\n" in value:
        raise FrontmatterError("frontmatter values are single lines")
    return value


def render(meta: Mapping[str, Value], text_body: str = "") -> str:
    lines = [f"{key}: {format_value(value)}".rstrip() for key, value in meta.items()]
    return "---\n" + "\n".join(lines) + "\n---\n" + text_body


def update(text: str, updates: Mapping[str, Value]) -> str:
    """Set keys in place, keeping order, other lines and the body untouched; new keys go last."""
    start, end = _bounds(text)
    parse(text)  # refuse to edit a block that does not parse
    lines = text[start:end].splitlines()
    pending = dict(updates)
    for index, line in enumerate(lines):
        key = line.partition(":")[0].strip()
        if key in pending:
            lines[index] = f"{key}: {format_value(pending.pop(key))}".rstrip()
    lines += [f"{key}: {format_value(value)}".rstrip() for key, value in pending.items()]
    return text[:start] + "\n".join(lines) + text[end:]


def _block(text: str) -> tuple[int, int, list[str]]:
    start, end = _bounds(text)
    parse(text)  # refuse to edit a block that does not parse
    return start, end, text[start:end].splitlines()


def _index(lines: list[str], key: str) -> int | None:
    return next((i for i, line in enumerate(lines) if line.partition(":")[0].strip() == key), None)


def remove(text: str, key: str) -> str:
    """Delete a key and its line, keeping the order of the other lines and the body untouched."""
    start, end, lines = _block(text)
    index = _index(lines, key)
    if index is None:
        raise FrontmatterError(f"no key '{key}'")
    del lines[index]
    return text[:start] + "\n".join(lines) + text[end:]


def rename(text: str, old: str, new: str) -> str:
    """Give a key another name where it stands, keeping its value, the other lines and the body untouched."""
    start, end, lines = _block(text)
    index = _index(lines, old)
    if index is None:
        raise FrontmatterError(f"no key '{old}'")
    if _index(lines, new) is not None:
        raise FrontmatterError(f"key '{new}' already exists")
    lines[index] = f"{new}:{lines[index].partition(':')[2]}"
    return text[:start] + "\n".join(lines) + text[end:]


def insert(text: str, key: str, value: Value, after: str | None = None) -> str:
    """Add a new key right after the key `after` (first line when `after` is None); the rest stays untouched."""
    start, end, lines = _block(text)
    if _index(lines, key) is not None:
        raise FrontmatterError(f"key '{key}' already exists")
    position = 0
    if after is not None:
        found = _index(lines, after)
        if found is None:
            raise FrontmatterError(f"no key '{after}'")
        position = found + 1
    lines.insert(position, f"{key}: {format_value(value)}".rstrip())
    return text[:start] + "\n".join(lines) + text[end:]
