"""Email drafts are validated by `uo check`: shape, forbidden text, and subjects shared outside one conversation."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import write_schema

from unfloat_os.drafts import DraftError, check_email_drafts, read_email
from unfloat_os.records import Store

MESSAGE = "Subject: {subject}\n\nHello,\n\nCould we talk?\n\nTo: first@example.org\n"


def draft_id(number: int) -> str:
    return f"DR-{number:03d}"


def write_draft(
    root: Path,
    number: int,
    *,
    subject: str = "A question",
    status: str = "draft",
    channel: str = "email",
    body: str | None = None,
    extra: str = "",
) -> Path:
    folder = root / "records" / "drafts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{draft_id(number)}-test.md"
    text = MESSAGE.format(subject=subject) if body is None else body
    path.write_text(
        f"---\nid: {draft_id(number)}\nchannel: {channel}\nstatus: {status}\ncreated: 2026-11-01\n{extra}---\n{text}",
        encoding="utf-8",
    )
    return path


def issues(root: Path) -> list[str]:
    write_schema(root)
    return [str(issue) for issue in check_email_drafts(Store(root).load()).issues]


def test_a_well_formed_draft_passes(tmp_path: Path):
    write_draft(tmp_path, 1)
    write_draft(tmp_path, 2, subject="Another question", status="approved")
    assert issues(tmp_path) == []


@pytest.mark.parametrize("status", ["draft", "approved"])
def test_missing_subject_line_is_an_error(tmp_path: Path, status: str):
    write_draft(tmp_path, 1, status=status, body="Hello\n\nTo: first@example.org\n")
    assert issues(tmp_path) == [
        f"error: records/drafts/{draft_id(1)}-test.md: first body line must be 'Subject: ...'",
    ]


def test_two_to_lines_are_an_error(tmp_path: Path):
    write_draft(tmp_path, 1, body=MESSAGE.format(subject="S") + "To: other@example.org\n")
    assert issues(tmp_path) == [
        f"error: records/drafts/{draft_id(1)}-test.md: needs exactly one 'To:' line after the message, found 2",
    ]


def test_missing_to_line_is_an_error(tmp_path: Path):
    write_draft(tmp_path, 1, body="Subject: S\n\nHello\n")
    assert issues(tmp_path) == [
        f"error: records/drafts/{draft_id(1)}-test.md: needs exactly one 'To:' line after the message, found 0",
    ]


def test_a_to_line_without_an_address_is_an_error(tmp_path: Path):
    write_draft(tmp_path, 1, body="Subject: S\n\nHello\n\nTo: nobody\n")
    assert issues(tmp_path) == [f"error: records/drafts/{draft_id(1)}-test.md: no address on the 'To:' line"]


def test_forbidden_text_in_the_subject_or_body_is_an_error(tmp_path: Path):
    write_draft(tmp_path, 1, subject="A question \u2014 now")
    write_draft(
        tmp_path, 2, subject="Other", body=MESSAGE.format(subject="Other").replace("talk?", "talk? [CONFIRM x]")
    )
    assert issues(tmp_path) == [
        f"error: records/drafts/{draft_id(1)}-test.md: message contains '\u2014'; fix the draft first",
        f"error: records/drafts/{draft_id(2)}-test.md: message contains '[CONFIRM'; fix the draft first",
    ]


@pytest.mark.parametrize("status", ["sent", "dropped"])
def test_sent_and_dropped_drafts_are_not_checked_for_shape(tmp_path: Path, status: str):
    write_draft(tmp_path, 1, status=status, body="no subject, no recipient \u2014 [CONFIRM]\n")
    assert issues(tmp_path) == []


def test_other_channels_are_ignored(tmp_path: Path):
    write_draft(tmp_path, 1, channel="discord", body="no subject at all\n")
    assert issues(tmp_path) == []


def test_a_new_email_with_the_subject_of_an_earlier_one_is_an_error(tmp_path: Path):
    write_draft(tmp_path, 1, status="sent")
    write_draft(tmp_path, 2)
    [message] = issues(tmp_path)
    assert message.startswith(
        f"error: records/drafts/{draft_id(2)}-test.md: subject 'A question' is already used by {draft_id(1)};"
    )
    assert "`uo gmail replies` matches threads by subject" in message


def test_a_dropped_draft_does_not_reserve_its_subject(tmp_path: Path):
    write_draft(tmp_path, 1, status="dropped")
    write_draft(tmp_path, 2)
    assert issues(tmp_path) == []


def test_replies_share_the_subject_of_their_conversation(tmp_path: Path):
    write_draft(tmp_path, 1, status="sent", subject="Re: Hello")
    write_draft(tmp_path, 2, status="sent", subject="Re: Hello", extra=f"reply_to: {draft_id(1)}\n")
    write_draft(tmp_path, 3, subject="Re: Hello", extra=f"reply_to: {draft_id(2)}\n")  # reply to a reply
    assert issues(tmp_path) == []


def test_replies_in_a_dropped_replys_chain_still_share_the_subject(tmp_path: Path):
    write_draft(tmp_path, 1, status="sent")
    write_draft(tmp_path, 2, status="dropped", extra=f"reply_to: {draft_id(1)}\n")
    write_draft(tmp_path, 3, extra=f"reply_to: {draft_id(2)}\n")
    assert issues(tmp_path) == []


def test_a_reply_to_another_conversation_may_not_reuse_the_subject(tmp_path: Path):
    write_draft(tmp_path, 1, status="sent", subject="One")
    write_draft(tmp_path, 2, status="sent", subject="Two")
    write_draft(tmp_path, 3, subject="One", extra=f"reply_to: {draft_id(2)}\n")
    [message] = issues(tmp_path)
    assert message.startswith(
        f"error: records/drafts/{draft_id(3)}-test.md: subject 'One' is already used by {draft_id(1)};"
    )


def test_drafts_in_one_gmail_thread_share_a_subject(tmp_path: Path):
    write_draft(tmp_path, 1, status="sent", subject="Re: Thread", extra="reply_thread: abc123\n")
    write_draft(tmp_path, 2, subject="Re: Thread", extra="reply_thread: abc123\n")
    assert issues(tmp_path) == []


def test_drafts_in_different_gmail_threads_may_not_share_a_subject(tmp_path: Path):
    write_draft(tmp_path, 1, status="sent", subject="Re: Thread", extra="reply_thread: abc123\n")
    write_draft(tmp_path, 2, subject="Re: Thread", extra="reply_thread: other\n")
    [message] = issues(tmp_path)
    assert message.startswith(
        f"error: records/drafts/{draft_id(2)}-test.md: subject 'Re: Thread' is already used by {draft_id(1)};"
    )


def test_a_reply_cycle_does_not_hang(tmp_path: Path):
    write_draft(tmp_path, 1, status="sent", extra=f"reply_to: {draft_id(2)}\n")
    write_draft(tmp_path, 2, status="sent", extra=f"reply_to: {draft_id(1)}\n")
    write_draft(tmp_path, 3, subject="Elsewhere")
    assert issues(tmp_path) == []


def test_read_email_error_names_the_file_and_the_reason(tmp_path: Path):
    path = write_draft(tmp_path, 1, body="Hello\n")
    with pytest.raises(
        DraftError, match=rf"{draft_id(1)}-test\.md: first body line must be 'Subject: \.\.\.'"
    ) as caught:
        read_email(path)
    assert caught.value.reason == "first body line must be 'Subject: ...'"
