import json
import subprocess
import sys
import time

import kitlib
from conftest import BIN, edit_payload, read_state, run_hook

CLEAN = "def f():\n    return 1\n"
CLOSING = (
    "These were introduced by your edit. Fix them in the code; don't add suppression comments or change lint config."
)


def test_legacy_file_keeps_its_old_violations_without_error(ruff_repo, edit):
    legacy = "import os\nx = {  'a':1 }\n\n\ndef f():\n    return 1\n"
    (ruff_repo / "legacy.py").write_text(legacy)
    result = edit(ruff_repo, "legacy.py", legacy + "\n\ndef g():\n    return 2\n")
    assert (result.returncode, result.stderr) == (0, "")
    content = (ruff_repo / "legacy.py").read_text()
    assert content.startswith("import os\nx = {  'a':1 }\n"), "a dirty legacy file must not be fixed or reformatted"


def test_new_violation_is_fed_back_with_its_line(ruff_repo, edit):
    (ruff_repo / "app.py").write_text(CLEAN)
    result = edit(ruff_repo, "app.py", CLEAN + "\n\ndef g():\n    return undefined_name\n")
    assert result.returncode == 2
    assert "kit: your edit to app.py introduced lint problems:" in result.stderr
    assert "- ruff F821: Undefined name `undefined_name` (line 6)" in result.stderr
    assert result.stderr.endswith(CLOSING)


def test_only_the_new_occurrence_counts_in_a_dirty_file(ruff_repo, edit):
    dirty = "def f():\n    return missing_one\n"
    (ruff_repo / "app.py").write_text(dirty)
    result = edit(ruff_repo, "app.py", dirty + "\n\ndef g():\n    return missing_two\n")
    assert result.returncode == 2
    assert "missing_two" in result.stderr
    assert "missing_one" not in result.stderr


def test_fixable_new_violation_is_autofixed_on_a_clean_file(ruff_repo, edit):
    (ruff_repo / "app.py").write_text(CLEAN)
    unsorted = "import sys\nimport os\n\n\ndef f():\n    return os.sep, sys.path\n"
    result = edit(ruff_repo, "app.py", unsorted)
    assert (result.returncode, result.stderr) == (0, "")
    assert (ruff_repo / "app.py").read_text() == "import os\nimport sys\n\n\ndef f():\n    return os.sep, sys.path\n"


def test_an_import_added_before_its_use_survives_the_autofix(ruff_repo, edit):
    (ruff_repo / "app.py").write_text(CLEAN)
    result = edit(ruff_repo, "app.py", "import json\n\n\n" + CLEAN)
    assert result.returncode == 2
    assert "- ruff F401: `json` imported but unused" in result.stderr
    assert (ruff_repo / "app.py").read_text() == "import json\n\n\n" + CLEAN


def test_a_noqa_inside_a_string_is_not_a_suppression(ruff_repo, edit):
    (ruff_repo / "app.py").write_text(CLEAN)
    result = edit(ruff_repo, "app.py", CLEAN + '\n\nDOC = "add # noqa only when a rule is wrong"\n')
    assert (result.returncode, result.stderr) == (0, "")


def test_new_noqa_is_counted_as_a_violation(ruff_repo, edit):
    (ruff_repo / "app.py").write_text(CLEAN)
    result = edit(ruff_repo, "app.py", CLEAN + "\n\nvalue = undefined_name  # noqa: F821\n")
    assert result.returncode == 2
    assert '- suppression comment "# noqa" added (line 5)' in result.stderr
    assert "F821" not in result.stderr.split("\n")[1]


def test_formatter_is_applied_to_a_clean_file(ruff_repo, edit):
    (ruff_repo / "app.py").write_text(CLEAN)
    result = edit(ruff_repo, "app.py", CLEAN + "\n\ny = {  'a':1 }\n")
    assert (result.returncode, result.stderr) == (0, "")
    assert (ruff_repo / "app.py").read_text() == CLEAN + '\n\ny = {"a": 1}\n'


def test_formatter_is_skipped_on_a_dirty_file(ruff_repo, edit):
    dirty = "x = {  'a':1 }\n"
    (ruff_repo / "app.py").write_text(dirty)
    result = edit(ruff_repo, "app.py", dirty + "y = {  'b':2 }\n")
    assert (result.returncode, result.stderr) == (0, "")
    assert (ruff_repo / "app.py").read_text() == dirty + "y = {  'b':2 }\n"


def test_a_new_file_gets_a_clean_baseline(ruff_repo, edit, data_dir):
    result = edit(ruff_repo, "pkg/new_module.py", "def g():\n    return undefined_name\n", tool="Write")
    assert result.returncode == 2
    assert "F821" in result.stderr
    entry = read_state(data_dir)["edits"][str(ruff_repo / "pkg" / "new_module.py")]
    assert entry["before"] == {"lint": {}, "clean_format": True, "clean_lint_fixable": True}


def test_no_linter_configured_does_nothing(make_repo, data_dir, edit):
    repo = make_repo(files={"app.py": "import os\n"})
    result = edit(repo, "app.py", "import os\nx = {  'a':1 }\nprint(undefined)  # noqa\n")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert (repo / "app.py").read_text() == "import os\nx = {  'a':1 }\nprint(undefined)  # noqa\n"
    assert "before" not in read_state(data_dir)["edits"][str(repo / "app.py")]


def test_exempt_paths_are_not_linted(ruff_repo, edit):
    for rel in ("app/migrations/0001_initial.py", ".claude/hooks/helper.py"):
        result = edit(ruff_repo, rel, "value = undefined_name\n")
        assert (result.returncode, result.stderr) == (0, ""), rel


def test_state_records_every_edit_with_a_sequence(ruff_repo, edit, data_dir):
    (ruff_repo / "a.py").write_text(CLEAN)
    edit(ruff_repo, "a.py", CLEAN + "\n\nA = 1\n")
    edit(ruff_repo, "app/migrations/0002.py", "B = 2\n")
    edit(ruff_repo, "a.py", CLEAN + "\n\nA = 2\n")
    state = read_state(data_dir)
    assert state["edit_seq"] == 3
    assert state["edits"][str(ruff_repo / "a.py")]["seq"] == 3
    assert state["edits"][str(ruff_repo / "app" / "migrations" / "0002.py")]["seq"] == 2
    assert set(state["edits"][str(ruff_repo / "a.py")]["before"]) == {"lint", "clean_format", "clean_lint_fixable"}


def test_subagent_edits_use_their_own_state_file(ruff_repo, edit, data_dir):
    (ruff_repo / "a.py").write_text(CLEAN)
    edit(ruff_repo, "a.py", CLEAN + "\n\nA = 1\n", agent_id="agent-42")
    assert read_state(data_dir, agent="agent-42")["edit_seq"] == 1
    assert not (data_dir / "state" / "s1" / "main.json").exists()


def test_a_held_state_lock_makes_the_hook_wait(ruff_repo, data_dir):
    (ruff_repo / "a.py").write_text(CLEAN)
    payload = {**edit_payload(ruff_repo, "a.py"), "hook_event_name": "PostToolUse"}
    with kitlib.State(payload):
        proc = subprocess.Popen(
            [sys.executable, str(BIN / "lint_edited_file.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write(json.dumps(payload))
        proc.stdin.close()
        time.sleep(0.5)
        assert proc.poll() is None, "the hook ran while another process held the state lock"
    assert proc.wait(timeout=30) == 0
    assert read_state(data_dir)["edit_seq"] == 1


def test_kit_bypass_disables_the_hooks(ruff_repo, edit, data_dir, monkeypatch):
    (ruff_repo / "app.py").write_text(CLEAN)
    monkeypatch.setenv("KIT_BYPASS", "all")
    result = edit(ruff_repo, "app.py", CLEAN + "\n\nvalue = undefined_name  # noqa\n")
    assert (result.returncode, result.stderr) == (0, "")
    assert not (data_dir / "state").exists()
    monkeypatch.setenv("KIT_BYPASS", "scan_secrets,lint_edited_file")
    result = edit(ruff_repo, "app.py", CLEAN + "\n\ndef g():\n    return undefined_name\n")
    assert (result.returncode, result.stderr) == (0, "")


def test_bad_hook_input_fails_open_with_a_warning(data_dir):
    for script in ("snapshot_before_edit", "lint_edited_file"):
        result = run_hook(script, stdin="{not json")
        assert result.returncode == 0
        assert f"kit: {script} hook failed (JSONDecodeError" in json.loads(result.stdout)["systemMessage"]


def test_a_broken_linter_config_fails_open(make_repo, ruff_bin, data_dir):
    repo = make_repo(files={"pyproject.toml": '[tool.ruff]\nline-length = "long"\n', "app.py": CLEAN})
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "ruff").symlink_to(ruff_bin)
    result = run_hook("snapshot_before_edit", edit_payload(repo, "app.py"))
    assert result.returncode == 0
    message = json.loads(result.stdout)["systemMessage"]
    assert "kit: snapshot_before_edit hook failed (RuntimeError: ruff check failed" in message
