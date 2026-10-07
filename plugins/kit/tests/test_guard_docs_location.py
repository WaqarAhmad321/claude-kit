import json

import doc_name_cases
import pytest
import run_hook
from hookrun import decision, launch, registered, tool_payload, write

SCRIPT = "guard_docs_location"
KIT_MARKER = "plugins/kit/.claude-plugin/plugin.json"
SCRATCHPAD = "/tmp/claude-1000/-home-user-project/abc123/scratchpad"
GENERIC_SCRATCHPAD = "your session scratchpad, outside the repo"
DENY_TEMPLATE = (
    "kit: new Markdown file blocked: {rel}. Docs live only in README.md, CLAUDE.md, CHANGELOG.md and "
    "docs/{{architecture,decisions,api,partners,runbooks,guides}}/. Summaries, plans, reports and fix notes go in "
    "your reply, the PR or a PM card; scratch work goes in {scratchpad}. For lasting docs, update a file listed in "
    "docs/README.md or add docs/decisions/NNNN-title.md, docs/runbooks/<task>.md or docs/api/handoffs/<feature>.md "
    "and index it."
)
ASK_TEMPLATE = (
    "New Markdown file outside the docs layout: {rel}. Approve only for a new kind of lasting doc, "
    "then add it to docs.allowed in .claude/kit.json."
)


@pytest.fixture(autouse=True)
def no_bypass(monkeypatch):
    monkeypatch.delenv("KIT_BYPASS", raising=False)


@pytest.fixture
def repo(make_repo):
    return make_repo(files={"src/app.py": "A = 1\n"})


def write_file(cwd, file_path, **extra):
    tool_input = {"file_path": str(file_path), "content": "# Title\n"}
    return launch(SCRIPT, tool_payload(cwd, "PreToolUse", "Write", tool_input) | extra)


def write_doc(repo, rel, **extra):
    return write_file(repo, repo / rel, **extra)


def bash(cwd, command, **extra):
    return launch(SCRIPT, tool_payload(cwd, "PreToolUse", "Bash", {"command": command}) | extra)


def passes(result) -> bool:
    return (result.returncode, result.stdout, result.stderr) == (0, "", "")


def denies(result) -> str:
    answer = decision(result)
    assert answer["permissionDecision"] == "deny", answer
    return answer["permissionDecisionReason"]


def asks(result) -> str:
    answer = decision(result)
    assert answer["permissionDecision"] == "ask", answer
    return answer["permissionDecisionReason"]


def test_editing_an_existing_markdown_file_passes(repo):
    write(repo, "SUMMARY.md", "# Old summary\n")
    assert passes(write_doc(repo, "SUMMARY.md"))


def test_a_new_architecture_doc_under_a_module_skill_passes(repo):
    assert passes(write_doc(repo, ".claude/skills/billing/ARCHITECTURE.md"))


@pytest.mark.parametrize("rel", ["summary.txt", "notes.py", "docs/guides/Fix_Notes.json"])
def test_a_new_file_that_isnt_markdown_passes(repo, rel):
    assert passes(write_doc(repo, rel))


def test_a_markdown_file_outside_the_repo_passes(repo):
    assert passes(write_file(repo, repo.parent / "scratchpad" / "SUMMARY.md"))


def test_nothing_is_checked_outside_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert passes(write_file(plain, plain / "SUMMARY.md"))
    assert passes(bash(plain, "cat > SUMMARY.md <<'EOF'\n# Summary\nEOF"))


@pytest.mark.parametrize(
    "rel", ["README.md", "CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE.md"]
)
def test_root_docs_pass(repo, rel):
    assert passes(write_doc(repo, rel))


@pytest.mark.parametrize("rel", ["docs/README.md", "packages/api/README.md", "apps/web/src/README.md"])
def test_the_docs_index_and_package_readmes_pass(repo, rel):
    assert passes(write_doc(repo, rel))


@pytest.mark.parametrize(
    "rel",
    [
        "docs/architecture/request-lifecycle.md",
        "docs/api/orders.md",
        "docs/api/handoffs/checkout-flow.md",
        "docs/partners/stripe.md",
        "docs/runbooks/restore-backup.md",
        "docs/guides/local-setup.mdx",
    ],
)
def test_a_kebab_case_doc_in_a_docs_dir_passes(repo, rel):
    assert passes(write_doc(repo, rel))


def test_a_numbered_decision_passes(repo):
    assert passes(write_doc(repo, "docs/decisions/0001-use-postgres.md"))


@pytest.mark.parametrize(
    "rel",
    [
        "docs/decisions/use-postgres.md",
        "docs/decisions/1-use-postgres.md",
        "docs/decisions/0001_Use_Postgres.md",
        "docs/decisions/0001-use-postgres.mdx",
    ],
)
def test_a_decision_without_a_four_digit_number_is_denied(repo, rel):
    assert rel in denies(write_doc(repo, rel))


@pytest.mark.parametrize(
    "rel", ["docs/guides/Local_Setup.md", "docs/architecture/Overview.md", "docs/api/handoffs/checkout flow.md"]
)
def test_a_name_that_isnt_kebab_case_in_a_docs_dir_is_denied(repo, rel):
    assert rel in denies(write_doc(repo, rel))


@pytest.mark.parametrize(
    "rel",
    [
        "SUMMARY.md",
        "fix-notes.md",
        "plan_v2.md",
        "round3.md",
        "final report.md",
        "src/billing/IMPLEMENTATION_STATUS.md",
        "docs/SUMMARY.md",
        "docs/runbooks/db/fix-notes.md",
    ],
)
def test_a_summary_plan_or_report_name_is_denied_outside_the_named_docs_dirs(repo, rel):
    assert rel in denies(write_doc(repo, rel))


@pytest.mark.parametrize(
    "rel",
    [
        "docs/api/handoffs/report-export.md",
        "docs/runbooks/fix-stuck-queue.md",
        "docs/decisions/0007-subscription-plan-tiers.md",
        "docs/guides/latest.md",
        "docs/architecture/status-page.md",
    ],
)
def test_a_scratch_word_passes_in_a_well_named_docs_dir_doc(repo, rel):
    assert passes(write_doc(repo, rel))


@pytest.mark.parametrize("rel", doc_name_cases.WELL_NAMED)
def test_a_well_named_doc_passes_as_in_docs_check(repo, rel):
    assert passes(write_doc(repo, rel))


@pytest.mark.parametrize("rel", doc_name_cases.SCRATCH)
def test_a_scratch_versioned_or_dated_name_is_denied_as_in_docs_check(repo, rel):
    assert rel in denies(write_doc(repo, rel))


@pytest.mark.parametrize("rel", ["docs/runbooks/FIX_NOTES.md", "docs/decisions/plan-v2.md"])
def test_a_scratch_word_in_a_badly_named_docs_dir_doc_is_still_denied(repo, rel):
    assert rel in denies(write_doc(repo, rel))


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/HANDOFF.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "plugins/x/skills/y/references/fix-patterns.md",
        "plugins/kit/agents/spec-reviewer.md",
        "fixtures/summary.md",
        "tests/fixtures/report.md",
        "web/src/__fixtures__/notes.md",
        "pkg/parser/testdata/plan.md",
        "web/node_modules/lib/NOTES.md",
    ],
)
def test_tooling_skill_template_and_fixture_dirs_pass(repo, rel):
    assert passes(write_doc(repo, rel))


def test_a_glob_in_docs_allowed_passes(repo):
    write(repo, ".claude/kit.json", json.dumps({"docs": {"allowed": ["specs/**"]}}))
    assert passes(write_doc(repo, "specs/checkout/flow-notes.md"))
    assert asks(write_doc(repo, "rfcs/checkout.md")) == ASK_TEMPLATE.format(rel="rfcs/checkout.md")


def test_a_malformed_docs_allowed_fails_closed(repo):
    write(repo, ".claude/kit.json", json.dumps({"docs": {"allowed": "specs/**"}}))
    result = write_doc(repo, "rfcs/checkout.md")
    assert result.returncode == 2
    assert "docs.allowed" in result.stderr and "KIT_BYPASS=guard_docs_location" in result.stderr


def test_a_scratch_name_is_denied_but_a_new_kind_of_doc_asks(repo):
    assert "notes-ideas.md" in denies(write_doc(repo, "notes-ideas.md"))
    reason = asks(write_doc(repo, "ideas/architecture-overview.md"))
    assert reason == ASK_TEMPLATE.format(rel="ideas/architecture-overview.md")


@pytest.mark.parametrize("rel", ["docs/overview.md", "docs/runbooks/db/restore.md", "Architecture.md"])
def test_a_doc_outside_the_layout_asks(repo, rel):
    assert asks(write_doc(repo, rel)) == ASK_TEMPLATE.format(rel=rel)


def test_the_deny_message_names_the_file_and_the_session_scratchpad(repo):
    reason = denies(write_doc(repo, "docs/SUMMARY.md", scratchpad_dir=SCRATCHPAD))
    assert reason == DENY_TEMPLATE.format(rel="docs/SUMMARY.md", scratchpad=SCRATCHPAD)


def test_without_a_scratchpad_dir_the_deny_message_points_at_the_scratchpad_generically(repo):
    reason = denies(write_doc(repo, "SUMMARY.md"))
    assert reason == DENY_TEMPLATE.format(rel="SUMMARY.md", scratchpad=GENERIC_SCRATCHPAD)


def test_a_relative_path_resolves_against_cwd(repo):
    assert "src/NOTES.md" in denies(write_file(repo / "src", "NOTES.md"))


def test_bash_heredoc_into_a_summary_is_denied(repo):
    reason = denies(bash(repo, "cat > SUMMARY.md <<'EOF'\n# Summary\n- done\nEOF", scratchpad_dir=SCRATCHPAD))
    assert reason == DENY_TEMPLATE.format(rel="SUMMARY.md", scratchpad=SCRATCHPAD)


def test_bash_append_to_a_new_kebab_doc_passes(repo):
    assert passes(bash(repo, "echo x >> docs/guides/setup.md"))


@pytest.mark.parametrize(
    "command",
    ["echo x | tee -a report.md", "echo x | tee --append report.md >/dev/null", "echo x | sudo tee report.md"],
)
def test_bash_tee_into_a_report_is_denied(repo, command):
    assert "report.md" in denies(bash(repo, command))


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --title t --body-file - <<'EOF'\n> SUMMARY.md\n> quoted > notes.md\nEOF",
        'git commit -F - <<"EOF"\nfix: x\n\n> see report.md\nEOF',
        "cat <<-EOF | wc -l\n\t> plan.md\n\tEOF",
    ],
)
def test_bash_blockquotes_in_a_heredoc_body_are_ignored(repo, command):
    assert passes(bash(repo, command))


@pytest.mark.parametrize("command", ["ls docs 2>/dev/null", "echo oops >&2", "make 2>&1 | tail -n 5"])
def test_bash_fd_redirects_are_ignored(repo, command):
    assert passes(bash(repo, command))


def test_bash_every_redirect_counts_not_only_the_last(repo):
    assert "SUMMARY.md" in denies(bash(repo, "echo x > SUMMARY.md 2>/dev/null"))


def test_bash_append_to_an_existing_markdown_file_passes(repo):
    write(repo, "NOTES.md", "# Notes\n")
    assert passes(bash(repo, "echo more >> NOTES.md"))


@pytest.mark.parametrize(
    ("command", "rel"),
    [
        ("cat > \"final report.md\" <<'EOF'\nx\nEOF", "final report.md"),
        ("echo x > 'docs/guides/Setup Guide.md'", "docs/guides/Setup Guide.md"),
    ],
)
def test_bash_quoted_targets_are_read(repo, command, rel):
    assert rel in denies(bash(repo, command))


def test_bash_with_several_targets_the_most_severe_wins(repo):
    assert "SUMMARY.md" in denies(bash(repo, "echo a > ideas.md && echo b > SUMMARY.md"))
    assert asks(bash(repo, "echo a > docs/guides/setup.md; echo b > ideas.md")) == ASK_TEMPLATE.format(rel="ideas.md")


def test_bash_follows_cd(repo):
    outside = repo.parent / "scratch"
    outside.mkdir()
    assert "docs/guides/Setup.md" in denies(bash(repo, "mkdir -p docs/guides && cd docs/guides && echo x > Setup.md"))
    assert passes(bash(repo, f"cd {outside} && cat > SUMMARY.md <<'EOF'\nx\nEOF"))


def test_the_kit_templates_pass_but_templates_elsewhere_are_checked(make_repo, repo):
    kit = make_repo("claude-kit", files={KIT_MARKER: "{}\n"})
    assert passes(write_doc(kit, "templates/mern/docs/plan-notes.md"))
    assert passes(bash(kit, "echo x > templates/mern/SUMMARY.md"))
    assert "templates/pr-summary.md" in denies(write_doc(repo, "templates/pr-summary.md"))


def test_a_repo_in_the_session_scratchpad_is_left_alone(make_repo, tmp_path, repo):
    scratchpad = tmp_path / "session" / "scratchpad"
    smoke = make_repo("session/scratchpad/smoke")
    extra = {"scratchpad_dir": str(scratchpad)}
    assert passes(write_file(repo, smoke / "SUMMARY.md", **extra))
    assert passes(bash(repo, f"echo x > {smoke / 'report.md'}", **extra))
    assert "SUMMARY.md" in denies(write_doc(repo, "SUMMARY.md", **extra)), "the project itself is still guarded"


def test_a_repo_under_tmpdir_is_left_alone_when_the_project_is_elsewhere(make_repo, tmp_path, monkeypatch):
    scratch = make_repo("scratch")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/nonexistent/kit-test-project")
    assert passes(write_doc(scratch, "SUMMARY.md"))
    assert passes(bash(scratch, "echo x | tee notes.md"))


def test_the_guard_is_fail_closed_and_runs_on_write_and_bash():
    matchers = [matcher for matcher, scripts in registered("PreToolUse").items() if SCRIPT in scripts]
    assert sorted(matchers) == ["Bash", "Write"]
    assert SCRIPT in run_hook.FAIL_CLOSED_HOOKS
