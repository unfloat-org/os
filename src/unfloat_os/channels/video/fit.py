"""Whether the text of a storyboard fits where the renderer draws it (`uo video check` and `uo video build`).

One judge: the renderer measures the text with the font the video uses (`node bin/render.mjs --fit`, the same
sizing code its scenes draw with), so a draft that passes here is not refused at render time for its text. Without an
installed renderer the check falls back to `CAPS`, character limits that are approximate and on the safe side, and
says so with a warning. Problems name the scene and the field (`rows[2]`, `items[0].label`), before anything is voiced.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from unfloat_os.channels import ChannelError
from unfloat_os.channels.video import build as building
from unfloat_os.channels.video.checks import Plan, where
from unfloat_os.channels.video.storyboard import Problem, Scene
from unfloat_os.workspace import Workspace

FIT_VERSION = 1
TEXT_KINDS = ("statement", "media", "number", "list", "roadmap")
# The fallback, in characters, when no renderer is installed: what fits at the smallest text size with ordinary
# words (measured with the renderer's `--fit`, then rounded down). Wide letters can still fail at render time.
CAPS = {
    ("media", "headline"): 70,  # one line
    ("media", "info"): 110,  # labels and caption, one line under one box
    ("media", "label"): 50,  # each label under its own box of a two-item scene
    ("statement", "points"): 120,  # two lines each
    ("list", "headline"): 90,  # two lines
    ("list", "rows"): 120,  # two lines each
    ("list", "pairs"): 60,  # two lines each cell
    ("number", "headline"): 90,
    ("number", "label"): 90,
    ("number", "denominator"): 90,
    ("roadmap", "headline"): 80,
}
FALLBACK_WARNING = "the renderer is not installed, so text was checked against character limits only (`uo video setup`)"

# (scene index, field, message) for every text that does not fit
Measure = Callable[[dict], list[dict]]


def payload(plan: Plan) -> dict:
    """The input of `--fit`: each text scene's properties as the manifest carries them; media items keep their label."""
    scenes = []
    for scene in plan.board.scenes:
        if scene.kind not in TEXT_KINDS:
            continue
        props = {k: v for k, v in scene.props.items() if k not in building.ENGINE_ONLY}
        if scene.kind == "media":
            items = props.get("items") if isinstance(props.get("items"), list) else []
            props["items"] = [{"label": i["label"]} if isinstance(i, dict) and "label" in i else {} for i in items]
        scenes.append({"id": scene.id, "kind": scene.kind, "props": props})
    return {"version": FIT_VERSION, "captions": plan.series.captions, "scenes": scenes}


def renderer_measure(renderer: Path, folder: Path) -> Measure:
    """`node bin/render.mjs --fit` in the renderer; its bundle and temp files go to `folder` (removed after)."""

    def measure(data: dict) -> list[dict]:
        if folder.exists():
            shutil.rmtree(folder)
        (folder / "tmp").mkdir(parents=True)
        texts = folder / "texts.json"
        texts.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not (k.startswith("REMOTION") and "LICENSE" in k)}
        tmp = str(folder / "tmp")
        env.update(TMPDIR=tmp, TMP=tmp, TEMP=tmp)
        command = ["node", str(building.RENDERER_ENTRY), "--fit", str(texts)]
        try:
            result = subprocess.run(command, cwd=renderer, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                lines = result.stderr.strip().splitlines()
                reason = next((line for line in lines if line.startswith("error:")), lines[-1] if lines else "")
                raise ChannelError(f"renderer failed (--fit): {reason.removeprefix('error:').strip()}")
            try:
                answer = json.loads(result.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError) as exc:
                raise ChannelError("renderer failed (--fit): no answer") from exc
            return list(answer.get("problems", []))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    return measure


def default_measure(ws: Workspace, plan: Plan) -> Measure | None:
    """The renderer's measurement when the renderer is installed, else None (the caps apply)."""
    renderer = building.renderer_dir()
    try:
        building.require_renderer(renderer)
    except ChannelError:
        return None
    folder = ws.data_dir("private") / "updates" / "cache" / "fit" / plan.record.id
    return renderer_measure(renderer, folder)


def _too_long(text: object, cap: int) -> str | None:
    if isinstance(text, str) and len(text) > cap:
        return f"has {len(text)} characters; at most {cap} fit (character limit: the renderer is not installed)"
    return None


def cap_problems(scene: Scene) -> list[tuple[str, str]]:
    """(field, message) for the scene's texts over their character limit."""
    p, kind, found = scene.props, scene.kind, []

    def check(field: str, text: object, key: str) -> None:
        if message := _too_long(text, CAPS[(kind, key)]):
            found.append((field, message))

    if kind == "media":
        check("headline", p.get("headline"), "headline")
        items = [i for i in p.get("items") or [] if isinstance(i, dict)]
        labels = [i.get("label") for i in items if isinstance(i.get("label"), str)]
        caption = p.get("caption") if isinstance(p.get("caption"), str) else ""
        if len(items) == 1 or caption:
            joined = "  ·  ".join([*labels, caption] if caption else labels)
            check("caption" if caption else "items[0].label", joined, "info")
        else:
            for n, item in enumerate(items):
                check(f"items[{n}].label", item.get("label"), "label")
    elif kind == "statement":
        for n, point in enumerate(p.get("points") or []):
            check(f"points[{n}]", point, "points")
    elif kind == "list":
        check("headline", p.get("headline"), "headline")
        for n, row in enumerate(p.get("rows") or []):
            if isinstance(row, dict):
                for side in ("left", "right"):
                    check(f"rows[{n}].{side}", row.get(side), "pairs")
            else:
                check(f"rows[{n}]", row, "rows")
    elif kind == "number":
        for field in ("headline", "label", "denominator"):
            check(field, p.get(field), field)
    elif kind == "roadmap":
        check("headline", p.get("headline"), "headline")
    return found


def check(ws: Workspace, plan: Plan, measure: Measure | str | None = "default") -> None:
    """Add every text that does not fit to the plan's report: measured by the renderer, or by `CAPS` without one."""
    here = where(plan.record)
    measure = default_measure(ws, plan) if measure == "default" else measure
    if measure is None:
        plan.report.warning(here, FALLBACK_WARNING)
        for scene in plan.board.scenes:
            for field, message in cap_problems(scene):
                plan.report.error(here, str(Problem(scene.id, "fit", f"'{field}' {message}")))
        return
    for problem in measure(payload(plan)):
        message = f"'{problem.get('field')}' {problem.get('message')}"
        plan.report.error(here, str(Problem(str(problem.get("id")), "fit", message)))
