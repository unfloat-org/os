# unfloat os

The operating tools of [unfloat](https://github.com/unfloat-org/unfloat), an open, non-profit project working toward a treatment for vitreous floaters. One command, `uo`, keeps the project's records consistent, checks every repository against the same rules, and puts drafts where a person reviews and sends them. Nothing here sends a message to anyone.

Standard library only; Python 3.12 or newer.

## What it does

| Area | Commands | Rule it enforces |
| --- | --- | --- |
| Records | `uo new`, `uo set`, `uo log`, `uo render` | People, tasks and drafts are Markdown notes with typed frontmatter. Kinds, fields and rules are code (`schema.py`); the project's vocabularies (milestones, task owners, log types) live in the records repository's `config/schema.toml`. New IDs are taken under a lock and above every number already pushed. Every fact becomes a log event. |
| Checks | `uo check`, `uo schema --check`, `uo status` | Records match the schema; email drafts are well-formed with unique subjects; the log and decisions are append-only once pushed; relative links resolve; open repositories carry no private identifiers and no links into private records; analyses keep their pre-registration unchanged and change frozen files only with a dated amendment; hooks are enabled, remotes match the manifest, nothing sits unpushed, the context brief is current. |
| Schema changes | `uo migrate` | Renaming or adding a field, or renaming a vocabulary value, is applied to every record at once (dry run first). |
| Several machines | `uo push-check` (pre-push), `uo renumber` | A new ID that another clone already pushed is caught before push and renumbered with every reference. |
| Data guard | `uo guard` (pre-commit) | Data, images, files over 2 MB and device file names stay out of git. |
| Channels | `uo gmail`, `uo discord` | Drafts go into Gmail for a person to send; the Discord server is configured from a spec. Outbound text passes one policy check. |
| Provenance | `unfloat_os.provenance` (library) | Every analysis run writes `run_meta.json` with the commit, uncommitted changes, Python and package versions and input hashes. |

## Workspace

`uo` works inside a workspace folder that holds the project's repositories and a data folder:

```
workspace/
├── os/            this repository (public)
├── ops/           records and policy (private); config/workspace.toml lists the members
├── research/      syntheses, surveys and analyses (open)
├── vitreometry/   measurement code (open)
├── site/          the public front door
└── data/          public/, private/, restricted/ (never in git)
```

Each repository describes itself in `.unfloat.toml` (`visibility = "private" | "open" | "public"`, and optional data-guard allowances), so `uo check --repo .` also runs on a single clone, as CI does.

## Install

```sh
uv tool install --editable path/to/os    # puts `uo` on PATH
uo setup                                 # inside the workspace: clone missing repositories, enable hooks
```

## Develop

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

History of these tools before 2026-11-09 is in the project's private archive.

## License

[Apache-2.0](LICENSE).
