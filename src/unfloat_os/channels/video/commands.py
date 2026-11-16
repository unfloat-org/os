"""The `uo video` commands (contract C9)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys

from unfloat_os import schema
from unfloat_os.channels import ChannelError
from unfloat_os.channels.video import build as building
from unfloat_os.channels.video import checks, fit, series, timing
from unfloat_os.channels.video.series import CHAPTERS, Series, SeriesError
from unfloat_os.channels.video.storyboard import PLACEHOLDER
from unfloat_os.records import Record, Records, Store, StoreError
from unfloat_os.workspace import Workspace

PLACEHOLDER_HEADLINES = {
    "bottom-line": "the result in one line",
    "goal": "the question this line of work answers",
    "done": "what was done since the last update",
    "results": "one result, with its number",
    "limits": "what cannot be said yet, and what would change that",
    "next": "the next step and the asks",
    "end": "how to reach us",
}


def _toml(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string or list of strings is valid TOML


def _scene(heading: str, props: dict[str, object]) -> str:
    lines = [f"{key} = {_toml(value)}" for key, value in props.items()]
    return f"## {heading}\n\n```toml\n" + "\n".join(lines) + "\n```\n"


def series_drafts(records: Records, name: str) -> list[tuple[int, Record]]:
    """The live (not dropped) drafts of a series with a valid number, in number order."""
    found = []
    for record in records.drafts.values():
        meta = record.meta
        number = schema.text(meta, "number")
        live = meta.get("channel") == "video" and meta.get("status") != "dropped"
        if live and schema.text(meta, "series") == name and number.isdigit():
            found.append((int(number), record))
    return sorted(found, key=lambda item: item[0])


def skeleton(s: Series, title: str, previous: Record | None) -> str:
    """The chapter skeleton of a new update: title, roadmap, a placeholder per required chapter, notes."""
    board = checks.board_of(previous) if previous else None
    scenes = [_scene("title", {"kind": "title", "title": s.title, "subtitle": title})]
    for chapter in CHAPTERS[1:]:
        if chapter == "goal" and s.roadmap:
            last = next((sc for sc in board.scenes if sc.kind == "roadmap"), None) if board else None
            props: dict[str, object] = {"kind": "roadmap", "headline": "Where we are"}
            props["current"] = (last.props.get("current") if last else None) or s.roadmap[0].id
            props["done"] = list(last.props.get("done") or []) if last else []
            scenes.append(_scene("goal: roadmap", props))
            continue
        if chapter in s.required:
            headline = f"{PLACEHOLDER}: {PLACEHOLDER_HEADLINES[chapter]}"
            scenes.append(_scene(chapter, {"kind": "statement", "headline": headline}))
    notes = ["## notes", ""]
    if previous is not None:
        asks = board.asks()
        number = schema.text(previous.meta, "number")
        notes.append(f"Asks of update {number} ({previous.id}), to follow up in this one:")
        notes += ["", *(f"- {ask}" for ask in asks)] if asks else ["", "- none marked (`asks = true`)"]
    else:
        notes.append("The first update of this series.")
    return "\n".join(scenes) + "\n" + "\n".join(notes) + "\n"


def new_draft(ws: Workspace, name: str, title: str, today: dt.date) -> Record:
    """Create the next update of a series as a draft record, under the records lock (ID and number together)."""
    try:
        s = series.load(ws, name)
    except SeriesError as exc:
        raise ChannelError(str(exc)) from exc
    title = title.strip()
    if not title or "\n" in title:
        raise ChannelError("the title is one non-empty line")
    values = {
        "status": "draft",
        "channel": "video",
        "task": s.task,
        "people": list(s.people),
        "created": today.isoformat(),
        "series": name,
    }

    def fill(records: Records) -> tuple[dict[str, str], str]:
        # under the records lock: the number and the asks carried forward come from the drafts as they are now
        drafts = series_drafts(records, name)
        number = (drafts[-1][0] if drafts else 0) + 1
        previous = drafts[-1][1] if drafts else None
        return {"number": str(number)}, skeleton(s, title, previous)

    try:
        return Store(ws.ops).create(schema.DRAFT.name, title, values, fill=fill)
    except StoreError as exc:
        raise ChannelError(str(exc)) from exc


# handlers -----------------------------------------------------------------------


def _print_issues(plan: checks.Plan) -> None:
    for issue in plan.report.warnings:
        print(issue)
    for issue in plan.report.errors:
        print(issue, file=sys.stderr)


def cmd_new(args, ws: Workspace) -> int:
    record = new_draft(ws, args.series, args.title, dt.date.today())
    print(f"{record.id} {record.path}")
    number = schema.text(record.meta, "number")
    print(f"update {number} of series '{args.series}'; write the scenes, then `uo video check {record.id}`")
    return 0


def _summary(plan: checks.Plan) -> str:
    s = plan.series
    return (
        f"{plan.record.id}: {len(plan.board.scenes)} scenes, estimated {timing.clock(plan.estimate)} "
        f"({plan.estimate:.0f} s; series bounds {s.min_seconds:.0f} to {s.max_seconds:.0f} s), "
        f"output class {plan.data_class}"
    )


def cmd_check(args, ws: Workspace) -> int:
    plan = checks.prepare(ws, args.id)
    fit.check(ws, plan)
    _print_issues(plan)
    if plan.report.errors:
        print(f"{len(plan.report.errors)} error(s)", file=sys.stderr)
        return 1
    print(f"ok: {_summary(plan)}")
    for use in plan.restricted_by:
        print(f"restricted by: scene {use.scene}, item {use.n} ({use.ref})")
    return 0


def cmd_build(args, ws: Workspace) -> int:
    plan = checks.prepare(ws, args.id)
    fit.check(ws, plan)  # before anything is voiced: text that cannot fit is refused here, not at render time
    _print_issues(plan)
    if plan.report.errors:
        print(f"{len(plan.report.errors)} error(s); nothing built", file=sys.stderr)
        return 1
    result = building.build(
        ws, plan, quality=args.quality, scene=args.scene, provider=args.provider, voice_name=args.voice
    )
    for path in result.written:
        print(f"wrote {path}")
    print(f"voice: {result.voice}, {result.voiced} sentence(s) synthesized, {result.voice_seconds:.1f} s")
    print(f"{plan.record.id}: {timing.clock(result.seconds)} ({result.seconds:.1f} s), output class {plan.data_class}")
    for use in result.restricted_by:
        print(
            f"restricted by: scene {use.scene}, item {use.n} ({use.ref}); not released by series '{plan.series.name}'"
        )
    if result.other is not None:
        print(
            f"note: an earlier build of {plan.record.id} in another output class is still in {result.other}; "
            "this build did not touch it: delete it if it should not stay"
        )
    return 0


def cmd_list(args, ws: Workspace) -> int:
    records = Store(ws.ops).load()
    known = series.names(ws)
    used = {schema.text(r.meta, "series") for r in records.drafts.values() if r.meta.get("channel") == "video"}
    if not known and not used:
        print(f"no series: add a profile under ops/{series.FOLDER.as_posix()}/")
        return 0
    for name in sorted(set(known) | (used - {""})):
        try:
            title = series.load(ws, name).title
        except SeriesError as exc:
            title = f"(profile problem: {exc})" if name in known else "(no profile)"
        print(f"{name}  {title}")
        drafts = [
            r
            for r in sorted(records.drafts.values(), key=lambda r: (schema.text(r.meta, "number").zfill(6), r.id))
            if r.meta.get("channel") == "video" and schema.text(r.meta, "series") == name
        ]
        if not drafts:
            print("  no updates yet")
        for r in drafts:
            m = r.meta
            print(f"  {schema.text(m, 'number'):>3}  {r.id}  {schema.text(m, 'status'):<8}  "
                  f"{schema.text(m, 'created')}  {r.title}")  # fmt: skip
    return 0


def cmd_setup(args, ws: Workspace | None) -> int:
    renderer = building.renderer_dir()
    if not (renderer / "package.json").is_file():
        raise ChannelError(f"no renderer at {renderer} (package.json missing); set {building.RENDERER_ENV}")
    if shutil.which("npm") is None:
        raise ChannelError("npm not found; install Node.js")
    # npm ci installs the packages; npm run setup downloads the headless browser, so no render fetches it (D16)
    for command in (["npm", "ci"], ["npm", "run", "setup"]):
        result = subprocess.run(command, cwd=renderer)
        if result.returncode != 0:
            raise ChannelError(f"{' '.join(command)} failed in {renderer} (exit {result.returncode})")
    print(f"renderer ready: {renderer}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    """Add `video` with its subcommands to the top-level parser."""
    from unfloat_os.channels import video

    parser = sub.add_parser(
        "video",
        help="narrated update videos",
        description=video.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="video_command", required=True)
    p = commands.add_parser("new", help="create the next update of a series as a draft record")
    p.add_argument("series")
    p.add_argument("title")
    p.set_defaults(handler=cmd_new)
    p = commands.add_parser("check", help="every check of a video draft, including sources, numbers, media and length")
    p.add_argument("id", metavar="DR-###")
    p.set_defaults(handler=cmd_check)
    p = commands.add_parser("build", help="check, voice and render a video draft into the data folder")
    p.add_argument("id", metavar="DR-###")
    p.add_argument("--quality", choices=("draft", "final"), default="final")
    p.add_argument("--scene", metavar="ID", help="render one scene only, to scene-<id>.mp4")
    p.add_argument("--provider", help="voice provider instead of the series' one")
    p.add_argument("--voice", help="voice name instead of the series' one")
    p.set_defaults(handler=cmd_build)
    p = commands.add_parser("list", help="every series with its updates")
    p.set_defaults(handler=cmd_list)
    p = commands.add_parser("setup", help="install the renderer's packages and its browser (npm ci, npm run setup)")
    p.set_defaults(handler=cmd_setup, needs_workspace=False)
