import json
import re
import shutil

import pytest
import run_hook
from hookrun import BIN, HOOKS_JSON, launch

LAUNCHER_ARG = "${CLAUDE_PLUGIN_ROOT}/hooks/bin/run_hook.py"


@pytest.fixture(autouse=True)
def claude_config(tmp_path, monkeypatch):
    """Keeps real Claude Code settings files and plugin data out of every test."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("KIT_BYPASS", raising=False)
    return tmp_path / "claude-config"


@pytest.fixture
def scratch_bin(tmp_path):
    """A copy of the launcher and kitlib next to hook scripts a test writes."""
    directory = tmp_path / "bin"
    directory.mkdir()
    for name in ("run_hook.py", "kitlib.py"):
        shutil.copy(BIN / name, directory / name)

    def add(name: str, source: str):
        (directory / f"{name}.py").write_text(source)
        return directory / "run_hook.py"

    return add


def test_a_syntax_error_in_a_fail_closed_script_blocks(scratch_bin):
    launcher = scratch_bin("stop_gate", "def main(:\n    pass\n")
    result = launch("stop_gate", launcher=launcher)
    assert result.returncode == 2
    assert result.stderr.startswith("kit: stop_gate hook failed (SyntaxError: ")
    assert result.stderr.endswith("Fix the hook, or relaunch Claude Code with KIT_BYPASS=stop_gate.")
    assert result.stdout == ""


def test_a_missing_fail_open_script_warns_and_continues(data_dir):
    result = launch("no_such_hook")
    assert result.returncode == 0
    message = json.loads(result.stdout)["systemMessage"]
    assert message.startswith("kit: no_such_hook hook failed: FileNotFoundError")


def test_a_missing_fail_closed_script_blocks(scratch_bin):
    launcher = scratch_bin("unrelated", "def main():\n    pass\n")
    result = launch("guard_commit", launcher=launcher)
    assert result.returncode == 2
    assert result.stderr.startswith("kit: guard_commit hook failed (FileNotFoundError")


def test_a_broken_kitlib_fails_closed_for_a_guard(scratch_bin, tmp_path):
    launcher = scratch_bin("guard_module_skill", "import kitlib\n\nFAIL_CLOSED = True\n\ndef main():\n    pass\n")
    (tmp_path / "bin" / "kitlib.py").write_text("def broken(:\n")
    result = launch("guard_module_skill", launcher=launcher)
    assert result.returncode == 2
    assert "kit: guard_module_skill hook failed (SyntaxError" in result.stderr


def test_system_exit_from_the_script_passes_through(scratch_bin):
    launcher = scratch_bin(
        "lint_edited_file",
        "import sys\n\nFAIL_CLOSED = False\n\n"
        "def main():\n    sys.stderr.write('new lint problems')\n    sys.exit(2)\n",
    )
    result = launch("lint_edited_file", launcher=launcher)
    assert (result.returncode, result.stdout, result.stderr) == (2, "", "new lint problems")
    launcher = scratch_bin(
        "stop_gate", 'import sys\n\ndef main():\n    print(\'{"decision": "block"}\')\n    sys.exit(0)\n'
    )
    result = launch("stop_gate", launcher=launcher)
    assert (result.returncode, result.stdout, result.stderr) == (0, '{"decision": "block"}\n', "")


def test_a_script_declaring_fail_closed_is_honoured(scratch_bin):
    launcher = scratch_bin("custom_guard", "FAIL_CLOSED = True\n\ndef main():\n    raise KeyError('tool_input')\n")
    result = launch("custom_guard", launcher=launcher)
    assert result.returncode == 2
    assert result.stderr.startswith("kit: custom_guard hook failed (KeyError: 'tool_input')")


def test_an_error_in_a_fail_open_script_warns(scratch_bin):
    launcher = scratch_bin("record_skill_seen", "FAIL_CLOSED = False\n\ndef main():\n    raise OSError('disk full')\n")
    result = launch("record_skill_seen", launcher=launcher)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"systemMessage": "kit: record_skill_seen hook failed: OSError: disk full"}


def test_kit_bypass_skips_the_hook_before_importing_it(scratch_bin):
    launcher = scratch_bin("stop_gate", "def main(:\n")
    for bypass in ("stop_gate", "guard_commit, stop_gate", "all"):
        result = launch("stop_gate", launcher=launcher, env={"KIT_BYPASS": bypass})
        assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), bypass


@pytest.mark.parametrize("event", ["Stop", "SubagentStop"])
def test_a_broken_stop_hook_blocks_a_stop_only_once(scratch_bin, event):
    launcher = scratch_bin("stop_gate", "def main(:\n")
    first = launch("stop_gate", {"hook_event_name": event, "stop_hook_active": False}, launcher=launcher)
    assert (first.returncode, first.stdout) == (2, "")
    again = launch("stop_gate", {"hook_event_name": event, "stop_hook_active": True}, launcher=launcher)
    assert (again.returncode, again.stderr) == (0, "")
    message = json.loads(again.stdout)["systemMessage"]
    assert message.startswith("kit: stop_gate hook failed again (SyntaxError: ")
    assert message.endswith(
        "so Claude may stop this time. Fix the hook, or relaunch Claude Code with KIT_BYPASS=stop_gate."
    )


def write_settings(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings if isinstance(settings, str) else json.dumps(settings))


QUIET_HOOK = "from pathlib import Path\n\ndef main():\n    Path({ran!r}).write_text('ran')\n"
BLOCKING_GATE = (
    "import kitlib\n\nFAIL_CLOSED = True\n\n@kitlib.hook_main('stop_gate', fail_closed=FAIL_CLOSED)\n"
    "def main():\n    kitlib.stop_block('journeys are stale')\n"
)
SETTINGS_NOTICE = (
    "kit: KIT_BYPASS is set in a Claude Code settings file, which Claude can edit, so the kit ignored it. "
    "Remove it there; to skip kit hooks, set KIT_BYPASS in the shell that launches Claude Code."
)


@pytest.mark.parametrize("where", ["project local", "project", "user", "CLAUDE_PROJECT_DIR"])
def test_a_bypass_from_a_settings_file_is_ignored_and_reported_once(scratch_bin, tmp_path, claude_config, where):
    project = tmp_path / "project"
    settings_file = {
        "project local": project / ".claude" / "settings.local.json",
        "project": project / ".claude" / "settings.json",
        "user": claude_config / "settings.json",
        "CLAUDE_PROJECT_DIR": project / ".claude" / "settings.local.json",
    }[where]
    write_settings(settings_file, {"env": {"KIT_BYPASS": "all"}})
    cwd = tmp_path if where == "CLAUDE_PROJECT_DIR" else project
    env = {"KIT_BYPASS": "all", **({"CLAUDE_PROJECT_DIR": str(project)} if where == "CLAUDE_PROJECT_DIR" else {})}
    ran = tmp_path / "ran"
    launcher = scratch_bin("quiet_hook", QUIET_HOOK.format(ran=str(ran)))
    payload = {"session_id": "s1", "cwd": str(cwd), "hook_event_name": "PostToolUse"}
    first = launch("quiet_hook", payload, launcher=launcher, env=env)
    assert ran.read_text() == "ran"
    assert (first.returncode, json.loads(first.stdout)) == (0, {"systemMessage": SETTINGS_NOTICE})
    second = launch("quiet_hook", payload, launcher=launcher, env=env)
    assert (second.returncode, second.stdout, second.stderr) == (0, "", "")


def test_the_settings_notice_repeats_when_nothing_can_remember_it(scratch_bin, tmp_path, monkeypatch):
    project = tmp_path / "project"
    write_settings(project / ".claude" / "settings.local.json", {"env": {"KIT_BYPASS": "all"}})
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    unwritable = [home / ".cache", tmp_path / "plugin-data"]
    for directory in unwritable:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o500)
    try:
        launcher = scratch_bin("quiet_hook", "def main():\n    pass\n")
        payload = {"session_id": "s1", "cwd": str(project), "hook_event_name": "PostToolUse"}
        for _ in range(2):
            result = launch("quiet_hook", payload, launcher=launcher, env={"KIT_BYPASS": "all"})
            assert (result.returncode, json.loads(result.stdout)) == (0, {"systemMessage": SETTINGS_NOTICE})
    finally:
        for directory in unwritable:
            directory.chmod(0o700)


def test_a_settings_bypass_cannot_skip_a_gate_and_its_output_is_kept(scratch_bin, tmp_path):
    write_settings(tmp_path / ".claude" / "settings.local.json", {"env": {"KIT_BYPASS": "stop_gate"}})
    launcher = scratch_bin("stop_gate", BLOCKING_GATE)
    payload = {"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "Stop"}
    result = launch("stop_gate", payload, launcher=launcher, env={"KIT_BYPASS": "stop_gate"})
    assert (result.returncode, json.loads(result.stdout)) == (0, {"decision": "block", "reason": "journeys are stale"})


def test_a_bypass_from_the_shell_still_works_next_to_unrelated_settings(scratch_bin, tmp_path):
    write_settings(tmp_path / ".claude" / "settings.json", {"env": {"OTHER": "1"}})
    write_settings(tmp_path / ".claude" / "settings.local.json", "{not json")
    launcher = scratch_bin("stop_gate", BLOCKING_GATE)
    payload = {"session_id": "s1", "cwd": str(tmp_path), "hook_event_name": "Stop"}
    result = launch("stop_gate", payload, launcher=launcher, env={"KIT_BYPASS": "stop_gate"})
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_the_script_sees_its_own_arguments(scratch_bin):
    launcher = scratch_bin("echo_args", "import json, sys\n\ndef main():\n    print(json.dumps(sys.argv))\n")
    result = launch("echo_args", None, "--compact", "x y", launcher=launcher)
    argv = json.loads(result.stdout)
    assert argv[0].endswith("echo_args.py")
    assert argv[1:] == ["--compact", "x y"]


def test_a_script_name_cannot_leave_the_bin_directory(data_dir):
    result = launch("../kitlib")
    assert result.returncode == 0
    assert "ValueError: not a hook script name" in json.loads(result.stdout)["systemMessage"]


def _command_hooks():
    config = json.loads(HOOKS_JSON.read_text())
    return [(event, hook) for event, groups in config["hooks"].items() for group in groups for hook in group["hooks"]]


def test_every_hook_runs_through_the_launcher():
    for event, hook in _command_hooks():
        assert hook["command"] == "python3", (event, hook)
        assert hook["args"][0] == LAUNCHER_ARG, (event, hook)
        assert (BIN / f"{hook['args'][1]}.py").is_file(), f"{event}: {hook['args'][1]}.py is missing"


def test_declared_fail_policies_match_the_launcher():
    scripts = {hook["args"][1] for _, hook in _command_hooks()}
    for script in sorted(scripts):
        declared = re.search(r"^FAIL_CLOSED = (True|False)$", (BIN / f"{script}.py").read_text(), re.MULTILINE)
        assert declared, f"{script}.py doesn't declare FAIL_CLOSED"
        assert (declared.group(1) == "True") == (script in run_hook.FAIL_CLOSED_HOOKS), script


def test_commit_checks_run_on_every_bash_call_and_edit_hooks_cover_notebooks():
    commit_hooks = [hook for _, hook in _command_hooks() if hook["args"][1] == "guard_commit"]
    assert commit_hooks and all("if" not in hook for hook in commit_hooks)
    config = json.loads(HOOKS_JSON.read_text())
    matchers = [group.get("matcher") for groups in config["hooks"].values() for group in groups]
    assert "Edit|Write" not in matchers
    assert matchers.count("Edit|Write|NotebookEdit") == 2


def test_protected_files_are_guarded_on_bash_too():
    config = json.loads(HOOKS_JSON.read_text())
    bash_scripts = [
        hook["args"][1]
        for group in config["hooks"]["PreToolUse"]
        if group.get("matcher") == "Bash"
        for hook in group["hooks"]
    ]
    assert "guard_protected_bash" in bash_scripts
    assert "guard_protected_bash" in run_hook.FAIL_CLOSED_HOOKS
