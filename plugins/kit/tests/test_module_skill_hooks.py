import json

import pytest
from conftest import read_state
from hookrun import base_payload, commit_all, decision, edit_input, findings, launch, tool_payload, write

BILLING_SKILL = "---\nname: billing\ndescription: Billing rules\npaths:\n  - src/billing/**\n---\n\n# Billing\n"
FRONTEND_SKILL = "---\nname: frontend\npaths: web/**/*.{ts,tsx}\n---\n\n# Frontend\n"
NOTES_SKILL = "---\nname: notes\ndescription: no paths, so not a module skill\n---\n"


@pytest.fixture
def repo(make_repo, data_dir):
    return make_repo(
        files={
            ".claude/skills/billing/SKILL.md": BILLING_SKILL,
            ".claude/skills/web-ui/SKILL.md": FRONTEND_SKILL,
            ".claude/skills/notes/SKILL.md": NOTES_SKILL,
            "src/billing/invoice.py": "TOTAL = 1\n",
            "src/billing/legacy.py": "OLD = 1\n",
            "src/app.py": "APP = 1\n",
        }
    )


def guard(repo, rel, **options):
    return launch("guard_module_skill", edit_input(repo, rel, **options))


def read_skill_md(repo, directory, extra=None, **options):
    tool_input = {"file_path": str(repo / ".claude" / "skills" / directory / "SKILL.md"), **(extra or {})}
    return launch("record_skill_seen", tool_payload(repo, "PostToolUse", "Read", tool_input, **options))


def invoke_skill(repo, value, **options):
    return launch("record_skill_seen", tool_payload(repo, "PostToolUse", "Skill", {"skill": value}, **options))


def expand_command(repo, command, expansion_type="slash_command"):
    payload = base_payload(repo, "UserPromptExpansion") | {
        "expansion_type": expansion_type,
        "command_name": command,
        "command_args": "",
        "command_source": "projectSettings",
        "prompt": f"/{command}",
    }
    return launch("record_skill_seen", payload)


def skills_seen(data_dir, agent="main"):
    return read_state(data_dir, agent=agent)["skills_seen"]


def test_editing_a_module_file_is_denied_until_its_skill_is_read(repo, data_dir):
    denied = decision(guard(repo, "src/billing/invoice.py"))
    assert denied["permissionDecision"] == "deny"
    assert denied["permissionDecisionReason"] == (
        "kit: src/billing/invoice.py belongs to a module whose skill you haven't loaded in this session.\n"
        "- Read .claude/skills/billing/SKILL.md (or invoke the billing skill)\n"
        "...then retry the edit."
    )
    assert read_skill_md(repo, "billing").returncode == 0
    assert skills_seen(data_dir) == {"billing": "read"}
    allowed = guard(repo, "src/billing/invoice.py")
    assert (allowed.returncode, allowed.stdout, allowed.stderr) == (0, "", "")


def test_the_deny_reason_names_the_skill_directory_and_name(repo):
    reason = decision(guard(repo, "web/app/page.tsx", tool="Write"))["permissionDecisionReason"]
    assert "- Read .claude/skills/web-ui/SKILL.md (or invoke the frontend skill)" in reason


@pytest.mark.parametrize(
    "rel",
    [
        "src/app.py",
        "web/app/page.js",
        "src/billing/migrations/0001_initial.py",
        ".claude/skills/billing/SKILL.md",
        ".claude/skills/billing/ARCHITECTURE.md",
    ],
)
def test_files_outside_module_skills_or_exempt_pass(repo, rel):
    result = guard(repo, rel)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_files_outside_git_pass(tmp_path, data_dir):
    plain = tmp_path / "plain"
    write(plain, ".claude/skills/billing/SKILL.md", BILLING_SKILL)
    result = guard(plain, "src/billing/invoice.py")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_each_subagent_must_load_the_skill_itself(repo):
    read_skill_md(repo, "billing")
    assert decision(guard(repo, "src/billing/invoice.py", agent_id="agent-1"))["permissionDecision"] == "deny"
    read_skill_md(repo, "billing", agent_id="agent-1")
    assert guard(repo, "src/billing/invoice.py", agent_id="agent-1").stdout == ""


def test_the_skill_tool_resolves_directory_aliases(repo, data_dir):
    invoke_skill(repo, "billing")
    invoke_skill(repo, "web-ui")
    assert skills_seen(data_dir) == {"billing": "skill", "frontend": "skill"}
    assert guard(repo, "web/app/page.tsx").stdout == ""


def test_a_plugin_skill_with_the_same_name_does_not_count(repo, data_dir):
    invoke_skill(repo, "someplugin:billing")
    expand_command(repo, "someplugin:billing")
    assert not (data_dir / "state").exists()
    assert decision(guard(repo, "src/billing/invoice.py"))["permissionDecision"] == "deny"


def test_a_directory_qualified_name_counts_when_it_names_the_repo_root(repo, data_dir):
    payload = tool_payload(repo, "PostToolUse", "Skill", {"skill": "../..:billing"})
    launch("record_skill_seen", payload | {"cwd": str(repo / "src" / "billing")})
    launch("record_skill_seen", payload | {"cwd": str(repo / "src"), "tool_input": {"skill": "billing-ui:frontend"}})
    assert skills_seen(data_dir) == {"billing": "skill"}


@pytest.mark.parametrize(
    "extra, counts",
    [
        ({}, True),
        ({"offset": 1, "limit": 2000}, True),
        ({"limit": 1}, False),
        ({"offset": 3}, False),
    ],
)
def test_only_a_read_of_the_whole_skill_file_counts(repo, data_dir, extra, counts):
    read_skill_md(repo, "billing", extra)
    seen = skills_seen(data_dir) if (data_dir / "state").exists() else {}
    assert seen == ({"billing": "read"} if counts else {})


def test_a_long_skill_file_needs_a_limit_that_covers_it(repo, data_dir):
    write(repo, ".claude/skills/billing/SKILL.md", BILLING_SKILL + "rule\n" * 2500)
    read_skill_md(repo, "billing")
    read_skill_md(repo, "billing", {"limit": 2000})
    assert not (data_dir / "state").exists()
    read_skill_md(repo, "billing", {"limit": 3000})
    assert skills_seen(data_dir) == {"billing": "read"}


def test_a_symlinked_module_directory_is_still_guarded(repo):
    (repo / "shared").mkdir()
    (repo / "src" / "billing").rename(repo / "shared" / "billing")
    (repo / "src" / "billing").symlink_to(repo / "shared" / "billing")
    reason = decision(guard(repo, "src/billing/invoice.py"))["permissionDecisionReason"]
    assert reason.startswith("kit: src/billing/invoice.py belongs to a module")
    write(repo, ".claude/skills/billing/SKILL.md", BILLING_SKILL.replace("src/billing/**", "shared/billing/**"))
    (repo / "link").symlink_to(repo / "shared" / "billing")
    assert decision(guard(repo, "link/invoice.py"))["permissionDecision"] == "deny"


def test_the_real_files_behind_a_symlinked_module_path_are_guarded(repo):
    (repo / "real_billing").mkdir()
    (repo / "src" / "billing" / "invoice.py").rename(repo / "real_billing" / "invoice.py")
    (repo / "src" / "billing" / "legacy.py").unlink()
    (repo / "src" / "billing").rmdir()
    (repo / "src" / "billing").symlink_to(repo / "real_billing")
    reason = decision(guard(repo, "real_billing/invoice.py"))["permissionDecisionReason"]
    assert reason.startswith("kit: real_billing/invoice.py belongs to a module")
    assert guard(repo, "real_elsewhere/invoice.py").stdout == ""
    read_skill_md(repo, "billing")
    assert guard(repo, "real_billing/invoice.py").stdout == ""


@pytest.fixture
def locked():
    """Makes directories read-only for one test and restores them afterwards."""
    locked_dirs = []

    def lock(directory):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o500)
        locked_dirs.append(directory)

    yield lock
    for directory in locked_dirs:
        directory.chmod(0o700)


def test_an_unwritable_plugin_data_dir_falls_back_to_the_user_cache(repo, tmp_path, monkeypatch, locked):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    locked(tmp_path / "locked-data")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "locked-data"))
    assert decision(guard(repo, "src/billing/invoice.py"))["permissionDecision"] == "deny"
    read_skill_md(repo, "billing")
    assert guard(repo, "src/billing/invoice.py").stdout == ""
    assert (home / ".cache" / "claude-kit" / "state" / "s1" / "main.json").is_file()


def test_the_guard_allows_with_a_warning_when_no_state_can_be_written(repo, tmp_path, monkeypatch, locked):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    locked(home / ".cache")
    locked(tmp_path / "locked-data")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "locked-data"))
    result = guard(repo, "src/billing/invoice.py")
    assert (result.returncode, result.stderr) == (0, "")
    message = json.loads(result.stdout)["systemMessage"]
    assert message.startswith("kit: the module-skill guard can't use its state (PermissionError")
    assert message.endswith(f"Make {home / '.cache' / 'claude-kit'} writable to turn the guard back on.")


def test_a_notebook_edit_under_a_module_is_guarded(repo):
    payload = tool_payload(
        repo, "PreToolUse", "NotebookEdit", {"notebook_path": str(repo / "src/billing/report.ipynb"), "new_source": ""}
    )
    assert decision(launch("guard_module_skill", payload))["permissionDecision"] == "deny"


def test_a_typed_slash_command_counts_as_loading_the_skill(repo, data_dir):
    expand_command(repo, "billing")
    expand_command(repo, "some-server:prompt", expansion_type="mcp_prompt")
    assert skills_seen(data_dir) == {"billing": "prompt"}


def test_reading_a_skill_file_outside_the_project_records_nothing(repo, tmp_path, data_dir):
    outside = write(tmp_path / "elsewhere", ".claude/skills/billing/SKILL.md", BILLING_SKILL)
    launch("record_skill_seen", tool_payload(repo, "PostToolUse", "Read", {"file_path": str(outside)}))
    launch("record_skill_seen", tool_payload(repo, "PostToolUse", "Read", {"file_path": str(repo / "src/app.py")}))
    assert not (data_dir / "state").exists()


def test_a_symlinked_skill_directory_still_counts_as_read(repo, tmp_path, data_dir):
    shared = write(tmp_path / "shared-skills", "payments/SKILL.md", "---\nname: payments\npaths: src/pay/**\n---\n")
    (repo / ".claude" / "skills" / "payments").symlink_to(shared.parent)
    read_skill_md(repo, "payments")
    assert skills_seen(data_dir) == {"payments": "read"}


def test_compaction_forgets_reads_but_keeps_skill_invocations(repo, data_dir):
    read_skill_md(repo, "billing")
    invoke_skill(repo, "frontend")
    expand_command(repo, "notes")
    read_skill_md(repo, "web-ui")
    assert skills_seen(data_dir) == {"billing": "read", "frontend": "skill", "notes": "prompt"}
    result = launch("record_skill_seen", base_payload(repo, "PostCompact") | {"trigger": "auto"}, "--compact")
    assert (result.returncode, result.stdout) == (0, "")
    assert skills_seen(data_dir) == {"frontend": "skill", "notes": "prompt"}
    assert decision(guard(repo, "src/billing/invoice.py"))["permissionDecision"] == "deny"


def session_start(repo, source="startup", session="s1"):
    return launch("session_context", base_payload(repo, "SessionStart", session=session) | {"source": source})


def stop(repo, active=False, agent_id=None):
    event = "SubagentStop" if agent_id else "Stop"
    payload = base_payload(repo, event, agent_id=agent_id) | {"stop_hook_active": active, "last_assistant_message": ""}
    return launch("stop_module_skills", payload)


@pytest.fixture
def started(repo):
    commit_all(repo)
    write(repo, "src/billing/legacy.py", "OLD = 2  # dirty before the session\n")
    assert session_start(repo).returncode == 0
    return repo


def test_stop_blocks_on_a_bash_edit_to_a_module_file_until_the_skill_is_read(started, data_dir):
    write(started, "src/billing/invoice.py", "TOTAL = 2\n")
    write(started, "src/billing/new/tax.py", "RATE = 1\n")
    write(started, "src/app.py", "APP = 2\n")
    blocked = stop(started)
    assert blocked.returncode == 0
    output = json.loads(blocked.stdout)
    assert output["decision"] == "block"
    assert output["reason"].startswith("kit: 2 changed files belong to module skills nobody loaded: billing.\n")
    assert findings(output["reason"]) == (
        "kit: you changed files that belong to module skills you haven't loaded:\n"
        "- billing (.claude/skills/billing/SKILL.md): src/billing/invoice.py, src/billing/new/tax.py\n"
        "Read the skill, re-check your change against it, then finish."
    )
    read_skill_md(started, "billing")
    allowed = stop(started, active=True)
    assert (allowed.returncode, allowed.stdout, allowed.stderr) == (0, "", "")
    assert "module_skills_streak" not in read_state(data_dir)


def test_stop_blocks_three_times_then_lets_go_with_a_warning(started):
    write(started, "src/billing/invoice.py", "TOTAL = 2\n")
    for attempt in range(3):
        assert json.loads(stop(started, active=attempt > 0).stdout)["decision"] == "block"
    released = json.loads(stop(started, active=True).stdout)
    assert set(released) == {"systemMessage"}
    assert released["systemMessage"].startswith(
        "kit: blocked 3 times in a row on the same problems, so Claude may finish, but changed files still "
        "belong to module skills that weren't loaded (billing): "
    )
    assert "\n" not in released["systemMessage"]
    assert stop(started).stdout == "", "the warning isn't repeated"
    write(started, "web/page.tsx", "export {}\n")
    assert json.loads(stop(started, active=True).stdout)["decision"] == "block", "a new missing skill blocks again"


def test_stop_blocks_a_new_module_skill_without_architecture(started):
    write(
        started,
        ".claude/skills/payments/SKILL.md",
        "---\nname: payments\npaths:\n  - src/payments/**\n---\n\n# Payments\n",
    )
    read_skill_md(started, "payments")
    blocked = stop(started)
    assert json.loads(blocked.stdout) == {
        "decision": "block",
        "reason": "kit: new module skill without ARCHITECTURE.md: payments. Add .claude/skills/<module>/"
        "ARCHITECTURE.md from the skills-bootstrap template, then finish.",
    }
    write(started, ".claude/skills/payments/ARCHITECTURE.md", "# Payments: architecture\n")
    assert stop(started, active=True).stdout == ""


def test_stop_reminds_without_blocking_on_an_existing_module_without_architecture(started):
    write(started, ".claude/skills/billing/FLOWS.md", "# Billing: user flows\n")
    read_skill_md(started, "billing")
    result = stop(started)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "systemMessage": "kit: module skill missing ARCHITECTURE.md: billing. Add it in a future change."
    }
    write(started, ".claude/skills/billing/ARCHITECTURE.md", "# Billing: architecture\n")
    assert stop(started, active=True).stdout == ""


def test_the_architecture_reminder_shows_once_per_module_per_session(started):
    write(started, ".claude/skills/billing/FLOWS.md", "# Billing: user flows\n")
    read_skill_md(started, "billing")
    assert "missing ARCHITECTURE.md: billing." in json.loads(stop(started).stdout)["systemMessage"]
    write(started, ".claude/skills/billing/FLOWS.md", "# Billing: user flows\n\nMore.\n")
    assert stop(started).stdout == ""
    launch("session_context", base_payload(started, "SubagentStart", agent_id="agent-1"))
    write(started, ".claude/skills/billing/FLOWS.md", "# Billing: user flows\n\nEven more.\n")
    read_skill_md(started, "billing", agent_id="agent-1")
    assert stop(started, agent_id="agent-1").stdout == "", "another agent of the session isn't reminded either"
    write(started, ".claude/skills/web-ui/FLOWS.md", "# Frontend: user flows\n")
    read_skill_md(started, "web-ui")
    assert json.loads(stop(started).stdout) == {
        "systemMessage": "kit: module skill missing ARCHITECTURE.md: frontend. Add it in a future change."
    }


def test_a_module_skill_folder_moved_with_plain_mv_is_not_a_new_module(started):
    (started / ".claude/skills/billing").rename(started / ".claude/skills/invoicing")
    read_skill_md(started, "invoicing")
    result = stop(started)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "systemMessage": "kit: module skill missing ARCHITECTURE.md: billing. Add it in a future change."
    }


def test_a_module_skill_renamed_in_its_frontmatter_is_a_new_module(started):
    write(started, ".claude/skills/billing/SKILL.md", BILLING_SKILL.replace("name: billing", "name: invoicing"))
    read_skill_md(started, "billing")
    blocked = json.loads(stop(started).stdout)
    assert blocked["decision"] == "block"
    assert blocked["reason"].startswith("kit: new module skill without ARCHITECTURE.md: invoicing.")


def test_stop_ignores_files_dirty_before_the_session_and_exempt_files(started):
    write(started, "src/billing/migrations/0002.py", "OPS = []\n")
    result = stop(started)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_a_subagent_is_checked_only_for_changes_made_since_it_started(started):
    write(started, "src/billing/invoice.py", "TOTAL = 2  # main agent's change\n")
    launch("session_context", base_payload(started, "SubagentStart", agent_id="agent-1"))
    assert stop(started, agent_id="agent-1").stdout == ""
    write(started, "web/page.tsx", "export {}\n")
    reason = json.loads(stop(started, agent_id="agent-1").stdout)["reason"]
    assert "- frontend (.claude/skills/web-ui/SKILL.md): web/page.tsx" in reason
    assert "billing" not in reason


def test_an_agent_without_a_start_snapshot_is_not_checked(started):
    write(started, "src/billing/invoice.py", "TOTAL = 2\n")
    result = stop(started, agent_id="internal-suggestion-agent")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_stop_passes_without_module_skills_or_outside_git(make_repo, tmp_path, data_dir):
    repo = make_repo("plain-repo", files={"a.py": "A = 1\n"})
    write(repo, "b.py", "B = 1\n")
    assert stop(repo).stdout == ""
    outside = tmp_path / "not-git"
    outside.mkdir()
    assert stop(outside).stdout == ""


def test_session_start_records_initially_dirty_once(started, data_dir):
    assert read_state(data_dir)["initially_dirty"] == ["src/billing/legacy.py"]
    write(started, "src/app.py", "APP = 3\n")
    for source in ("resume", "compact", "clear"):
        session_start(started, source=source)
    assert read_state(data_dir)["initially_dirty"] == ["src/billing/legacy.py"]


def test_session_context_lists_facts_for_claude(repo):
    write(
        repo,
        ".claude/kit.json",
        json.dumps(
            {
                "gate": {
                    "journeys": {"command": "hurl --test j/*.hurl"},
                    "typecheck": ["uv run mypy src", "npx tsc --noEmit"],
                }
            }
        ),
    )
    commit_all(repo)
    output = decision(session_start(repo))
    assert output["hookEventName"] == "SessionStart"
    context = output["additionalContext"]
    lines = context.splitlines()
    assert lines[1] in ("- Git branch: main", "- Git branch: master")
    assert "- Journeys command (stop gate): hurl --test j/*.hurl" in lines
    assert "typecheck" not in context.lower() and "Type-check" not in context
    assert "  - billing: src/billing/**" in lines
    assert "  - frontend: web/**/*.{ts,tsx}" in lines
    assert "notes" not in context
    assert "lint-on-edit" in context and "secret scanning" in context and "stop gates" in context


def test_session_context_stays_under_its_size_limit(repo):
    for index in range(80):
        write(repo, f".claude/skills/module-{index:02}/SKILL.md", f"---\npaths: src/module_{index:02}/**\n---\n")
    context = decision(session_start(repo))["additionalContext"]
    assert len(context) <= 1500
    assert context.splitlines()[-1].startswith("  - ...and ")
    assert context.splitlines()[-1].endswith(" more in .claude/skills/")


def test_session_context_is_silent_outside_git(tmp_path, data_dir):
    result = session_start(tmp_path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (data_dir / "state").exists()


def test_subagent_start_records_the_agent_snapshot_and_prints_nothing(started, data_dir):
    write(started, "src/app.py", "APP = 4\n")
    result = launch("session_context", base_payload(started, "SubagentStart", agent_id="agent-9"))
    assert (result.returncode, result.stdout) == (0, "")
    assert read_state(data_dir, agent="agent-9")["initially_dirty"] == ["src/app.py", "src/billing/legacy.py"]
