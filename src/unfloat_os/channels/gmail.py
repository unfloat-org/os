"""Daniil's Gmail for outreach (D-016 to D-018, D-025): drafts from records/drafts, and the mail around them.

Scopes: gmail.compose (drafts) and gmail.readonly (reading). The adapter never
sends, deletes or changes mail, and reading does not mark anything as read.
Reading is limited to threads with people in the records (their `email` field and
the To and Cc lines of email drafts) and threads carrying the subject of a recently
sent email draft; there is no free-text search. Threads are kept in a local cache
and only threads that changed are downloaded again (unfloat_os.channels.mailbox).
A thread listed under [gmail.blind] in config/channels.toml is shown without its
text or attachments (for example while a pre-registration is not yet committed).

  uo gmail inbox [--days N] [--offline]     # mail in scope that the records do not hold yet
  uo gmail done MSG[,MSG] REFS "summary"    # file messages from the inbox as one log event
  uo gmail show DR-###                      # print the parsed email; no network
  uo gmail auth [--client FILE]             # Google consent in the browser
  uo gmail push DR-### DR-###               # create Gmail drafts; existing ones are left alone
  uo gmail push --update DR-###             # overwrite the Gmail draft with the same subject; never creates one
  uo gmail replies [--days N] [DR-### ...]  # replies, auto-replies and bounces for sent drafts
  uo gmail attachments [--days N] [--out DIR] [DR-### ...]  # save files attached to those replies
  uo gmail sync [DR-### ...]                # copy the text Gmail actually sent back into email drafts;
                                            # a draft found in the sent mail is marked sent

A draft file must have `channel: email`, a first body line `Subject: ...`, then
the message, then one `To:` line with the addresses, optionally a `Cc:` line
right after it (notes may follow; the format lives in unfloat_os.drafts and `uo check` enforces it).
Frontmatter `attachments: [path, ...]` attaches files from the private data folder.
A draft with `reply_to: DR-###` in its
frontmatter is pushed as a reply inside that sent email's thread, answering the
latest message there from someone else; when nobody has answered yet it follows
up on the sent email itself (a reminder). A draft with `reply_thread: <Gmail thread
id>` instead replies inside that thread (for a thread someone else started, used
only when Daniil asks to answer it). Drafts carry an HTML part, so Gmail
opens and sends them as rich text instead of hard-wrapped plain text.
The Gmail account is set in config/channels.toml of the records repository.
Credentials stay outside every repository in ~/.config/unfloat/gmail/:
client_secret.json (Desktop OAuth client) and token.json (written by auth).
Attachments are saved only from received messages in those same threads, by
default into papers/ of the workspace's private data folder (untracked), and an
existing file is never overwritten.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import difflib
import hashlib
import html
import http.server
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import time
import tomllib
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from pathlib import Path

from unfloat_os import credentials, frontmatter, schema
from unfloat_os.channels import ChannelError, mailbox
from unfloat_os.channels.mailbox import header
from unfloat_os.drafts import DraftError, attachment_problems, forbidden_marks
from unfloat_os.drafts import read_email as parse_email
from unfloat_os.http import HttpError, send
from unfloat_os.records import Records, Store
from unfloat_os.workspace import Workspace

COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = (COMPOSE, READONLY)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
CONSENT_WAIT_S = 300
BODY_CHARS = 1500
# Where quoted earlier messages start in a reply body.
QUOTE_RE = re.compile(r"^(>|On .+ wrote:\s*$|Am .+ schrieb .+:\s*$|-+ ?Original Message ?-+|From: )", re.IGNORECASE)
# Gmail hard-wraps the plain-text mail it sends at 70 characters (observed 2026-11-04).
WRAP_WIDTH = 70
LIST_RE = re.compile(r"^\s*(\d+[.)]|[-*\u2022])\s")


@dataclass(frozen=True)
class GmailConfig:
    """Where the drafts live, where files are saved, where credentials are, and which account is Daniil's."""

    store: Store
    papers: Path
    conf_dir: Path
    account: str
    blind: dict[str, str] = field(default_factory=dict)  # Gmail thread ID -> why its text is withheld
    untagged_until: str = ""  # mail older than this may be filed by log events without a Gmail tag

    @property
    def drafts(self) -> Path:
        return self.store.folder(schema.DRAFT)

    @property
    def cache(self) -> mailbox.Cache:
        return mailbox.Cache.for_account(self.account)

    @property
    def private(self) -> Path:
        """The private data folder; email attachments are named relative to it."""
        return self.papers.parent

    def is_me(self, address: str) -> bool:
        return address.lower() == self.account.lower()

    @classmethod
    def from_workspace(cls, ws: Workspace) -> GmailConfig:
        path = ws.ops / "config" / "channels.toml"
        try:
            section = tomllib.loads(path.read_text(encoding="utf-8")).get("gmail", {})
        except FileNotFoundError as exc:
            raise ChannelError(f"{path} not found") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ChannelError(f"{path}: {exc}") from exc
        account = section.get("account")
        if not isinstance(account, str) or not account.strip():
            raise ChannelError(f'{path}: [gmail] needs account = "..."')
        blind = section.get("blind", {})
        if not isinstance(blind, dict) or not all(isinstance(v, str) for v in blind.values()):
            raise ChannelError(f'{path}: [gmail.blind] maps a thread ID to a reason: "<id>" = "why"')
        until = str(section.get("untagged_until", ""))
        try:
            mailbox.parse_until(until)
        except ValueError as exc:
            raise ChannelError(f'{path}: [gmail] untagged_until must look like "2026-11-17T21:30"') from exc
        return cls(
            Store(ws.ops),
            ws.data_dir("private") / "papers",
            credentials.channel_dir("gmail"),
            account.strip(),
            dict(blind),
            until,
        )


def draft_path(cfg: GmailConfig, draft_id: str) -> Path:
    return cfg.store.path_of(draft_id)


def read_email(path: Path) -> dict:
    """The parsed email draft file; the format is defined in `unfloat_os.drafts`."""
    try:
        return parse_email(path)
    except DraftError as exc:
        raise ChannelError(str(exc)) from exc


def load_draft(cfg: GmailConfig, draft_id: str) -> dict:
    draft = read_email(draft_path(cfg, draft_id))
    if draft["status"] in ("sent", "dropped"):
        raise ChannelError(f"{draft['file']}: status is {draft['status']!r}")
    refusal = forbidden_marks(draft)
    if refusal:
        raise ChannelError(f"{draft['file']}: {refusal}")
    draft["files"] = attachment_files(cfg, draft)
    return draft


# Gmail refuses messages over 25 MB; base64 adds a third, so the files themselves must stay well below.
ATTACH_LIMIT = 18 * 1024 * 1024


def attachment_files(cfg: GmailConfig, draft: dict) -> list[Path]:
    """The draft's attachments as existing files inside the private data folder, or a ChannelError."""
    problems = attachment_problems(draft)
    if problems:
        raise ChannelError(f"{draft['file']}: {'; '.join(problems)}")
    root = cfg.private.resolve()
    files = []
    for name in draft["attachments"]:
        path = (root / str(name)).resolve()
        if not path.is_relative_to(root):
            raise ChannelError(f"{draft['file']}: attachment {name!r} is outside {root}")
        if not path.is_file():
            raise ChannelError(f"{draft['file']}: attachment {name!r} not found in {root}")
        files.append(path)
    if sum(f.stat().st_size for f in files) > ATTACH_LIMIT:
        raise ChannelError(f"{draft['file']}: attachments exceed {ATTACH_LIMIT // (1024 * 1024)} MB")
    return files


def html_body(text: str) -> str:
    """The message as Gmail-style HTML: one div per line, an empty div with a break between paragraphs."""
    rows = [
        f"<div>{html.escape(line)}</div>" if line.strip() else "<div><br></div>" for line in text.strip().split("\n")
    ]
    return '<div dir="ltr">' + "".join(rows) + "</div>"


def raw_message(draft: dict, target: dict | None = None) -> str:
    msg = EmailMessage()
    msg["To"] = ", ".join(draft["to"])
    if draft["cc"]:
        msg["Cc"] = ", ".join(draft["cc"])
    msg["Subject"] = draft["subject"]
    if target:
        msg["In-Reply-To"] = target["in_reply_to"]
        msg["References"] = target["references"]
    msg.set_content(draft["body"])
    msg.add_alternative(html_body(draft["body"]), subtype="html")
    for path in draft.get("files", []):
        kind, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (kind or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


# Gmail's per-user quota is counted per minute, so the waits add up to a little over a minute.
RETRY_WAITS_S = (2, 5, 10, 20, 40)
RATE_LIMIT_MARKS = ("rateLimitExceeded", "userRateLimitExceeded", "Quota exceeded", "RESOURCE_EXHAUSTED")


def rate_limited(exc: HttpError) -> bool:
    return exc.status == 429 or (exc.status == 403 and any(mark in exc.detail for mark in RATE_LIMIT_MARKS))


def call(method: str, url: str, data: dict | None = None, token: str | None = None, form: bool = False) -> dict:
    """One API request; a rate limit or a server error is retried after a pause, others raise at once."""
    for wait in (*RETRY_WAITS_S, None):
        try:
            return send(method, url, data=data, form=form, bearer=token)
        except HttpError as exc:
            retry = rate_limited(exc) or exc.status >= 500
            if retry and wait is not None:
                why = "Gmail rate limit" if rate_limited(exc) else f"Gmail HTTP {exc.status}"
                print(f"{why}; retrying in {wait} s", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            if rate_limited(exc):
                raise ChannelError(
                    "Gmail's per-minute quota is still used up after waiting about a minute; try again shortly"
                ) from exc
            raise ChannelError(str(exc)) from exc
    raise AssertionError("unreachable")


def client(cfg: GmailConfig) -> tuple[str, str]:
    path = cfg.conf_dir / "client_secret.json"
    if not path.exists():
        raise ChannelError(
            f"missing {path}: run `auth --client <downloaded client_secret JSON>` (see records/tasks/T-034)"
        )
    installed = json.loads(path.read_text()).get("installed")
    if not installed:
        raise ChannelError(f"{path} is not a Desktop app OAuth client (no 'installed' key)")
    return installed["client_id"], installed["client_secret"]


def cmd_auth(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    conf = credentials.channel_dir("gmail", create=True)
    if args.client:
        shutil.copyfile(args.client, conf / "client_secret.json")
        os.chmod(conf / "client_secret.json", 0o600)
    client_id, client_secret = client(cfg)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    answer: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in query and "error" not in query:
                self.send_response(404)
                self.end_headers()
                return
            answer.update({key: values[0] for key, values in query.items()})
            ok = answer.get("state") == state and "code" in answer
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Done: Gmail access granted. You can close this tab."
                if ok
                else b"Authorisation failed; see the terminal."
            )

        def log_message(self, *_):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 2
    redirect = f"http://127.0.0.1:{server.server_port}/"
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
                "login_hint": cfg.account,
            }
        )
    )
    print(f"Opening the Google consent page (waiting up to {CONSENT_WAIT_S // 60} min):\n{url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    deadline = time.monotonic() + CONSENT_WAIT_S
    try:
        while not answer and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not answer:
        raise ChannelError("no answer from the consent page in time")
    if answer.get("state") != state:
        raise ChannelError("state mismatch; response ignored")
    if "error" in answer:
        raise ChannelError(f"consent not given: {answer['error']}")
    token = call(
        "POST",
        TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": answer["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
        },
        form=True,
    )
    granted = set(token.get("scope", "").split())
    if granted != set(SCOPES):
        raise ChannelError(f"granted scopes {sorted(granted)}, expected exactly {list(SCOPES)}; nothing saved")
    if "refresh_token" not in token:
        raise ChannelError("Google returned no refresh token; nothing saved")
    credentials.write_private(
        conf / "token.json", json.dumps({"refresh_token": token["refresh_token"], "scope": " ".join(SCOPES)})
    )
    print(f"Saved {conf / 'token.json'} (gmail.compose and gmail.readonly).")
    return 0


def access_token(cfg: GmailConfig, read: bool = False) -> str:
    path = cfg.conf_dir / "token.json"
    if not path.exists():
        raise ChannelError("not authorised yet: run `uo gmail auth`")
    client_id, client_secret = client(cfg)
    saved = json.loads(credentials.read_secret(path))
    if read and READONLY not in saved.get("scope", "").split():
        raise ChannelError("the saved login covers drafts only: run `uo gmail auth` to add read access")
    refresh = saved["refresh_token"]
    try:
        token = call(
            "POST",
            TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            form=True,
        )
    except ChannelError as exc:
        if "invalid_grant" in str(exc):
            raise ChannelError(
                "Google rejected the saved login (expired or revoked; apps in Testing mode "
                "expire after 7 days): run `uo gmail auth` again"
            ) from exc
        raise
    return token["access_token"]


def drafts_with_subject(token: str, subject: str) -> list[str]:
    query = urllib.parse.urlencode({"q": f'subject:"{subject}"', "maxResults": 20})
    found = []
    for item in call("GET", f"{API}/drafts?{query}", token=token).get("drafts", []):
        draft = call("GET", f"{API}/drafts/{item['id']}?format=metadata", token=token)
        headers = draft.get("message", {}).get("payload", {}).get("headers", [])
        if any(h["name"].lower() == "subject" and h["value"] == subject for h in headers):
            found.append(item["id"])
    return found


def thread_target(cfg: GmailConfig, token: str, thread_id: str, label: str, follow_up: bool = False) -> dict:
    """The latest message from someone else in a Gmail thread, as In-Reply-To/References for a reply.

    With `follow_up`, a thread nobody else has written in is answered at Daniil's own latest
    sent message; Gmail drafts sitting in the thread are never the target."""
    thread = call("GET", f"{API}/threads/{thread_id}?format=metadata", token=token)
    messages = [m for m in thread.get("messages", []) if "DRAFT" not in m.get("labelIds", [])]
    theirs = [m for m in messages if not cfg.is_me(parseaddr(header(m, "From"))[1])]
    if not theirs and not (follow_up and messages):
        raise ChannelError(f"{label}: nobody else has written in that thread")
    last = max(theirs or messages, key=lambda m: int(m["internalDate"]))
    message_id = header(last, "Message-ID")
    references = " ".join(part for part in (header(last, "References"), message_id) if part)
    return {"thread": thread_id, "in_reply_to": message_id, "references": references, "follow_up": not theirs}


def cmd_push(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    drafts = [load_draft(cfg, draft_id) for draft_id in args.ids]
    token = access_token(cfg, read=any(draft["reply_to"] or draft["reply_thread"] for draft in drafts))
    problems = 0
    for draft in drafts:
        existing = drafts_with_subject(token, draft["subject"])
        if draft["reply_to"]:
            target = reply_target(cfg, token, read_email(draft_path(cfg, draft["reply_to"])))
        elif draft["reply_thread"]:
            target = thread_target(cfg, token, draft["reply_thread"], draft["id"])
        else:
            target = None
        message = {"raw": raw_message(draft, target)}
        if target:
            message["threadId"] = target["thread"]
        payload = {"message": message}
        if len(existing) > 1:
            print(f"{draft['id']}: {len(existing)} Gmail drafts have this subject; none touched")
            problems += 1
        elif existing and not args.update:
            print(f"{draft['id']}: already in Gmail drafts ({existing[0]}); left unchanged (--update overwrites)")
        elif args.update and not existing:
            print(f"{draft['id']}: no Gmail draft with this subject (sent or deleted?); nothing created")
            problems += 1
        else:
            if existing:
                result = call("PUT", f"{API}/drafts/{existing[0]}", {"id": existing[0], **payload}, token)
                verb = "updated"
            else:
                result = call("POST", f"{API}/drafts", payload, token)
                verb = "created"
            kind = "follow-up, nobody has answered yet," if target and target["follow_up"] else "reply"
            where = f" ({kind} in the {draft['reply_to'] or 'given'} thread)" if target else ""
            files = f" with {', '.join(f.name for f in draft['files'])}" if draft["files"] else ""
            print(
                f"{draft['id']}: {verb} Gmail draft {result['id']} to {', '.join(draft['to'])}{where}: "
                f"{draft['subject']}{files}"
            )
    return 1 if problems else 0


def when(message: dict) -> str:
    return dt.datetime.fromtimestamp(int(message["internalDate"]) / 1000).strftime("%Y-%m-%d %H:%M")


def message_parts(payload: dict) -> tuple[list[str], list[str]]:
    """Decoded text/plain (and delivery-status) parts, and text/html parts."""
    plain, rich = [], []

    def walk(part: dict) -> None:
        data = part.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            if part.get("mimeType") in ("text/plain", "message/delivery-status"):
                plain.append(text)
            elif part.get("mimeType") == "text/html":
                rich.append(text)
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return plain, rich


# S/MIME signatures arrive as a named part (smime.p7s) but are not files anyone attached.
SIGNATURE_TYPES = ("application/pkcs7-signature", "application/x-pkcs7-signature")


def attached_files(payload: dict) -> list[dict]:
    """Parts sent as files (those with a filename, except signatures), in message order."""
    found = []

    def walk(part: dict) -> None:
        if part.get("filename") and part.get("mimeType") not in SIGNATURE_TYPES:
            found.append(part)
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return found


def tidy(text: str) -> str:
    lines = (
        line.rstrip() for line in text.replace("\r\n", "\n").replace("\xa0", " ").replace("\ufeff", "").split("\n")
    )
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _HTMLText(HTMLParser):
    """Block-aware HTML to text: divs and paragraphs become lines, list items get '1.' or '-'."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.lists: list[int | None] = []

    def newline(self) -> None:
        if self.out and not self.out[-1].endswith("\n"):
            self.out.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.out.append("\n")
        elif tag in ("div", "p", "tr", "blockquote"):
            self.newline()
        elif tag in ("ol", "ul"):
            self.newline()
            self.lists.append(0 if tag == "ol" else None)
        elif tag == "li":
            self.newline()
            if self.lists and self.lists[-1] is not None:
                self.lists[-1] += 1
                self.out.append(f"{self.lists[-1]}. ")
            else:
                self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in ("div", "p", "li", "tr", "blockquote"):
            self.newline()
        elif tag in ("ol", "ul"):
            if self.lists:
                self.lists.pop()
            self.newline()

    def handle_data(self, data):
        self.out.append(re.sub(r"[ \t\r\n]+", " ", data))


def html_to_text(markup: str) -> str:
    parser = _HTMLText()
    parser.feed(markup)
    parser.close()
    return tidy("\n".join(line.strip() for line in "".join(parser.out).split("\n")))


def message_text(payload: dict) -> str:
    """Readable body of a received message, cut where quoted earlier mail starts."""
    plain, rich = message_parts(payload)
    text = "\n".join(plain) if plain else html_to_text(rich[0]) if rich else ""
    return trim_quoted(tidy(text))


def sent_text(payload: dict) -> str:
    """Daniil's own text in a sent message: the HTML part without Gmail's quote block, else unwrapped plain text."""
    plain, rich = message_parts(payload)
    if rich:
        cut = re.search(r'<[^>]+class="gmail_quote', rich[0])
        return html_to_text(rich[0][: cut.start()] if cut else rich[0])
    return trim_quoted(unwrap(tidy("\n".join(plain))))


def trim_quoted(text: str) -> str:
    """Cut a reply at the start of the quoted earlier message."""
    kept = []
    for line in text.split("\n"):
        if QUOTE_RE.match(line.strip()):
            break
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def unwrap(text: str) -> str:
    """Undo Gmail's hard wrapping of plain-text mail.

    A line is joined to the one before when its first word would not have fit there."""
    out, previous = [], ""
    for line in text.split("\n"):
        words = line.split()
        if out and previous and words and not LIST_RE.match(line) and len(previous) + 1 + len(words[0]) > WRAP_WIDTH:
            out[-1] += " " + line.strip()
        else:
            out.append(line)
        previous = line
    return "\n".join(out).strip()


def sent_outreach(cfg: GmailConfig, ids: list[str]) -> tuple[list[dict], list[str]]:
    paths = [draft_path(cfg, i) for i in ids] if ids else sorted(cfg.drafts.glob("DR-*.md"))
    drafts, skipped = [], []
    for path in paths:
        meta = frontmatter.parse(path.read_text(encoding="utf-8"))
        if meta.get("channel") != "email" or meta.get("status") != "sent":
            if ids:
                raise ChannelError(f"{path.name}: not a sent email draft")
            continue
        try:
            drafts.append(read_email(path))
        except ChannelError as exc:
            skipped.append(str(exc))
    return drafts, skipped


def show_messages(cfg: GmailConfig, messages: list[dict], blind: str | None = None) -> None:
    replies = 0
    for message in messages:
        name, addr = parseaddr(header(message, "From"))
        if cfg.is_me(addr):
            # Threads also return Daniil's unsent Gmail drafts; only SENT messages went out.
            state = "draft, not sent, saved" if "DRAFT" in message.get("labelIds", []) else "sent"
            print(f"   {state} {when(message)} to {header(message, 'To')}")
            continue
        replies += 1
        print(f"   -- {when(message)} from {name or addr} <{addr}>: {header(message, 'Subject')}")
        if header(message, "Cc"):
            print(f"      cc: {header(message, 'Cc')}")
        files = attached_files(message.get("payload", {}))
        if blind:
            print(f"      [text and {len(files)} attachment(s) withheld: {blind}]")
            continue
        body = message_text(message.get("payload", {}))
        if len(body) > BODY_CHARS:
            body = body[:BODY_CHARS] + " [...]"
        for line in body.split("\n"):
            print(f"      {line}")
        if files:
            print(
                "      attachments: "
                + "; ".join(f"{part['filename']} ({part.get('body', {}).get('size', 0) // 1024} KB)" for part in files)
            )
    if not replies:
        print("   no reply yet")


def reply_drafts(cfg: GmailConfig, ids: list[str]) -> list[dict]:
    """Sent email drafts whose threads can hold replies (a reply_to draft lives in its parent's thread)."""
    drafts, skipped = sent_outreach(cfg, ids)
    for note in skipped:
        print(f"skipped, not in the tool's draft format: {note}")
    drafts = [draft for draft in drafts if not draft["reply_to"]]
    if not drafts:
        raise ChannelError("no sent email drafts to check (records with channel: email and status: sent)")
    return drafts


# the mail in scope (D-025) ------------------------------------------------------------------------

INBOX_DAYS = 30
EXCERPT_CHARS = 700
# How close the sent text must be before `sync` takes a message as the one an unsent draft became.
MATCH_RATIO = 0.6


def email_drafts(cfg: GmailConfig) -> list[dict]:
    """Every email draft in the tool's format, whatever its status."""
    found = []
    for path in sorted(cfg.drafts.glob("DR-*.md")):
        try:
            found.append(read_email(path))
        except ChannelError:
            continue  # not an email draft, or not in the tool's format
    return found


def email_subjects(cfg: GmailConfig) -> set[str]:
    return {draft["subject"] for draft in email_drafts(cfg)}


def created(draft: dict) -> dt.date:
    try:
        return dt.date.fromisoformat(str(draft["created"]))
    except ValueError:
        return dt.date.min


@dataclass
class Scope:
    """What decides which mail is read: the address book, the email drafts and the records."""

    book: mailbox.Book
    drafts: list[dict]
    records: Records


def scope(cfg: GmailConfig, records: Records | None = None) -> Scope:
    records = records or cfg.store.load()
    people = {pid: [str(a) for a in (rec.meta.get("email") or [])] for pid, rec in records.people.items()}
    drafts = email_drafts(cfg)
    return Scope(mailbox.build_book(cfg.account, people, drafts), drafts, records)


def refreshed(cfg: GmailConfig, token: str, days: int) -> Scope:
    """The scope, after bringing the local cache up to date for the last `days` days."""
    found = scope(cfg)
    since = dt.date.today() - dt.timedelta(days=days)
    subjects = [d["subject"] for d in found.drafts if d["status"] == "sent" and created(d) >= since]
    listed, fetched = mailbox.refresh(call, token, cfg.cache, found.book, subjects, days)
    print(f"Gmail: {listed} thread(s) in scope from the last {days} days; {fetched} new or changed, downloaded")
    return found


def thread_drafts(drafts: list[dict], all_mail: list[mailbox.Mail]) -> dict[str, list[dict]]:
    """Gmail thread ID -> the email drafts known to live in it (by `gmail_id` or `reply_thread`)."""
    thread_of = {mail.id: mail.thread for mail in all_mail}
    links: dict[str, list[dict]] = {}
    for draft in drafts:
        threads = {thread_of.get(draft["gmail_id"] or ""), draft["reply_thread"]} - {None}
        for thread in threads:
            links.setdefault(thread, []).append(draft)
    return links


def similarity(mail: mailbox.Mail, draft: dict) -> float:
    return difflib.SequenceMatcher(None, sent_text(mail.message.get("payload", {})), draft["body"]).ratio()


def find_sent(
    draft: dict,
    candidates: list[mailbox.Mail],
    other_subjects: set[str],
    parent_thread: str | None = None,
    strict: bool = False,
) -> mailbox.Mail | None:
    """Daniil's sent message for a draft: same subject, else the only message to those recipients with a new subject.

    Replies in one thread share a subject, so among same-subject messages the one whose text is
    closest to the draft wins (Daniil may have edited it before sending). `strict` (for a draft not
    yet marked sent) takes only a same-subject message sent on or after the draft's day whose text is
    close to the draft's."""
    since = created(draft) if strict else created(draft) - dt.timedelta(days=1)
    to = {address.lower() for address in draft["to"]}
    threads = {draft["reply_thread"], parent_thread} - {None}
    pool = [m for m in candidates if m.mine and m.date >= since and (set(m.recipients) & to or m.thread in threads)]
    exact = [m for m in pool if m.subject == draft["subject"]]
    if exact:
        best = max(exact, key=lambda m: similarity(m, draft))
        return None if strict and similarity(best, draft) < MATCH_RATIO else best
    if strict:
        return None
    renamed = [m for m in pool if m.subject not in other_subjects]
    return renamed[0] if len(renamed) == 1 else None


def sent_message(cfg: GmailConfig, token: str, draft: dict) -> mailbox.Mail | None:
    """The message a sent draft went out as, from its `gmail_id` or by matching."""
    days = max(INBOX_DAYS, (dt.date.today() - created(draft)).days + 2)
    found = refreshed(cfg, token, days)
    all_mail = mailbox.mails(cfg.cache, found.book)
    if draft["gmail_id"]:
        known = next((m for m in all_mail if m.id == draft["gmail_id"]), None)
        if known:
            return known
    others = {d["subject"] for d in found.drafts} - {draft["subject"]}
    return find_sent(draft, all_mail, others)


def reply_target(cfg: GmailConfig, token: str, parent: dict) -> dict:
    """What a reply to the parent's sent email answers: the latest message from someone else in
    its thread, or the sent email itself while nobody has answered (a reminder)."""
    sent = sent_message(cfg, token, parent)
    if sent is None:
        raise ChannelError(f"{parent['id']}: its sent email was not found, so there is no thread to reply in")
    return thread_target(cfg, token, sent.thread, parent["id"], follow_up=True)


def matched_threads(cfg: GmailConfig, found: Scope, drafts: list[dict], days: int) -> tuple[dict, list]:
    """Messages per draft from the cached threads it lives in or whose subject it carries, and the
    other threads in scope with a received message in the window, as (thread ID, messages)."""
    since = dt.date.today() - dt.timedelta(days=days)
    by_draft: dict[str, list] = {draft["id"]: [] for draft in drafts}
    other = []
    for thread in cfg.cache.threads():
        messages = [m for m in thread.get("messages", []) if "internalDate" in m]
        if not messages or max(mailbox.local_date(m) for m in messages) < since:
            continue
        ids = {m["id"] for m in messages}
        subjects = " ".join(header(m, "Subject") for m in messages)
        match = next(
            (d for d in drafts if (d["gmail_id"] and d["gmail_id"] in ids) or d["reply_thread"] == thread["id"]),
            None,
        ) or next((d for d in drafts if d["subject"] in subjects), None)
        if match:
            by_draft[match["id"]].append((thread["id"], messages))
        elif any(not cfg.is_me(parseaddr(header(m, "From"))[1]) and mailbox.local_date(m) >= since for m in messages):
            other.append((thread["id"], messages))
    return by_draft, other


def cmd_replies(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    drafts = reply_drafts(cfg, args.ids)
    found = refreshed(cfg, access_token(cfg, read=True), args.days)
    by_draft, other = matched_threads(cfg, found, drafts, args.days)
    for draft in drafts:
        print(f"== {draft['id']}  {draft['subject']}")
        if not by_draft[draft["id"]]:
            print(f"   no sent message found in the last {args.days} days")
            continue
        # Auto-replies arrive as separate threads; show one timeline per email.
        for thread, messages in by_draft[draft["id"]]:
            show_messages(cfg, sorted(messages, key=lambda m: int(m["internalDate"])), cfg.blind.get(thread))
    if args.ids:
        return 0
    for thread, messages in other:
        print(f"== Other thread with people in the records: {header(messages[0], 'Subject')}")
        show_messages(cfg, messages, cfg.blind.get(thread))
    return 0


def cmd_attachments(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    out = args.out or cfg.papers
    drafts = reply_drafts(cfg, args.ids)
    token = access_token(cfg, read=True)
    by_draft, other = matched_threads(cfg, refreshed(cfg, token, args.days), drafts, args.days)
    out.mkdir(parents=True, exist_ok=True)
    saved = 0
    for thread, messages in [*(pair for pairs in by_draft.values() for pair in pairs), *other]:
        if thread in cfg.blind:
            print(f"thread {thread} skipped: {cfg.blind[thread]}")
            continue
        for message in messages:
            if cfg.is_me(parseaddr(header(message, "From"))[1]):
                continue
            for part in attached_files(message.get("payload", {})):
                target = out / Path(part["filename"]).name
                if target.exists():
                    print(f"exists, left alone: {target}")
                    continue
                body = part.get("body", {})
                data = (
                    body.get("data")
                    or call("GET", f"{API}/messages/{message['id']}/attachments/{body['attachmentId']}", token=token)[
                        "data"
                    ]
                )
                target.write_bytes(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
                saved += 1
                print(
                    f"saved {target} ({target.stat().st_size // 1024} KB), "
                    f"from {header(message, 'From')} on {when(message)}"
                )
    if not saved:
        print("no new attachments")
    return 0


# the inbox: mail the records do not hold ---------------------------------------------------------


@dataclass
class Inbox:
    items: list[mailbox.Mail]  # open messages, oldest first
    all_mail: list[mailbox.Mail]
    links: dict[str, list[dict]]  # thread -> drafts in it


def inbox(cfg: GmailConfig, found: Scope, days: int, today: dt.date | None = None) -> Inbox:
    all_mail = mailbox.mails(cfg.cache, found.book)
    since = (today or dt.date.today()) - dt.timedelta(days=days)
    logged = mailbox.logged_from(
        found.records.events, [d["gmail_id"] for d in found.drafts], mailbox.parse_until(cfg.untagged_until)
    )
    items = mailbox.open_mail([m for m in all_mail if m.date >= since], logged)
    return Inbox(items, all_mail, thread_drafts(found.drafts, all_mail))


def describe(cfg: GmailConfig, box: Inbox, mail: mailbox.Mail, chars: int) -> list[str]:
    message = mail.message
    stamp = when(message)
    people = ", ".join(sorted(mail.people)) or "not in the records"
    if mail.mine:
        lines = [f"- {mail.id}  {stamp}  you to {header(message, 'To')}  [{people}]"]
    else:
        lines = [f"- {mail.id}  {stamp}  from {header(message, 'From')}  [{people}]"]
    lines.append(f"    Subject: {mail.subject}")
    drafts = box.links.get(mail.thread, [])
    notes = [f"thread {mail.thread}"]
    if drafts:
        notes.append("drafts " + ", ".join(d["id"] + (f" ({d['task']})" if d["task"] else "") for d in drafts))
    if not mail.mine:
        answer = mailbox.replied_after(mail, box.all_mail)
        notes.append(f"you replied {when(answer.message)}" if answer else "no reply from you yet")
        kind = mailbox.kind_of(mail)
        if kind:
            notes.append(kind)
    lines.append("    " + "; ".join(notes))
    files = attached_files(message.get("payload", {}))
    if mail.thread in cfg.blind:
        lines.append(f"    [text and {len(files)} attachment(s) withheld: {cfg.blind[mail.thread]}]")
        return lines
    body = sent_text(message.get("payload", {})) if mail.mine else message_text(message.get("payload", {}))
    if chars and len(body) > chars:
        body = body[:chars].rstrip() + " [...]"
    lines += [f"      {line}" for line in body.split("\n") if line.strip()]
    if files:
        lines.append(
            "    attachments: "
            + "; ".join(f"{part['filename']} ({part.get('body', {}).get('size', 0) // 1024} KB)" for part in files)
        )
    return lines


def checked_line(cfg: GmailConfig) -> str:
    state = cfg.cache.state()
    if not state:
        return "never checked on this machine"
    return f"checked {state['checked'].replace('T', ' ')} over the last {state['days']} days"


def cmd_inbox(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    found = scope(cfg) if args.offline else refreshed(cfg, access_token(cfg, read=True), args.days)
    box = inbox(cfg, found, args.days)
    received = [m for m in box.items if not m.mine]
    sent = [m for m in box.items if m.mine]
    print(
        f"Not in the records: {len(received)} received, {len(sent)} sent (last {args.days} days; {checked_line(cfg)})"
    )
    if received:
        print("\n== Received")
        for mail in received:
            print("\n".join(describe(cfg, box, mail, 0 if args.full else EXCERPT_CHARS)))
    if sent:
        print("\n== Sent by you, held by no draft or log event")
        for mail in sent:
            print("\n".join(describe(cfg, box, mail, 0 if args.full else EXCERPT_CHARS // 2)))
    unknown = sorted({m.sender for m in received if not m.people})
    if unknown:
        print("\nSenders not in the records (add to a person's `email` to link them): " + ", ".join(unknown))
    if box.items:
        print('\nFile each: uo gmail done ID[,ID] REFS "facts, not text" (an email draft that went out: uo gmail sync)')
    return 0


GMAIL_ID_RE = re.compile(r"^[0-9a-f]{4,}$")


def cmd_done(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    ids = [part.strip() for part in args.messages.split(",") if part.strip()]
    bad = [i for i in ids if not GMAIL_ID_RE.match(i)]
    if not ids or bad:
        raise ChannelError(f"not Gmail message IDs: {', '.join(bad) or args.messages!r}")
    found = scope(cfg)
    known = {m.id: m for m in mailbox.mails(cfg.cache, found.book)}
    missing = [i for i in ids if i not in known]
    if missing:
        raise ChannelError(f"not in the local cache: {', '.join(missing)} (run `uo gmail inbox` first)")
    kind = args.type or ("email-out" if all(known[i].mine for i in ids) else "email-in")
    refs = [] if args.refs.strip() == "-" else [r.strip() for r in args.refs.split(",") if r.strip()]
    summary = args.summary.strip()
    if mailbox.TAG_RE.search(summary):
        raise ChannelError("the summary already ends with a [gmail ...] tag; give the IDs only as the first argument")
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    event = cfg.store.append_event(found.records, date, kind, refs, f"{summary} [gmail {', '.join(ids)}]")
    print(event.format())
    return 0


def status_lines(ws: Workspace, records: Records, today: dt.date, shown: int = 5) -> list[str]:
    """The Gmail section of `uo status`, from the local cache only (no network)."""
    try:
        cfg = GmailConfig.from_workspace(ws)
    except ChannelError:
        return []  # no Gmail in this workspace: no section
    state = cfg.cache.state()
    if not state:
        return ["never checked on this machine: run `uo gmail inbox`"]
    box = inbox(cfg, scope(cfg, records), int(state.get("days", INBOX_DAYS)), today)
    received = [m for m in box.items if not m.mine]
    sent = [m for m in box.items if m.mine]
    lines = [f"{len(received)} received, {len(sent)} sent not in the records ({checked_line(cfg)}; `uo gmail inbox`)"]
    for mail in received[-shown:]:
        who = ", ".join(sorted(mail.people)) or mail.sender
        lines.append(f"{when(mail.message)} from {who}: {mail.subject}")
    return lines


# sync: the draft holds what went out -------------------------------------------------------------


def write_sent_version(path: Path, subject: str, body: str, to: list[str] | None) -> None:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 3) + 4
    lines = text[end:].strip("\n").split("\n")
    tail = lines[next(i for i, line in enumerate(lines) if line.startswith("To: ")) :]
    if to is not None:
        note = tail[0][tail[0].find(" (") :] if " (" in tail[0] else ""
        tail[0] = "To: " + ", ".join(to) + note
    path.write_text(
        f"{text[:end]}\nSubject: {subject}\n\n{body.strip()}\n\n" + "\n".join(tail) + "\n", encoding="utf-8"
    )


def sync_targets(drafts: list[dict], ids: list[str]) -> list[dict]:
    """Named drafts, else sent drafts without a `gmail_id` and drafts not yet marked sent."""
    if ids:
        by_id = {d["id"]: d for d in drafts}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise ChannelError(f"not email drafts in the tool's format: {', '.join(unknown)}")
        dropped = [i for i in ids if by_id[i]["status"] == "dropped"]
        if dropped:
            raise ChannelError(f"dropped: {', '.join(dropped)}")
        return [by_id[i] for i in ids]
    return [d for d in drafts if (d["status"] == "sent" and not d["gmail_id"]) or d["status"] in ("draft", "approved")]


def cmd_sync(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    drafts = email_drafts(cfg)
    targets = sync_targets(drafts, args.ids)
    already = sum(1 for d in drafts if d["status"] == "sent" and d["gmail_id"])
    if not targets:
        print(f"nothing to sync ({already} sent draft(s) already hold their Gmail ID)")
        return 0
    days = max(INBOX_DAYS, (dt.date.today() - min(created(d) for d in targets)).days + 2)
    found = refreshed(cfg, access_token(cfg, read=True), days)
    all_mail = mailbox.mails(cfg.cache, found.book)
    by_id = {d["id"]: d for d in drafts}
    claimed = {d["gmail_id"]: d["id"] for d in drafts if d["gmail_id"]}
    thread_of = {m.id: m.thread for m in all_mail}
    subjects = {d["subject"] for d in drafts}
    problems = 0
    # Sent drafts first, so each takes its own message before an unsent draft looks for one.
    for draft in sorted(targets, key=lambda d: (d["status"] != "sent", d["id"])):
        strict = draft["status"] != "sent"
        if draft["gmail_id"]:
            message = next((m for m in all_mail if m.id == draft["gmail_id"]), None)
        else:
            parent = by_id.get(draft["reply_to"] or "")
            parent_thread = thread_of.get(parent["gmail_id"] or "") if parent else None
            free = [m for m in all_mail if claimed.get(m.id, draft["id"]) == draft["id"]]
            message = find_sent(draft, free, subjects - {draft["subject"]}, parent_thread, strict)
        if message is None:
            if not strict:
                print(f"{draft['id']}: sent message not found, or more than one candidate; file unchanged")
                problems += 1
            continue
        claimed[message.id] = draft["id"]
        subject = message.subject
        body = sent_text(message.message.get("payload", {}))
        to = [a for _, a in getaddresses([header(message.message, "To")]) if a]
        changes = []
        if subject != draft["subject"]:
            changes.append("subject")
        if body.strip() != draft["body"].strip():
            changes.append("text")
        if {a.lower() for a in to} != {a.lower() for a in draft["to"]}:
            changes.append("recipients")
        path = draft_path(cfg, draft["id"])
        if changes:
            write_sent_version(path, subject, body, to if "recipients" in changes else None)
        updates = {"gmail_id": message.id, **({"status": "sent"} if strict else {})}
        path.write_text(frontmatter.update(path.read_text(encoding="utf-8"), updates), encoding="utf-8")
        verb = f"found in your sent mail ({when(message.message)}), marked sent" if strict else "synced"
        detail = f"updated to the sent version ({', '.join(changes)})" if changes else "matches the sent email"
        print(f"{draft['id']}: {verb}; {detail}")
        if "subject" in changes:
            print(f"   subject: {draft['subject']!r} -> {subject!r}")
        for line in difflib.unified_diff(
            draft["body"].strip().splitlines(), body.splitlines(), "draft", "sent", lineterm="", n=0
        ):
            print(f"   {line}")
    if already and not args.ids:
        print(f"{already} sent draft(s) already hold their Gmail ID; name one to compare it again")
    return 1 if problems else 0


def cmd_show(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    for draft_id in args.ids:
        draft = load_draft(cfg, draft_id)
        cc = f"Cc: {', '.join(draft['cc'])}\n" if draft["cc"] else ""
        where = draft["reply_to"] or (f"Gmail {draft['reply_thread']}" if draft["reply_thread"] else "")
        thread = f"Reply in the {where} thread\n" if where else ""
        print(
            f"== {draft['id']} ({draft['file']})\nTo: {', '.join(draft['to'])}\n{cc}{thread}"
            f"Subject: {draft['subject']}\n\n{draft['body']}"
        )
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    """Add `gmail` with its subcommands to the top-level parser."""
    gmail = sub.add_parser(
        "gmail",
        help="Gmail drafts and replies",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = gmail.add_subparsers(dest="gmail_command", required=True)
    auth = commands.add_parser("auth", help="Google consent (gmail.compose and gmail.readonly)")
    auth.add_argument("--client", type=Path, help="downloaded Desktop OAuth client JSON to install")
    auth.add_argument("--no-browser", action="store_true", help="print the consent URL without opening it")
    auth.set_defaults(handler=cmd_auth)
    push = commands.add_parser("push", help="create Gmail drafts from records/drafts")
    push.add_argument(
        "--update", action="store_true", help="overwrite the Gmail draft with the same subject; never creates one"
    )
    push.add_argument("ids", nargs="+", metavar="DR-###")
    push.set_defaults(handler=cmd_push)
    show = commands.add_parser("show", help="print the parsed email without contacting Gmail")
    show.add_argument("ids", nargs="+", metavar="DR-###")
    show.set_defaults(handler=cmd_show)
    replies = commands.add_parser("replies", help="replies, auto-replies and bounces for sent email drafts")
    replies.add_argument("--days", type=int, default=14, help="look back this many days (default 14)")
    replies.add_argument(
        "ids", nargs="*", metavar="DR-###", help="limit to these drafts (default: every sent email draft)"
    )
    replies.set_defaults(handler=cmd_replies)
    attach = commands.add_parser("attachments", help="save files attached to replies to sent email drafts")
    attach.add_argument("--days", type=int, default=14, help="look back this many days (default 14)")
    attach.add_argument("--out", type=Path, help="folder to save into (default papers/ in the private data folder)")
    attach.add_argument(
        "ids", nargs="*", metavar="DR-###", help="limit to these drafts (default: every sent email draft)"
    )
    attach.set_defaults(handler=cmd_attachments)
    sync = commands.add_parser(
        "sync", help="copy the text Gmail actually sent into email drafts; mark drafts found in the sent mail as sent"
    )
    sync.add_argument(
        "ids",
        nargs="*",
        metavar="DR-###",
        help="compare these drafts (default: sent drafts without a gmail_id, and drafts not yet marked sent)",
    )
    sync.set_defaults(handler=cmd_sync)
    box = commands.add_parser("inbox", help="mail with people in the records that the records do not hold yet")
    box.add_argument("--days", type=int, default=INBOX_DAYS, help=f"look back this many days (default {INBOX_DAYS})")
    box.add_argument("--offline", action="store_true", help="use the local cache only; no network")
    box.add_argument("--full", action="store_true", help="print whole messages instead of excerpts")
    box.set_defaults(handler=cmd_inbox)
    done = commands.add_parser("done", help="file inbox messages as one log event tagged with their Gmail IDs")
    done.add_argument("messages", metavar="MSG[,MSG]", help="Gmail message IDs, as `uo gmail inbox` prints them")
    done.add_argument("refs", metavar="REFS", help="comma-separated record IDs, or - for none")
    done.add_argument("summary", help="one line of facts (not message text)")
    done.add_argument("--type", help="log type (default email-in, or email-out when every message is yours)")
    done.add_argument("--date", metavar="YYYY-MM-DD", help="event date (default today)")
    done.set_defaults(handler=cmd_done)
