"""Text that may reach a person: one rule set for every channel, with per-channel additions.

Agents draft; people send. Adapters call `problems()` and refuse text that fails it.
"""

from __future__ import annotations

EM_DASH = "\u2014"
EN_DASH = "\u2013"

# Review markers must never reach a reader; em dashes read as machine-written in Daniil's voice.
COMMON = ("[CONFIRM", "[INFERENCE", EM_DASH)
CHANNEL_EXTRA = {
    "discord": (EN_DASH, "@everyone", "@here"),
}


def forbidden(channel: str) -> tuple[str, ...]:
    return COMMON + CHANNEL_EXTRA.get(channel, ())


def problems(text: str, channel: str) -> list[str]:
    """The forbidden marks present in `text`, in rule order."""
    return [mark for mark in forbidden(channel) if mark in text]
