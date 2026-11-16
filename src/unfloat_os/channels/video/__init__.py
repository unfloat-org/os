"""Narrated update videos: an outbound channel whose drafts are storyboards (docs/video.md).

  uo video new SERIES "title"     # next update of a series: a draft with the chapter skeleton and the last asks
  uo video check DR-###           # every check, including sources, the number check, media and the length
  uo video build DR-### [--quality draft|final] [--scene ID] [--provider NAME] [--voice NAME]
  uo video list                   # every series with its updates
  uo video setup                  # npm ci in the renderer

A draft with `channel: video` names its `series` (a profile `config/video/<series>.toml` in the records
repository) and its `number`. Its body is the storyboard: one `## <chapter>` or `## <chapter>: <label>` heading
per scene, a fenced toml block with the scene's properties, then the narration. The build writes the video and
its companions to `<class>:updates/<draft id>/`, where the class is restricted when any media is restricted and
not released by the series. Nothing is sent: Daniil sends.
"""

from __future__ import annotations

import argparse


def register(sub: argparse._SubParsersAction) -> None:
    from unfloat_os.channels.video.commands import register as add

    add(sub)
