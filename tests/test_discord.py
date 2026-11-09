"""The Discord adapter's pure parts: permissions, channel links, visibility and spec validation."""

from __future__ import annotations

import copy
import time

import pytest

from unfloat_os.channels import ChannelError
from unfloat_os.channels.discord import (
    PERMISSIONS,
    bits,
    everyone_can_view,
    mention_text,
    snowflake_time,
    validate,
)

VIEW = 1 << PERMISSIONS["VIEW_CHANNEL"]
GUILD = "100"


def test_mention_text_links_known_channels_only():
    text = "Say hi in #general, see #unknown and https://example.org/#general or word#general."
    assert mention_text(text, {"general": "42"}) == (
        "Say hi in <#42>, see #unknown and https://example.org/#general or word#general."
    )


def test_mention_text_keeps_hyphenated_names_whole():
    assert mention_text("#start-here and #start", {"start-here": "7"}) == "<#7> and #start"


def test_bits_sums_permission_flags_once_each():
    assert bits(["VIEW_CHANNEL", "SEND_MESSAGES", "VIEW_CHANNEL"]) == (1 << 10) | (1 << 11)
    assert bits([]) == 0


def test_bits_places_permissions_after_the_gap_in_discord_numbering():
    assert PERMISSIONS["USE_SOUNDBOARD"] == 42
    assert PERMISSIONS["CREATE_GUILD_EXPRESSIONS"] == 43
    assert PERMISSIONS["SEND_VOICE_MESSAGES"] == 46
    assert PERMISSIONS["SEND_POLLS"] == 49


def test_bits_rejects_unknown_permissions():
    with pytest.raises(ChannelError, match="unknown permissions: FLY, TELEPORT"):
        bits(["VIEW_CHANNEL", "TELEPORT", "FLY"])


def overwrite(target: str, *, allow: int = 0, deny: int = 0) -> dict:
    return {"id": target, "allow": str(allow), "deny": str(deny)}


def test_everyone_can_view_by_default_permissions():
    channel = {"id": "1"}
    assert everyone_can_view(channel, GUILD, VIEW, {"1": channel})
    assert not everyone_can_view(channel, GUILD, 0, {"1": channel})


def test_everyone_can_view_ignores_overwrites_for_other_roles():
    channel = {"id": "1", "permission_overwrites": [overwrite("555", deny=VIEW)]}
    assert everyone_can_view(channel, GUILD, VIEW, {"1": channel})


def test_category_deny_hides_the_channel_below_it():
    category = {"id": "9", "permission_overwrites": [overwrite(GUILD, deny=VIEW)]}
    channel = {"id": "1", "parent_id": "9"}
    assert not everyone_can_view(channel, GUILD, VIEW, {"9": category, "1": channel})


def test_channel_allow_beats_category_deny():
    category = {"id": "9", "permission_overwrites": [overwrite(GUILD, deny=VIEW)]}
    channel = {"id": "1", "parent_id": "9", "permission_overwrites": [overwrite(GUILD, allow=VIEW)]}
    assert everyone_can_view(channel, GUILD, VIEW, {"9": category, "1": channel})


def test_channel_deny_beats_category_allow():
    category = {"id": "9", "permission_overwrites": [overwrite(GUILD, allow=VIEW)]}
    channel = {"id": "1", "parent_id": "9", "permission_overwrites": [overwrite(GUILD, deny=VIEW)]}
    assert not everyone_can_view(channel, GUILD, 0, {"9": category, "1": channel})


def test_a_thread_is_visible_when_its_parent_is():
    parent = {"id": "1", "permission_overwrites": [overwrite(GUILD, deny=VIEW)]}
    thread = {"id": "2", "type": 11, "parent_id": "1"}
    assert not everyone_can_view(thread, GUILD, VIEW, {"1": parent, "2": thread})
    open_parent = {"id": "1"}
    assert everyone_can_view(thread, GUILD, VIEW, {"1": open_parent, "2": thread})


def test_snowflake_time_decodes_the_creation_moment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        # The example ID from Discord's documentation: 2016-04-30 11:18:25.796 UTC.
        assert snowflake_time("175928847299117063") == "2016-04-30 11:18"
        assert snowflake_time(175928847299117063) == "2016-04-30 11:18"
    finally:
        monkeypatch.undo()
        time.tzset()


CHANNELS = ("start-here", "general", "intro", "ideas", "help", "mod-log", "core")


def minimal_spec() -> dict:
    channels = [{"name": name, "type": "text"} for name in CHANNELS]
    return {
        "everyone_permissions": ["VIEW_CHANNEL", "SEND_MESSAGES"],
        "roles": [{"name": "Founder", "assign_to_owner": True}, {"name": "Helper"}],
        "categories": [
            {"name": "Talk", "channels": channels},
            {
                "name": "Work",
                "channels": [
                    {
                        "name": "tasks",
                        "type": "forum",
                        "tags": [{"name": "1 hour"}, {"name": "sharing"}, {"name": "open"}],
                    }
                ],
            },
        ],
        "guild": {
            "name": "demo",
            "description": "A demo server.",
            "verification_level": 2,
            "explicit_content_filter": 2,
            "system_channel": "general",
            "rules_channel": "start-here",
            "public_updates_channel": "general",
            "safety_alerts_channel": "mod-log",
        },
        "forum_guidelines": {},
        "posts": [],
        "task_forum": "tasks",
        "task_default_tags": ["open"],
        "onboarding": {"default_channels": [*CHANNELS[:5], "tasks", "core"], "prompts": []},
        "automod": [],
        "invite": {"channel": "start-here"},
        "prune_defaults": {"max_members": 2},
    }


def minimal_sections(task_body: str = "Share a paper.") -> dict:
    topics = "\n".join(f"- #{name}: About {name}." for name in CHANNELS)
    return {"Channel topics": topics, "Task: Share a paper": f"Tags: 1 hour, sharing\n\n{task_body}"}


def test_validate_accepts_a_minimal_spec():
    data = validate(minimal_spec(), minimal_sections())
    assert data["writable"] >= 5
    assert data["tasks"] == [
        {
            "section": "Task: Share a paper",
            "title": "Share a paper",
            "body": "Share a paper.",
            "tags": ["1 hour", "sharing"],
        }
    ]
    assert set(data["channels"]) == {*CHANNELS, "tasks"}


@pytest.mark.parametrize("mark", ["\u2014", "\u2013", "[CONFIRM", "[INFERENCE", "@everyone", "@here"])
def test_validate_reports_forbidden_text_in_a_post(mark: str):
    with pytest.raises(ChannelError) as error:
        validate(minimal_spec(), minimal_sections(task_body=f"Share a paper {mark} today."))
    assert str(error.value) == (f"validation failed:\n  - Task: Share a paper: forbidden text {mark!r}")


def test_validate_reports_every_problem_in_one_pass():
    spec = copy.deepcopy(minimal_spec())
    spec["guild"]["description"] = "Open \u2014 and free"
    spec["everyone_permissions"] = ["VIEW_CHANNEL", "TELEPORT"]
    with pytest.raises(ChannelError) as error:
        validate(spec, minimal_sections())
    message = str(error.value)
    assert "everyone_permissions: unknown permissions: TELEPORT" in message
    assert "server description: forbidden text '\u2014'" in message
