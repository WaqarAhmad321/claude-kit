import json

import pytest
from hookrun import add_worktree, decision, edit_input, launch, write

PYPROJECT = """\
[project]
name = "app"
version = "1.0.0"

[tool.ruff]
line-length = 88

[tool.ruff.lint]
select = ["E", "F"]

[tool.pytest.ini_options]
addopts = "-q"
"""

SETUP_CFG = """\
[metadata]
name = app

[flake8]
max-line-length = 88
"""


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A scratch home, and none of the Claude Code variables of the session running the tests."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def repo(make_repo, data_dir):
    return make_repo(files={"pyproject.toml": PYPROJECT, "setup.cfg": SETUP_CFG, "src/app.py": "A = 1\n"})


def guard(repo, rel, tool="Edit", **fields):
    return launch("guard_protected_files", edit_input(repo, rel, tool=tool, extra=fields))


def assert_asks(result, rel):
    assert decision(result) == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"This changes quality-gate configuration ({rel}); the user has to approve it.",
    }


def assert_asks_about(result, what, shown):
    reason = decision(result)["permissionDecisionReason"]
    assert reason == f"This changes {what} ({shown}); the user has to approve it."


def assert_passes(result):
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/kit.json",
        ".claude/kit-baseline.json",
        "ruff.toml",
        "backend/.ruff.toml",
        ".oxlintrc.json",
        "web/eslint.config.mjs",
        ".eslintrc.cjs",
        "biome.json",
        "tsconfig.json",
        "web/tsconfig.app.json",
        "lefthook.yml",
        ".lefthook/django.yml",
        "web/.lefthook/lint.yaml",
        ".pre-commit-config.yaml",
        ".github/workflows/ci.yml",
        ".github/workflows/nested/deploy.yaml",
        "mypy.ini",
        ".mypy.ini",
        "pytest.ini",
        ".flake8",
        "tox.ini",
        "conftest.py",
    ],
)
def test_quality_gate_files_need_approval(repo, rel):
    assert_asks(guard(repo, rel, tool="Write", content="anything"), rel)


@pytest.mark.parametrize(
    "rel",
    [
        "lefthook.yaml",
        "lefthook.toml",
        "lefthook.json",
        "lefthook.jsonc",
        ".lefthook.yml",
        ".lefthook.toml",
        ".config/lefthook.yml",
        "lefthook-local.yml",
        "lefthook-local.toml",
        ".lefthook-local.yml",
        ".config/lefthook-local.json",
        ".lefthook/mern.toml",
        "web/.lefthook/lint.json",
    ],
)
def test_every_lefthook_config_name_needs_approval(repo, rel):
    assert_asks(guard(repo, rel, tool="Write", content="anything"), rel)


@pytest.mark.parametrize(
    "rel",
    [
        ".lefthook/pre-commit/lint.sh",
        ".lefthook/commit-msg/check.py",
        ".lefthook/README.md",
        ".lefthook-local/pre-push/run-tests",
        "web/.lefthook/pre-commit/format.sh",
    ],
)
def test_every_file_in_lefthook_script_dirs_needs_approval(repo, rel):
    assert_asks(guard(repo, rel, tool="Write", content="exit 0\n"), rel)


@pytest.mark.parametrize(
    "rel",
    [
        "src/app.py",
        "README.md",
        "docs/ruff.md",
        ".github/CODEOWNERS",
        "src/tsconfig.ts",
        "web/kit.json",
        "tests/conftest.py",
        "settings.json",
        "Makefile",
        "lefthook/django.yml",
        "docs/lefthook/pre-commit.sh",
        "lefthook.txt",
        "lefthook-local.md",
    ],
)
def test_other_files_pass(repo, rel):
    assert_passes(guard(repo, rel, tool="Write", content="anything"))


def test_a_pyproject_edit_inside_a_ruff_table_needs_approval(repo):
    result = guard(repo, "pyproject.toml", old_string="line-length = 88", new_string="line-length = 200")
    assert_asks(result, "pyproject.toml")


def test_a_pyproject_edit_outside_gate_tables_passes(repo):
    assert_passes(guard(repo, "pyproject.toml", old_string='version = "1.0.0"', new_string='version = "1.1.0"'))


def test_an_edit_that_breaks_the_toml_needs_approval(repo):
    assert_asks(guard(repo, "pyproject.toml", old_string="[tool.ruff]", new_string="[tool.ruff"), "pyproject.toml")


def test_an_edit_whose_old_string_is_missing_changes_nothing(repo):
    assert_passes(guard(repo, "pyproject.toml", old_string="not in the file", new_string="[tool.mypy]\nstrict = false"))


def test_a_pyproject_write_is_compared_table_by_table(repo):
    same_gates = PYPROJECT.replace('version = "1.0.0"', 'version = "2.0.0"')
    assert_passes(guard(repo, "pyproject.toml", tool="Write", content=same_gates))
    looser_tests = PYPROJECT.replace('addopts = "-q"', 'addopts = "-q -p no:warnings"')
    assert_asks(guard(repo, "pyproject.toml", tool="Write", content=looser_tests), "pyproject.toml")


def test_a_new_pyproject_with_gate_tables_needs_approval(repo):
    write(repo, "backend/placeholder.txt", "")
    new = '[project]\nname = "api"\n\n[tool.mypy]\nstrict = true\n'
    assert_asks(guard(repo, "backend/pyproject.toml", tool="Write", content=new), "backend/pyproject.toml")
    assert_passes(guard(repo, "backend/pyproject.toml", tool="Write", content='[project]\nname = "api"\n'))


def test_setup_cfg_only_guards_flake8_and_mypy_sections(repo):
    assert_asks(
        guard(repo, "setup.cfg", old_string="max-line-length = 88", new_string="max-line-length = 200"), "setup.cfg"
    )
    assert_passes(guard(repo, "setup.cfg", old_string="name = app", new_string="name = application"))
    adds_mypy = SETUP_CFG + "\n[mypy-vendor.*]\nignore_errors = True\n"
    assert_asks(guard(repo, "setup.cfg", tool="Write", content=adds_mypy), "setup.cfg")


def test_files_outside_git_pass(tmp_path, data_dir):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert_passes(guard(plain, "ruff.toml", tool="Write", content="line-length = 200\n"))


PACKAGE_JSON = {
    "name": "web",
    "scripts": {"dev": "vite", "lint": "oxlint .", "test:unit": "vitest run", "typecheck": "tsc --noEmit"},
    "dependencies": {"react": "19.0.0"},
}


def package_json(**changes) -> str:
    return json.dumps(PACKAGE_JSON | changes, indent=2)


def test_package_json_asks_only_when_lint_test_or_typecheck_config_changes(repo):
    write(repo, "web/package.json", package_json())

    def edited(**changes):
        return guard(repo, "web/package.json", tool="Write", content=package_json(**changes))

    assert_passes(edited(dependencies={"react": "19.1.0"}))
    assert_passes(edited(scripts={**PACKAGE_JSON["scripts"], "dev": "vite --host"}))
    for scripts in (
        {**PACKAGE_JSON["scripts"], "lint": "true"},
        {**PACKAGE_JSON["scripts"], "test:unit": "vitest run --passWithNoTests"},
        {key: value for key, value in PACKAGE_JSON["scripts"].items() if key != "typecheck"},
    ):
        assert_asks(edited(scripts=scripts), "web/package.json")
    assert_asks(edited(eslintConfig={"rules": {}}), "web/package.json")


MAKEFILE = """\
.PHONY: journeys seed build

build:
\tcc -o app main.c

journeys: seed
\thurl --test journeys/*.hurl

seed:
\t./scripts/seed.sh
"""


def test_a_makefile_asks_only_for_the_recipes_the_journeys_command_runs(repo):
    write(repo, "Makefile", MAKEFILE)

    def edit(old, new):
        return guard(repo, "Makefile", old_string=old, new_string=new)

    assert_passes(edit("hurl --test journeys/*.hurl", "true"))
    write(repo, ".claude/kit.json", json.dumps({"gate": {"journeys": {"command": "make journeys"}}}))
    assert_asks(edit("hurl --test journeys/*.hurl", "true"), "Makefile")
    assert_asks(edit("./scripts/seed.sh", "true"), "Makefile")
    assert_asks(edit("journeys: seed", "journeys:"), "Makefile")
    assert_passes(edit("cc -o app main.c", "cc -O2 -o app main.c"))


def test_a_makefile_in_a_make_directory_is_matched(repo):
    write(repo, "backend/Makefile", MAKEFILE)
    write(repo, ".claude/kit.json", json.dumps({"gate": {"journeys": {"command": "cd x && make -C backend journeys"}}}))
    result = guard(repo, "backend/Makefile", old_string="hurl --test journeys/*.hurl", new_string="true")
    assert_asks(result, "backend/Makefile")
    write(repo, "Makefile", MAKEFILE)
    assert_passes(guard(repo, "Makefile", old_string="hurl --test journeys/*.hurl", new_string="true"))


def test_a_gate_file_reached_through_a_symlink_needs_approval(repo):
    write(repo, "config/lint.toml", "line-length = 100\n")
    (repo / "ruff.toml").symlink_to(repo / "config" / "lint.toml")
    assert_asks(guard(repo, "ruff.toml", tool="Write", content="line-length = 200\n"), "ruff.toml")
    write(repo, ".flake8", "[flake8]\nmax-line-length = 88\n")
    (repo / "style.cfg").symlink_to(repo / ".flake8")
    assert_asks(guard(repo, "style.cfg", tool="Write", content="[flake8]\nignore = E\n"), ".flake8")


@pytest.mark.parametrize(
    "rel, content",
    [
        ("settings.json", '{"disableAllHooks": true}'),
        ("settings.json", '{"env": {"KIT_BYPASS": "all"}}'),
        ("settings.local.json", '{"env": {"KIT_BYPASS": "all"}}'),
        ("settings.json", '{"hooks": {}}'),
    ],
)
def test_claude_code_settings_need_approval_outside_any_repo(home, data_dir, rel, content):
    result = guard(home / ".claude", rel, tool="Write", content=content)
    assert_asks_about(result, "Claude Code settings", f"~/.claude/{rel}")


def test_project_and_configured_settings_need_approval(repo, home, tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    for directory, rel in (
        (repo, ".claude/settings.json"),
        (repo, "web/.claude/settings.local.json"),
        (plain, ".claude/settings.local.json"),
    ):
        result = guard(directory, rel, old_string='"x"', new_string='"y"')
        assert_asks_about(result, "Claude Code settings", str(directory / rel))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    result = guard(tmp_path / "config", "settings.json", tool="Write", content="{}")
    assert_asks_about(result, "Claude Code settings", str(tmp_path / "config" / "settings.json"))
    assert_passes(guard(plain, "notes/settings.json", tool="Write", content="{}"))


def test_claude_code_hooks_plugins_and_the_kit_state_need_approval(home, data_dir):
    for rel in (
        ".claude/hooks/orchestrator.sh",
        ".claude/kit-hooks/style-reminder.sh",
        ".claude/plugins/cache/kit/hooks/hooks.json",
    ):
        assert_asks_about(
            guard(home, rel, tool="Write", content="exit 0\n"), "Claude Code hooks or plugins", f"~/{rel}"
        )
    state = data_dir / "state" / "s1" / "main.json"
    result = guard(home, str(state), tool="Write", content='{"skills_seen": {"billing": "read"}}')
    assert_asks_about(result, "the kit's state", str(state))
    fallback = home / ".cache" / "claude-kit" / "state" / "s1" / "main.json"
    assert_asks_about(
        guard(home, str(fallback), tool="Write", content="{}"), "the kit's state", f"~/{fallback.relative_to(home)}"
    )


KIT_FILES = {
    "plugins/kit/.claude-plugin/plugin.json": '{"name": "kit"}',
    "plugins/kit/hooks/hooks.json": "{}",
    "plugins/kit/hooks/bin/run_hook.py": "",
    "settings/settings.kit.json": "{}",
    "home/hooks/orchestrator.sh": "",
    "install.sh": "",
    "home/rules/code-style.md": "# Code style\n",
    "README.md": "# kit\n",
}
KIT_OWN = [
    "plugins/kit/hooks/hooks.json",
    "plugins/kit/hooks/bin/run_hook.py",
    "settings/settings.kit.json",
    "home/hooks/orchestrator.sh",
    "install.sh",
]


@pytest.fixture
def kit_repo(make_repo, data_dir):
    return make_repo("claude-kit", files=KIT_FILES)


@pytest.fixture
def live_kit(kit_repo, monkeypatch):
    """The kit checkout Claude Code runs its plugin from."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(kit_repo / "plugins" / "kit"))
    return kit_repo


def kit_edit(session_repo, file):
    return guard(session_repo, str(file), tool="Write", content="exit 0\n")


def test_the_kit_own_hook_files_need_approval_from_another_project(repo, live_kit, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    for rel in KIT_OWN:
        assert_asks_about(kit_edit(repo, live_kit / rel), "the kit's own hook files", str(live_kit / rel))
    for rel in ("home/rules/code-style.md", "README.md"):
        assert_passes(kit_edit(repo, live_kit / rel))
    assert_asks_about(
        kit_edit(live_kit, live_kit / "install.sh"), "the kit's own hook files", str(live_kit / "install.sh")
    )


def test_a_worktree_of_the_live_kit_is_ordinary_files(repo, live_kit, home, tmp_path, monkeypatch):
    worktree = add_worktree(live_kit, tmp_path / "scratchpad" / "kit-wt" / "fix", "fix")
    for session in (repo, worktree):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(session))
        for rel in KIT_OWN:
            assert_passes(kit_edit(session, worktree / rel))
            assert_asks_about(kit_edit(session, live_kit / rel), "the kit's own hook files", str(live_kit / rel))
        assert_asks_about(
            kit_edit(session, home / ".claude" / "settings.json"), "Claude Code settings", "~/.claude/settings.json"
        )
        result = kit_edit(session, worktree / ".claude" / "settings.local.json")
        assert_asks_about(result, "Claude Code settings", str(worktree / ".claude" / "settings.local.json"))


HOOKS_JSON = "plugins/kit/hooks/hooks.json"


def test_without_a_plugin_root_the_live_kit_is_the_rules_link_target_else_the_marketplace_install(
    repo, make_repo, home, monkeypatch
):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    installed = make_repo("marketplaces/claude-kit", files=KIT_FILES)
    linked = make_repo("elsewhere/claude-kit", files=KIT_FILES)
    assert_passes(kit_edit(repo, installed / HOOKS_JSON))
    write(
        home, ".claude/plugins/known_marketplaces.json", json.dumps({"claude-kit": {"installLocation": str(installed)}})
    )
    assert_asks_about(kit_edit(repo, installed / HOOKS_JSON), "the kit's own hook files", str(installed / HOOKS_JSON))
    assert_passes(kit_edit(repo, linked / HOOKS_JSON))
    (home / ".claude" / "rules").symlink_to(linked / "home" / "rules")
    assert_asks_about(kit_edit(repo, linked / HOOKS_JSON), "the kit's own hook files", str(linked / HOOKS_JSON))
    assert_passes(kit_edit(repo, installed / HOOKS_JSON))


def test_the_live_kit_is_looked_up_in_the_configured_claude_config_dir(repo, make_repo, home, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    configured = make_repo("configured/claude-kit", files=KIT_FILES)
    default = make_repo("default/claude-kit", files=KIT_FILES)
    (home / ".claude" / "rules").symlink_to(default / "home" / "rules")
    config = tmp_path / "config"
    config.mkdir()
    (config / "rules").symlink_to(configured / "home" / "rules")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    shown = str(configured / HOOKS_JSON)
    assert_asks_about(kit_edit(repo, configured / HOOKS_JSON), "the kit's own hook files", shown)
    assert_passes(kit_edit(repo, default / HOOKS_JSON))


def test_symlinks_into_the_kit_repo_are_followed(repo, live_kit, home, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    (home / ".claude" / "rules").symlink_to(live_kit / "home" / "rules")
    (home / "bin").mkdir()
    (home / "bin" / "run_hook.py").symlink_to(live_kit / "plugins/kit/hooks/bin/run_hook.py")
    assert_passes(kit_edit(repo, home / ".claude" / "rules" / "code-style.md"))
    target = live_kit / "plugins/kit/hooks/bin/run_hook.py"
    assert_asks_about(kit_edit(repo, home / "bin" / "run_hook.py"), "the kit's own hook files", str(target))


def test_a_session_in_the_kit_repo_may_change_the_kit_but_not_claude_settings(live_kit, home, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(live_kit))
    for rel in KIT_OWN:
        assert_passes(kit_edit(live_kit, live_kit / rel))
    assert_asks_about(
        kit_edit(live_kit, home / ".claude" / "settings.json"), "Claude Code settings", "~/.claude/settings.json"
    )
    result = kit_edit(live_kit, live_kit / ".claude" / "settings.local.json")
    assert_asks_about(result, "Claude Code settings", str(live_kit / ".claude" / "settings.local.json"))
    (home / ".claude" / "settings.local.json").symlink_to(live_kit / "settings" / "settings.kit.json")
    result = kit_edit(live_kit, live_kit / "settings" / "settings.kit.json")
    assert_asks_about(result, "Claude Code settings", str(live_kit / "settings" / "settings.kit.json"))


def test_the_installed_plugin_needs_approval(repo, tmp_path, monkeypatch):
    plugin_root = tmp_path / "plugin-cache" / "kit"
    write(plugin_root, "hooks/bin/run_hook.py", "")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
    target = plugin_root / "hooks" / "bin" / "run_hook.py"
    assert_asks_about(kit_edit(repo, target), "the kit's own hook files", str(target))
