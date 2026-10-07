import datetime
import json
import os
import re
import stat

import pytest
import run_hook
from hookrun import base_payload, launch, registered, write

HANDOFF = ".claude/HANDOFF.md"
NOTES_LIMIT_CHARS = 6000
CLAUDE_CONTEXT_CAP_CHARS = 10_000
ENTRY_HEADER = re.compile(r"^## (\S+) \(trigger: (\w+)\)$", re.MULTILINE)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch, data_dir):
    """A throwaway HOME, so no test touches the real ~/.claude."""
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return directory


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


def session_start(cwd, source: str, project_dir=None):
    env = {"CLAUDE_PROJECT_DIR": str(project_dir)} if project_dir else None
    return launch("inject_handoff", base_payload(cwd, "SessionStart") | {"source": source}, env=env)


def injected(result) -> str:
    assert result.returncode == 0, result
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    return output["additionalContext"]


def compact(tmp_path, session_id="s1", **fields):
    payload = base_payload(tmp_path, "PostCompact", session=session_id) | {"trigger": "auto"} | fields
    return launch("log_compact", payload)


def log_path(home, session_id="s1"):
    return home / ".claude" / "compact-log" / f"{session_id}.md"


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_handoff_notes_are_injected_after_a_compaction_or_resume(project, source):
    handoff = write(project, HANDOFF, "Next: wire the refund webhook.\nOpen question: idempotency key.\n")
    context = injected(session_start(project, source, project_dir=project))
    header, _, notes = context.partition("\n")
    assert header.startswith(f"Handoff notes from {handoff}, written before a compaction or resume (last saved ")
    assert notes == "Next: wire the refund webhook.\nOpen question: idempotency key."


@pytest.mark.parametrize("source", ["startup", "clear", "fork"])
def test_handoff_notes_are_not_injected_on_other_starts(project, source):
    write(project, HANDOFF, "Next: wire the refund webhook.\n")
    result = session_start(project, source, project_dir=project)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize("content", [None, "", "  \n\n"])
def test_a_missing_or_empty_handoff_file_injects_nothing(project, content):
    if content is not None:
        write(project, HANDOFF, content)
    result = session_start(project, "compact", project_dir=project)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_oversized_handoff_notes_are_capped_with_a_pointer_to_the_file(project):
    handoff = write(project, HANDOFF, "x" * (NOTES_LIMIT_CHARS * 3))
    context = injected(session_start(project, "compact", project_dir=project))
    _, _, notes = context.partition("\n")
    assert len(notes) == NOTES_LIMIT_CHARS
    assert notes.endswith(f"\n[truncated; the rest is in {handoff}]")
    assert notes.startswith("x" * 100)
    assert len(context) < CLAUDE_CONTEXT_CAP_CHARS


def test_notes_at_the_cap_are_not_truncated(project):
    write(project, HANDOFF, "x" * NOTES_LIMIT_CHARS)
    context = injected(session_start(project, "compact", project_dir=project))
    assert context.partition("\n")[2] == "x" * NOTES_LIMIT_CHARS


def test_the_session_project_dir_wins_over_cwd(project):
    handoff = write(project, HANDOFF, "notes at the session root\n")
    moved_into = project / "backend"
    write(moved_into, HANDOFF, "notes in a subdirectory\n")
    context = injected(session_start(moved_into, "compact", project_dir=project))
    assert str(handoff) in context
    assert context.endswith("notes at the session root")


def test_cwd_is_used_when_the_project_dir_is_unset(project):
    write(project, HANDOFF, "notes from cwd\n")
    assert injected(session_start(project, "resume")).endswith("notes from cwd")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files regardless of permissions")
def test_an_unreadable_handoff_file_fails_open(project):
    handoff = write(project, HANDOFF, "secret plan\n")
    handoff.chmod(0)
    try:
        result = session_start(project, "compact", project_dir=project)
    finally:
        handoff.chmod(0o600)
    assert result.returncode == 0
    assert json.loads(result.stdout)["systemMessage"].startswith("kit: inject_handoff hook failed (PermissionError")


def test_a_compaction_summary_is_appended_to_the_session_log(tmp_path, home):
    result = compact(
        tmp_path, session_id="abc-123_X", compact_summary="  Kept: refund webhook plan.\n", trigger="manual"
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    log = log_path(home, "abc-123_X")
    text = log.read_text()
    header = ENTRY_HEADER.match(text)
    assert header and header.group(2) == "manual"
    assert datetime.datetime.fromisoformat(header.group(1)).tzinfo is not None
    assert text == f"{header.group(0)}\n\nKept: refund webhook plan.\n\n"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.parent.stat().st_mode) == 0o700


def test_each_compaction_appends_its_own_entry(tmp_path, home):
    compact(tmp_path, compact_summary="First summary.", trigger="auto")
    compact(tmp_path, compact_summary="Second summary.", trigger="manual")
    text = log_path(home).read_text()
    assert [trigger for _, trigger in ENTRY_HEADER.findall(text)] == ["auto", "manual"]
    assert text.index("First summary.") < text.index("Second summary.")


def test_a_missing_trigger_is_logged_as_unknown(tmp_path, home):
    payload = base_payload(tmp_path, "PostCompact") | {"compact_summary": "Kept it."}
    assert launch("log_compact", payload).returncode == 0
    assert ENTRY_HEADER.match(log_path(home).read_text()).group(2) == "unknown"


@pytest.mark.parametrize("session_id", ["../x", "a/b", ".hidden", "", None, 42])
def test_an_unsafe_session_id_is_skipped(tmp_path, home, session_id):
    result = compact(tmp_path, session_id=session_id, compact_summary="Kept it.")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (home / ".claude").exists()
    assert not (tmp_path / "x.md").exists()


@pytest.mark.parametrize("summary", [None, "", " \n", 7])
def test_a_missing_or_empty_summary_is_skipped(tmp_path, home, summary):
    fields = {} if summary is None else {"compact_summary": summary}
    result = compact(tmp_path, **fields)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (home / ".claude").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes regardless of permissions")
def test_an_unwritable_log_dir_fails_open(tmp_path, home):
    log_dir = log_path(home).parent
    log_dir.mkdir(parents=True)
    log_dir.chmod(0o500)
    try:
        result = compact(tmp_path, compact_summary="Kept it.")
    finally:
        log_dir.chmod(0o700)
    assert result.returncode == 0
    assert json.loads(result.stdout)["systemMessage"].startswith("kit: log_compact hook failed (PermissionError")
    assert not log_path(home).exists()


def test_the_helpers_run_after_a_compaction_or_resume_and_fail_open():
    assert registered("SessionStart")["resume|compact"] == ["inject_handoff"]
    assert "log_compact" in registered("PostCompact")[None]
    assert not {"inject_handoff", "log_compact"} & run_hook.FAIL_CLOSED_HOOKS
