"""The Gmail cache, the inbox of mail the records lack, `done`, the retry on rate limits, and `sync` on the cache."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
from pathlib import Path

import pytest
from conftest import PERSON, rid, write_record, write_schema

from unfloat_os import frontmatter
from unfloat_os.channels import ChannelError, gmail, mailbox
from unfloat_os.channels.gmail import GmailConfig
from unfloat_os.http import HttpError
from unfloat_os.records import Store

ME = "me@example.org"
ANN = "ann@example.com"
TODAY = dt.date.today()


def stamp(day: dt.date, hour: int = 12) -> str:
    return str(int(dt.datetime(day.year, day.month, day.day, hour).timestamp() * 1000))


def message(
    mid: str,
    sender: str,
    to: str,
    subject: str,
    text: str,
    day: dt.date = TODAY,
    hour: int = 12,
    labels: tuple[str, ...] = (),
    extra_headers: tuple[tuple[str, str], ...] = (),
    mime: str = "text/plain",
) -> dict:
    headers = [{"name": "From", "value": sender}, {"name": "To", "value": to}, {"name": "Subject", "value": subject}]
    headers += [{"name": k, "value": v} for k, v in extra_headers]
    data = base64.urlsafe_b64encode(text.encode()).decode()
    return {
        "id": mid,
        "internalDate": stamp(day, hour),
        "labelIds": list(labels),
        "payload": {"mimeType": mime, "headers": headers, "body": {"data": data}},
    }


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GmailConfig:
    monkeypatch.setenv(mailbox.CACHE_ENV, str(tmp_path / "cache"))
    write_schema(tmp_path)
    for folder in ("people", "tasks", "drafts", "log"):
        (tmp_path / "records" / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "records" / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
    write_record(tmp_path, "people", rid("P", 1), "ann", {**PERSON, "name": "Ann", "email": [ANN]})
    return GmailConfig(Store(tmp_path), tmp_path / "papers", tmp_path / "conf", ME)


def write_email_draft(
    config: GmailConfig, number: int, *, status: str, subject: str, body: str, extra: str = ""
) -> Path:
    path = config.drafts / f"{rid('DR', number)}-mail.md"
    path.write_text(
        f"---\nid: {rid('DR', number)}\ntitle: Mail\nstatus: {status}\nchannel: email\ncreated: {TODAY.isoformat()}\n"
        f"{extra}---\nSubject: {subject}\n\n{body}\n\nTo: {ANN}\n",
        encoding="utf-8",
    )
    return path


def cache_thread(config: GmailConfig, thread: str, *messages: dict, history: str = "1") -> None:
    config.cache.save({"id": thread, "historyId": history, "messages": list(messages)})


def log(config: GmailConfig, line: str) -> None:
    path = config.store.log_dir / f"{TODAY.strftime('%Y-%m')}.md"
    path.write_text(f"# Log\n\n{line}\n", encoding="utf-8")


# refresh ---------------------------------------------------------------------------------------


def test_refresh_downloads_only_threads_whose_history_changed(config: GmailConfig):
    cache_thread(config, "t1", message("m1", ANN, ME, "Hi", "old"), history="5")
    requests = []

    def fake_call(method, url, data=None, token=None, form=False):
        requests.append(url)
        if "/threads?" in url:
            return {"threads": [{"id": "t1", "historyId": "5"}, {"id": "t2", "historyId": "9"}]}
        return {"historyId": "9", "messages": [message("m2", ANN, ME, "New", "hello")]}

    book = gmail.scope(config).book
    listed, fetched = mailbox.refresh(fake_call, "token", config.cache, book, [], 30)
    assert (listed, fetched) == (2, 1)
    assert [u for u in requests if "/threads/" in u] == [f"{mailbox.API}/threads/t2?format=full"]
    assert config.cache.load("t2")["messages"][0]["id"] == "m2"
    assert config.cache.state()["listed"] == 2


def test_refresh_searches_people_from_their_email_field(config: GmailConfig):
    queries = []

    def fake_call(method, url, data=None, token=None, form=False):
        queries.append(url)
        return {}

    mailbox.refresh(fake_call, "token", config.cache, gmail.scope(config).book, [], 7)
    assert len(queries) == 1 and "ann%40example.com" in queries[0] and "newer_than%3A7d" in queries[0]


def test_call_retries_a_rate_limit_and_then_answers(monkeypatch: pytest.MonkeyPatch):
    answers = [HttpError("GET", "u", 403, '{"reason": "rateLimitExceeded"}'), {"ok": True}]

    def fake_send(*args, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    waits = []
    monkeypatch.setattr(gmail, "send", fake_send)
    monkeypatch.setattr(gmail.time, "sleep", waits.append)
    assert gmail.call("GET", "u") == {"ok": True}
    assert waits == [gmail.RETRY_WAITS_S[0]]


def test_call_does_not_retry_other_errors(monkeypatch: pytest.MonkeyPatch):
    def fake_send(*args, **kwargs):
        raise HttpError("GET", "u", 404, "not found")

    monkeypatch.setattr(gmail, "send", fake_send)
    monkeypatch.setattr(gmail.time, "sleep", lambda s: pytest.fail("slept"))
    with pytest.raises(ChannelError, match="HTTP 404"):
        gmail.call("GET", "u")


def test_call_gives_up_after_the_waits(monkeypatch: pytest.MonkeyPatch):
    def fake_send(*args, **kwargs):
        raise HttpError("GET", "u", 429, "slow down")

    monkeypatch.setattr(gmail, "send", fake_send)
    monkeypatch.setattr(gmail.time, "sleep", lambda s: None)
    with pytest.raises(ChannelError, match="per-minute quota"):
        gmail.call("GET", "u")


# the inbox -------------------------------------------------------------------------------------


def open_ids(config: GmailConfig) -> list[str]:
    return [m.id for m in gmail.inbox(config, gmail.scope(config), 30).items]


def test_a_new_reply_is_open_until_a_tagged_event_files_it(config: GmailConfig):
    cache_thread(config, "t1", message("aa01", f"Ann <{ANN}>", ME, "Re: Hi", "Yes, Friday."))
    assert open_ids(config) == ["aa01"]
    log(config, f"- {TODAY.isoformat()} | email-in | {rid('P', 1)} | Ann agrees to Friday [gmail aa01]")
    assert open_ids(config) == []


def with_until(config: GmailConfig, until: dt.datetime) -> GmailConfig:
    return GmailConfig(config.store, config.papers, config.conf_dir, ME, {}, until.isoformat(timespec="minutes"))


def open_ids_until(config: GmailConfig, until: dt.datetime) -> list[str]:
    return open_ids(with_until(config, until))


def test_an_untagged_event_files_older_mail_of_that_day_about_the_sender(config: GmailConfig):
    yesterday = TODAY - dt.timedelta(days=1)
    cache_thread(
        config,
        "t1",
        message("aa01", ANN, ME, "Re: Hi", "first", day=yesterday),
        message("aa02", ANN, ME, "Re: Hi", "second", day=TODAY),
    )
    log(config, f"- {yesterday.isoformat()} | email-in | {rid('P', 1)} | Ann wrote")
    assert open_ids_until(config, dt.datetime.combine(TODAY, dt.time(23))) == ["aa02"]


def test_an_untagged_event_never_hides_mail_newer_than_the_cutoff(config: GmailConfig):
    cache_thread(
        config,
        "t1",
        message("aa01", ANN, ME, "Re: Hi", "morning", hour=9),
        message("aa02", ANN, ME, "Re: Hi", "evening", hour=20),
    )
    log(config, f"- {TODAY.isoformat()} | email-in | {rid('P', 1)} | Ann wrote in the morning")
    assert open_ids_until(config, dt.datetime.combine(TODAY, dt.time(12))) == ["aa02"]
    assert open_ids(config) == ["aa01", "aa02"]  # no cutoff configured: only tags file mail


def test_a_bounce_belongs_to_the_people_of_its_thread(config: GmailConfig):
    cache_thread(
        config,
        "t1",
        message("aa01", ME, ANN, "Hi", "Hello", hour=9),
        message("aa02", "mailer-daemon@example.net", ME, "Delivery Status Notification (Failure)", "x", hour=10),
    )
    bounce = next(m for m in mailbox.mails(config.cache, gmail.scope(config).book) if m.id == "aa02")
    assert bounce.people == {rid("P", 1)}


def test_an_event_of_the_other_direction_does_not_count(config: GmailConfig):
    cache_thread(config, "t1", message("aa01", ANN, ME, "Re: Hi", "answer"))
    log(config, f"- {TODAY.isoformat()} | email-out | {rid('P', 1)} | Daniil wrote to Ann")
    assert open_ids_until(config, dt.datetime.combine(TODAY, dt.time(23))) == ["aa01"]


def test_sent_mail_held_by_a_draft_is_not_open(config: GmailConfig):
    write_email_draft(config, 1, status="sent", subject="Hi", body="Hello", extra="gmail_id: bb01\n")
    cache_thread(config, "t1", message("bb01", ME, ANN, "Hi", "Hello"), message("bb02", ME, ANN, "Hi", "More"))
    assert open_ids(config) == ["bb02"]


def test_gmail_drafts_and_old_mail_are_left_out(config: GmailConfig):
    cache_thread(
        config,
        "t1",
        message("cc01", ME, ANN, "Hi", "unsent", labels=("DRAFT",)),
        message("cc02", ANN, ME, "Hi", "old", day=TODAY - dt.timedelta(days=90)),
    )
    assert open_ids(config) == []


def test_describe_withholds_the_text_of_a_blind_thread(config: GmailConfig):
    cache_thread(config, "t1", message("aa01", ANN, ME, "Re: Data", "values 1.2 3.4"))
    blind = GmailConfig(config.store, config.papers, config.conf_dir, ME, {"t1": "plan not committed"})
    box = gmail.inbox(blind, gmail.scope(blind), 30)
    text = "\n".join(gmail.describe(blind, box, box.items[0], 700))
    assert "withheld: plan not committed" in text
    assert "1.2" not in text


def test_describe_says_whether_daniil_replied(config: GmailConfig):
    cache_thread(
        config,
        "t1",
        message("aa01", ANN, ME, "Re: Hi", "Question?", hour=9),
        message("aa02", ME, ANN, "Re: Hi", "Answer", hour=10),
    )
    box = gmail.inbox(config, gmail.scope(config), 30)
    first = next(m for m in box.items if m.id == "aa01")
    assert "you replied" in "\n".join(gmail.describe(config, box, first, 700))


@pytest.mark.parametrize(
    ("sender", "subject", "headers", "mime", "kind"),
    [
        ("mailer-daemon@example.com", "Failure", (), "text/plain", "bounce"),
        (ANN, "Automatic reply: Hi", (), "text/plain", "auto-reply"),
        (ANN, "Hi", (("Auto-Submitted", "auto-replied"),), "text/plain", "auto-reply"),
        (ANN, "Call", (), "text/calendar", "invite"),
        (ANN, "Hi", (("Auto-Submitted", "no"),), "text/plain", ""),
    ],
)
def test_kind_of(config: GmailConfig, sender, subject, headers, mime, kind):
    cache_thread(config, "t1", message("aa01", sender, ME, subject, "x", extra_headers=headers, mime=mime))
    (mail,) = mailbox.mails(config.cache, gmail.scope(config).book)
    assert mailbox.kind_of(mail) == kind


def done_args(messages: str, refs: str, summary: str) -> argparse.Namespace:
    return argparse.Namespace(messages=messages, refs=refs, summary=summary, type=None, date=None)


def test_done_writes_a_tagged_event(config: GmailConfig, monkeypatch: pytest.MonkeyPatch):
    cache_thread(config, "t1", message("aa01", ANN, ME, "Re: Hi", "Yes"))
    monkeypatch.setattr(GmailConfig, "from_workspace", classmethod(lambda cls, ws: config))
    gmail.cmd_done(done_args("aa01", rid("P", 1), "Ann agrees"), None)
    events = config.store.load().events
    assert events[-1].type == "email-in" and events[-1].summary == "Ann agrees [gmail aa01]"
    assert open_ids(config) == []


def test_done_refuses_ids_not_in_the_cache(config: GmailConfig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(GmailConfig, "from_workspace", classmethod(lambda cls, ws: config))
    with pytest.raises(ChannelError, match="not in the local cache"):
        gmail.cmd_done(done_args("ffff00", "-", "x"), None)


def test_status_lines_count_open_mail_without_network(config: GmailConfig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(GmailConfig, "from_workspace", classmethod(lambda cls, ws: config))
    assert gmail.status_lines(None, config.store.load(), TODAY) == [
        "never checked on this machine: run `uo gmail inbox`"
    ]
    cache_thread(config, "t1", message("aa01", ANN, ME, "Re: Hi", "Yes"))
    config.cache.mark_checked(30, 1, 1)
    lines = gmail.status_lines(None, config.store.load(), TODAY)
    assert lines[0].startswith("1 received, 0 sent not in the records")
    assert lines[1].endswith(f"from {rid('P', 1)}: Re: Hi")


# sync ------------------------------------------------------------------------------------------


def run_sync(config: GmailConfig, monkeypatch: pytest.MonkeyPatch, *ids: str) -> int:
    monkeypatch.setattr(GmailConfig, "from_workspace", classmethod(lambda cls, ws: config))
    monkeypatch.setattr(gmail, "access_token", lambda cfg, read=False: "token")
    monkeypatch.setattr(mailbox, "refresh", lambda *args, **kwargs: (0, 0))
    return gmail.cmd_sync(argparse.Namespace(ids=list(ids)), None)


def test_sync_marks_a_draft_found_in_the_sent_mail_as_sent(config: GmailConfig, monkeypatch: pytest.MonkeyPatch):
    path = write_email_draft(config, 1, status="draft", subject="Hi", body="Hello Ann,\n\nCould we talk on Friday?")
    cache_thread(config, "t1", message("bb01", ME, ANN, "Hi", "Hello Ann,\n\nCould we talk Friday?"))
    assert run_sync(config, monkeypatch) == 0
    meta = frontmatter.parse(path.read_text(encoding="utf-8"))
    assert (meta["status"], meta["gmail_id"]) == ("sent", "bb01")
    assert "Could we talk Friday?" in path.read_text(encoding="utf-8")


def test_sync_leaves_an_unsent_draft_alone_when_the_text_differs(config: GmailConfig, monkeypatch: pytest.MonkeyPatch):
    path = write_email_draft(config, 1, status="approved", subject="Re: Hi", body="A long answer about the plan.")
    cache_thread(config, "t1", message("bb01", ME, ANN, "Re: Hi", "Thanks, see you then."))
    before = path.read_text(encoding="utf-8")
    assert run_sync(config, monkeypatch) == 0
    assert path.read_text(encoding="utf-8") == before


def test_sync_records_the_gmail_id_and_skips_it_next_time(config: GmailConfig, monkeypatch: pytest.MonkeyPatch, capsys):
    path = write_email_draft(config, 1, status="sent", subject="Hi", body="Hello")
    cache_thread(config, "t1", message("bb01", ME, ANN, "Hi", "Hello"))
    run_sync(config, monkeypatch)
    assert frontmatter.parse(path.read_text(encoding="utf-8"))["gmail_id"] == "bb01"
    capsys.readouterr()
    run_sync(config, monkeypatch)
    assert "nothing to sync (1 sent draft(s) already hold their Gmail ID)" in capsys.readouterr().out


def test_two_drafts_never_claim_the_same_message(config: GmailConfig, monkeypatch: pytest.MonkeyPatch):
    first = write_email_draft(config, 1, status="sent", subject="Re: Hi", body="Thanks, Friday works.")
    second = write_email_draft(config, 2, status="draft", subject="Re: Hi", body="Thanks, Friday works.")
    cache_thread(config, "t1", message("bb01", ME, ANN, "Re: Hi", "Thanks, Friday works."))
    run_sync(config, monkeypatch)
    assert frontmatter.parse(first.read_text(encoding="utf-8"))["gmail_id"] == "bb01"
    assert frontmatter.parse(second.read_text(encoding="utf-8"))["status"] == "draft"
