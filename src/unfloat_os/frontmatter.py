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
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        else:
            meta[key] = value
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
