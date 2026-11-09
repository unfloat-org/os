"""Daniil's Gmail for outreach (D-016 to D-018): drafts from records/drafts, and the replies to them.

Scopes: gmail.compose (drafts) and gmail.readonly (replies). The adapter never
sends, deletes or changes mail, and reading does not mark anything as read.
Reading is limited to threads that match the sent email drafts in records/drafts
(their To addresses and subjects); there is no free-text search.

  uo gmail show DR-###                      # print the parsed email; no network
  uo gmail auth [--client FILE]             # Google consent in the browser
  uo gmail push DR-### DR-###               # create Gmail drafts; existing ones are left alone
  uo gmail push --update DR-###             # overwrite the Gmail draft with the same subject; never creates one
  uo gmail replies [--days N] [DR-### ...]  # replies, auto-replies and bounces for sent drafts
  uo gmail attachments [--days N] [--out DIR] [DR-### ...]  # save files attached to those replies
  uo gmail sync [DR-### ...]                # copy the text Gmail actually sent back into sent drafts

A draft file must have `channel: email`, a first body line `Subject: ...`, then
the message, then one `To:` line with the addresses, optionally a `Cc:` line
right after it (notes may follow). A draft with `reply_to: DR-###` in its
frontmatter is pushed as a reply inside that sent email's thread, answering the
latest message there from someone else. A draft with `reply_thread: <Gmail thread
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
import os
import re
import secrets
import shutil
import time
import tomllib
import urllib.parse
import webbrowser
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from pathlib import Path

from unfloat_os import credentials, frontmatter, policy, schema
from unfloat_os.channels import ChannelError
from unfloat_os.http import HttpError, send
from unfloat_os.records import Store
from unfloat_os.workspace import Workspace

COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = (COMPOSE, READONLY)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
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

    @property
    def drafts(self) -> Path:
        return self.store.folder(schema.KINDS["draft"])

    def is_me(self, address: str) -> bool:
        return address.lower() == self.account.lower()

    @classmethod
    def from_workspace(cls, ws: Workspace) -> GmailConfig:
        path = ws.ops / "config" / "channels.toml"
        try:
            account = tomllib.loads(path.read_text(encoding="utf-8")).get("gmail", {}).get("account")
        except FileNotFoundError as exc:
            raise ChannelError(f"{path} not found") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ChannelError(f"{path}: {exc}") from exc
        if not isinstance(account, str) or not account.strip():
            raise ChannelError(f'{path}: [gmail] needs account = "..."')
        return cls(Store(ws.ops), ws.data_dir("private") / "papers", credentials.channel_dir("gmail"), account.strip())


def draft_path(cfg: GmailConfig, draft_id: str) -> Path:
    return cfg.store.path_of(draft_id)


def read_email(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        meta = frontmatter.parse(text)
    except frontmatter.FrontmatterError as exc:
        raise ChannelError(f"{path.name}: {exc}") from exc
    if meta.get("channel") != "email":
        raise ChannelError(f"{path.name}: channel is {meta.get('channel')!r}, not 'email'")
    lines = frontmatter.body(text).strip("\n").split("\n")
    if not lines[0].startswith("Subject: "):
        raise ChannelError(f"{path.name}: first body line must be 'Subject: ...'")
    subject = lines[0].removeprefix("Subject: ").strip()
    to_index = [i for i, line in enumerate(lines) if line.startswith("To: ")]
    if len(to_index) != 1:
        raise ChannelError(f"{path.name}: needs exactly one 'To:' line after the message, found {len(to_index)}")
    to = EMAIL_RE.findall(lines[to_index[0]])
    if not to:
        raise ChannelError(f"{path.name}: no address on the 'To:' line")
    after = lines[to_index[0] + 1] if to_index[0] + 1 < len(lines) else ""
    cc = EMAIL_RE.findall(after) if after.startswith("Cc: ") else []
    body = "\n".join(lines[1 : to_index[0]]).strip() + "\n"
    return {
        "id": meta.get("id"),
        "file": path.name,
        "status": meta.get("status"),
        "created": meta.get("created"),
        "to": to,
        "cc": cc,
        "reply_to": meta.get("reply_to") or None,
        "reply_thread": meta.get("reply_thread") or None,
        "subject": subject,
        "body": body,
    }


def load_draft(cfg: GmailConfig, draft_id: str) -> dict:
    draft = read_email(draft_path(cfg, draft_id))
    if draft["status"] in ("sent", "dropped"):
        raise ChannelError(f"{draft['file']}: status is {draft['status']!r}")
    bad = policy.problems(draft["subject"] + draft["body"], "email")
    if bad:
        raise ChannelError(f"{draft['file']}: message contains {', '.join(map(repr, bad))}; fix the draft first")
    return draft


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
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def call(method: str, url: str, data: dict | None = None, token: str | None = None, form: bool = False) -> dict:
    try:
        return send(method, url, data=data, form=form, bearer=token)
    except HttpError as exc:
        raise ChannelError(str(exc)) from exc


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


def reply_target(cfg: GmailConfig, token: str, parent: dict) -> dict:
    """The latest message from someone else in the thread of the parent's sent email."""
    sent = sent_message(cfg, token, parent, email_subjects(cfg) - {parent["subject"]})
    if sent is None:
        raise ChannelError(f"{parent['id']}: its sent email was not found, so there is no thread to reply in")
    return thread_target(cfg, token, sent["threadId"], parent["id"])


def thread_target(cfg: GmailConfig, token: str, thread_id: str, label: str) -> dict:
    """The latest message from someone else in a Gmail thread, as In-Reply-To/References for a reply."""
    thread = call("GET", f"{API}/threads/{thread_id}?format=metadata", token=token)
    theirs = [m for m in thread.get("messages", []) if not cfg.is_me(parseaddr(header(m, "From"))[1])]
    if not theirs:
        raise ChannelError(f"{label}: nobody else has written in that thread")
    last = max(theirs, key=lambda m: int(m["internalDate"]))
    message_id = header(last, "Message-ID")
    references = " ".join(part for part in (header(last, "References"), message_id) if part)
    return {"thread": thread_id, "in_reply_to": message_id, "references": references}


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
            where = f" (reply in the {draft['reply_to'] or 'given'} thread)" if target else ""
            print(
                f"{draft['id']}: {verb} Gmail draft {result['id']} to {', '.join(draft['to'])}{where}: "
                f"{draft['subject']}"
            )
    return 1 if problems else 0


def header(message: dict, name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


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


def show_messages(cfg: GmailConfig, messages: list[dict]) -> None:
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
        body = message_text(message.get("payload", {}))
        if len(body) > BODY_CHARS:
            body = body[:BODY_CHARS] + " [...]"
        for line in body.split("\n"):
            print(f"      {line}")
        files = attached_files(message.get("payload", {}))
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


def matched_threads(drafts: list[dict], days: int, token: str) -> tuple[dict[str, list], list[list]]:
    """Messages per draft from threads carrying its subject, and the other threads with the same recipients."""
    addresses = sorted({address.lower() for draft in drafts for address in draft["to"]})
    terms = [f"from:({' OR '.join(addresses)})"] + [f'subject:"{draft["subject"]}"' for draft in drafts]
    query = f"newer_than:{days}d ({' OR '.join(terms)})"
    listing = call("GET", f"{API}/threads?" + urllib.parse.urlencode({"q": query, "maxResults": 100}), token=token)
    by_draft: dict[str, list] = {draft["id"]: [] for draft in drafts}
    other = []
    for item in listing.get("threads", []):
        messages = call("GET", f"{API}/threads/{item['id']}?format=full", token=token).get("messages", [])
        subjects = " ".join(header(message, "Subject") for message in messages)
        match = next((draft for draft in drafts if draft["subject"] in subjects), None)
        if match:
            by_draft[match["id"]].extend(messages)
        else:
            other.append(messages)
    return by_draft, other


def cmd_replies(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    drafts = reply_drafts(cfg, args.ids)
    by_draft, other = matched_threads(drafts, args.days, access_token(cfg, read=True))
    for draft in drafts:
        print(f"== {draft['id']}  {draft['subject']}")
        if not by_draft[draft["id"]]:
            print(f"   no sent message found in the last {args.days} days")
            continue
        # Auto-replies arrive as separate threads; show one timeline per email.
        show_messages(cfg, sorted(by_draft[draft["id"]], key=lambda message: int(message["internalDate"])))
    for messages in other:
        print(f"== Other thread with these contacts: {header(messages[0], 'Subject')}")
        show_messages(cfg, messages)
    return 0


def cmd_attachments(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    out = args.out or cfg.papers
    drafts = reply_drafts(cfg, args.ids)
    token = access_token(cfg, read=True)
    by_draft, other = matched_threads(drafts, args.days, token)
    out.mkdir(parents=True, exist_ok=True)
    saved = 0
    for messages in [*by_draft.values(), *other]:
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


def email_subjects(cfg: GmailConfig) -> set[str]:
    subjects = set()
    for path in cfg.drafts.glob("DR-*.md"):
        try:
            subjects.add(read_email(path)["subject"])
        except ChannelError:
            continue  # not an email draft in the tool's format
    return subjects


def sent_message(cfg: GmailConfig, token: str, draft: dict, other_subjects: set[str]) -> dict | None:
    """Daniil's sent message for a draft: same subject, else the only message to those recipients with a new subject.

    Replies in one thread share a subject, so among same-subject messages the one whose text is
    closest to the draft wins (Daniil may have edited it before sending)."""
    since = (dt.date.fromisoformat(draft["created"]) - dt.timedelta(days=1)).strftime("%Y/%m/%d")
    query = f"in:sent after:{since} to:({' OR '.join(draft['to'])})"
    listing = call("GET", f"{API}/messages?" + urllib.parse.urlencode({"q": query, "maxResults": 50}), token=token)
    messages = [
        call("GET", f"{API}/messages/{item['id']}?format=full", token=token) for item in listing.get("messages", [])
    ]
    messages = sorted(
        (m for m in messages if cfg.is_me(parseaddr(header(m, "From"))[1])), key=lambda m: int(m["internalDate"])
    )
    exact = [m for m in messages if header(m, "Subject") == draft["subject"]]
    if exact:
        return max(
            exact,
            key=lambda m: difflib.SequenceMatcher(None, sent_text(m.get("payload", {})), draft["body"]).ratio(),
        )
    renamed = [m for m in messages if header(m, "Subject") not in other_subjects]
    return renamed[0] if len(renamed) == 1 else None


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


def cmd_sync(args, ws: Workspace) -> int:
    cfg = GmailConfig.from_workspace(ws)
    drafts, skipped = sent_outreach(cfg, args.ids)
    for note in skipped:
        print(f"skipped, not in the tool's draft format: {note}")
    token = access_token(cfg, read=True)
    subjects = email_subjects(cfg)
    problems = 0
    for draft in drafts:
        message = sent_message(cfg, token, draft, subjects - {draft["subject"]})
        if message is None:
            print(f"{draft['id']}: sent message not found, or more than one candidate; file unchanged")
            problems += 1
            continue
        subject = header(message, "Subject")
        body = sent_text(message.get("payload", {}))
        to = [address for _, address in getaddresses([header(message, "To")]) if address]
        changes = []
        if subject != draft["subject"]:
            changes.append("subject")
        if body.strip() != draft["body"].strip():
            changes.append("text")
        if {a.lower() for a in to} != {a.lower() for a in draft["to"]}:
            changes.append("recipients")
        if not changes:
            print(f"{draft['id']}: matches the sent email")
            continue
        write_sent_version(draft_path(cfg, draft["id"]), subject, body, to if "recipients" in changes else None)
        print(f"{draft['id']}: updated to the sent version ({', '.join(changes)})")
        if "subject" in changes:
            print(f"   subject: {draft['subject']!r} -> {subject!r}")
        for line in difflib.unified_diff(
            draft["body"].strip().splitlines(), body.splitlines(), "draft", "sent", lineterm="", n=0
        ):
            print(f"   {line}")
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
    sync = commands.add_parser("sync", help="replace sent email drafts with the text Gmail actually sent")
    sync.add_argument(
        "ids", nargs="*", metavar="DR-###", help="limit to these drafts (default: every sent email draft)"
    )
    sync.set_defaults(handler=cmd_sync)
