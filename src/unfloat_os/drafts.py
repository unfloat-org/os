"""The email draft file format, and the rules `uo check` enforces on email drafts before anything reaches Gmail.

A draft with `channel: email` is a record whose body is: a first line `Subject: ...`, the message, one
`To:` line with the addresses, optionally a `Cc:` line right after it; notes may follow. Frontmatter
`attachments: [path, ...]` names files in the private data folder to attach. That is the
only format the Gmail adapter can push, so it is read here, once, and checked here for every draft
that can still be sent (status `draft` or `approved`): the shape, and the channel's forbidden marks
(`policy`). A broken draft is found by `uo check`, not when someone tries to push it.

Subjects must be unique among email drafts that are not `dropped`: `uo gmail replies` finds the
thread of a sent email by its subject, so two emails with one subject would be mixed up. Real drafts
share a subject on purpose in exactly one situation: several messages in one conversation. The
exception, derived from the records themselves (drafts with `reply_to`, and drafts with
`reply_thread`, all sent inside an existing thread), is:

- a draft with `reply_to: DR-###` belongs to the conversation of that draft (and so to that
  draft's conversation in turn, along `reply_to` links);
- a draft with `reply_thread: <Gmail thread id>` belongs to the conversation of that thread;
- any other draft starts a conversation of its own.

Two drafts may have the same subject only if they belong to the same conversation. Anything else
(for example a new outreach email reusing the subject of an earlier one) is an error.
"""

from __future__ import annotations

import re
from pathlib import Path

from unfloat_os import frontmatter, policy
from unfloat_os.records import Records
from unfloat_os.report import Report

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UNSENT = ("draft", "approved")


class DraftError(Exception):
    """A draft file that is not a valid email draft; `reason` says what is wrong, `str()` adds the file name."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name}: {reason}")
        self.name = name
        self.reason = reason


def read_email(path: Path) -> dict:
    """Parse an email draft file into its metadata, recipients, subject and message body."""
    text = path.read_text(encoding="utf-8")
    try:
        meta = frontmatter.parse(text)
    except frontmatter.FrontmatterError as exc:
        raise DraftError(path.name, str(exc)) from exc
    if meta.get("channel") != "email":
        raise DraftError(path.name, f"channel is {meta.get('channel')!r}, not 'email'")
    lines = frontmatter.body(text).strip("\n").split("\n")
    if not lines[0].startswith("Subject: "):
        raise DraftError(path.name, "first body line must be 'Subject: ...'")
    subject = lines[0].removeprefix("Subject: ").strip()
    to_index = [i for i, line in enumerate(lines) if line.startswith("To: ")]
    if len(to_index) != 1:
        raise DraftError(path.name, f"needs exactly one 'To:' line after the message, found {len(to_index)}")
    to = EMAIL_RE.findall(lines[to_index[0]])
    if not to:
        raise DraftError(path.name, "no address on the 'To:' line")
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
        "attachments": list(meta.get("attachments") or []),
        "subject": subject,
        "body": body,
    }


def attachment_problems(draft: dict) -> list[str]:
    """Attachment paths that are not plain relative paths inside the private data folder.

    Only `../data/private/` can be attached: patient data (`restricted/`) never leaves by email, and a path
    must not climb out of the folder. Whether the files exist is checked when the draft is pushed.
    """
    bad = []
    for name in draft["attachments"]:
        path = Path(str(name))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            bad.append(f"attachment {name!r} must be a path relative to the private data folder")
    return bad


def forbidden_marks(draft: dict) -> str | None:
    """The refusal text when the subject or body contains marks the email channel forbids, else None."""
    bad = policy.problems(draft["subject"] + draft["body"], "email")
    return f"message contains {', '.join(map(repr, bad))}; fix the draft first" if bad else None


def _conversation(draft_id: str, drafts: dict[str, dict]) -> str:
    """The conversation a draft belongs to: its Gmail thread, else the root of its `reply_to` chain.

    Drafts in a `reply_to` loop (a mistake) belong to one conversation, named by the smallest ID in the loop.
    """
    path: list[str] = []
    while draft_id not in path:
        path.append(draft_id)
        draft = drafts[draft_id]
        if draft["reply_thread"]:
            return f"thread {draft['reply_thread']}"
        parent = draft["reply_to"]
        if parent not in drafts:
            return draft_id
        draft_id = parent
    return min(path[path.index(draft_id) :])


def check_email_drafts(records: Records) -> Report:
    """Shape and forbidden-text errors of unsent email drafts, and subjects shared outside one conversation."""
    report = Report()
    parsed: dict[str, dict] = {}
    live: list[str] = []
    for draft_id, record in sorted(records.drafts.items()):
        if record.meta.get("channel") != "email":
            continue
        status = record.meta.get("status")
        where = "/".join(record.path.parts[-3:])
        try:
            draft = read_email(record.path)
        except DraftError as exc:
            if status in UNSENT:
                report.error(where, exc.reason)
            continue
        if status in UNSENT:
            refusal = forbidden_marks(draft)
            if refusal:
                report.error(where, refusal)
            for problem in attachment_problems(draft):
                report.error(where, problem)
        draft["where"] = where
        parsed[draft_id] = draft
        if status != "dropped":
            live.append(draft_id)
    by_subject: dict[str, list[str]] = {}
    for draft_id in live:
        by_subject.setdefault(parsed[draft_id]["subject"], []).append(draft_id)
    for subject, ids in by_subject.items():
        first = ids[0]
        home = _conversation(first, parsed)
        for draft_id in ids[1:]:
            if _conversation(draft_id, parsed) != home:
                report.error(
                    parsed[draft_id]["where"],
                    f"subject {subject!r} is already used by {first}; `uo gmail replies` matches threads by subject "
                    "(a reply shares its parent's subject only through reply_to or reply_thread)",
                )
    return report
