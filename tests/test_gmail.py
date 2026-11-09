"""The Gmail adapter's pure parts: draft parsing, the policy refusal, and text recovered from sent mail."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from unfloat_os.channels import ChannelError
from unfloat_os.channels.gmail import (
    GmailConfig,
    html_body,
    html_to_text,
    load_draft,
    read_email,
    trim_quoted,
    unwrap,
)
from unfloat_os.records import Store


def draft_id(number: int) -> str:
    return f"DR-{number:03d}"


def write_draft(
    folder: Path, number: int, *, status: str = "draft", channel: str = "email", body: str, extra: str = ""
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{draft_id(number)}-test.md"
    path.write_text(
        f"---\nid: {draft_id(number)}\nchannel: {channel}\nstatus: {status}\ncreated: 2026-11-01\n{extra}---\n{body}",
        encoding="utf-8",
    )
    return path


MESSAGE = "Subject: A question\n\nHello,\n\nCould we talk?\n\nTo: first@example.org, second@example.com\n"


@pytest.fixture
def config(tmp_path: Path) -> GmailConfig:
    return GmailConfig(Store(tmp_path), tmp_path / "papers", tmp_path / "conf", "me@example.org")


def drafts_folder(config: GmailConfig) -> Path:
    return config.drafts


def test_read_email_parses_subject_recipients_and_body(config: GmailConfig):
    body = MESSAGE + "Cc: third@example.net\n(notes: not part of the message)\n"
    path = write_draft(drafts_folder(config), 1, body=body, extra=f"reply_to: {draft_id(2)}\n")
    draft = read_email(path)
    assert draft["id"] == draft_id(1)
    assert draft["subject"] == "A question"
    assert draft["to"] == ["first@example.org", "second@example.com"]
    assert draft["cc"] == ["third@example.net"]
    assert draft["reply_to"] == draft_id(2)
    assert draft["reply_thread"] is None
    assert draft["body"] == "Hello,\n\nCould we talk?\n"


def test_read_email_without_cc_line(config: GmailConfig):
    draft = read_email(write_draft(drafts_folder(config), 1, body=MESSAGE))
    assert draft["cc"] == []
    assert draft["reply_to"] is None


def test_read_email_requires_a_subject_line(config: GmailConfig):
    path = write_draft(drafts_folder(config), 1, body="Hello\n\nTo: first@example.org\n")
    with pytest.raises(ChannelError, match=r"first body line must be 'Subject: \.\.\.'"):
        read_email(path)


def test_read_email_rejects_two_to_lines(config: GmailConfig):
    path = write_draft(drafts_folder(config), 1, body=MESSAGE + "To: other@example.org\n")
    with pytest.raises(ChannelError, match=r"exactly one 'To:' line .* found 2"):
        read_email(path)


def test_read_email_rejects_a_to_line_without_address(config: GmailConfig):
    path = write_draft(drafts_folder(config), 1, body="Subject: S\n\nText\n\nTo: nobody\n")
    with pytest.raises(ChannelError, match="no address on the 'To:' line"):
        read_email(path)


def test_read_email_rejects_other_channels(config: GmailConfig):
    path = write_draft(drafts_folder(config), 1, channel="discord", body=MESSAGE)
    with pytest.raises(ChannelError, match="channel is 'discord', not 'email'"):
        read_email(path)


def test_load_draft_returns_an_unsent_draft(config: GmailConfig):
    write_draft(drafts_folder(config), 3, body=MESSAGE)
    assert load_draft(config, draft_id(3))["subject"] == "A question"


@pytest.mark.parametrize("status", ["sent", "dropped"])
def test_load_draft_refuses_finished_drafts(config: GmailConfig, status: str):
    write_draft(drafts_folder(config), 3, status=status, body=MESSAGE)
    with pytest.raises(ChannelError, match=f"status is '{status}'"):
        load_draft(config, draft_id(3))


def test_load_draft_refuses_review_markers(config: GmailConfig):
    write_draft(drafts_folder(config), 3, body=MESSAGE.replace("Could we talk?", "Could we talk? [CONFIRM date]"))
    with pytest.raises(ChannelError, match=r"message contains '\[CONFIRM'; fix the draft first"):
        load_draft(config, draft_id(3))


def test_load_draft_refuses_em_dash_in_the_subject(config: GmailConfig):
    write_draft(drafts_folder(config), 3, body=MESSAGE.replace("A question", "A question \u2014 now"))
    with pytest.raises(ChannelError, match="message contains '\u2014'"):
        load_draft(config, draft_id(3))


def test_html_body_makes_one_div_per_line_and_escapes():
    assert html_body("Hi <you>\n\nBye\n") == (
        '<div dir="ltr"><div>Hi &lt;you&gt;</div><div><br></div><div>Bye</div></div>'
    )


def test_unwrap_restores_text_hard_wrapped_at_70_columns():
    paragraph = (
        "I am writing about the vitreous opacities that many people notice as floaters, "
        "and about a way to study how they form in the living eye."
    )
    wrapped = "\n".join(textwrap.wrap(paragraph, 70))
    assert "\n" in wrapped
    assert unwrap(wrapped) == paragraph


def test_unwrap_keeps_paragraphs_and_list_items():
    near_full = "x" * 68
    text = f"{near_full}\n\n{near_full}\n- next item\n1. numbered"
    assert unwrap(text) == text


def test_trim_quoted_cuts_at_the_attribution_line():
    text = "Thanks, Friday works.\n\nOn Mon, 3 Nov 2026 at 10:00, Daniil <me@example.org> wrote:\n> Could we talk?"
    assert trim_quoted(text) == "Thanks, Friday works."


@pytest.mark.parametrize(
    "marker", ["> quoted", "Am 3. Nov 2026 schrieb Daniil <me@example.org>:", "-----Original Message-----", "From: me"]
)
def test_trim_quoted_recognises_other_quote_starts(marker: str):
    assert trim_quoted(f"Reply\n\n{marker}\nold text") == "Reply"


def test_trim_quoted_keeps_text_without_quotes():
    assert trim_quoted("One\n\n\n\nTwo\n") == "One\n\nTwo"


def test_html_to_text_turns_divs_into_lines():
    assert html_to_text("<div>Hello,</div><div><br></div><div>Bye&nbsp;now</div>") == "Hello,\n\nBye now"


def test_html_to_text_numbers_ordered_lists_and_dashes_bullets():
    markup = "<div>Options:</div><ol><li>one</li><li>two</li></ol><ul><li>red</li><li>blue</li></ul>"
    assert html_to_text(markup) == "Options:\n1. one\n2. two\n- red\n- blue"
