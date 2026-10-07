import fcntl
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import kitlib
import pytest
from conftest import BIN, fake_exe

# Shape copied from a real `oxlint --format json` run (oxlint 1.86).
OXLINT_JSON = (
    '{ "diagnostics": [{"message": "Variable \'unused\' is declared but never used.",'
    '"code": "eslint(no-unused-vars)","severity": "warning","url": "https://oxc.rs",'
    '"help": "Consider removing this declaration.","filename": "app.ts",'
    '"labels": [{"label": "\'unused\' is declared here","span": {"offset": 6,"length": 6,"line": 12,"column": 7}}]},\n'
    '{"message": "`debugger` statement is not allowed","code": "eslint(no-debugger)","severity": "warning",'
    '"url": "https://oxc.rs","help": "Remove the debugger statement","filename": "app.ts",'
    '"labels": [{"span": {"offset": 18,"length": 9,"line": 30,"column": 1}}]}],\n'
    '  "number_of_files": 1, "number_of_rules": 96, "threads_count": 12, "start_time": 0.01 }'
)

ESLINT_JSON = json.dumps(
    [
        {
            "filePath": "app.js",
            "messages": [
                {"ruleId": "no-debugger", "severity": 2, "message": "Unexpected 'debugger' statement.", "line": 3},
                {
                    "ruleId": "semi",
                    "severity": 2,
                    "message": "Missing semicolon.",
                    "line": 4,
                    "fix": {"range": [1, 1], "text": ";"},
                },
            ],
        }
    ]
)


def run_python(code: str, env: dict | None = None):
    return subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(BIN)!r}); import kitlib\n{code}"],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=60,
    )


@pytest.fixture
def isolated_path(tmp_path, monkeypatch):
    """PATH and mise shims that hold no real linters."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "no-mise"))
    return empty


def test_bypassed_matches_the_named_hook_or_all(monkeypatch):
    monkeypatch.setenv("KIT_BYPASS", "scan_secrets, lint_edited_file")
    assert kitlib.bypassed("lint_edited_file")
    assert not kitlib.bypassed("stop_gate")
    monkeypatch.setenv("KIT_BYPASS", "all")
    assert kitlib.bypassed("stop_gate")
    monkeypatch.delenv("KIT_BYPASS")
    assert not kitlib.bypassed("stop_gate")


def test_load_config_reads_kit_json_or_returns_empty(tmp_path):
    assert kitlib.load_config(tmp_path) == {}
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "kit.json").write_text('{"gate": {"typecheck": ["mypy ."]}}')
    assert kitlib.load_config(tmp_path) == {"gate": {"typecheck": ["mypy ."]}}


def test_project_root_is_the_git_toplevel_or_none(tmp_path, make_repo):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert kitlib.project_root(str(plain)) is None
    repo = make_repo()
    assert kitlib.project_root(str(repo / "src" / "not_yet_created.py")) == repo


def test_find_tool_prefers_node_modules_then_venvs_then_path(tmp_path, isolated_path):
    assert kitlib.find_tool("tool", tmp_path) is None
    on_path = fake_exe(isolated_path / "tool")
    assert kitlib.find_tool("tool", tmp_path) == str(on_path)
    plain_venv = fake_exe(tmp_path / "venv" / "bin" / "tool")
    assert kitlib.find_tool("tool", tmp_path) == str(plain_venv)
    dot_venv = fake_exe(tmp_path / ".venv" / "bin" / "tool")
    assert kitlib.find_tool("tool", tmp_path) == str(dot_venv)
    node = fake_exe(tmp_path / "node_modules" / ".bin" / "tool")
    assert kitlib.find_tool("tool", tmp_path) == str(node)


def test_linters_for_python_needs_ruff_config_and_ruff(tmp_path, isolated_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert kitlib.linters_for(repo, repo / "app.py") == []
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    fake_exe(repo / ".venv" / "bin" / "ruff")
    assert kitlib.linters_for(repo, repo / "app.py") == []
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n\n[tool.ruff.lint]\nselect = ['F']\n")
    assert kitlib.linters_for(repo, repo / "pkg" / "app.py") == ["ruff"]
    assert kitlib.linters_for(repo, repo / "notes.md") == []


def test_linters_for_ruff_toml_in_a_subproject(tmp_path, isolated_path):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "ruff.toml").write_text("line-length = 100\n")
    fake_exe(repo / "backend" / ".venv" / "bin" / "ruff")
    assert kitlib.linters_for(repo, repo / "backend" / "api" / "views.py") == ["ruff"]
    assert kitlib.linters_for(repo, repo / "scripts" / "tool.py") == []


def test_linters_for_javascript_prefers_oxlint_over_eslint(tmp_path, isolated_path):
    web = tmp_path / "repo" / "web"
    web.mkdir(parents=True)
    root = tmp_path / "repo"
    assert kitlib.linters_for(root, web / "src" / "app.tsx") == []
    (web / "eslint.config.mjs").write_text("export default [];\n")
    assert kitlib.linters_for(root, web / "src" / "app.tsx") == []
    fake_exe(web / "node_modules" / ".bin" / "eslint")
    assert kitlib.linters_for(root, web / "src" / "app.tsx") == ["eslint"]
    (web / ".oxlintrc.json").write_text("{}")
    assert kitlib.linters_for(root, web / "src" / "app.tsx") == ["eslint"]
    fake_exe(web / "node_modules" / ".bin" / "oxlint")
    assert kitlib.linters_for(root, web / "src" / "app.tsx") == ["oxlint"]
    (web / ".oxlintrc.json").unlink()
    assert kitlib.linters_for(root, web / "src" / "app.jsx") == ["oxlint"]


def fingerprints(root, path) -> Counter:
    return Counter(violation.fingerprint for violation in kitlib._violations(root, path))


def test_violations_drop_line_numbers_and_count_js_suppressions_by_kind(tmp_path, isolated_path):
    repo = tmp_path / "repo"
    fake_exe(repo / "node_modules" / ".bin" / "oxlint", stdout=OXLINT_JSON)
    source = repo / "app.ts"
    source.write_text(
        "// eslint-disable-next-line no-console\n"
        "// @ts-ignore\n"
        "const a = 1; // @ts-ignore\n"
        "/* oxlint-disable no-debugger */\n"
        "// @ts-expect-error\n"
        "/* eslint-disable */\n"
        "call(); // eslint-disable-line\n"
        "// oxlint-disable-next-line no-alert\n"
        "call(); // oxlint-disable-line\n"
        "// @ts-nocheck\n"
    )
    found = fingerprints(repo, source)
    assert found == Counter(
        {
            ("oxlint", "eslint(no-unused-vars)", "Variable 'unused' is declared but never used."): 1,
            ("oxlint", "eslint(no-debugger)", "`debugger` statement is not allowed"): 1,
            ("suppression", "eslint-disable-next-line", ""): 1,
            ("suppression", "eslint-disable", ""): 1,
            ("suppression", "eslint-disable-line", ""): 1,
            ("suppression", "@ts-ignore", ""): 2,
            ("suppression", "oxlint-disable", ""): 1,
            ("suppression", "oxlint-disable-next-line", ""): 1,
            ("suppression", "oxlint-disable-line", ""): 1,
            ("suppression", "@ts-expect-error", ""): 1,
            ("suppression", "@ts-nocheck", ""): 1,
        }
    )
    assert all(isinstance(part, str) for key in found for part in key)
    assert [v.line for v in kitlib._violations(repo, source) if v.tool == "oxlint"] == [12, 30]


PY_SUPPRESSIONS = '''\
# flake8: noqa
# ruff: noqa: F401
# mypy: ignore-errors
# ruff: disable[F401]
import os  # NOQA: F401
x = y  # type: ignore[name-defined]  # pyright: ignore[reportUndefinedVariable]
if x:  # pragma: no cover
    pass
DOC = "use # noqa sparingly"
TEXT = """
# type: ignore
// eslint-disable
"""
'''


def test_python_suppressions_are_read_from_real_comments_only(tmp_path, isolated_path):
    source = tmp_path / "mod.py"
    source.write_text(PY_SUPPRESSIONS)
    assert fingerprints(tmp_path, source) == Counter(
        {
            ("suppression", "# flake8: noqa", ""): 1,
            ("suppression", "# ruff: noqa", ""): 1,
            ("suppression", "# mypy: ignore-errors", ""): 1,
            ("suppression", "# ruff: disable", ""): 1,
            ("suppression", "# noqa", ""): 1,
            ("suppression", "# type: ignore", ""): 1,
            ("suppression", "# pyright: ignore", ""): 1,
            ("suppression", "# pragma: no cover", ""): 1,
        }
    )


def test_python_that_does_not_tokenize_is_scanned_line_by_line(tmp_path):
    violations = kitlib.suppression_violations('x = """\nimport os  # noqa\n', tmp_path / "mod.py")
    assert [(v.code, v.line) for v in violations] == [("# noqa", 2)]
    assert kitlib.suppression_violations("# noqa\n// eslint-disable\n", tmp_path / "README.md") == []


def recording_exe(path, log):
    """An executable that records its arguments, standing in for a linter's fix run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
    path.chmod(0o755)


def test_autofix_never_removes_unused_imports_or_variables(tmp_path, isolated_path):
    (tmp_path / "ruff.toml").write_text("line-length = 100\n")
    recording_exe(tmp_path / ".venv" / "bin" / "ruff", tmp_path / "ruff-args.json")
    recording_exe(tmp_path / "node_modules" / ".bin" / "oxlint", tmp_path / "oxlint-args.json")
    for name in ("app.py", "app.ts"):
        (tmp_path / name).write_text("x = 1\n")
        kitlib.apply_fixes(tmp_path, tmp_path / name)
    ruff_args = json.loads((tmp_path / "ruff-args.json").read_text())
    assert ruff_args[ruff_args.index("--unfixable") + 1] == "F401,F841"
    assert json.loads((tmp_path / "oxlint-args.json").read_text())[:3] == ["--fix", "-A", "no-unused-vars"]


def test_edited_rel_paths_give_the_written_and_the_resolved_path(tmp_path):
    root = tmp_path.resolve() / "repo"
    (root / "shared" / "orders").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "app" / "orders").symlink_to(root / "shared" / "orders")
    inp = {"cwd": str(root), "tool_input": {"file_path": "app/tmp/../orders/x.py"}}
    assert kitlib._edited_rel_paths(inp, root) == ["app/orders/x.py", "shared/orders/x.py"]
    assert kitlib._edited_rel_paths({"cwd": str(root), "tool_input": {"file_path": "/elsewhere/x.py"}}, root) == []
    notebook = {"cwd": str(root), "tool_input": {"notebook_path": "nb/analysis.ipynb"}}
    assert kitlib._edited_path(notebook) == root / "nb" / "analysis.ipynb"


def test_eslint_json_is_parsed_with_fixability(tmp_path, isolated_path):
    repo = tmp_path / "repo"
    (repo / "eslint.config.js").parent.mkdir(parents=True)
    (repo / "eslint.config.js").write_text("export default [];\n")
    fake_exe(repo / "node_modules" / ".bin" / "eslint", stdout=ESLINT_JSON, exit_code=1)
    source = repo / "app.js"
    source.write_text("debugger\n")
    violations = kitlib._violations(repo, source)
    assert [(v.code, v.line, v.fixable) for v in violations] == [("no-debugger", 3, False), ("semi", 4, True)]


def test_messages_are_normalized_without_merging_identifiers():
    assert kitlib._normalize_message("Line too long  (95 > 88)") == "Line too long (N > N)"
    assert kitlib._normalize_message("`x1` imported but unused") == "`x1` imported but unused"


def test_format_check_is_none_without_a_formatter(tmp_path, isolated_path):
    source = tmp_path / "app.ts"
    source.write_text("const a = 1\n")
    assert kitlib.format_check(tmp_path, source) is None
    assert kitlib.apply_format(tmp_path, source) is False


def test_format_check_uses_the_configured_js_formatter(tmp_path, isolated_path):
    (tmp_path / ".prettierrc").write_text("{}")
    fake_exe(tmp_path / "node_modules" / ".bin" / "prettier", exit_code=1)
    source = tmp_path / "app.ts"
    source.write_text("const a = 1\n")
    assert kitlib.format_check(tmp_path, source) is False
    assert kitlib.format_check(tmp_path, tmp_path / "new.ts") is True


def test_pre_tool_decision_prints_the_permission_json():
    proc = run_python("kitlib.pre_tool_decision('deny', 'no secrets')")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "no secrets",
        }
    }
    with pytest.raises(ValueError):
        kitlib.pre_tool_decision("maybe", "unknown")


def test_stop_block_and_notify_user_print_json():
    stop = run_python("kitlib.stop_block('journeys are stale')")
    assert (stop.returncode, json.loads(stop.stdout)) == (0, {"decision": "block", "reason": "journeys are stale"})
    note = run_python("kitlib.notify_user('heads up')")
    assert (note.returncode, json.loads(note.stdout)) == (0, {"systemMessage": "heads up"})


def test_feedback_exits_2_with_truncated_stderr():
    proc = run_python("kitlib.feedback('x' * 9000)")
    assert proc.returncode == 2
    assert len(proc.stderr) == 8000
    assert proc.stderr.endswith("[truncated]")


HOOK_SCRIPT = """
import sys
sys.path.insert(0, {bin!r})
import kitlib

@kitlib.hook_main("demo", fail_closed={fail_closed})
def main():
    raise ValueError("boom")

main()
"""


@pytest.mark.parametrize("fail_closed", [True, False])
def test_hook_main_failure_policy(tmp_path, monkeypatch, fail_closed):
    monkeypatch.delenv("KIT_BYPASS", raising=False)
    script = tmp_path / "demo.py"
    script.write_text(HOOK_SCRIPT.format(bin=str(BIN), fail_closed=fail_closed))
    proc = subprocess.run([sys.executable, str(script)], input="", capture_output=True, text=True, timeout=60)
    if fail_closed:
        assert proc.returncode == 2
        assert proc.stderr == (
            "kit: demo hook failed (ValueError: boom). Fix the hook, or relaunch Claude Code with KIT_BYPASS=demo."
        )
    else:
        assert proc.returncode == 0
        assert "kit: demo hook failed (ValueError: boom)" in json.loads(proc.stdout)["systemMessage"]


@pytest.mark.parametrize(
    "payload, blocks",
    [
        ({"hook_event_name": "Stop", "stop_hook_active": False}, True),
        ({"hook_event_name": "Stop", "stop_hook_active": True}, False),
        ({"hook_event_name": "SubagentStop", "stop_hook_active": True}, False),
        ({"hook_event_name": "PreToolUse", "stop_hook_active": True}, True),
    ],
)
def test_a_crashing_fail_closed_hook_blocks_a_stop_only_once(tmp_path, monkeypatch, payload, blocks):
    monkeypatch.delenv("KIT_BYPASS", raising=False)
    script = tmp_path / "demo.py"
    script.write_text(HOOK_SCRIPT.format(bin=str(BIN), fail_closed=True))
    proc = subprocess.run(
        [sys.executable, str(script)], input=json.dumps(payload), capture_output=True, text=True, timeout=60
    )
    if blocks:
        assert (proc.returncode, proc.stdout) == (2, "")
        return
    assert (proc.returncode, proc.stderr) == (0, "")
    assert json.loads(proc.stdout) == {
        "systemMessage": "kit: demo hook failed again (ValueError: boom), so Claude may stop this time. "
        "Fix the hook, or relaunch Claude Code with KIT_BYPASS=demo."
    }


def test_hook_main_skips_bypassed_hooks(tmp_path):
    script = tmp_path / "demo.py"
    script.write_text(HOOK_SCRIPT.format(bin=str(BIN), fail_closed=True))
    env = {**os.environ, "KIT_BYPASS": "other,demo"}
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=60)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


def test_state_has_defaults_and_persists_per_agent(data_dir):
    with kitlib.State({"session_id": "s1"}) as st:
        assert st == {"edits": {}, "edit_seq": 0, "journey_pass_seq": 0, "skills_seen": {}}
        st["edit_seq"] = 3
    with kitlib.State({"session_id": "s1", "agent_id": "agent-7"}) as st:
        assert st["edit_seq"] == 0
        st["skills_seen"]["billing"] = "Skill"
    assert json.loads((data_dir / "state" / "s1" / "main.json").read_text())["edit_seq"] == 3
    assert json.loads((data_dir / "state" / "s1" / "agent-7.json").read_text())["skills_seen"] == {"billing": "Skill"}


def test_state_saves_on_sys_exit_but_not_on_errors(data_dir):
    with pytest.raises(SystemExit), kitlib.State({"session_id": "s1"}) as st:
        st["edit_seq"] = 1
        sys.exit(2)
    with pytest.raises(RuntimeError), kitlib.State({"session_id": "s1"}) as st:
        st["edit_seq"] = 99
        raise RuntimeError("half-done update")
    with kitlib.State({"session_id": "s1"}) as st:
        assert st["edit_seq"] == 1


def test_state_holds_an_exclusive_lock(data_dir):
    inp = {"session_id": "s1"}
    lock_path = data_dir / "state" / "s1" / "main.lock"
    with kitlib.State(inp), open(lock_path, "a") as other, pytest.raises(BlockingIOError):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(lock_path, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_state_keeps_hostile_ids_inside_the_state_dir(data_dir):
    with kitlib.State({"session_id": "../../escape", "agent_id": "../a"}):
        pass
    written = [p for p in data_dir.rglob("*.json")]
    assert written and all((data_dir / "state") in p.parents for p in written)


def test_new_sessions_prune_state_older_than_a_week(data_dir):
    state = data_dir / "state"
    old, recent = state / "old-session", state / "recent-session"
    old.mkdir(parents=True)
    recent.mkdir()
    (old / "main.json").write_text("{}")
    week_ago = time.time() - 8 * 24 * 3600
    os.utime(old, (week_ago, week_ago))
    with kitlib.State({"session_id": "new-session"}):
        pass
    assert not old.exists()
    assert recent.exists()


def test_the_data_dir_falls_back_to_the_user_cache_when_plugin_data_is_not_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    fallback = tmp_path / "home" / ".cache" / "claude-kit"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    assert kitlib._data_dir() == tmp_path / "data"
    (tmp_path / "data").mkdir()
    (tmp_path / "data").chmod(0o500)
    try:
        assert kitlib._data_dir() == fallback
    finally:
        (tmp_path / "data").chmod(0o700)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA")
    assert kitlib._data_dir() == fallback


def test_scratch_dirs_are_the_absolute_temp_dirs_that_do_not_hold_the_project(tmp_path):
    scratchpad = tmp_path / "session" / "scratchpad"
    found = kitlib._scratch_dirs(Path("/srv/project"), (str(scratchpad), "/tmp", "", None, "relative/tmp"))
    assert set(found) == {scratchpad, scratchpad.resolve(), Path("/tmp"), Path("/tmp").resolve()}


def test_a_temp_dir_that_holds_the_project_is_not_scratch():
    scratchpad = "/tmp/claude-1/session/scratchpad"
    assert kitlib._scratch_dirs(Path("/tmp/checkout/project"), (scratchpad, "/tmp", "/")) == (Path(scratchpad),)
