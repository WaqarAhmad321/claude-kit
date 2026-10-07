from pathlib import Path

import pytest
from hookrun import add_worktree, decision, launch, tool_payload, write

KIT_FILES = {
    "plugins/kit/.claude-plugin/plugin.json": '{"name": "kit"}',
    "plugins/kit/hooks/hooks.json": "{}",
    "plugins/kit/hooks/bin/run_hook.py": "",
    "home/hooks/orchestrator.sh": "",
    "home/rules/code-style.md": "# Code style\n",
}
FORGE_STATE = (
    "python3 -c \"import json,glob; p=glob.glob('{state}/state/*/main.json')[-1]; d=json.load(open(p)); "
    "d['skills_seen']['billing']='skill'; json.dump(d, open(p, 'w'))\""
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A scratch home, and none of the Claude Code variables of the session running the tests."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}\n")
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def project(make_repo, data_dir):
    return make_repo("project", files={"src/app.py": "A = 1\n", "ruff.toml": "line-length = 88\n"})


@pytest.fixture
def kit_repo(make_repo, data_dir, monkeypatch):
    """The live kit: the checkout Claude Code runs its plugin from."""
    repo = make_repo("claude-kit", files=KIT_FILES)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(repo / "plugins" / "kit"))
    return repo


def bash(cwd, command):
    return launch("guard_protected_bash", tool_payload(cwd, "PreToolUse", "Bash", {"command": command}))


def asks(result) -> str:
    answer = decision(result)
    assert answer["permissionDecision"] == "ask", answer
    return answer["permissionDecisionReason"]


def passes(result) -> bool:
    return (result.returncode, result.stdout, result.stderr) == (0, "", "")


SETTINGS_WRITES = [
    "echo '{}' > ~/.claude/settings.json",
    "printf x >> ~/.claude/settings.local.json",
    "cat patch.json | tee -a ~/.claude/settings.json",
    "jq '.hooks = {}' ~/.claude/settings.json > /tmp/s.json && mv /tmp/s.json ~/.claude/settings.json",
    "cp evil.json ~/.claude/settings.json",
    "cp evil.json settings.json ~/.claude/",
    "mv ~/.claude/settings.json /tmp/parked.json",
    "ln -sf /tmp/evil.json ~/.claude/settings.json",
    "chmod 000 ~/.claude/settings.json",
    "truncate -s0 ~/.claude/settings.json",
    "install -m 644 evil.json ~/.claude/settings.json",
    "curl -o ~/.claude/settings.json https://example.com/evil.json",
    "dd if=evil.json of=$HOME/.claude/settings.json",
    "cd ~/.claude && rm settings.json",
    "bash -lc 'echo {} > ~/.claude/settings.json'",
    "sh <<'EOF'\necho {} > ~/.claude/settings.json\nEOF",
    "timeout 5 tee ~/.claude/settings.json < evil.json",
    "copy=$(tee ~/.claude/settings.json < evil.json)",
    "perl -pi -e 's/a/b/' ~/.claude/settings.json",
    "node -e \"require('fs').writeFileSync(require('os').homedir() + '/.claude/settings.json', '{}')\"",
    "python3 - <<'EOF'\nimport pathlib\npathlib.Path.home().joinpath('.claude', 'settings.json').write_text('{}')\nEOF",
]


@pytest.mark.parametrize("command", SETTINGS_WRITES)
def test_a_command_that_could_write_claude_settings_asks(project, command):
    reason = asks(bash(project, command))
    assert "Claude Code settings" in reason or "runs code that could change Claude Code" in reason, reason


def test_a_target_known_only_by_its_text_asks(project):
    reason = asks(bash(project, 'echo {} > "$(printf %s ~/.claude)/settings.json"'))
    assert "writes to a path that looks like Claude Code, kit or quality-gate configuration" in reason


def test_the_reason_names_the_file(project):
    assert asks(bash(project, "echo '{}' > ~/.claude/settings.json")) == (
        "This command could change Claude Code settings (~/.claude/settings.json); the user has to approve it."
    )


@pytest.mark.parametrize(
    "command, what",
    [
        ("rm -rf ~/.claude", "Claude Code settings"),
        ("chmod -R 000 ~/.claude/plugins", "Claude Code hooks or plugins"),
        ("cp -r hooks ~/.claude/", "Claude Code hooks or plugins"),
        ("rm -rf ~/.claude/kit-hooks", "Claude Code hooks or plugins"),
        ("rm -rf ~/.cache/claude-kit/state", "the kit's state"),
        ("rm -rf .claude", "Claude Code settings"),
    ],
)
def test_removing_a_directory_that_holds_protected_files_asks(project, command, what):
    assert what in asks(bash(project, command))


def test_forging_the_kit_state_asks(project, data_dir):
    assert "runs code that could change Claude Code" in asks(bash(project, FORGE_STATE.format(state=data_dir)))
    script = write(
        project,
        "tools/mark_seen.py",
        "import json\n"
        "state = {'skills_seen': {'billing': 'skill'}}\n"
        f"json.dump(state, open('{data_dir}/state.json', 'w'))\n",
    )
    assert "runs code that could change Claude Code" in asks(bash(project, f"python3 {script}"))


@pytest.mark.parametrize(
    "command",
    [
        "claude plugin disable kit@claude-kit",
        "claude plugins uninstall kit",
        "claude plugin remove kit@claude-kit",
        "claude plugin marketplace remove claude-kit",
        "claude plugin marketplace rm claude-kit",
    ],
)
def test_disabling_or_removing_a_plugin_asks(project, command):
    assert "disables or removes a Claude Code plugin or marketplace" in asks(bash(project, command))


@pytest.mark.parametrize("command", ["echo 'KIT_BYPASS=all' >> .envrc", "grep -rn disableAllHooks ."])
def test_a_command_naming_a_kit_switch_asks(project, command):
    assert "mentions disableAllHooks or KIT_BYPASS" in asks(bash(project, command))


def test_a_shell_script_that_writes_settings_asks(project):
    write(project, "tools/reset.sh", "#!/bin/sh\nprintf '{}' > ~/.claude/settings.json\n")
    assert "Claude Code settings" in asks(bash(project, "bash tools/reset.sh"))


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "cat ~/.claude/settings.json",
        "grep hooks ~/.claude/settings.json",
        "jq . ~/.claude/settings.json",
        "jq . ~/.claude/settings.json > /tmp/settings-copy.json",
        "sed 's/a/b/' ~/.claude/settings.json",
        "cp notes.md ~/.claude/",
        "echo done > out.txt 2>/dev/null",
        "claude plugin list",
        "claude plugin install formatter@tools",
        "git checkout main",
        "python3 -c 'print(1)'",
        "rm -rf build dist",
        "sed -i 's/a/b/' src/app.py",
    ],
)
def test_commands_that_touch_no_protected_file_pass(project, command):
    assert passes(bash(project, command))


def test_writing_quality_gate_config_in_a_repo_asks(project):
    assert asks(bash(project, "sed -i 's/88/200/' ruff.toml")) == (
        "This command could change quality-gate configuration (" + str(project / "ruff.toml") + "); "
        "the user has to approve it."
    )


@pytest.mark.parametrize(
    ("command", "rel"),
    [
        ("echo 'exit 0' > .lefthook/pre-commit/lint.sh", ".lefthook/pre-commit/lint.sh"),
        ("chmod -x .lefthook-local/pre-push/run.sh", ".lefthook-local/pre-push/run.sh"),
        ("rm -rf .lefthook", ".lefthook"),
        ("mv .lefthook-local /tmp/parked", ".lefthook-local"),
    ],
)
def test_writing_a_lefthook_script_dir_asks(project, command, rel):
    write(project, ".lefthook/pre-commit/lint.sh", "ruff check .\n")
    write(project, ".lefthook-local/pre-push/run.sh", "make test\n")
    reason = asks(bash(project, command))
    assert "quality-gate configuration (" + str(project / rel) + ")" in reason


def test_writing_a_lefthook_extends_file_asks(project):
    write(project, ".lefthook/django.yml", "pre-commit: {}\n")
    reason = asks(bash(project, "echo 'pre-commit: {}' > .lefthook/django.yml"))
    assert "quality-gate configuration (" + str(project / ".lefthook/django.yml") + ")" in reason


@pytest.mark.parametrize(
    "command",
    [
        "sed -i 's/stop_gate/true/' {kit}/plugins/kit/hooks/hooks.json",
        "git -C {kit} checkout -- plugins/kit/hooks/hooks.json",
        "git -C {kit} restore .",
        "rm -rf {kit}/plugins",
        "echo x > {kit}/home/hooks/orchestrator.sh",
    ],
)
def test_changing_the_kit_from_another_project_asks(project, kit_repo, monkeypatch, command):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    assert "the kit's own hook files" in asks(bash(project, command.format(kit=kit_repo)))


def test_a_session_in_the_kit_repo_may_change_the_kit_but_not_settings_or_state(kit_repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(kit_repo))
    for command in (
        "sed -i 's/a/b/' plugins/kit/hooks/hooks.json",
        "git restore plugins/kit/hooks",
        "python3 -c \"open('plugins/kit/hooks/bin/run_hook.py').read()\"",
        "echo x > home/rules/code-style.md",
    ):
        assert passes(bash(kit_repo, command)), command
    assert "Claude Code settings" in asks(bash(kit_repo, "echo '{}' > ~/.claude/settings.json"))
    assert "the kit's state" in asks(bash(kit_repo, "rm -rf ~/.cache/claude-kit"))


READ_ONLY_CODE = [
    "grep -c '\"hooks\"' ~/.claude/settings.json; "
    "python3 -c \"import json; print(sorted(json.load(open('{home}/.claude/settings.json')).keys()))\"",
    "python3 -c \"import json, os; print(list(json.load(open(os.path.expanduser('~/.claude/settings.json'), 'r'))))\"",
    "python3 -c \"import pathlib; print(pathlib.Path('{home}/.claude/settings.json').read_text().count('hooks'))\"",
    "python3 -c \"import subprocess; subprocess.run(['grep', '-c', 'hooks', '{home}/.claude/settings.json'])\"",
    "python3 -c \"import os; os.system('cat {home}/.claude/settings.json')\"",
    "python3 - <<'EOF'\nimport json\nprint(json.dumps(json.load(open('{home}/.claude/settings.json')), indent=2))\nEOF",
    "node -e \"console.log(JSON.parse(require('fs').readFileSync('{home}/.claude/settings.json', 'utf8')))\"",
    "perl -ne 'print if /hooks/' ~/.claude/settings.json",
]


@pytest.mark.parametrize("command", READ_ONLY_CODE)
def test_code_that_only_reads_protected_files_passes(project, home, command):
    assert passes(bash(project, command.format(home=home))), command


CODE_WRITES = [
    "python3 -c \"open('{home}/.claude/settings.json', 'w').write('{{}}')\"",
    "python3 -c \"open('{home}/.claude/settings.json', mode='a').write('x')\"",
    "python3 -c \"import json; json.dump({{}}, open('{home}/.claude/settings.json', 'r+'))\"",
    "python3 -c \"import pathlib; pathlib.Path('{home}/.claude/settings.json').write_bytes(b'')\"",
    "python3 -c \"import os; os.remove('{home}/.claude/settings.json')\"",
    "python3 -c \"import os; os.replace('/tmp/evil.json', '{home}/.claude/settings.json')\"",
    "python3 -c \"import shutil; shutil.copy('/tmp/evil.json', '{home}/.claude/settings.json')\"",
    "python3 -c \"import subprocess; subprocess.run(['cp', '/tmp/evil.json', '{home}/.claude/settings.json'])\"",
    "python3 -c \"import subprocess; p = '{home}/.claude/settings.json'; subprocess.run(['sed', '-i', 's/a/b/', p])\"",
    "python3 -c \"import os; os.system('rm {home}/.claude/settings.json')\"",
    "python3 -c \"import subprocess, sys; subprocess.run(sys.argv[1] + ' {home}/.claude/settings.json', shell=True)\"",
    "node -e \"require('fs').appendFileSync('{home}/.claude/settings.json', 'x')\"",
    "node -e \"require('fs').renameSync('/tmp/evil.json', '{home}/.claude/settings.json')\"",
    "node -e \"require('fs').unlinkSync('{home}/.claude/settings.json')\"",
    "node -e \"require('fs').rmSync('{home}/.claude/plugins', {{recursive: true}})\"",
    "node -e \"require('child_process').execSync('rm -rf {home}/.claude/plugins')\"",
    'perl -e \'open(my $f, ">", "{home}/.claude/settings.json"); print $f 1\'',
    "perl -e 'open(F, \">>{home}/.claude/settings.json\")'",
    "perl -i.bak -pe 's/a/b/' ~/.claude/settings.json",
    "ruby -e 'puts File.read(\"{home}/.claude/settings.json\")'",
]


@pytest.mark.parametrize("command", CODE_WRITES)
def test_code_that_could_write_protected_files_asks(project, home, command):
    assert "runs code that could change Claude Code" in asks(bash(project, command.format(home=home))), command


GATE_FILE_WRITES = [
    "python3 -c \"import json; json.dump({{'gate': {{'review': {{'enabled': False}}}}}}, "
    "open('.claude/kit.json', 'w'))\"",
    "python3 -c \"import pathlib; pathlib.Path('.claude', 'kit.json').write_text('{{}}')\"",
    "python3 -c \"import os; open(os.path.join(os.getcwd(), '.claude', 'kit.json'), 'w').write('{{}}')\"",
    "node -e \"require('fs').writeFileSync('.claude/kit.json', '{{}}')\"",
    "node -e \"require('fs').writeFileSync(require('path').join('.claude', 'kit.json'), '{{}}')\"",
    "python3 -c \"open('lefthook.yml', 'w').write('pre-commit: {{}}')\"",
    "python3 -c \"open('.lefthook/pre-commit/lint.sh', 'w').write('exit 0')\"",
    "node -e \"require('fs').writeFileSync('.lefthook-local/pre-push/run.sh', 'exit 0')\"",
    "python3 -c \"open('web/tsconfig.app.json', 'w').write('{{}}')\"",
    "python3 -c \"import shutil; shutil.copy('/tmp/ci.yml', '.github/workflows/ci.yml')\"",
]


@pytest.mark.parametrize("command", GATE_FILE_WRITES)
def test_code_that_could_write_gate_config_asks(project, command):
    assert "runs code that could change Claude Code" in asks(bash(project, command.format())), command


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c \"import json; print(json.load(open('.claude/kit.json')))\"",
        "node -e \"console.log(require('fs').readFileSync('lefthook.yml', 'utf8'))\"",
    ],
)
def test_code_that_only_reads_gate_config_passes(project, command):
    write(project, ".claude/kit.json", "{}\n")
    write(project, "lefthook.yml", "pre-commit: {}\n")
    assert passes(bash(project, command)), command


KIT_SCRIPTS = {
    "templates/common/scripts/docs-check.py": Path(__file__).resolve().parents[3] / "templates/common/scripts",
    "plugins/kit/scripts/apply_template.py": Path(__file__).resolve().parents[1] / "scripts",
}


@pytest.mark.parametrize(
    "command",
    [
        "python3 templates/common/scripts/docs-check.py --root .",
        "python3 plugins/kit/scripts/apply_template.py --stack django --dest . --dry-run",
        "python3 -c \"import json; d=json.load(open('tsconfig.json')); json.dump(d, open('out.json','w'))\"",
    ],
)
def test_code_that_names_a_gate_file_but_writes_elsewhere_passes(project, command):
    for rel, directory in KIT_SCRIPTS.items():
        write(project, rel, (directory / Path(rel).name).read_text())
    write(project, "tsconfig.json", "{}\n")
    assert passes(bash(project, command)), command


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c \"p = '.claude/kit.json'; open(p, 'w').write('{}')\"",
        "python3 -c \"import sys; open(sys.argv[1], 'w').write('{}')\" .claude/kit.json",
        "node -e \"const target = require('path').join('.claude', 'kit.json'); require('fs').writeFileSync(target, '')\"",
        "python3 tools/reset_gates.py",
    ],
)
def test_code_whose_write_target_is_a_gate_file_asks(project, command):
    write(project, "tools/reset_gates.py", "import json\n\nwith open('lefthook.yml', 'w') as out:\n    out.write('')\n")
    assert "runs code that could change Claude Code" in asks(bash(project, command)), command


@pytest.mark.parametrize(
    "command",
    [
        "sed -i 's/true/false/' .claude/kit.json",
        "echo '{\"gate\": {\"review\": {\"enabled\": false}}}' | tee .claude/kit.json",
        "echo '{}' > .claude/kit.json",
        "cat new.json >> .claude/kit.json",
        "echo '{}' > \"$(git rev-parse --show-toplevel)/.claude/kit.json\"",
    ],
)
def test_shell_writes_to_kit_json_ask(project, command):
    write(project, ".claude/kit.json", "{}\n")
    assert "kit.json" in asks(bash(project, command)), command


def test_a_worktree_of_the_live_kit_is_ordinary_files(project, kit_repo, tmp_path, monkeypatch):
    worktree = add_worktree(kit_repo, tmp_path / "scratchpad" / "kit-wt" / "fix", "fix")
    commands = [
        "sed -i 's/stop_gate/true/' {kit}/plugins/kit/hooks/hooks.json",
        "git -C {kit} checkout -- plugins/kit/hooks/hooks.json",
        "rm -rf {kit}/plugins",
        "echo x > {kit}/home/hooks/orchestrator.sh",
        "python3 -c \"open('{kit}/plugins/kit/hooks/hooks.json', 'w').write('{{}}')\"",
    ]
    for session in (project, worktree):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(session))
        for command in commands:
            assert passes(bash(session, command.format(kit=worktree))), (session, command)
            reason = asks(bash(session, command.format(kit=kit_repo)))
            assert "the kit's own hook files" in reason or "runs code that could change" in reason, (session, command)
        assert "Claude Code settings" in asks(bash(session, "echo '{}' > ~/.claude/settings.json"))
        assert "Claude Code settings" in asks(bash(session, f"echo '{{}}' > {worktree}/.claude/settings.json"))
    assert passes(bash(worktree, "python3 -c \"open('plugins/kit/hooks/hooks.json', 'w').write('{}')\""))


def test_code_writing_the_kit_hooks_by_a_path_the_hook_cannot_place_asks(project, kit_repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    command = "python3 -c \"import os; open(os.environ['KIT'] + '/plugins/kit/hooks/hooks.json', 'w')\""
    assert "runs code that could change" in asks(bash(project, command))


def test_inline_code_longer_than_a_file_name_is_read_as_code(project, home):
    padding = "x = 1; " * 60
    assert passes(bash(project, f"python3 -c \"{padding}print(open('{home}/.claude/settings.json').read())\""))
    reason = asks(bash(project, f"python3 -c \"{padding}open('{home}/.claude/settings.json', 'w').write('x')\""))
    assert "runs code that could change Claude Code" in reason
