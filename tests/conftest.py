"""Fixtures: a throwaway workspace with a records repository and an open repository.

Person and draft IDs are built at runtime (`rid`) because this public repository may not contain
literal ones (contract C5).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from unfloat_os import frontmatter
from unfloat_os.workspace import Workspace


def rid(prefix: str, number: int) -> str:
    return f"{prefix}-{number:03d}"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout


def git_init(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.org")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")


def commit(repo: Path, message: str, *paths: str) -> None:
    git(repo, "add", *(paths or ("-A",)))
    git(repo, "commit", "-q", "-m", message)


def write_record(ops: Path, folder: str, rid_: str, slug: str, meta: dict, body: str = "") -> Path:
    path = ops / "records" / folder / f"{rid_}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter.render({"id": rid_, **meta}, body), encoding="utf-8")
    return path


TASK = {
    "title": "Do a thing",
    "status": "todo",
    "owner": "claude",
    "milestone": "ops",
    "due": "",
    "people": [],
    "waiting_on": "",
    "done_when": "It is done",
    "created": "2026-11-01",
    "closed": "",
}
PERSON = {
    "name": "A Person",
    "kind": "person",
    "org": "",
    "role": "",
    "location": "",
    "via": "",
    "milestones": ["ops"],
    "tags": [],
    "status": "to-contact",
    "last_contact": "",
    "next_task": "",
}
DRAFT = {
    "title": "A draft",
    "status": "draft",
    "channel": "document",
    "task": "",
    "people": [],
    "created": "2026-11-01",
    "reply_to": "",
    "reply_thread": "",
}

# Generic vocabulary for tests; the project's own words live in the private records repository.
MILESTONES = ("ops", "build", "launch", "parked")
LOG_TYPES = ("email-in", "email-out", "call", "meeting", "post", "comment", "dm", "result", "decision", "note", "task")
OWNERS = """\
[owners.founder]
name = "Daniil"
heading = "Daniil: next actions"

[owners.claude]
name = "Claude"
heading = "Claude: queue"
"""


def schema_toml(milestones=MILESTONES, log_types=LOG_TYPES, owners: str = OWNERS) -> str:
    return f"milestones = {json.dumps(list(milestones))}\nlog_types = {json.dumps(list(log_types))}\n\n{owners}"


def write_schema(ops: Path, milestones=MILESTONES, log_types=LOG_TYPES, owners: str = OWNERS) -> Path:
    """Write `config/schema.toml` of a records repository (a bare `Store(ops)` needs one to load)."""
    path = ops / "config" / "schema.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_toml(milestones, log_types, owners), encoding="utf-8")
    return path


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    root = tmp_path / "workspace"
    ops = root / "ops"
    (ops / "config").mkdir(parents=True)
    (ops / "config" / "workspace.toml").write_text(
        '[repos.ops]\nremote = ""\n\n[repos.research]\nremote = ""\n\n'
        '[data]\npublic = "data/public"\nprivate = "data/private"\n',
        encoding="utf-8",
    )
    write_schema(ops)
    (ops / ".unfloat.toml").write_text('visibility = "private"\n', encoding="utf-8")
    for folder in ("people", "tasks", "drafts", "log"):
        (ops / "records" / folder).mkdir(parents=True)
    (ops / "records" / "decisions.md").write_text(
        "# Decisions\n\n## D-001 | 2026-11-01 | First\n\n- Decision: x\n", encoding="utf-8"
    )
    (ops / "records" / "log" / "2026-11.md").write_text("# Log 2026-11\n\n", encoding="utf-8")
    # Every records repository has a context brief; `uo check` reads its "Updated" line.
    (ops / "CONTEXT.md").write_text("# Context brief\n\nUpdated 2026-11-09. Read this first.\n", encoding="utf-8")
    research = root / "research"
    research.mkdir()
    (research / ".unfloat.toml").write_text('visibility = "open"\n', encoding="utf-8")
    git_init(ops)
    git_init(research)
    return Workspace.load(root)


@pytest.fixture
def in_workspace(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    """The workspace as `uo` finds it from the environment, for tests that run commands through `cli.main`."""
    monkeypatch.setenv("UNFLOAT_WORKSPACE", str(ws.root))
    return ws
