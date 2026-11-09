from __future__ import annotations

from pathlib import Path

from conftest import DRAFT, PERSON, TASK, commit, git, rid, write_record

from unfloat_os import check, frontmatter, guard, studies
from unfloat_os.workspace import RepoConfig


def messages(report) -> list[str]:
    return [f"{i.level}: {i.message}" for i in report.issues]


def test_valid_records_pass(ws):
    write_record(ws.ops, "people", rid("P", 1), "someone", PERSON)
    write_record(ws.ops, "tasks", "T-001", "thing", {**TASK, "people": [rid("P", 1)]})
    write_record(ws.ops, "drafts", rid("DR", 1), "note", {**DRAFT, "task": "T-001"})
    report, summary = check.check_records(ws)
    assert messages(report) == []
    assert summary.startswith("ok: 1 people, 1 tasks, 1 drafts, 1 decisions")


def test_record_rules_report_errors_and_warnings(ws):
    write_record(ws.ops, "tasks", "T-001", "waiting", {**TASK, "status": "waiting"})
    write_record(ws.ops, "tasks", "T-002", "done", {**TASK, "status": "done"})
    write_record(ws.ops, "tasks", "T-003", "open", {**TASK, "closed": "2026-11-05"})
    write_record(ws.ops, "tasks", "T-004", "owner", {**TASK, "owner": "somebody"})
    write_record(ws.ops, "tasks", "T-005", "bad", {**TASK, "milestone": "moon", "colour": "red"})
    write_record(ws.ops, "people", rid("P", 1), "p", {**PERSON, "status": "replied", "next_task": "T-099"})
    found = messages(check.check_records(ws)[0])
    assert "error: status 'waiting' needs 'waiting_on'" in found
    assert "error: status 'done' needs a 'closed' date" in found
    assert "warning: open task has a 'closed' date" in found
    assert "error: owner 'somebody' must be founder, claude, an existing P-###" in found
    assert (
        "error: milestone 'moon' not in map, composition, clearance, rebuild, animal, measurement, community, "
        "funding, ops, parked" in found
    )
    assert "error: unknown field(s): colour" in found
    assert "error: next_task 'T-099' must be an existing T-###" in found
    assert "warning: status implies contact but last_contact is empty" in found


def test_log_and_decisions_are_checked(ws):
    log = ws.ops / "records/log/2026-11.md"
    log.write_text(log.read_text() + "- 2026-11-09 | note | T-404 | missing task\n- 2026-11-09 | gossip | - | x\n")
    decisions = ws.ops / "records/decisions.md"
    decisions.write_text(decisions.read_text() + "\n## D-001 | 2026-11-02 | Again\n")
    found = messages(check.check_records(ws)[0])
    assert "error: T-404 does not exist" in found
    assert "error: unknown type 'gossip'" in found
    assert "error: duplicate D-001" in found
    assert "error: D-001 is out of order" in found


def test_schema_doc_must_match_the_schema(ws):
    doc = ws.ops / "SCHEMA.md"
    stale = "".join(
        f"<!-- uo:{name} -->\nold\n<!-- /uo -->\n" for name in ("person", "task", "draft", "study", "log-types")
    )
    doc.write_text(stale)
    assert messages(check.check_schema_doc(ws)) == ["error: generated tables are out of date; run `uo schema`"]
    doc.write_text(check.fill_schema_blocks(stale))
    assert messages(check.check_schema_doc(ws)) == []
    assert "| `owner` | `founder` or `claude` or `P-###` | required; IDs must exist |" in doc.read_text()


def test_links_must_resolve_and_open_repos_must_not_link_out(ws):
    research = ws.members["research"].path
    (research / "README.md").write_text(
        "[ok](notes.md) [missing](gone.md) `[in code](nowhere.md)` [out](../ops/policy.md) "
        "[web](https://example.org/x.md) [anchor](#top)\n```\n[fenced](nowhere.md)\n```\n"
    )
    (research / "notes.md").write_text("notes\n")
    (ws.ops / "policy.md").write_text("[research](../research/notes.md) [bad](../research/none.md)\n")
    open_report = messages(check.check_repo(RepoConfig.load(research)))
    assert open_report == [
        "error: broken link 'gone.md'",
        "error: link '../ops/policy.md' leaves the repository; use a URL",
    ]
    private_report = messages(check.check_repo(RepoConfig.load(ws.ops)))
    assert private_report == ["error: broken link '../research/none.md'"]


def test_open_repositories_carry_no_person_or_draft_ids(ws):
    research = ws.members["research"].path
    (research / "NOTE.md").write_text(f"Asked {rid('P', 40)} via {rid('DR', 33)}; task T-024 and D-013 are fine.\n")
    (ws.ops / "NOTE.md").write_text(f"{rid('P', 40)} is fine here.\n")
    assert messages(check.check_visibility(RepoConfig.load(research))) == [
        f"error: private record ID {rid('P', 40)} in an open repository",
        f"error: private record ID {rid('DR', 33)} in an open repository",
    ]
    assert messages(check.check_visibility(RepoConfig.load(ws.ops))) == []


def _study(folder: Path, **meta) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    base = {
        "id": "A9",
        "title": "Q",
        "status": "result",
        "task": "",
        "prereg": "PREREG.md",
        "result": "RESULT.md",
        "review": "",
        "verdict": "X",
    }
    (folder / "STUDY.md").write_text(frontmatter.render({**base, **meta}, "\nWhy.\n"))


def test_preregistration_must_reach_git_before_the_result(ws):
    research = ws.members["research"].path
    good, bad, same = (research / "analysis" / name for name in ("a9_good", "a9_bad", "a9_same"))
    for folder in (good, bad, same):
        _study(folder)
    (good / "PREREG.md").write_text("plan\n")
    (bad / "RESULT.md").write_text("result\n")
    (same / "PREREG.md").write_text("plan\n")
    (same / "RESULT.md").write_text("result\n")
    commit(research, "first", ".")
    (good / "RESULT.md").write_text("result\n")
    (bad / "PREREG.md").write_text("plan written later\n")
    commit(research, "second", ".")
    found = messages(studies.check(RepoConfig.load(research)))
    assert found == [
        "error: analysis/a9_bad/RESULT.md was committed before the pre-registration",
        "warning: analysis/a9_same/RESULT.md and the pre-registration were added in the same commit; "
        "order not provable",
    ]


def test_study_index_rules(ws):
    research = ws.members["research"].path
    (research / "analysis" / "a8_noindex").mkdir(parents=True)
    _study(research / "analysis" / "a9_reviewed", status="reviewed", prereg="none", result="RESULT.md")
    (research / "analysis" / "a9_reviewed" / "RESULT.md").write_text("r\n")
    found = messages(studies.check(RepoConfig.load(research)))
    assert found == ["error: missing STUDY.md", "error: status 'reviewed' needs a review file"]


def test_guard_refuses_data_large_files_and_device_names(ws):
    research = ws.members["research"].path
    (research / "scan.png").write_bytes(b"x")
    (research / "big.csv").write_bytes(b"x" * 2_000_001)
    # Built from parts so this file itself does not trip the guard's staged-text check.
    (research / "_".join(["Smith", "123456", "20261108101010", "OD", "1.txt"])).write_text("x")
    (research / "ok.csv").write_text("a,b\n")
    git(research, "add", ".")
    found = sorted(messages(guard.check_staged(RepoConfig.load(research))))
    assert found == [
        "error: 2000001 bytes (limit 2000000)",
        "error: device file name with patient identifiers",
        "error: looks like data, an image or a document; it belongs in the data folder",
    ]


def test_guard_allowances_come_from_the_repository_config(ws):
    research = ws.members["research"].path
    (research / ".unfloat.toml").write_text('visibility = "public"\n[guard]\nallow = ["brand/*.png"]\n')
    (research / "brand").mkdir()
    (research / "brand" / "icon.png").write_bytes(b"x")
    (research / "photo.png").write_bytes(b"x")
    git(research, "add", ".")
    found = messages(guard.check_staged(RepoConfig.load(research)))
    assert found == ["error: looks like data, an image or a document; it belongs in the data folder"]
