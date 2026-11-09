"""Outbound and read-only channel adapters. None of them can send a message to a person."""


class ChannelError(Exception):
    """A refusal or failure the user must act on; printed as `error: ...`."""
