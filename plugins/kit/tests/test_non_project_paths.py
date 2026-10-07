"""Scratch work and the kit's starter templates aren't project code: the lint, the stop checks and the gate-config
guards leave them alone, and the stop reports don't list them."""

import json
import os
from pathlib import Path

import pytest
from conftest import read_state
from hookrun import base_payload, decision, findings, launch, tool_payload, write

CLEAN = "def f():\n    return 1\n"
VIOLATION = "def f():\n    return undefined_name\n"
KIT_MARKER = "plugins/kit/.claude-plugin/plugin.json"
PROJECT_OUTSIDE_TEMP = "/nonexistent/kit-test-project"


def hook_input(payload: dict, scratchpad: Path | None) -> dict:
    return payload | ({"scratchpad_dir": str(scratchpad)} if scratchpad else {})


def start(cwd: Path, scratchpad: Path | None = None) -> None:
    assert launch("session_context", hook_input(base_payload(cwd, "SessionStart"), scratchpad)).returncode == 0


def tool_edit(cwd: Path, path: Path, content: str, scratchpad: Path | None = None):
    payload = hook_input(tool_payload(cwd, "PreToolUse", "Write", {"file_path": str(path)}), scratchpad)
    snapshot = launch("snapshot_before_edit", payload)
    assert quiet(snapshot), snapshot
    write(path.parent, path.name, content)
    return launch("lint_edited_file", payload | {"hook_event_name": "PostToolUse"})


def guard_edit(cwd: Path, path: Path, scratchpad: Path | None = None):
    payload = tool_payload(cwd, "PreToolUse", "Write", {"file_path": str(path), "content": "{}\n"})
    return launch("guard_protected_files", hook_input(payload, scratchpad))


def guard_bash(cwd: Path, command: str, scratchpad: Path | None = None):
    payload = tool_payload(cwd, "PreToolUse", "Bash", {"command": command})
    return launch("guard_protected_bash", hook_input(payload, scratchpad))


def stops(cwd: Path, scratchpad: Path | None = None) -> dict:
    payload = hook_input(base_payload(cwd, "Stop") | {"stop_hook_active": False}, scratchpad)
    return {hook: launch(hook, payload) for hook in ("stop_gate", "stop_module_skills")}


def quiet(result) -> bool:
    return (result.returncode, result.stdout, result.stderr) == (0, "", "")


def asks(result) -> bool:
    return decision(result)["permissionDecision"] == "ask"


def test_work_in_the_session_scratchpad_is_not_linted_guarded_or_checked(ruff_repo_at, tmp_path, data_dir):
    project = ruff_repo_at("project", {"app.py": CLEAN})
    scratchpad = tmp_path / "session" / "scratchpad"
    smoke = ruff_repo_at("session/scratchpad/smoke", {"app.py": CLEAN})
    start(project, scratchpad)
    assert quiet(tool_edit(project, smoke / "app.py", VIOLATION, scratchpad))
    assert str(smoke / "app.py") not in read_state(data_dir)["edits"]
    assert quiet(guard_edit(project, smoke / "ruff.toml", scratchpad))
    assert quiet(guard_bash(project, f"echo 'line-length = 200' > {smoke / 'ruff.toml'}", scratchpad))
    write(smoke, "ruff.toml", "line-length = 200\n")
    assert all(quiet(result) for result in stops(project, scratchpad).values())
    assert asks(guard_edit(project, project / "ruff.toml", scratchpad)), "the project's own config is still guarded"
    assert tool_edit(project, project / "app.py", VIOLATION, scratchpad).returncode == 2, "the project is still linted"


def test_scratch_edits_recorded_before_the_scratchpad_was_known_are_left_out_of_the_stop(
    ruff_repo_at, tmp_path, data_dir
):
    project = ruff_repo_at("project")
    scratchpad = tmp_path / "session" / "scratchpad"
    smoke = ruff_repo_at("session/scratchpad/smoke", {"app.py": CLEAN})
    start(project)
    tool_edit(project, smoke / "app.py", VIOLATION)
    assert str(smoke / "app.py") in read_state(data_dir)["edits"]
    assert json.loads(stops(project)["stop_gate"].stdout)["decision"] == "block"
    assert all(quiet(result) for result in stops(project, scratchpad).values())


def test_work_under_the_temp_directory_is_exempt_when_the_project_is_elsewhere(ruff_repo_at, tmp_path, monkeypatch):
    temp_dirs = [Path(directory).resolve() for directory in (os.environ.get("TMPDIR"), "/tmp") if directory]
    if not any(tmp_path.resolve().is_relative_to(directory) for directory in temp_dirs):
        pytest.skip("pytest's temp directory is under neither $TMPDIR nor /tmp")
    scratch = ruff_repo_at("scratch", {"app.py": CLEAN})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", PROJECT_OUTSIDE_TEMP)
    start(scratch)
    assert quiet(tool_edit(scratch, scratch / "app.py", VIOLATION))
    assert quiet(guard_edit(scratch, scratch / "ruff.toml"))
    write(scratch, "other.py", VIOLATION)
    assert all(quiet(result) for result in stops(scratch).values())


def test_the_kit_templates_are_starter_files_not_live_config(ruff_repo_at, monkeypatch):
    kit = ruff_repo_at("claude-kit", {KIT_MARKER: "{}\n", "templates/mern/app.py": CLEAN})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(kit))
    start(kit)
    oxlintrc = kit / "templates" / "mern" / "client" / ".oxlintrc.json"
    assert quiet(tool_edit(kit, kit / "templates" / "mern" / "app.py", VIOLATION))
    assert quiet(guard_edit(kit, oxlintrc))
    assert quiet(guard_bash(kit, f"echo '{{}}' > {oxlintrc}"))
    write(oxlintrc.parent, oxlintrc.name, "{}\n")
    write(kit, "templates/mern/lefthook.yml", "pre-commit: {}\n")
    assert all(quiet(result) for result in stops(kit).values())
    assert asks(guard_edit(kit, kit / ".oxlintrc.json")), "the kit's own lint config is still guarded"
    assert asks(guard_bash(kit, "echo '{}' > .oxlintrc.json"))
    write(kit, "app.py", VIOLATION)
    reason = json.loads(stops(kit)["stop_gate"].stdout)["reason"]
    assert "app.py:\n- ruff F821" in findings(reason)
    assert "templates" not in findings(reason)
