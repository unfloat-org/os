from __future__ import annotations

import hashlib
import json

from conftest import commit, git, git_init

from unfloat_os import provenance


def test_run_meta_records_commit_dirty_state_and_input_hashes(tmp_path):
    repo = tmp_path / "research"
    (repo / "analysis" / "a9").mkdir(parents=True)
    git_init(repo)
    script = repo / "analysis" / "a9" / "run.py"
    script.write_text("print('x')\n")
    commit(repo, "script")
    data = tmp_path / "input.bin"
    data.write_bytes(b"abc" * 1000)

    meta = provenance.run_meta(script, inputs=[data], packages=["pytest"], extra={"seed": 7})
    assert meta["script"] == "analysis/a9/run.py"
    assert meta["commit"] == git(repo, "rev-parse", "HEAD").strip()
    assert meta["uncommitted_changes"] is False
    assert meta["input_files"] == [
        {"name": "input.bin", "bytes": 3000, "sha256": hashlib.sha256(b"abc" * 1000).hexdigest()}
    ]
    assert set(meta["packages"]) == {"pytest"}
    assert meta["seed"] == 7

    script.write_text("print('changed')\n")
    assert provenance.run_meta(script)["uncommitted_changes"] is True

    out = repo / "analysis" / "a9" / "run_meta.json"
    provenance.write(out, meta)
    assert json.loads(out.read_text()) == meta
