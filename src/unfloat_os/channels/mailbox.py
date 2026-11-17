"""The part of Daniil's Gmail the records care about, kept in a local cache, and what of it the records lack.

Scope (D-025): threads with anyone in the records (people's `email` field, and the To and Cc lines
of email drafts), plus threads carrying the subject of a recently sent email draft (auto-replies
from other addresses). Nothing else is listed or read.

Cost: Gmail charges quota units per call and allows about 15,000 per user per minute. A refresh
lists the matching threads (10 units a page, each with its current historyId) and downloads only
the threads whose historyId changed since the cached copy (10 units each), so a check with nothing
new costs a few tens of units instead of one download per thread.

The cache lives outside every repository, in ~/.cache/unfloat/gmail/<account>/ (mode 700, files 600),
or under $UNFLOAT_GMAIL_CACHE. It is a copy, safe to delete; the next refresh downloads again.

A message counts as addressed when the records hold it:
- a log event names its Gmail ID (`[gmail ID]` at the end of the summary; `uo gmail done` writes it),
- or, for Daniil's own sent mail, an email draft carries it as `gmail_id` (`uo gmail sync` writes it),
- or, for mail older than `untagged_until` in config/channels.toml (when the records began tagging
  Gmail IDs), an untagged event of the same direction (`email-in` for received mail, `email-out` for
  sent mail) on or after the message's day names one of the people it came from or went to. Newer
  mail needs the tag: an untagged event cannot tell a reply that came later the same day from one
  it describes, so it never hides one.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr
from pathlib import Path

from unfloat_os import credentials

API = "https://gmail.googleapis.com/gmail/v1/users/me"
CACHE_ENV = "UNFLOAT_GMAIL_CACHE"
# Addresses per search: Gmail accepts long queries, but short ones keep a failure easy to read.
QUERY_CHUNK = 15
# A log summary ends with "[gmail ID]" or "[gmail ID, ID]" when it files those messages.
TAG_RE = re.compile(r"\[gmail ([0-9a-f]+(?:,\s*[0-9a-f]+)*)\]\s*$")
AUTO_SUBJECT_RE = re.compile(
    r"^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office|abwesenheit|undeliverable|"
    r"delivery status notification|mail delivery (failed|subsystem))",
    re.IGNORECASE,
)

Call = Callable[..., dict]


def header(message: dict, name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


def addresses_in(value: str) -> list[str]:
    return [address.lower() for _, address in getaddresses([value]) if "@" in address]


def local_date(message: dict) -> dt.date:
    return dt.datetime.fromtimestamp(int(message["internalDate"]) / 1000).date()


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class Cache:
    """Threads as Gmail returned them (format=full), one JSON file each, plus when the last refresh ran."""

    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def for_account(cls, account: str) -> Cache:
        base = (
            Path(os.environ[CACHE_ENV]) if os.environ.get(CACHE_ENV) else Path.home() / ".cache" / "unfloat" / "gmail"
        )
        return cls(base / account.lower())

    @property
    def threads_dir(self) -> Path:
        return self.root / "threads"

    def _ensure(self) -> None:
        for path in (self.root, self.threads_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)

    def load(self, thread_id: str) -> dict | None:
        path = self.threads_dir / f"{thread_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def save(self, thread: dict) -> None:
        self._ensure()
        credentials.write_private(self.threads_dir / f"{thread['id']}.json", json.dumps(thread))

    def threads(self) -> list[dict]:
        if not self.threads_dir.is_dir():
            return []
        found = []
        for path in sorted(self.threads_dir.glob("*.json")):
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue  # a half-written file; the next refresh replaces it
        return found

    def state(self) -> dict:
        try:
            return json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def mark_checked(self, days: int, listed: int, fetched: int) -> None:
        self._ensure()
        state = {"checked": dt.datetime.now().isoformat(timespec="minutes"), "days": days}
        state.update({"listed": listed, "fetched": fetched})
        credentials.write_private(self.root / "state.json", json.dumps(state))


@dataclass
class Book:
    """Who the records know: every address in scope, and the people each one belongs to."""

    me: str
    people: dict[str, set[str]] = field(default_factory=dict)  # address -> person IDs
    addresses: set[str] = field(default_factory=set)

    def add(self, address: str, person: str | None = None) -> None:
        address = address.strip().lower()
        if not address or "@" not in address or address == self.me.lower():
            return
        self.addresses.add(address)
        if person:
            self.people.setdefault(address, set()).add(person)

    def who(self, addresses: Iterable[str]) -> set[str]:
        return {pid for address in addresses for pid in self.people.get(address.lower(), ())}


def build_book(me: str, people: dict[str, list[str]], drafts: Iterable[dict]) -> Book:
    """`people` maps a person ID to its `email` list; drafts are parsed email drafts (their To and Cc count)."""
    book = Book(me)
    for pid, addresses in people.items():
        for address in addresses:
            book.add(str(address), pid)
    for draft in drafts:
        for address in [*draft["to"], *draft["cc"]]:
            book.add(address)
    return book


def refresh(call: Call, token: str, cache: Cache, book: Book, subjects: list[str], days: int) -> tuple[int, int]:
    """Bring the cache up to date for threads in scope from the last `days` days: (threads listed, downloaded)."""
    queries = []
    for group in chunks(sorted(book.addresses), QUERY_CHUNK):
        terms = " OR ".join(group)
        queries.append(f"newer_than:{days}d (from:({terms}) OR to:({terms}) OR cc:({terms}))")
    for group in chunks(sorted(set(subjects)), QUERY_CHUNK):
        quoted = " OR ".join(f'subject:"{s.replace(chr(34), "")}"' for s in group)
        queries.append(f"newer_than:{days}d ({quoted})")
    listed: dict[str, str] = {}
    for query in queries:
        page = None
        while True:
            params = {"q": query, "maxResults": 100, **({"pageToken": page} if page else {})}
            answer = call("GET", f"{API}/threads?" + urllib.parse.urlencode(params), token=token)
            for item in answer.get("threads", []):
                listed[item["id"]] = item.get("historyId", "")
            page = answer.get("nextPageToken")
            if not page:
                break
    fetched = 0
    for thread_id, history in listed.items():
        cached = cache.load(thread_id)
        if cached and history and cached.get("historyId") == history:
            continue
        thread = call("GET", f"{API}/threads/{thread_id}?format=full", token=token)
        cache.save(
            {"id": thread_id, "historyId": thread.get("historyId", history), "messages": thread.get("messages", [])}
        )
        fetched += 1
    cache.mark_checked(days, len(listed), fetched)
    return len(listed), fetched


# what a message is -----------------------------------------------------------------------------


@dataclass
class Mail:
    """One message of a cached thread, with what the records need to know about it."""

    message: dict
    thread: str
    mine: bool
    sender: str
    recipients: list[str]
    people: set[str]  # the other side: senders of received mail, recipients of sent mail
    date: dt.date

    @property
    def id(self) -> str:
        return self.message["id"]

    @property
    def stamp(self) -> int:
        return int(self.message["internalDate"])

    @property
    def subject(self) -> str:
        return header(self.message, "Subject")


def mails(cache: Cache, book: Book, days: int | None = None, today: dt.date | None = None) -> list[Mail]:
    """Sent and received messages of the cached threads (Gmail drafts left out), oldest first."""
    since = (today or dt.date.today()) - dt.timedelta(days=days) if days else None
    found = []
    for thread in cache.threads():
        in_thread = []
        for message in thread.get("messages", []):
            labels = message.get("labelIds", [])
            if "DRAFT" in labels or "internalDate" not in message:
                continue
            sender = parseaddr(header(message, "From"))[1].lower()
            recipients = addresses_in(", ".join(header(message, h) for h in ("To", "Cc")))
            mine = sender == book.me.lower()
            people = book.who(recipients if mine else [sender])
            in_thread.append(Mail(message, thread["id"], mine, sender, recipients, people, local_date(message)))
        # A bounce or an auto-reply from an address nobody knows belongs to the people of its thread.
        everyone = {pid for mail in in_thread for pid in mail.people}
        for mail in in_thread:
            if not mail.people:
                mail.people = set(everyone)
            if since is None or mail.date >= since:
                found.append(mail)
    return sorted(found, key=lambda m: m.stamp)


def kind_of(mail: Mail) -> str:
    """'bounce', 'auto-reply', 'invite' or '' for an ordinary message."""
    payload = mail.message.get("payload", {})
    types = set()

    def walk(part: dict) -> None:
        types.add(part.get("mimeType", ""))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    local = mail.sender.split("@")[0]
    if local in ("mailer-daemon", "postmaster") or "message/delivery-status" in types:
        return "bounce"
    auto = header(mail.message, "Auto-Submitted").lower()
    if (auto and auto != "no") or header(mail.message, "X-Autoreply") or AUTO_SUBJECT_RE.match(mail.subject):
        return "auto-reply"
    if "text/calendar" in types:
        return "invite"
    return ""


@dataclass
class Logged:
    """What the log and the drafts say about Gmail messages."""

    tagged: set[str]  # message IDs named in log summaries
    claimed: set[str]  # gmail_id of email drafts
    untagged: list[tuple[dt.date, str, frozenset[str]]]  # (date, type, refs) of events without a Gmail tag
    until: int | None = None  # untagged events file only mail older than this (ms since the epoch); None: none


def parse_until(value: str | None) -> int | None:
    """`untagged_until` from the config ("YYYY-MM-DDTHH:MM", local time) as ms since the epoch."""
    if not value:
        return None
    return int(dt.datetime.fromisoformat(value).timestamp() * 1000)


def logged_from(events: Iterable, draft_gmail_ids: Iterable[str], until: int | None = None) -> Logged:
    tagged: set[str] = set()
    untagged = []
    for event in events:
        match = TAG_RE.search(event.summary)
        if match:
            tagged.update(part.strip() for part in match.group(1).split(","))
        else:
            try:
                day = dt.date.fromisoformat(event.date)
            except ValueError:
                continue
            untagged.append((day, event.type, frozenset(event.refs)))
    return Logged(tagged, {gid for gid in draft_gmail_ids if gid}, untagged, until)


def addressed(mail: Mail, logged: Logged) -> bool:
    if mail.id in logged.tagged or (mail.mine and mail.id in logged.claimed):
        return True
    if logged.until is None or mail.stamp >= logged.until:
        return False
    direction = "email-out" if mail.mine else "email-in"
    return any(kind == direction and day >= mail.date and refs & mail.people for day, kind, refs in logged.untagged)


def open_mail(all_mail: list[Mail], logged: Logged) -> list[Mail]:
    return [mail for mail in all_mail if not addressed(mail, logged)]


def replied_after(mail: Mail, all_mail: list[Mail]) -> Mail | None:
    """Daniil's first message in the same thread after a received one."""
    return next((m for m in all_mail if m.mine and m.thread == mail.thread and m.stamp > mail.stamp), None)
