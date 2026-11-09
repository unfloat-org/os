"""Provenance of an analysis run (contract C4): the code, environment and inputs behind its outputs.

from unfloat_os import provenance
meta = provenance.run_meta(Path(__file__), inputs=[h5ad], packages=["anndata", "numpy"])
provenance.write(OUT / "run_meta.json", meta | {"runtime_s": round(elapsed, 1)})
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path

CHUNK = 1 << 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def run_meta(
    script: Path, inputs: Iterable[Path] = (), packages: Iterable[str] = (), extra: Mapping[str, object] | None = None
) -> dict:
    """Commit (and whether the tree had uncommitted changes), Python, package versions and input hashes."""
    script = script.resolve()
    top = _git(script.parent, "rev-parse", "--show-toplevel")
    meta: dict[str, object] = {
        "generated_at_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": script.relative_to(top).as_posix() if top else script.name,
        "commit": _git(script.parent, "rev-parse", "HEAD") or None,
        "uncommitted_changes": bool(_git(script.parent, "status", "--porcelain")) if top else None,
        "python": platform.python_version(),
        "packages": {name: importlib.metadata.version(name) for name in packages},
        "input_files": [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in inputs],
    }
    meta.update(extra or {})
    return meta


def write(path: Path, meta: Mapping[str, object]) -> None:
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
