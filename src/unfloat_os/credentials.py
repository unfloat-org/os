"""Credentials live outside every repository, in ~/.config/unfloat/<channel>/, readable only by the user."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path.home() / ".config" / "unfloat"


def channel_dir(channel: str, create: bool = False) -> Path:
    path = BASE / channel
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    return path


def write_private(path: Path, text: str) -> None:
    """Write a file only the user can read (mode 600), replacing any previous content."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def read_secret(path: Path, env: str | None = None) -> str:
    """The secret from `$env` if set, else from `path`; empty if neither. Warns on loose file permissions."""
    if path.exists() and path.stat().st_mode & 0o077:
        print(f"warning: {path} is group/world readable; run chmod 600 {path}", file=sys.stderr)
    value = os.environ.get(env, "").strip() if env else ""
    if not value and path.exists():
        value = path.read_text(encoding="utf-8").strip()
    return value
