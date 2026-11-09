# unfloat os: instructions for agents

Read `../ops/AGENTS.md` first when it exists: the project's records, policy and session routine live there. This repository holds the code of `uo`; it is public (D-023, D-024).

## Design rules

- **Standard library only at runtime.** `dependencies = []` stays empty; pytest and ruff are dev tools.
- **One source per rule.** Record kinds, fields, statuses and rules live in `schema.py`; the project's vocabularies (milestones, task owners and their tracker headings, log types) live in the records repository's `config/schema.toml`, loaded by `schema.load`. `SCHEMA.md` in ops is generated from both (`uo schema`). Outbound text rules live in `policy.py`. Paths come from the workspace (`workspace.py`) and each repository's `.unfloat.toml`, never from constants pointing at a folder.
- **Every rule that files or git history can decide is code.** A new rule gets a check function returning a `Report` and is wired into `check.py`; prose in `AGENTS.md` or `SCHEMA.md` describes it and marks it "(checked)".
- **No send code.** Channel adapters create or update drafts and approved setup content, and read where a decision allows it. Every outbound text passes `policy.problems()`. A new channel is a module in `channels/` with a frozen config dataclass built from the workspace and a `register(subparsers)` function; its HTTP goes through `http.send`, its secrets through `credentials`.
- **Safe under parallel sessions and clones.** New IDs are taken under `Store.lock()` and above every number already pushed (`ids.highest_upstream`); log lines are appended under a file lock; `uo push-check` catches a collision with another clone before push. Keep it that way for anything that allocates or appends.
- **Public repository.** No literal person or draft IDs (`P-` or `DR-` followed by three digits) anywhere, including tests: build them at runtime (`rid()` in `tests/conftest.py`). No account names, tokens or project content: those live in ops `config/`.
- **Tests check behaviour** a user of `uo` would notice: rules, boundaries, error messages, ordering. No tests of wiring or of mocks echoing input.

## Layout

| Module | Role |
| --- | --- |
| `cli.py` | `uo` and its subcommands |
| `workspace.py` | Workspace discovery, members, data folders, `.unfloat.toml` |
| `schema.py`, `frontmatter.py`, `records.py` | The record model, its file format and the store (create, update, decisions, log) |
| `check.py`, `links.py`, `studies.py`, `guard.py`, `gitfiles.py` | The checks behind `uo check` and `uo guard` |
| `render.py` | `TRACKER.md` |
| `policy.py`, `http.py`, `credentials.py`, `channels/` | Outbound policy and the Gmail and Discord adapters |
| `provenance.py` | `run_meta.json` for analysis runs: commit, uncommitted changes, Python, package versions, input hashes |
| `drafts.py` | The email draft file format, shared by `uo check` and `uo gmail` |
| `integrity.py`, `ids.py` | Append-only log and decisions; ID collisions across machines, `uo push-check`, `uo renumber` |
| `health.py`, `status.py` | Hooks enabled, remotes, unpushed commits, CONTEXT freshness; `uo status` |
| `migrate.py` | `uo migrate`: schema changes applied to every record |

## Commands

| Task | Command |
| --- | --- |
| Set up | `uv sync`; `uv tool install --editable .` puts `uo` on PATH |
| Tests | `uv run pytest` |
| Lint, format | `uv run ruff check . && uv run ruff format .` |
| Checks | `uo check --repo .` (also the pre-commit hook and CI) |

## End of a session

Commit here and push (CI runs the same checks), then record what changed in ops (`uo log`, update the task) and commit there.
