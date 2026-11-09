"""Set up unfloat's Discord server from a local spec and content draft.

  uo discord plan [--draft FILE] [--spec FILE]  # offline
  uo discord check [--guild ID]                # read only
  uo discord apply [--guild ID] [--draft FILE]
  uo discord verify [--guild ID] [--draft FILE] # read only
  uo discord invite [--guild ID]
  uo discord members [--guild ID]                      # read only
  uo discord read [--guild ID] [--days N] [--channel NAME ...]  # read only

Daniil creates the server and invites the bot with Administrator first. Bots
cannot create servers. Run plan before apply; apply publishes the draft.
The server structure is the spec named in config/channels.toml of the records
repository (config/discord/server.json); the post texts come from the draft it names.
Put the bot token in ~/.config/unfloat/discord/bot_token (chmod 600), or set
DISCORD_BOT_TOKEN. The token is never printed. IDs and recovery records live in
~/.config/unfloat/discord/state.json, keyed by guild ID. Keep that file to retain
post identity when editing titles or text. A local lock prevents concurrent writes.

Guards: never sends DMs or posts outside spec channels; never changes members
except giving the Founder role to the guild owner; never deletes anything except
empty named Discord defaults while the member count is within prune_defaults.
No launch messages or Notes are posted. All messages suppress notifications,
embeds and mentions. verify makes GET requests only and does not change state.

members and read (D-022) are GET-only too: who joined, their roles, and what people
wrote in channels @everyone can see. read skips the bot's own posts and, unless named
with --channel, #living-with-floaters; hidden channels (#core, #mod-log, #leads) are
never read. They need the Server Members and Message Content intents switched on in
the Developer Portal (Bot tab); without them Discord answers 403 or blanks message text.

Creates are checkpointed immediately. After an interrupted request, resources
are recovered by saved ID, name/type, or bot-authored content/title before retrying.
Ambiguous matches stop rather than choosing an unrelated resource. Voice defaults
are kept if their occupancy or message history cannot be checked. Missing icons
are skipped with a warning; supplied icons must be 1024 by 1024 PNG files.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import struct
import sys
import time
import tomllib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from unfloat_os import credentials, frontmatter, http, policy
from unfloat_os.channels import ChannelError
from unfloat_os.records import Store, StoreError
from unfloat_os.workspace import Workspace

API = "https://discord.com/api/v10"
TYPES = {"text": 0, "voice": 2, "category": 4, "announcement": 5, "forum": 15}
PERMISSIONS = dict(
    zip(
        (  # noqa: SIM905
            "CREATE_INSTANT_INVITE KICK_MEMBERS BAN_MEMBERS ADMINISTRATOR MANAGE_CHANNELS "
            "MANAGE_GUILD ADD_REACTIONS VIEW_AUDIT_LOG PRIORITY_SPEAKER STREAM VIEW_CHANNEL "
            "SEND_MESSAGES SEND_TTS_MESSAGES MANAGE_MESSAGES EMBED_LINKS ATTACH_FILES "
            "READ_MESSAGE_HISTORY MENTION_EVERYONE USE_EXTERNAL_EMOJIS VIEW_GUILD_INSIGHTS "
            "CONNECT SPEAK MUTE_MEMBERS DEAFEN_MEMBERS MOVE_MEMBERS USE_VAD CHANGE_NICKNAME "
            "MANAGE_NICKNAMES MANAGE_ROLES MANAGE_WEBHOOKS MANAGE_GUILD_EXPRESSIONS "
            "USE_APPLICATION_COMMANDS REQUEST_TO_SPEAK MANAGE_EVENTS MANAGE_THREADS "
            "CREATE_PUBLIC_THREADS CREATE_PRIVATE_THREADS USE_EXTERNAL_STICKERS "
            "SEND_MESSAGES_IN_THREADS USE_EMBEDDED_ACTIVITIES MODERATE_MEMBERS "
            "USE_SOUNDBOARD CREATE_GUILD_EXPRESSIONS CREATE_EVENTS USE_EXTERNAL_SOUNDS "
            "SEND_VOICE_MESSAGES SET_VOICE_CHANNEL_STATUS SEND_POLLS USE_EXTERNAL_APPS "
            "PIN_MESSAGES BYPASS_SLOWMODE"
        ).split(),
        [*range(41), 42, 43, 44, 45, 46, 48, 49, 50, 51, 52],
        strict=True,
    )
)
SIZE_TAGS = {"5 minutes", "1 hour", "an evening"}
STATUS_TAGS = {"open", "someone's on it", "done", "proposal"}
KIND_TAGS = {"reading papers", "finding people", "translating", "writing & design", "data & AI", "sharing"}
MENTION = re.compile(r"(?<![\w/#])#([A-Za-z0-9][A-Za-z0-9-]*)(?![\w-])")
MESSAGE_FLAGS = 4 | 4096


@dataclass(frozen=True)
class DiscordConfig:
    """Where the records live (for the content draft), the workspace root (for the icon), credentials and the spec."""

    store: Store
    root: Path
    conf_dir: Path
    spec: Path

    @classmethod
    def from_workspace(cls, ws: Workspace) -> DiscordConfig:
        path = ws.ops / "config" / "channels.toml"
        try:
            spec = tomllib.loads(path.read_text(encoding="utf-8")).get("discord", {}).get("spec")
        except FileNotFoundError as exc:
            raise ChannelError(f"{path} not found") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ChannelError(f"{path}: {exc}") from exc
        if not isinstance(spec, str) or not spec.strip():
            raise ChannelError(f'{path}: [discord] needs spec = "..."')
        return cls(Store(ws.ops), ws.root, credentials.channel_dir("discord"), ws.ops / spec.strip())


class HTTPFail(ChannelError):
    def __init__(self, method, path, status, detail):
        self.status = status
        super().__init__(f"{method} {path.split('?')[0]}: HTTP {status}: {detail}")


def warn(message):
    print(f"warning: {message}", file=sys.stderr)


def bits(names):
    unknown = set(names) - PERMISSIONS.keys()
    if unknown:
        raise ChannelError(f"unknown permissions: {', '.join(sorted(unknown))}")
    return sum(1 << PERMISSIONS[name] for name in set(names))


def role_matches(existing, payload):
    color = (existing.get("colors") or {}).get("primary_color", existing.get("color", 0))
    return (
        existing["name"] == payload["name"]
        and str(existing["permissions"]) == payload["permissions"]
        and color == payload["colors"]["primary_color"]
        and bool(existing.get("hoist")) == payload["hoist"]
    )


def mention_text(text, channel_ids):
    return MENTION.sub(lambda match: f"<#{channel_ids[match[1]]}>" if match[1] in channel_ids else match[0], text)


def merged_overwrites(category, channel):
    result = {}
    for item in category.get("overwrites", []) + channel.get("overwrites", []):
        target = result.setdefault(item["role"], {"allow": set(), "deny": set()})
        allow, deny = set(item.get("allow", [])), set(item.get("deny", []))
        target["allow"] = (target["allow"] - deny) | allow
        target["deny"] = (target["deny"] - allow) | deny
    return result


def load_inputs(cfg, spec_path, draft_path=None):
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if draft_path is None:
            try:
                draft_path = cfg.store.path_of(spec["content_draft"])
            except StoreError as exc:
                raise ChannelError(f"expected one {spec['content_draft']} draft; use --draft FILE") from exc
        raw = draft_path.read_text(encoding="utf-8")
        meta = frontmatter.parse(raw)
        if meta.get("channel") != "discord":
            raise ChannelError(f"{draft_path}: frontmatter channel must be discord")
        body = frontmatter.body(raw)
        sections = {}
        current = None
        for line in body.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                if current in sections:
                    raise ChannelError(f"duplicate draft section: {current}")
                sections[current] = []
            elif current is not None:
                sections[current].append(line)
        sections = {name: "\n".join(lines).strip() for name, lines in sections.items()}
        return validate(spec, sections)
    except (OSError, ValueError) as exc:
        raise ChannelError(f"cannot read spec/draft: {exc}") from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise ChannelError(f"invalid spec structure near {exc}; check config/discord/server.json") from exc


def validate(spec, sections):
    """Collect independent content errors so plan reports them in one pass."""
    errors = []
    channels, categories, roles = {}, {}, {}

    def require(ok, message):
        if not ok:
            errors.append(message)

    def text(value, label, maximum=None):
        require(isinstance(value, str) and bool(value.strip()), f"{label}: must be nonempty text")
        if not isinstance(value, str):
            return
        errors.extend(f"{label}: forbidden text {mark!r}" for mark in policy.problems(value, "discord"))
        if maximum is not None:
            require(
                len(mention_text(value, {name: "0" * 19 for name in channels})) <= maximum,
                f"{label}: exceeds {maximum} characters after channel-link expansion",
            )

    def permissions(names, label):
        try:
            bits(names)
        except ChannelError as exc:
            errors.append(f"{label}: {exc}")

    permissions(spec["everyone_permissions"], "everyone_permissions")
    for role in spec["roles"]:
        name = role["name"]
        require(name not in roles and name != "@everyone", f"duplicate/reserved role: {name}")
        roles[name] = role
        text(name, "role name", 100)
        permissions(role.get("permissions", []), f"role {name}")
        require(not role.get("assign_to_owner") or name == "Founder", "only Founder may be assigned to the owner")
        require(bool(re.fullmatch(r"#[0-9a-fA-F]{6}", role.get("color", "#000000"))), f"role {name}: invalid color")
    for category in spec["categories"]:
        name = category["name"]
        require(name not in categories, f"duplicate category: {name}")
        categories[name] = category
        text(name, "category name", 100)
        for channel in category["channels"]:
            cname = channel["name"]
            require(cname not in channels and cname not in categories, f"duplicate channel: {cname}")
            channels[cname] = {**channel, "category": name}
            text(cname, "channel name", 100)
            require(channel["type"] in ("text", "voice", "announcement", "forum"), f"#{cname}: unsupported type")
            tags = channel.get("tags", [])
            require(len(tags) <= 20, f"#{cname}: more than 20 tags")
            require(len({tag["name"] for tag in tags}) == len(tags), f"#{cname}: duplicate tags")
            for tag in tags:
                text(tag["name"], f"#{cname} tag", 20)
        for item in [category] + category["channels"]:
            seen_roles = set()
            for overwrite in item.get("overwrites", []):
                role = overwrite["role"]
                require(role == "@everyone" or role in roles, f"{item['name']}: unknown overwrite role {role}")
                require(role not in seen_roles, f"{item['name']}: duplicate overwrite for {role}")
                seen_roles.add(role)
                allow, deny = overwrite.get("allow", []), overwrite.get("deny", [])
                permissions(allow + deny, f"{item['name']} overwrite")
                require(not set(allow) & set(deny), f"{item['name']}: overlapping allow/deny")
    require(not categories.keys() & channels.keys(), "category and channel names must not overlap")
    guild = spec["guild"]
    text(guild["name"], "server name", 100)
    text(guild["description"], "server description", 1000)
    require(guild.get("verification_level") == 2, "guild verification_level must be 2")
    require(guild.get("explicit_content_filter") == 2, "guild explicit_content_filter must be 2")
    for field in ("system_channel", "rules_channel", "public_updates_channel", "safety_alerts_channel"):
        require(guild.get(field) in channels, f"guild {field}: unknown channel {guild.get(field)!r}")
    for field in ("rules_channel", "public_updates_channel"):
        require(
            channels.get(guild.get(field), {}).get("type") == "text", f"guild {field}: must reference a text channel"
        )
    for required in ("start-here", "mod-log", "core"):
        require(channels.get(required, {}).get("type") == "text", f"#{required}: required text channel")

    topics = {}
    require("Channel topics" in sections, "missing section: Channel topics")
    for line in sections.get("Channel topics", "").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"- #([a-z0-9-]+): (.+)", line)
        if not match:
            errors.append(f"Channel topics: invalid line {line!r}")
            continue
        name, topic = match.groups()
        require(name in channels, f"Channel topics: unknown channel #{name}")
        require(name not in topics, f"Channel topics: duplicate #{name}")
        topics[name] = topic
        text(topic, f"#{name} topic", 1024)
        require(
            channels.get(name, {}).get("type") in ("text", "announcement"), f"#{name}: topic must be for a text channel"
        )
    for name, channel in channels.items():
        if channel["type"] in ("text", "announcement"):
            require(name in topics, f"Channel topics: missing #{name}")

    def section(name):
        require(name in sections, f"missing section: {name}")
        return sections.get(name, "")

    for name, section_name in spec["forum_guidelines"].items():
        require(channels.get(name, {}).get("type") == "forum", f"guidelines: #{name} is not a forum")
        text(section(section_name), section_name, 1024)
    post_keys = set()
    pinned_forums = set()
    for post in spec["posts"]:
        name, key = post["channel"], post["section"]
        require(key not in post_keys, f"duplicate post section: {key}")
        post_keys.add(key)
        require(
            channels.get(name, {}).get("type") in ("text", "announcement", "forum"),
            f"post {key}: invalid channel #{name}",
        )
        text(section(key), key, 2000)
        if channels.get(name, {}).get("type") == "forum":
            text(post.get("title", ""), f"{key} thread title", 100)
            if post.get("pin"):
                require(name not in pinned_forums, f"#{name}: at most one pinned forum post")
                pinned_forums.add(name)
    task_forum = spec["task_forum"]
    require(channels.get(task_forum, {}).get("type") == "forum", "task_forum must reference a forum")
    task_tags = {tag["name"] for tag in channels.get(task_forum, {}).get("tags", [])}
    defaults = spec.get("task_default_tags", [])
    require(set(defaults) <= task_tags, "task_default_tags: unknown tags")
    require(set(defaults) <= STATUS_TAGS, "task_default_tags must contain only status tags")
    tasks = []
    for key, body in sections.items():
        if not key.startswith("Task: "):
            continue
        title = key[6:]
        text(title, f"{key} thread title", 100)
        first, _, content = body.partition("\n")
        require(first.startswith("Tags: "), f"{key}: first line must be Tags: ...")
        require(content.startswith("\n"), f"{key}: add a blank line after Tags")
        tags = [tag.strip() for tag in first.removeprefix("Tags: ").split(",") if tag.strip()]
        require(len(tags) == len(set(tags)), f"{key}: duplicate tags")
        require(set(tags) <= task_tags, f"{key}: unknown tags {sorted(set(tags) - task_tags)}")
        require(not set(tags) & STATUS_TAGS, f"{key}: status tags belong in task_default_tags, not the draft")
        require(len(set(tags) & SIZE_TAGS) == 1, f"{key}: needs exactly one size tag")
        require(bool(set(tags) & KIND_TAGS), f"{key}: needs at least one kind tag")
        require(set(tags) <= KIND_TAGS | SIZE_TAGS, f"{key}: only kind and size tags are allowed")
        require(len(set(tags) | set(defaults)) <= 5, f"{key}: Discord allows at most 5 applied tags")
        content = content.strip()
        text(content, key, 2000)
        tasks.append({"section": key, "title": title, "body": content, "tags": tags})
    require(bool(tasks), "draft needs at least one Task: section")
    for key, body in sections.items():
        if key.startswith("Launch:"):
            text(body, key)

    onboarding = spec["onboarding"]
    default_channels = onboarding["default_channels"]
    require(len(default_channels) == len(set(default_channels)), "onboarding: duplicate default channel")
    require(len(set(default_channels)) >= 7, "onboarding: needs at least 7 default channels")
    writable = 0
    for name in default_channels:
        require(name in channels, f"onboarding: unknown default channel #{name}")
        channel = channels.get(name)
        if not channel:
            continue
        ow = merged_overwrites(categories[channel["category"]], channel).get(
            "@everyone", {"allow": set(), "deny": set()}
        )
        effective = (set(spec["everyone_permissions"]) - ow["deny"]) | ow["allow"]
        if channel["type"] in ("text", "announcement", "forum") and {"SEND_MESSAGES", "VIEW_CHANNEL"} <= effective:
            writable += 1
    require(writable >= 5, f"onboarding: needs 5 writable default channels, found {writable}")
    prompt_titles = set()
    for prompt in onboarding["prompts"]:
        text(prompt["title"], "onboarding prompt", 100)
        require(prompt["title"] not in prompt_titles, f"duplicate onboarding prompt: {prompt['title']}")
        prompt_titles.add(prompt["title"])
        require(bool(prompt["options"]), f"{prompt['title']}: needs options")
        option_titles = set()
        for option in prompt["options"]:
            text(option["title"], "onboarding option", 100)
            require(option["title"] not in option_titles, f"duplicate onboarding option: {option['title']}")
            option_titles.add(option["title"])
            if option.get("description"):
                text(option["description"], "onboarding description", 100)
            for name in option.get("channels", []):
                require(name in channels, f"onboarding option: unknown channel #{name}")
            for name in option.get("roles", []):
                require(name in roles, f"onboarding option: unknown role {name}")
                if name in roles:
                    require(
                        not roles[name].get("permissions") and not roles[name].get("assign_to_owner"),
                        f"onboarding cannot grant privileged role {name}",
                    )
    trigger_counts = {}
    rule_names = set()
    for rule in spec["automod"]:
        text(rule["name"], "AutoMod rule name", 100)
        require(rule["name"] not in rule_names, f"duplicate AutoMod rule: {rule['name']}")
        rule_names.add(rule["name"])
        trigger = rule["trigger_type"]
        require(trigger in (1, 3, 4, 5), f"{rule['name']}: unsupported trigger type")
        trigger_counts[trigger] = trigger_counts.get(trigger, 0) + 1
        require(trigger_counts[trigger] <= (6 if trigger == 1 else 1), f"too many AutoMod rules for trigger {trigger}")
        if trigger == 1:
            require(bool(rule.get("keyword_filter")), f"{rule['name']}: needs keywords")
        for keyword in rule.get("keyword_filter", []):
            text(keyword, f"{rule['name']} keyword", 60)
        if "block_message" in rule:
            text(rule["block_message"], f"{rule['name']} custom message", 150)
        require(rule.get("alert_channel") in channels, f"{rule['name']}: unknown alert channel")
        for name in rule.get("exempt_roles", []):
            require(name in roles, f"{rule['name']}: unknown exempt role {name}")
    require(spec["invite"]["channel"] in channels, "invite: unknown channel")
    require(
        spec["invite"].get("max_age", 0) == 0 and spec["invite"].get("max_uses", 0) == 0,
        "invite must have unlimited age and uses",
    )
    require(spec["prune_defaults"].get("max_members", -1) >= 0, "prune_defaults.max_members must be nonnegative")
    if errors:
        raise ChannelError("validation failed:\n" + "\n".join(f"  - {error}" for error in errors))
    return {
        "spec": spec,
        "sections": sections,
        "channels": channels,
        "categories": categories,
        "roles": roles,
        "topics": topics,
        "tasks": tasks,
        "writable": writable,
    }


def plan(data):
    spec = data["spec"]
    print(f"Server: {spec['guild']['name']} (Community)")
    print("Roles (highest first): " + ", ".join(data["roles"]))
    for category in spec["categories"]:
        print(category["name"] + ":")
        for channel in category["channels"]:
            print(f"  #{channel['name']} ({channel['type']})")
    estimate = {name: "0" * 19 for name in data["channels"]}
    for post in spec["posts"]:
        body = mention_text(data["sections"][post["section"]], estimate)
        print(f"Post #{post['channel']}: {post['section']} ({len(body)} chars)")
        print(data["sections"][post["section"]])
    for task in data["tasks"]:
        tags = task["tags"] + spec["task_default_tags"]
        print(f"Task: {task['title']} ({len(mention_text(task['body'], estimate))} chars; tags: {', '.join(tags)})")
        print(task["body"])
    print(f"Onboarding: {len(spec['onboarding']['default_channels'])} defaults, {data['writable']} writable")
    print("AutoMod: " + ", ".join(rule["name"] for rule in spec["automod"]))
    print("OK: plan validated; no network calls")


def token(conf):
    path = conf / "bot_token"
    value = credentials.read_secret(path, env="DISCORD_BOT_TOKEN")
    if not value:
        raise ChannelError(
            f"no bot token; put it in {path} (chmod 600), or set DISCORD_BOT_TOKEN. "
            "Create a server yourself and invite the bot with Administrator first."
        )
    return value


class Client:
    def __init__(self, secret, readonly=False):
        self.secret = secret
        self.readonly = readonly
        self.last_mutation = 0.0

    def request(self, method, path, data=None, recover=None):
        if self.readonly and method != "GET":
            raise ChannelError("read-only command attempted a write")
        headers = {
            "Authorization": f"Bot {self.secret}",
            "User-Agent": "DiscordBot (https://github.com/unfloat-org, 1.0)",
            "Content-Type": "application/json",
            "X-Audit-Log-Reason": urllib.parse.quote("unfloat setup (uo discord)"),
        }
        server_retries = rate_retries = 0
        while True:
            if method != "GET":
                time.sleep(max(0, 0.3 - (time.monotonic() - self.last_mutation)))
                self.last_mutation = time.monotonic()
            try:
                return http.send(
                    method, API + path, data=data, headers=headers, redact=(self.secret,), detail_chars=700
                )
            except http.HttpError as exc:
                if exc.status == 429 and rate_retries < 12:
                    try:
                        delay = float(json.loads(exc.detail)["retry_after"])
                    except (ValueError, KeyError, TypeError) as error:
                        raise ChannelError("Discord returned a 429 without a valid retry_after") from error
                    if not 0 <= delay <= 3600:
                        raise ChannelError("Discord returned an invalid retry_after") from exc
                    rate_retries += 1
                    time.sleep(delay + 0.1)
                    continue
                if 500 <= exc.status < 600 and server_retries < 3:
                    server_retries += 1
                    time.sleep(2 ** (server_retries - 1))
                    if recover is not None:
                        existing = recover()
                        if existing is not None:
                            return existing
                    continue
                raise HTTPFail(method, path, exc.status, exc.detail) from exc
            except http.NetworkError as exc:
                # A write may have reached Discord. Resume via the saved recovery record.
                raise ChannelError(f"{method} {path.split('?')[0]}: network failure; rerun to resume safely") from exc

    def get(self, path, missing=False):
        try:
            return self.request("GET", path)
        except HTTPFail as exc:
            if missing and exc.status == 404:
                return None
            raise


def unique(items, description):
    if len(items) > 1:
        raise ChannelError(f"ambiguous {description}: found {len(items)} matches; resolve duplicates in Discord first")
    return items[0] if items else None


def connect(client, guild_id):
    me = client.get("/users/@me")
    if not me.get("bot"):
        raise ChannelError("the credential must be a bot token")
    if guild_id is None:
        guilds = []
        after = None
        while True:
            query = "?limit=200" + (f"&after={after}" if after else "")
            page = client.get("/users/@me/guilds" + query)
            guilds.extend(page)
            if len(page) < 200:
                break
            after = max(int(guild["id"]) for guild in page)
        if len(guilds) != 1:
            raise ChannelError(f"bot belongs to {len(guilds)} servers; use --guild ID (or invite it to your server)")
        guild_id = guilds[0]["id"]
    if not str(guild_id).isdigit():
        raise ChannelError("--guild must be a numeric Discord server ID")
    guild = client.get(f"/guilds/{guild_id}?with_counts=true")
    roles = client.get(f"/guilds/{guild_id}/roles")
    member = client.get(f"/guilds/{guild_id}/members/{me['id']}")
    granted = 0
    for role in roles:
        if role["id"] == guild_id or role["id"] in member["roles"]:
            granted |= int(role["permissions"])
    admin = bool(granted & (1 << PERMISSIONS["ADMINISTRATOR"])) or me["id"] == guild["owner_id"]
    print(f"Bot: {me['username']} ({me['id']})")
    print(f"Server: {guild['name']} ({guild['id']}), members: {guild.get('approximate_member_count', 'unknown')}")
    print(f"Administrator: {'yes' if admin else 'no'}")
    return me, guild, roles, member, admin


@contextlib.contextmanager
def state_lock(conf):
    conf.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(conf / "state.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ChannelError("another Discord apply/invite command is running") from exc
        yield
    finally:
        os.close(fd)


class State:
    def __init__(self, conf, guild_id, readonly=False):
        self.conf = conf
        self.path = conf / "state.json"
        self.readonly = readonly
        self.all = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.all, dict):
            raise ChannelError(f"{self.path}: expected an object keyed by guild ID")
        self.data = self.all.setdefault(guild_id, {})
        for key in ("roles", "channels", "messages", "threads", "rules", "pending"):
            self.data.setdefault(key, {})

    def save(self):
        if self.readonly:
            return
        self.conf.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.all, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)

    def remember(self, kind, key, value):
        self.data[kind][key] = value
        self.save()


class Server:
    def __init__(self, client, data, identity, state, root):
        self.client, self.data, self.state, self.root = client, data, state, root
        self.me, self.guild, self.roles, self.member, self.admin = identity
        self.gid = self.guild["id"]
        self.base = f"/guilds/{self.gid}"
        self.spec = data["spec"]
        self.live_channels = client.get(self.base + "/channels")
        self.role_ids = {"@everyone": self.gid}
        self.channel_ids = {}
        self.thread_cache = {}
        self.active_threads = None
        for name in data["roles"]:
            role = self.named("roles", name, self.roles)
            if role:
                self.role_ids[name] = role["id"]
        for category in self.spec["categories"]:
            for item in [{**category, "type": "category"}] + category["channels"]:
                channel = self.named("channels", item["name"], self.live_channels, TYPES[item["type"]])
                if channel:
                    self.channel_ids[item["name"]] = channel["id"]

    def named(self, kind, name, items, channel_type=None):
        saved = self.state.data[kind].get(name)
        existing = next((item for item in items if item["id"] == saved), None)
        if existing is not None:
            if channel_type is not None and existing["type"] != channel_type:
                raise ChannelError(f"saved channel {name} has a different type; refusing to replace it")
            return existing
        return unique(
            [item for item in items if item["name"] == name and (channel_type is None or item["type"] == channel_type)],
            f"{kind} named {name!r}",
        )

    def render(self, text):
        return mention_text(text, self.channel_ids)

    def overwrites(self, category, channel):
        return [
            {
                "id": self.role_ids[name],
                "type": 0,
                "allow": str(bits(value["allow"])),
                "deny": str(bits(value["deny"])),
            }
            for name, value in merged_overwrites(category, channel).items()
        ]

    def apply_roles(self):
        # Roles the owner dragged above the bot's role stay as they are if they already match the spec.
        above = set()
        for role in self.spec["roles"]:
            name = role["name"]
            self.roles = self.client.get(self.base + "/roles")
            existing = self.named("roles", name, self.roles)
            top = max((item["position"] for item in self.roles if item["id"] in self.member["roles"]), default=0)
            payload = {
                "name": name,
                "permissions": str(bits(role.get("permissions", []))),
                "colors": {"primary_color": int(role.get("color", "#000000")[1:], 16)},
                "hoist": role.get("hoist", False),
                "mentionable": False,
            }
            if existing:
                if existing.get("managed"):
                    raise ChannelError(f"{name} is a managed role; give the spec role another name")
                if existing["position"] >= top:
                    if not role_matches(existing, payload):
                        raise ChannelError(
                            f"move the bot's role above {name} in Server Settings > Roles, then rerun apply"
                        )
                    warn(f"{name} sits above the bot's role and already matches the spec; left as it is")
                    above.add(name)
                    result = existing
                else:
                    result = self.client.request("PATCH", self.base + f"/roles/{existing['id']}", payload)
            else:
                if top == 0:
                    raise ChannelError("give the bot its own Administrator role above the setup roles")
                result = self.client.request(
                    "POST",
                    self.base + "/roles",
                    payload,
                    recover=lambda name=name: self.named("roles", name, self.client.get(self.base + "/roles")),
                )
            self.role_ids[name] = result["id"]
            self.state.remember("roles", name, result["id"])
        self.roles = self.client.get(self.base + "/roles")
        top = max((item["position"] for item in self.roles if item["id"] in self.member["roles"]), default=0)
        below = [role for role in self.spec["roles"] if role["name"] not in above]
        if top <= len(below):
            raise ChannelError(
                "move the bot's highest role above all spec roles in Server Settings > Roles, then rerun apply"
            )
        positions = [{"id": self.role_ids[role["name"]], "position": len(below) - i} for i, role in enumerate(below)]
        self.client.request("PATCH", self.base + "/roles", positions)
        self.client.request(
            "PATCH", self.base + f"/roles/{self.gid}", {"permissions": str(bits(self.spec["everyone_permissions"]))}
        )
        for role in self.spec["roles"]:
            if not role.get("assign_to_owner"):
                continue
            owner = self.client.get(self.base + f"/members/{self.guild['owner_id']}")
            if self.role_ids[role["name"]] in owner["roles"]:
                continue
            if role["name"] in above:
                warn(
                    f"the bot cannot give the owner {role['name']} because that role sits above it; "
                    "the owner can add it in Server Settings > Members"
                )
                continue
            self.client.request(
                "PUT", self.base + f"/members/{self.guild['owner_id']}/roles/{self.role_ids[role['name']]}"
            )

    def apply_channel(self, item, category=None):
        name = item["name"]
        ctype = TYPES[item["type"]]
        existing = self.named("channels", name, self.live_channels, ctype)
        payload = {"name": name, "type": ctype, "permission_overwrites": self.overwrites(category or {}, item)}
        if category:
            payload["parent_id"] = self.channel_ids[category["name"]]
        if ctype == 15:
            old_tags = {tag["name"]: tag["id"] for tag in (existing or {}).get("available_tags", [])}
            payload.update(
                {
                    "available_tags": [
                        dict(
                            {"name": tag["name"], "moderated": tag.get("moderated", False)},
                            **({"id": old_tags[tag["name"]]} if tag["name"] in old_tags else {}),
                        )
                        for tag in item.get("tags", [])
                    ],
                    "default_sort_order": item.get("sort_order", 0),
                    "default_forum_layout": 1,
                    "default_auto_archive_duration": 10080,
                }
            )
        if existing:
            # Discord's modify endpoint cannot change arbitrary channel types.
            payload.pop("type")
            result = self.client.request("PATCH", f"/channels/{existing['id']}", payload)
            self.live_channels = [channel for channel in self.live_channels if channel["id"] != result["id"]]
        else:
            result = self.client.request(
                "POST",
                self.base + "/channels",
                payload,
                recover=lambda: self.named("channels", name, self.client.get(self.base + "/channels"), ctype),
            )
        self.live_channels.append(result)
        self.channel_ids[name] = result["id"]
        self.state.remember("channels", name, result["id"])

    def apply_guild(self, initial=False):
        settings = self.spec["guild"]
        payload = {
            key: settings[key]
            for key in (
                "name",
                "description",
                "verification_level",
                "explicit_content_filter",
                "default_message_notifications",
                "preferred_locale",
                "system_channel_flags",
            )
        }
        for field in ("system_channel", "rules_channel", "public_updates_channel", "safety_alerts_channel"):
            if settings[field] in self.channel_ids:
                payload[field + "_id"] = self.channel_ids[settings[field]]
        current = self.client.get(self.base + "?with_counts=true")
        payload["features"] = sorted(set(current.get("features", [])) | {"COMMUNITY"})
        if initial and settings.get("icon"):
            root = self.root.resolve()
            path = (root / settings["icon"]).resolve()
            if not path.is_relative_to(root):
                raise ChannelError("guild icon must be inside the workspace")
            if not path.exists():
                warn(f"icon missing: {path}; skipping icon")
            else:
                raw = path.read_bytes()
                if (
                    len(raw) < 24
                    or raw[:8] != b"\x89PNG\r\n\x1a\n"
                    or raw[12:16] != b"IHDR"
                    or struct.unpack(">II", raw[16:24]) != (1024, 1024)
                ):
                    raise ChannelError(f"{path}: expected a 1024 by 1024 PNG")
                payload["icon"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        self.guild = self.client.request("PATCH", self.base, payload)

    def prune_defaults(self):
        pruning = self.spec["prune_defaults"]
        guild = self.client.get(self.base + "?with_counts=true")
        count = guild.get("approximate_member_count", pruning["max_members"] + 1)
        if count > pruning["max_members"]:
            return
        channels = self.client.get(self.base + "/channels")
        owned = set(self.channel_ids.values())

        def candidates(kind, ctype):
            return [
                channel
                for channel in channels
                if channel["type"] == ctype and channel["name"] in pruning.get(kind, []) and channel["id"] not in owned
            ]

        def only_system(channel):
            try:
                messages = self.client.get(f"/channels/{channel['id']}/messages?limit=50")
            except ChannelError as exc:
                warn(f"keeping default channel {channel['name']}: {exc}")
                return False
            # Types 0 and 19 are ordinary messages and replies; join notices and other system messages don't count.
            return len(messages) < 50 and not any(message.get("type") in (0, 19) for message in messages)

        doomed = [channel for channel in candidates("text", 0) if only_system(channel)]
        voice = candidates("voice", 2)
        if voice and count <= 2:
            # A new server with at most two members holds only the owner and this bot: check both voice states.
            try:
                occupied = set()
                for user_id in {guild["owner_id"], self.me["id"]}:
                    state = self.client.get(self.base + f"/voice-states/{user_id}", missing=True)
                    if state and state.get("channel_id"):
                        occupied.add(state["channel_id"])
                doomed += [channel for channel in voice if channel["id"] not in occupied and only_system(channel)]
            except ChannelError as exc:
                warn(f"keeping default voice channels: occupancy could not be checked ({exc})")
        elif voice:
            warn("keeping default voice channels: more than two members, occupancy unknown")
        for channel in doomed:
            self.client.request("DELETE", f"/channels/{channel['id']}")
        gone = {channel["id"] for channel in doomed}
        channels = [channel for channel in channels if channel["id"] not in gone]
        for category in candidates("categories", 4):
            if not any(channel.get("parent_id") == category["id"] for channel in channels):
                self.client.request("DELETE", f"/channels/{category['id']}")

    def rule_body(self, rule):
        metadata = {
            key: rule[key]
            for key in ("keyword_filter", "presets", "mention_total_limit", "mention_raid_protection_enabled")
            if key in rule
        }
        action = {"type": 1}
        if rule.get("block_message"):
            action["metadata"] = {"custom_message": self.render(rule["block_message"])}
        return {
            "name": rule["name"],
            "event_type": 1,
            "trigger_type": rule["trigger_type"],
            "trigger_metadata": metadata,
            "actions": [action, {"type": 2, "metadata": {"channel_id": self.channel_ids[rule["alert_channel"]]}}],
            "enabled": True,
            "exempt_roles": [self.role_ids[name] for name in rule.get("exempt_roles", [])],
            "exempt_channels": [self.channel_ids[name] for name in ("core", "mod-log")],
        }

    def find_rule(self, rule, rules):
        existing = self.named("rules", rule["name"], rules)
        if not existing and rule["trigger_type"] in (3, 4, 5):
            existing = unique(
                [item for item in rules if item["trigger_type"] == rule["trigger_type"]], "AutoMod trigger"
            )
        if existing and existing["trigger_type"] != rule["trigger_type"]:
            raise ChannelError(f"AutoMod {rule['name']}: existing rule has another trigger; change it manually")
        return existing

    def apply_automod(self):
        path = self.base + "/auto-moderation/rules"
        rules = self.client.get(path)
        for rule in self.spec["automod"]:
            existing = self.find_rule(rule, rules)
            payload = self.rule_body(rule)
            if existing:
                payload.pop("trigger_type")  # Immutable on PATCH.
                try:
                    result = self.client.request("PATCH", path + f"/{existing['id']}", payload)
                except HTTPFail as exc:
                    if exc.status != 404 or existing.get("creator_id") == self.me["id"]:
                        raise
                    # Discord's built-in rules (for example Block Mention Spam) are listed but cannot be edited.
                    warn(f"kept Discord's built-in rule '{existing['name']}' in place of '{rule['name']}'")
                    result = existing
            else:
                result = self.client.request(
                    "POST", path, payload, recover=lambda rule=rule: self.find_rule(rule, self.client.get(path))
                )
            self.state.remember("rules", rule["name"], result["id"])

    def all_messages(self, channel_id):
        before = None
        while True:
            query = "?limit=100" + (f"&before={before}" if before else "")
            page = self.client.get(f"/channels/{channel_id}/messages" + query)
            yield from page
            if len(page) < 100:
                break
            before = page[-1]["id"]

    def find_message(self, channel_id, key, body):
        mid = self.state.data["messages"].get(key)
        if mid:
            message = self.client.get(f"/channels/{channel_id}/messages/{mid}", missing=True)
            if message:
                if message.get("author", {}).get("id") != self.me["id"]:
                    raise ChannelError(f"saved post {key} is not authored by this bot")
                return message
        pending = self.state.data["pending"].get(key, {})
        contents = {body, pending.get("body", body)}
        return unique(
            [
                message
                for message in self.all_messages(channel_id)
                if message.get("author", {}).get("id") == self.me["id"] and message.get("content") in contents
            ],
            f"post {key}",
        )

    def all_threads(self, forum_id, refresh=False):
        if refresh:
            self.thread_cache.pop(forum_id, None)
            self.active_threads = None
        if forum_id not in self.thread_cache:
            if self.active_threads is None:
                self.active_threads = self.client.get(self.base + "/threads/active")["threads"]
            threads = [thread for thread in self.active_threads if thread.get("parent_id") == forum_id]
            before = None
            while True:
                query = "?limit=100" + ("&before=" + urllib.parse.quote(before) if before else "")
                page = self.client.get(f"/channels/{forum_id}/threads/archived/public" + query)
                threads.extend(page["threads"])
                if not page.get("has_more"):
                    break
                if not page["threads"]:
                    raise ChannelError("Discord returned an empty archived-thread page with has_more")
                before = page["threads"][-1]["thread_metadata"]["archive_timestamp"]
            self.thread_cache[forum_id] = {thread["id"]: thread for thread in threads}
        return list(self.thread_cache[forum_id].values())

    def find_thread(self, forum_id, key, title, refresh=False):
        title = self.render(title)
        tid = self.state.data["threads"].get(key)
        if tid:
            thread = self.client.get(f"/channels/{tid}", missing=True)
            if thread:
                if thread.get("parent_id") != forum_id or thread.get("owner_id") != self.me["id"]:
                    raise ChannelError(f"saved thread {key} is outside its spec forum or belongs to someone else")
                return thread
        pending = self.state.data["pending"].get(key, {})
        titles = {title, pending.get("title", title)}
        return unique(
            [
                thread
                for thread in self.all_threads(forum_id, refresh)
                if thread["name"] in titles and thread.get("owner_id") == self.me["id"]
            ],
            f"thread {key}",
        )

    def tag_ids(self, forum_id):
        channel = self.client.get(f"/channels/{forum_id}")
        return {tag["name"]: tag["id"] for tag in channel.get("available_tags", [])}

    def post_thread(self, channel_name, key, title, body, tags=None, pin=False):
        forum_id = self.channel_ids[channel_name]
        title = self.render(title)
        body = self.render(body)
        thread = self.find_thread(forum_id, key, title)
        tag_map = self.tag_ids(forum_id)
        applied = None
        if tags is not None:
            statuses = (
                self.spec["task_default_tags"]
                if thread is None
                else [
                    name
                    for name, tid in tag_map.items()
                    if name in STATUS_TAGS and tid in thread.get("applied_tags", [])
                ]
            )
            applied = [tag_map[name] for name in dict.fromkeys(tags + statuses)]
            if len(applied) > 5:
                raise ChannelError(f"{key}: draft tags plus existing status tags exceed Discord's 5-tag limit")
        if thread is None:
            self.state.remember("pending", key, {"title": title, "body": body})
            payload = {
                "name": title,
                "message": {"content": body, "flags": MESSAGE_FLAGS, "allowed_mentions": {"parse": []}},
                "auto_archive_duration": 10080,
                "applied_tags": applied or [],
            }
            thread = self.client.request(
                "POST",
                f"/channels/{forum_id}/threads",
                payload,
                recover=lambda: self.find_thread(forum_id, key, title, refresh=True),
            )
            self.state.remember("threads", key, thread["id"])
            self.thread_cache.setdefault(forum_id, {})[thread["id"]] = thread
        else:
            self.state.remember("threads", key, thread["id"])
            if thread.get("thread_metadata", {}).get("archived"):
                self.client.request("PATCH", f"/channels/{thread['id']}", {"archived": False})
            self.client.request(
                "PATCH",
                f"/channels/{thread['id']}/messages/{thread['id']}",
                {"content": body, "allowed_mentions": {"parse": []}, "flags": 4},
            )
        payload = {"name": title}
        if applied is not None:
            payload["applied_tags"] = applied
        if pin:
            payload["flags"] = thread.get("flags", 0) | 2
        self.client.request("PATCH", f"/channels/{thread['id']}", payload)
        self.state.data["pending"].pop(key, None)
        self.state.save()

    def apply_posts(self):
        for post in self.spec["posts"]:
            key, name = post["section"], post["channel"]
            body = self.data["sections"][key]
            if self.data["channels"][name]["type"] == "forum":
                self.post_thread(name, key, post["title"], body, pin=post.get("pin", False))
                continue
            channel_id = self.channel_ids[name]
            body = self.render(body)
            message = self.find_message(channel_id, key, body)
            if message is None:
                self.state.remember("pending", key, {"body": body})
                nonce = hashlib.sha256((self.gid + key).encode()).hexdigest()[:24]
                message = self.client.request(
                    "POST",
                    f"/channels/{channel_id}/messages",
                    {
                        "content": body,
                        "flags": MESSAGE_FLAGS,
                        "allowed_mentions": {"parse": []},
                        "nonce": nonce,
                        "enforce_nonce": True,
                    },
                    recover=lambda channel_id=channel_id, key=key, body=body: self.find_message(channel_id, key, body),
                )
                self.state.remember("messages", key, message["id"])
            else:
                self.state.remember("messages", key, message["id"])
                self.client.request(
                    "PATCH",
                    f"/channels/{channel_id}/messages/{message['id']}",
                    {"content": body, "flags": 4, "allowed_mentions": {"parse": []}},
                )
            if post.get("pin"):
                self.client.request("PUT", f"/channels/{channel_id}/messages/pins/{message['id']}")
            self.state.data["pending"].pop(key, None)
            self.state.save()
        for task in self.data["tasks"]:
            self.post_thread(self.spec["task_forum"], task["section"], task["title"], task["body"], task["tags"])

    def onboarding_body(self, existing):
        prompts = []
        counter = 0

        def snowflake():
            nonlocal counter
            counter += 1
            return str(((time.time_ns() // 1_000_000 - 1420070400000) << 22) | counter)

        old_prompts = {prompt["title"]: prompt for prompt in existing.get("prompts", [])}
        for prompt in self.spec["onboarding"]["prompts"]:
            title = self.render(prompt["title"])
            old = old_prompts.get(title, {})
            old_options = {option["title"]: option for option in old.get("options", [])}
            options = []
            for option in prompt["options"]:
                option_title = self.render(option["title"])
                value = {
                    "id": old_options.get(option_title, {}).get("id") or snowflake(),
                    "title": option_title,
                    "description": self.render(option.get("description", "")),
                    "channel_ids": [self.channel_ids[name] for name in option.get("channels", [])],
                    "role_ids": [self.role_ids[name] for name in option.get("roles", [])],
                }
                if option.get("emoji"):
                    value["emoji_name"] = option["emoji"]
                options.append(value)
            prompts.append(
                {
                    "id": old.get("id") or snowflake(),
                    "type": 0,
                    "title": title,
                    "single_select": False,
                    "required": False,
                    "in_onboarding": True,
                    "options": options,
                }
            )
        return {
            "prompts": prompts,
            "default_channel_ids": [self.channel_ids[name] for name in self.spec["onboarding"]["default_channels"]],
            "enabled": True,
            "mode": 0,
        }

    def invite(self, create=True):
        code = self.state.data.get("invite")
        channel_name = self.spec["invite"]["channel"]
        channel_id = self.channel_ids.get(channel_name)
        if not channel_id:
            raise ChannelError(f"#{channel_name} does not exist; run apply first")
        if code:
            invite = self.client.get("/invites/" + urllib.parse.quote(code, safe=""), missing=True)
            if (
                invite
                and invite.get("guild", {}).get("id") == self.gid
                and invite.get("channel", {}).get("id") == channel_id
            ):
                return "https://discord.gg/" + code
        if not create:
            return None

        def recover():
            invites = self.client.get(f"/channels/{channel_id}/invites")
            valid = [
                item
                for item in invites
                if item.get("max_age") == 0 and item.get("max_uses") == 0 and not item.get("temporary")
            ]
            return valid[0] if valid else None

        invite = recover()
        if invite is None:
            invite = self.client.request(
                "POST",
                f"/channels/{channel_id}/invites",
                {"max_age": 0, "max_uses": 0, "unique": False, "temporary": False},
                recover=recover,
            )
        self.state.data["invite"] = invite["code"]
        self.state.save()
        return "https://discord.gg/" + invite["code"]

    def apply(self):
        if not self.admin:
            raise ChannelError("invite the bot with Administrator, or grant that permission before apply")
        self.apply_roles()
        for category in self.spec["categories"]:
            self.apply_channel({**category, "type": "category"})
        for category in self.spec["categories"]:
            for channel in category["channels"]:
                if channel["name"] in ("start-here", "mod-log"):
                    self.apply_channel(channel, category)
        self.apply_guild(initial=True)
        positions = []
        for ci, category in enumerate(self.spec["categories"]):
            positions.append({"id": self.channel_ids[category["name"]], "position": ci})
            for position, channel in enumerate(category["channels"]):
                if channel["name"] not in ("start-here", "mod-log"):
                    self.apply_channel(channel, category)
                # apply_channel already set the parent; Discord rejects bulk parent_id changes.
                positions.append({"id": self.channel_ids[channel["name"]], "position": position})
        self.client.request("PATCH", self.base + "/channels", positions)
        self.apply_guild()
        self.prune_defaults()
        self.apply_automod()
        for name, section in self.spec["forum_guidelines"].items():
            self.client.request(
                "PATCH",
                f"/channels/{self.channel_ids[name]}",
                {"topic": self.render(self.data["sections"][section])},
            )
        for name, topic in self.data["topics"].items():
            self.client.request("PATCH", f"/channels/{self.channel_ids[name]}", {"topic": self.render(topic)})
        self.apply_posts()
        existing = self.client.get(self.base + "/onboarding")
        self.client.request("PUT", self.base + "/onboarding", self.onboarding_body(existing))
        print(self.invite())

    def verify(self):
        failures = 0

        def report(ok, label):
            nonlocal failures
            print(f"{'OK' if ok else 'FAIL'}: {label}")
            failures += not ok

        live = {channel["id"]: channel for channel in self.live_channels}
        for category in self.spec["categories"]:
            cid = self.channel_ids.get(category["name"])
            report(cid in live and live[cid]["type"] == 4, f"category {category['name']}")
            for channel in category["channels"]:
                item = live.get(self.channel_ids.get(channel["name"]), {})
                report(
                    item.get("type") == TYPES[channel["type"]]
                    and item.get("parent_id") == cid
                    and item.get("name") == channel["name"],
                    f"#{channel['name']} type/name/parent",
                )
        everyone = next(role for role in self.roles if role["id"] == self.gid)
        for name in ("leads", "Core", "core", "mod-log"):
            item = live.get(self.channel_ids.get(name))
            effective = int(everyone["permissions"])
            if item:
                for overwrite in item.get("permission_overwrites", []):
                    if overwrite["id"] == self.gid:
                        effective = (effective & ~int(overwrite["deny"])) | int(overwrite["allow"])
            report(
                bool(item)
                and not effective & ((1 << PERMISSIONS["VIEW_CHANNEL"]) | (1 << PERMISSIONS["ADMINISTRATOR"])),
                f"{name} hidden from everyone",
            )
        for name in self.data["roles"]:
            role = next((role for role in self.roles if role["id"] == self.role_ids.get(name)), {})
            report(role.get("name") == name, f"role {name}")
        report("COMMUNITY" in self.guild.get("features", []), "Community enabled")
        report(self.guild.get("verification_level") == 2, "verification level 2")
        report(self.guild.get("explicit_content_filter") == 2, "explicit content filter 2")
        for field in ("rules_channel", "public_updates_channel", "system_channel", "safety_alerts_channel"):
            expected = self.channel_ids.get(self.spec["guild"][field])
            report(bool(expected) and self.guild.get(field + "_id") == expected, field)
        for name, topic in self.data["topics"].items():
            report(live.get(self.channel_ids.get(name), {}).get("topic") == self.render(topic), f"#{name} topic")
        for name, section in self.spec["forum_guidelines"].items():
            report(
                live.get(self.channel_ids.get(name), {}).get("topic") == self.render(self.data["sections"][section]),
                f"#{name} guidelines",
            )
        try:
            onboarding = self.client.get(self.base + "/onboarding")
            report(onboarding.get("enabled") is True and onboarding.get("mode") == 0, "onboarding enabled, mode 0")
            missing_refs = (
                set(self.data["channels"]) - self.channel_ids.keys() or set(self.data["roles"]) - self.role_ids.keys()
            )
            if missing_refs:
                report(False, "onboarding references missing channels/roles")
            else:
                expected = self.onboarding_body(onboarding)
                report(
                    set(onboarding.get("default_channel_ids", [])) == set(expected["default_channel_ids"]),
                    "onboarding default channels",
                )
                actual_prompts = {prompt["title"]: prompt for prompt in onboarding.get("prompts", [])}
                report(
                    set(actual_prompts) == {prompt["title"] for prompt in expected["prompts"]},
                    "onboarding prompt titles",
                )
                for prompt in expected["prompts"]:
                    actual = actual_prompts.get(prompt["title"], {})
                    same = all(
                        actual.get(key) == prompt[key] for key in ("type", "single_select", "required", "in_onboarding")
                    )
                    options = {option["title"]: option for option in actual.get("options", [])}
                    same = same and set(options) == {option["title"] for option in prompt["options"]}
                    for option in prompt["options"]:
                        value = options.get(option["title"], {})
                        same = same and all(
                            set(value.get(key, [])) == set(option[key]) for key in ("channel_ids", "role_ids")
                        )
                        same = same and (value.get("description") or "") == option["description"]
                        same = same and (
                            (value.get("emoji") or {}).get("name") or value.get("emoji_name")
                        ) == option.get("emoji_name")
                    report(same, f"onboarding prompt: {prompt['title']}")
        except ChannelError as exc:
            report(False, f"onboarding: {exc}")
        try:
            rules = self.client.get(self.base + "/auto-moderation/rules")
            for rule in self.spec["automod"]:
                actual = self.find_rule(rule, rules)
                # Discord's own default rule, kept by apply
                builtin = bool(actual) and actual.get("creator_id") != self.me["id"]
                report(
                    bool(actual) and actual.get("enabled") is True and (actual.get("name") == rule["name"] or builtin),
                    f"AutoMod {rule['name']} enabled"
                    + (f" (Discord's built-in '{actual['name']}')" if builtin else ""),
                )
        except ChannelError as exc:
            report(False, f"AutoMod: {exc}")
        posts = [{**post, "body": self.data["sections"][post["section"]]} for post in self.spec["posts"]]
        posts += [{**task, "channel": self.spec["task_forum"]} for task in self.data["tasks"]]
        for post in posts:
            key, name = post["section"], post["channel"]
            cid = self.channel_ids.get(name)
            if not cid:
                report(False, f"post {key}: missing #{name}")
                continue
            try:
                body = self.render(post["body"])
                if self.data["channels"][name]["type"] == "forum":
                    thread = self.find_thread(cid, key, post["title"])
                    message = (
                        self.client.get(f"/channels/{thread['id']}/messages/{thread['id']}", missing=True)
                        if thread
                        else None
                    )
                    ok = (
                        bool(thread)
                        and thread["name"] == self.render(post["title"])
                        and bool(message)
                        and message.get("content") == body
                    )
                    if post.get("pin"):
                        ok = ok and bool(thread.get("flags", 0) & 2)
                    if "tags" in post and thread:
                        tag_map = self.tag_ids(cid)
                        expected = {tag_map[tag] for tag in post["tags"] if tag in tag_map}
                        nonstatus = set(thread.get("applied_tags", [])) - {
                            tid for tag, tid in tag_map.items() if tag in STATUS_TAGS
                        }
                        ok = ok and len(expected) == len(post["tags"]) and expected == nonstatus
                else:
                    message = self.find_message(cid, key, body)
                    ok = (
                        bool(message)
                        and message.get("content") == body
                        and (not post.get("pin") or message.get("pinned"))
                    )
                report(ok, f"post {key}")
            except ChannelError as exc:
                report(False, f"post {key}: {exc}")
        try:
            report(bool(self.invite(create=False)), "stored invite is valid for this server/channel")
        except ChannelError as exc:
            report(False, f"invite: {exc}")
        return 1 if failures else 0


def snowflake_time(snowflake):
    """Creation time of a Discord ID, as local 'YYYY-MM-DD HH:MM'."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(((int(snowflake) >> 22) + 1420070400000) / 1000))


def show_members(client, guild, roles):
    names = {role["id"]: role["name"] for role in roles}
    members, after = [], "0"
    while True:
        try:
            page = client.get(f"/guilds/{guild['id']}/members?limit=1000&after={after}")
        except HTTPFail as exc:
            if exc.status == 403:
                raise ChannelError(
                    "Discord refused the member list: switch on Server Members Intent "
                    "(Developer Portal > unfloat > Bot)"
                ) from exc
            raise
        members.extend(page)
        if len(page) < 1000:
            break
        after = page[-1]["user"]["id"]
    people = sorted((m for m in members if not m["user"].get("bot")), key=lambda m: m.get("joined_at") or "")
    print(f"People: {len(people)} (bots left out)")
    for m in people:
        user = m["user"]
        shown = m.get("nick") or user.get("global_name") or user["username"]
        role_names = ", ".join(sorted(names.get(r, r) for r in m.get("roles", []))) or "no roles"
        joined = (m.get("joined_at") or "")[:16].replace("T", " ")
        # Member flags: 1<<1 completed onboarding, 1<<3 started it. Discord does not expose the answers,
        # only the roles the chosen options grant.
        flags = m.get("flags", 0)
        onboarding = "onboarding done" if flags & 2 else "onboarding started" if flags & 8 else "no onboarding"
        print(f"- {shown} (@{user['username']}), joined {joined} UTC, {onboarding}: {role_names}")
    return 0


def everyone_can_view(channel, guild_id, everyone_perms, by_id):
    """Whether @everyone can see a channel, after category and channel overwrites (threads use their parent)."""
    if channel.get("type") in (10, 11, 12):
        channel = by_id.get(channel.get("parent_id"), channel)
    allowed = everyone_perms
    for source in (by_id.get(channel.get("parent_id")) or {}, channel):
        for overwrite in source.get("permission_overwrites", []):
            if overwrite["id"] == guild_id:
                allowed = (allowed & ~int(overwrite["deny"])) | int(overwrite["allow"])
    return bool(allowed & (1 << PERMISSIONS["VIEW_CHANNEL"]))


def read_messages(client, me, guild, roles, days, names):
    guild_id = guild["id"]
    everyone = int(next(role for role in roles if role["id"] == guild_id)["permissions"])
    channels = client.get(f"/guilds/{guild_id}/channels")
    by_id = {channel["id"]: channel for channel in channels}
    wanted = set(names or [])
    unknown = wanted - {channel["name"] for channel in channels}
    if unknown:
        raise ChannelError(f"no channel named {', '.join(sorted(unknown))}")
    threads = client.get(f"/guilds/{guild_id}/threads/active").get("threads", [])
    for forum in (c for c in channels if c["type"] == TYPES["forum"]):
        threads += client.get(f"/channels/{forum['id']}/threads/archived/public?limit=100").get("threads", [])
    since = time.time() - days * 86400
    sources = []
    for item in [c for c in channels if c["type"] in (TYPES["text"], TYPES["announcement"])] + threads:
        home = by_id.get(item.get("parent_id"), {}) if item.get("type") in (10, 11, 12) else item
        label = f"#{home.get('name', '?')} > {item['name']}" if home is not item else f"#{item['name']}"
        if not everyone_can_view(item, guild_id, everyone, by_id):
            continue
        if wanted and home.get("name") not in wanted:
            continue
        if not wanted and home.get("name") == "living-with-floaters":
            continue
        last = item.get("last_message_id")
        if last and ((int(last) >> 22) + 1420070400000) / 1000 < since:
            continue
        sources.append((label, item))
    blank = shown = 0
    for label, item in sorted(sources, key=lambda pair: pair[0]):
        messages = client.get(f"/channels/{item['id']}/messages?limit=100")
        mine = [
            m
            for m in reversed(messages)
            if m["author"]["id"] != me["id"]
            and not m["author"].get("bot")
            and ((int(m["id"]) >> 22) + 1420070400000) / 1000 >= since
        ]
        if not mine:
            continue
        print(f"== {label}")
        for m in mine:
            author = m["author"].get("global_name") or m["author"]["username"]
            when = snowflake_time(m["id"])
            if m.get("type") == 7:  # Discord's automatic "X joined" notice
                print(f"   {when} {author} (@{m['author']['username']}) joined the server")
                continue
            if m.get("type") not in (0, 19, 21):  # other system notices (pins, boosts, thread created)
                continue
            text = m.get("content") or ""
            if not text and not m.get("attachments") and not m.get("embeds") and not m.get("sticker_items"):
                blank += 1
            extra = f" [{len(m['attachments'])} attachment(s)]" if m.get("attachments") else ""
            reply = " (reply)" if m.get("type") == 19 else ""
            print(f"   {when} {author} (@{m['author']['username']}){reply}: {text}{extra}")
            shown += 1
    if not shown:
        print(f"no messages from people in the last {days} days")
    if blank:
        warn(
            f"{blank} message(s) came back without text: switch on Message Content Intent "
            "(Developer Portal > unfloat > Bot)"
        )
    return 0


def _guarded(handler):
    """Report the failures the old standalone script reported, as one-line errors, and exit 130 on Ctrl-C."""

    @functools.wraps(handler)
    def run(args, ws):
        try:
            return handler(args, ws)
        except (OSError, ValueError) as exc:
            raise ChannelError(str(exc)) from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise ChannelError(f"invalid spec, saved state or API response near {exc}") from exc
        except KeyboardInterrupt:
            print("\nInterrupted. Saved state is intact; rerun to resume.", file=sys.stderr)
            return 130

    return run


@_guarded
def run(args, ws):
    cfg = DiscordConfig.from_workspace(ws)
    command = args.discord_command
    spec_path = getattr(args, "spec", None) or cfg.spec
    data = None
    if command == "invite":
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        data = {"spec": spec, "roles": {role["name"]: role for role in spec["roles"]}}
        names = {channel["name"] for category in spec["categories"] for channel in category["channels"]}
        if spec["invite"]["channel"] not in names:
            raise ChannelError("invite: channel is not in the spec")
    elif command not in ("check", "members", "read"):
        data = load_inputs(cfg, spec_path, args.draft)
    if command == "plan":
        plan(data)
        return 0
    client = Client(token(cfg.conf_dir), readonly=command in ("check", "verify", "members", "read"))
    identity = connect(client, args.guild)
    if command == "check":
        if not identity[4]:
            raise ChannelError("bot needs Administrator before apply; grant it in Server Settings > Roles")
        return 0
    if command == "members":
        return show_members(client, identity[1], identity[2])
    if command == "read":
        return read_messages(client, identity[0], identity[1], identity[2], args.days, args.channel)
    with state_lock(cfg.conf_dir) if command in ("apply", "invite") else contextlib.nullcontext():
        state = State(cfg.conf_dir, identity[1]["id"], readonly=command == "verify")
        server = Server(client, data, identity, state, cfg.root)
        if command == "apply":
            server.apply()
            return 0
        if command == "invite":
            print(server.invite())
            return 0
        return server.verify()


def register(sub: argparse._SubParsersAction) -> None:
    """Add `discord` with its subcommands to the top-level parser."""
    discord = sub.add_parser(
        "discord",
        help="Discord server setup and reading",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = discord.add_subparsers(dest="discord_command", required=True)
    for name in ("plan", "check", "apply", "verify", "invite", "members", "read"):
        command = commands.add_parser(name)
        if name not in ("check", "members", "read"):
            command.add_argument("--spec", type=Path)
        if name in ("plan", "apply", "verify"):
            command.add_argument("--draft", type=Path)
        if name != "plan":
            command.add_argument("--guild")
        if name == "read":
            command.add_argument("--days", type=int, default=7, help="look back this many days (default 7)")
            command.add_argument(
                "--channel",
                action="append",
                metavar="NAME",
                help="only this channel (repeatable); needed for #living-with-floaters",
            )
        command.set_defaults(handler=run)
