import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import reviewlib
import stopscope
from conftest import read_state
from hookrun import (
    GIT_IDENTITY,
    MAIN_REASON_LINES,
    base_payload,
    commit_all,
    findings,
    launch,
    registered,
    tool_payload,
    write,
)

BLOCK_REASON = (
    "Non-trivial change without an independent review: run the kit:reviewer agent on the current diff, fix what "
    "it reports, and re-run it until it ends with VERDICT: PASS."
)
REVIEW_ACTION = "Run the kit:reviewer agent on the current diff; fix FIX findings and re-run until PASS."
TEAMMATE_IDENTITY = ["-c", "user.name=teammate", "-c", "user.email=teammate@example.com", "-c", "commit.gpgsign=false"]
REPORT = "Base: abc123 (start commit)\nChange: adds invoices, 2 files\n\nNo findings.\n\n"
PASS = REPORT + "VERDICT: PASS"
FIX = REPORT + "VERDICT: FIX"


def numbered(count: int, word: str = "line") -> str:
    return "".join(f"{word} {number}\n" for number in range(count))


@pytest.fixture
def repo(make_repo, data_dir) -> Path:
    """A committed repo whose git identity is the one its commits use, as on the user's own machine."""
    repo = make_repo(files={"README.md": "# app\n", "app.py": numbered(5, "base")})
    commit_all(repo)
    git(repo, "config", "user.name", "kit")
    git(repo, "config", "user.email", "kit@example.com")
    return repo


def configured(repo: Path, review: dict) -> Path:
    write(repo, ".claude/kit.json", json.dumps({"gate": {"review": review}}))
    commit_all(repo)
    return repo


def start(repo: Path, agent_id: str | None = None) -> None:
    event = "SubagentStart" if agent_id else "SessionStart"
    assert launch("session_context", base_payload(repo, event, agent_id=agent_id)).returncode == 0


def stop(repo: Path, active: bool = False, running: tuple[str, ...] = ()):
    tasks = [{"id": task_id, "type": "subagent", "status": "running", "description": "work"} for task_id in running]
    payload = base_payload(repo, "Stop") | {"stop_hook_active": active, "background_tasks": tasks}
    return launch("stop_gate", payload)


def report(message: str, checkout: Path | str) -> str:
    """`message` with a `Reviewed:` line after its Base line, naming the checkout the reviewer reviewed."""
    base_line, rest = message.split("\n", 1)
    return f"{base_line}\nReviewed: {checkout}\n{rest}"


def reviewer_payload(repo: Path, message: str, agent_type: str = "kit:reviewer") -> dict:
    return base_payload(repo, "SubagentStop", agent_id="reviewer-1") | {
        "agent_type": agent_type,
        "stop_hook_active": False,
        "last_assistant_message": message,
    }


def reviewed(repo: Path, message: str, agent_type: str = "kit:reviewer") -> None:
    result = launch("record_review", reviewer_payload(repo, message, agent_type))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result


def handed_back(repo: Path, message: str) -> None:
    payload = tool_payload(repo, "PostToolUse", "SubagentHandback", {"message": message}, agent_id="reviewer-1")
    payload |= {"agent_type": "kit:reviewer", "tool_response": {}}
    result = launch("record_review", payload)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result


def reason_of(result) -> str:
    """The block reason itself, as the main chat shows it."""
    assert result.returncode == 0, result
    output = json.loads(result.stdout)
    assert output["decision"] == "block", output
    return output["reason"]


def blocked(result) -> str:
    """The findings of a block: a main-thread reason points to the full report, which holds them."""
    return findings(reason_of(result))


def passes(result) -> None:
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result


def notice(result) -> str:
    assert result.returncode == 0, result
    output = json.loads(result.stdout)
    assert "decision" not in output, output
    return output["systemMessage"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.mark.parametrize(
    ("message", "verdict"),
    [
        (PASS, "PASS"),
        (FIX, "FIX"),
        ("VERDICT: FIX", "FIX"),
        (PASS + "\n\n   \n", "PASS"),
        ("report\n  VERDICT: PASS  ", "PASS"),
        (REPORT + "**VERDICT: PASS**", None),
        (REPORT + "VERDICT: PASS (low findings only)", None),
        (REPORT + "Verdict: PASS", None),
        (REPORT + "VERDICT: PASS\nDocs to update: none", None),
        ("", None),
        (None, None),
    ],
)
def test_the_verdict_is_the_exact_last_non_empty_line(message, verdict):
    assert reviewlib.parse_verdict(message) == verdict


def test_a_trivial_change_passes(repo):
    start(repo)
    write(repo, "app.py", numbered(5, "base") + numbered(50))
    passes(stop(repo))


def test_a_change_over_the_size_limit_blocks_with_its_size_and_paths(repo):
    start(repo)
    write(repo, "app.py", numbered(5, "base") + numbered(51))
    reason = blocked(stop(repo))
    assert BLOCK_REASON in reason
    assert "Changed lines since the session started: 51." in reason
    assert "Paths: app.py." in reason
    assert "No reviewer verdict is recorded for this change." in reason


def test_the_block_lists_ten_paths_and_counts_the_rest(repo):
    start(repo)
    for number in range(12):
        write(repo, f"src/module_{number:02}.py", numbered(5))
    reason = blocked(stop(repo))
    listed = ", ".join(f"src/module_{number:02}.py" for number in range(10))
    assert f"Changed lines since the session started: 60.\nPaths: {listed} and 2 more." in reason
    assert "module_10" not in reason


@pytest.mark.parametrize(
    "path",
    [
        "auth/views.py",
        "backend/auth/views.py",
        "migrations/0002_invoice.py",
        "app/migrations/0002_invoice.py",
        "permissions.py",
        "core/user_permissions.py",
        "payments/stripe.py",
        "api/billing/models.py",
        "security.py",
        "core/security_headers.py",
    ],
)
def test_a_small_change_under_an_always_glob_blocks(repo, path):
    start(repo)
    write(repo, path, "x = 1\n")
    reason = blocked(stop(repo))
    assert BLOCK_REASON in reason
    assert "Changed lines since the session started: 1." in reason
    assert f"Paths: {path}." in reason


def test_configured_globs_replace_the_defaults(repo):
    configured(repo, {"always": ["**/*.sql"]})
    start(repo)
    write(repo, "auth/views.py", "x = 1\n")
    passes(stop(repo))
    write(repo, "db/schema.sql", "create table t ();\n")
    assert "Paths: db/schema.sql." in blocked(stop(repo))


def test_a_pass_verdict_clears_the_block(repo):
    start(repo)
    write(repo, "auth/views.py", numbered(80))
    blocked(stop(repo))
    reviewed(repo, PASS)
    passes(stop(repo))


@pytest.mark.parametrize("named", ["{repo}", "`{repo}`", "{repo}/"])
def test_a_pass_naming_the_checkout_clears_the_block(repo, named):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, report(PASS, named.format(repo=repo)))
    passes(stop(repo))


def test_a_pass_naming_another_place_counts_for_none_even_in_a_one_checkout_session(repo, tmp_path, data_dir):
    start(repo)
    write(repo, "app.py", numbered(80))
    message = notice(launch("record_review", reviewer_payload(repo, report(PASS, tmp_path))))
    assert message.startswith("kit: the reviewer's VERDICT: PASS counts for no checkout.")
    assert reviewlib.STATE_KEY not in read_state(data_dir)
    blocked(stop(repo))


def test_a_fix_verdict_keeps_the_block(repo):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, FIX)
    assert "The latest review ended with VERDICT: FIX." in blocked(stop(repo))
    reviewed(repo, PASS)
    passes(stop(repo))
    reviewed(repo, FIX)
    assert "VERDICT: FIX." in blocked(stop(repo)), "the latest verdict wins"


def test_a_message_without_a_verdict_records_nothing(repo, data_dir):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS + "\nThanks!")
    assert reviewlib.STATE_KEY not in read_state(data_dir)
    blocked(stop(repo))


def test_a_non_reviewer_agent_is_ignored(repo, data_dir):
    start(repo)
    write(repo, "app.py", numbered(80))
    for agent_type in ("general-purpose", "Explore", "security-reviewer", "other:reviewer", ""):
        reviewed(repo, PASS, agent_type=agent_type)
    assert reviewlib.STATE_KEY not in read_state(data_dir)
    blocked(stop(repo))


@pytest.mark.parametrize("agent_type", ["reviewer", "kit:reviewer", "kit-reviewer"])
def test_each_form_of_the_reviewer_agent_type_is_accepted(repo, data_dir, agent_type):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS, agent_type=agent_type)
    review = read_state(data_dir)[reviewlib.STATE_KEY][str(repo)]
    assert (review["verdict"], review["lines"]) == ("PASS", 85)
    passes(stop(repo))


def test_a_report_handed_back_through_subagent_handback_counts(repo):
    start(repo)
    write(repo, "app.py", numbered(80))
    handed_back(repo, PASS)
    reviewed(repo, "Report delivered.")
    passes(stop(repo))


def test_a_small_follow_up_after_a_pass_passes(repo):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS)
    write(repo, "app.py", numbered(80) + numbered(15, "follow-up"))
    passes(stop(repo))


def test_a_large_follow_up_after_a_pass_blocks(repo):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS)
    write(repo, "app.py", numbered(80) + numbered(16, "follow-up"))
    reason = blocked(stop(repo))
    assert "16 lines changed since the last VERDICT: PASS; a review covers at most 15 more." in reason


def test_a_rewrite_with_the_same_total_after_a_pass_blocks(repo):
    start(repo)
    write(repo, "app.py", numbered(5, "base") + numbered(60, "reviewed"))
    reviewed(repo, PASS)
    write(repo, "app.py", numbered(5, "base") + numbered(60, "rewritten"))
    reason = blocked(stop(repo))
    assert "Changed lines since the session started: 60." in reason
    assert "120 lines changed since the last VERDICT: PASS" in reason


def test_committing_the_reviewed_change_keeps_the_review(repo):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS)
    commit_all(repo)
    passes(stop(repo))


def test_a_disabled_review_gate_passes(repo):
    configured(repo, {"enabled": False})
    start(repo)
    write(repo, "auth/views.py", numbered(80))
    passes(stop(repo))


@pytest.mark.parametrize(
    ("review", "rel", "content"),
    [
        ({"enabled": False}, "app.py", numbered(70)),
        ({"min_lines": 1000}, "app.py", numbered(70)),
        ({"always": []}, "auth/views.py", "x = 1\n"),
    ],
    ids=["enabled", "min_lines", "always"],
)
def test_review_settings_changed_in_the_session_do_not_apply_to_it(repo, review, rel, content):
    configured(repo, {"min_lines": 60})
    start(repo)
    write(repo, ".claude/kit.json", json.dumps({"gate": {"review": review}}))
    write(repo, rel, content)
    reason = blocked(stop(repo))
    assert BLOCK_REASON in reason
    assert re.search(rf"^Paths: .*{re.escape(rel)}", reason, re.MULTILINE)


def test_a_kit_json_added_in_the_session_leaves_the_default_review(repo):
    start(repo)
    write(repo, ".claude/kit.json", json.dumps({"gate": {"review": {"enabled": False}}}))
    write(repo, "auth/views.py", "x = 1\n")
    assert "Paths: auth/views.py." in blocked(stop(repo))


def test_an_invalid_review_config_is_a_finding(repo):
    configured(repo, {"min_lines": "fifty"})
    start(repo)
    write(repo, "app.py", "x = 1\n")
    reason = blocked(stop(repo))
    assert ".claude/kit.json is invalid: gate.review.min_lines must be a whole number, 0 or more" in reason


def test_the_same_unreviewed_change_blocks_three_times_then_releases_with_one_line(repo):
    start(repo)
    write(repo, "app.py", numbered(80))
    for attempt in range(3):
        assert BLOCK_REASON in blocked(stop(repo, active=attempt > 0))
    message = notice(stop(repo, active=True))
    assert message.startswith("kit stop gate: blocked 3 times in a row on the same problems, so Claude may finish")
    assert len(message.splitlines()) == 1
    report = Path(message.rpartition("still unresolved: ")[2])
    assert BLOCK_REASON in report.read_text()
    passes(stop(repo, active=True))


def test_a_growing_unreviewed_change_blocks_five_times_in_total_then_releases(repo):
    start(repo)
    for attempt in range(5):
        write(repo, f"src/part_{attempt}.py", numbered(60))
        assert BLOCK_REASON in blocked(stop(repo, active=attempt > 0))
    write(repo, "src/part_5.py", numbered(60))
    assert notice(stop(repo, active=True)).startswith("kit stop gate: blocked 5 times in a row, so Claude may finish")


def test_a_main_thread_block_is_three_lines_that_name_the_reviewer(repo):
    start(repo)
    write(repo, "invoices.py", numbered(80))
    lines = reason_of(stop(repo)).splitlines()
    assert lines[:2] == ["Non-trivial change (80 lines) without an independent PASS review.", REVIEW_ACTION]
    assert re.fullmatch(r"Full report: /\S+-stop\.md", lines[2])
    assert len(lines) == MAIN_REASON_LINES


def test_a_one_line_change_under_an_always_glob_says_line_not_lines(repo):
    start(repo)
    write(repo, "auth/views.py", "x = 1\n")
    first_line = reason_of(stop(repo)).splitlines()[0]
    assert first_line == "Non-trivial change (1 line) without an independent PASS review."


def test_an_unreviewed_change_with_other_findings_gets_the_general_three_lines(repo):
    write(repo, ".claude/kit.json", "{not json")
    commit_all(repo)
    start(repo)
    write(repo, "app.py", numbered(80))
    reason = reason_of(stop(repo))
    assert reason.splitlines()[0].startswith("kit stop gate: invalid kit config, unreviewed change in ")
    assert len(reason.splitlines()) <= MAIN_REASON_LINES
    assert BLOCK_REASON in findings(reason)


def test_a_trivial_change_takes_no_snapshot_of_the_working_state(repo, monkeypatch):
    def no_snapshot(root):
        raise AssertionError("snapshot taken for a trivial change")

    start(repo)
    write(repo, "app.py", numbered(5, "base") + numbered(20))
    write(repo, "notes.txt", numbered(20))
    nested_repo_without_commits(repo)
    monkeypatch.setattr(reviewlib, "snapshot_tree", no_snapshot)
    head = git(repo, "rev-parse", "HEAD").strip()
    changes = stopscope.RepoChanges(repo, {"app.py": "app.py", "notes.txt": "notes.txt"}, head, frozenset(), True)
    assert reviewlib.unreviewed(changes, reviewlib.ReviewConfig(), {}) is None


def test_a_file_moved_without_git_counts_as_a_rename_not_twice_its_lines(repo):
    own_commit(repo, "worker.py", numbered(40))
    start(repo)
    (repo / "worker.py").rename(repo / "jobs.py")
    passes(stop(repo))


def test_binary_files_count_no_lines(repo):
    start(repo)
    (repo / "logo.png").write_bytes(bytes(range(256)) * 400)
    passes(stop(repo))


def test_a_subagent_stop_is_not_review_gated(repo, edit):
    start(repo)
    start(repo, agent_id="builder-1")
    edit(repo, "app.py", numbered(80), agent_id="builder-1")
    payload = base_payload(repo, "SubagentStop", agent_id="builder-1") | {"stop_hook_active": False}
    passes(launch("stop_gate", payload))
    blocked(stop(repo))


def test_the_review_waits_while_a_background_agent_is_still_running(repo):
    start(repo)
    write(repo, "app.py", numbered(80))
    passes(stop(repo, running=("builder-1",)))
    assert BLOCK_REASON in blocked(stop(repo))


def test_the_review_waits_for_a_running_builder_and_its_edits_are_deferred(repo, edit):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS)
    start(repo, agent_id="builder-1")
    edit(repo, "worker.py", numbered(40), agent_id="builder-1")
    passes(stop(repo, running=("builder-1",)))
    assert "40 lines changed since the last VERDICT: PASS" in blocked(stop(repo))


def test_the_snapshot_leaves_the_real_index_alone_and_cleans_up(repo, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    start(repo)
    write(repo, "app.py", numbered(80))
    write(repo, "staged.py", "x = 1\n")
    git(repo, "add", "staged.py")
    write(repo, "untracked.py", "y = 2\n")
    before = (git(repo, "status", "--porcelain"), git(repo, "diff", "--cached", "--name-only"))
    reviewed(repo, PASS)
    passes(stop(repo))
    assert (git(repo, "status", "--porcelain"), git(repo, "diff", "--cached", "--name-only")) == before
    assert list(scratch.iterdir()) == []


def teammate_commits(repo: Path, tmp_path: Path, files: dict[str, str]) -> Path:
    """A clone of `repo` with one commit of `files` on top, as a teammate would push it."""
    teammate = tmp_path / "teammate"
    if not teammate.exists():
        git(tmp_path, "clone", "-q", str(repo), str(teammate))
    for rel, content in files.items():
        write(teammate, rel, content)
    git(teammate, "add", "-A")
    git(teammate, *TEAMMATE_IDENTITY, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "teammate work")
    return teammate


def pull(repo: Path, teammate: Path, *options: str) -> None:
    git(repo, *GIT_IDENTITY, "-c", "core.hooksPath=/dev/null", "pull", "-q", *options, str(teammate), "HEAD")


def own_commit(repo: Path, rel: str, content: str) -> None:
    write(repo, rel, content)
    commit_all(repo)


def test_a_fast_forwarded_teammate_commit_needs_no_review(repo, tmp_path):
    start(repo)
    teammate = teammate_commits(repo, tmp_path, {"teammate.py": numbered(120), "auth/views.py": "x = 1\n"})
    pull(repo, teammate, "--ff-only", "--no-rebase")
    passes(stop(repo))


def test_a_pull_counts_only_the_session_s_own_change(repo, tmp_path):
    start(repo)
    write(repo, "app.py", numbered(5, "base") + numbered(60))
    teammate = teammate_commits(repo, tmp_path, {"teammate.py": numbered(120)})
    pull(repo, teammate, "--ff-only", "--no-rebase")
    reason = blocked(stop(repo))
    assert "Changed lines since the session started: 60.\nPaths: app.py." in reason


def test_a_merging_pull_counts_only_the_session_s_own_commits(repo, tmp_path):
    start(repo)
    teammate = teammate_commits(repo, tmp_path, {"teammate.py": numbered(120)})
    own_commit(repo, "app.py", numbered(5, "base") + numbered(60))
    pull(repo, teammate, "--no-rebase", "--no-edit")
    assert len(git(repo, "log", "-1", "--format=%P").split()) == 2, "the pull made a merge commit"
    assert "Changed lines since the session started: 60.\nPaths: app.py." in blocked(stop(repo))


def test_a_rebasing_pull_keeps_the_session_s_own_commits(repo, tmp_path):
    start(repo)
    teammate = teammate_commits(repo, tmp_path, {"teammate.py": numbered(120)})
    own_commit(repo, "app.py", numbered(5, "base") + numbered(60))
    pull(repo, teammate, "--rebase")
    assert "Changed lines since the session started: 60.\nPaths: app.py." in blocked(stop(repo))


def test_switching_to_a_teammate_s_branch_needs_no_review(repo, tmp_path):
    teammate = teammate_commits(repo, tmp_path, {"teammate.py": numbered(120)})
    git(repo, "fetch", "-q", str(teammate), "HEAD:theirs")
    start(repo)
    git(repo, "checkout", "-q", "theirs")
    passes(stop(repo))


def test_resetting_onto_an_own_commit_keeps_the_session_s_commits(repo):
    start(repo)
    own_commit(repo, "app.py", numbered(5, "base") + numbered(60))
    own_commit(repo, "worker.py", numbered(10))
    git(repo, "reset", "-q", "--soft", "HEAD~1")
    assert "Changed lines since the session started: 70." in blocked(stop(repo))


def test_a_pull_after_a_pass_is_no_follow_up(repo, tmp_path):
    start(repo)
    write(repo, "app.py", numbered(5, "base") + numbered(80))
    reviewed(repo, PASS)
    teammate = teammate_commits(repo, tmp_path, {"teammate.py": numbered(120)})
    pull(repo, teammate, "--ff-only", "--no-rebase")
    passes(stop(repo))
    write(repo, "app.py", numbered(5, "base") + numbered(80) + numbered(16, "follow-up"))
    assert "16 lines changed since the last VERDICT: PASS" in blocked(stop(repo))


def worktree_branch(repo: Path, tmp_path: Path, branch: str = "feat") -> Path:
    worktree = tmp_path / "worktrees" / branch
    git(repo, "worktree", "add", "-q", "-b", branch, str(worktree))
    return worktree


@pytest.mark.parametrize("merge", [["--ff-only"], ["--no-ff", "--no-edit"]])
def test_merging_a_branch_the_session_committed_in_a_worktree_still_needs_a_review(repo, tmp_path, merge):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    own_commit(worktree, "app.py", numbered(5, "base") + numbered(60))
    git(repo, "-c", "core.hooksPath=/dev/null", "merge", "-q", *merge, "feat")
    assert "Changed lines since the session started: 60.\nPaths: app.py." in blocked(stop(repo))


def test_the_session_s_commit_identity_can_come_from_the_environment(repo, tmp_path):
    git(repo, "config", "--unset", "user.email")
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    own_commit(worktree, "app.py", numbered(5, "base") + numbered(60))
    git(repo, "merge", "-q", "--ff-only", "feat")
    payload = base_payload(repo, "Stop") | {"stop_hook_active": False, "background_tasks": []}
    result = launch("stop_gate", payload, env={"GIT_COMMITTER_EMAIL": "kit@example.com"})
    assert "Changed lines since the session started: 60.\nPaths: app.py." in blocked(result)


def test_moving_head_onto_a_commit_built_with_commit_tree_still_needs_a_review(repo):
    start(repo)
    write(repo, "app.py", numbered(5, "base") + numbered(60))
    git(repo, "add", "app.py")
    tree = git(repo, "write-tree").strip()
    commit = git(repo, *GIT_IDENTITY, "commit-tree", tree, "-p", "HEAD", "-m", "work").strip()
    git(repo, "update-ref", "HEAD", commit)
    assert "Changed lines since the session started: 60.\nPaths: app.py." in blocked(stop(repo))


def test_own_commits_made_before_the_session_arrive_like_a_teammate_s(repo, tmp_path):
    worktree = worktree_branch(repo, tmp_path)
    write(worktree, "app.py", numbered(5, "base") + numbered(60))
    git(worktree, "add", "-A")
    earlier = {"GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z", "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z"}
    subprocess.run(
        ["git", *GIT_IDENTITY, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "earlier work"],
        cwd=worktree,
        check=True,
        env={**os.environ, **earlier},
    )
    start(repo)
    git(repo, "merge", "-q", "--ff-only", "feat")
    passes(stop(repo))


@pytest.mark.parametrize("merge", [["--ff-only"], ["--no-ff", "--no-edit"]])
def test_a_pass_on_the_worktree_covers_merging_its_reviewed_branch(repo, tmp_path, edit, merge):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    commit_all(worktree)
    merge_into(repo, "feat", *merge)
    passes(stop(repo))


def test_a_worktree_pass_doesn_t_cover_unreviewed_work_merged_beside_it(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    commit_all(worktree)
    own_commit(repo, "worker.py", numbered(60))
    merge_into(repo, "feat", "--no-ff", "--no-edit")
    assert "60 lines changed since the last VERDICT: PASS" in blocked(stop(repo))


def parallel_worktrees(repo: Path, tmp_path: Path, edit) -> tuple[Path, Path]:
    """Two worktrees, each holding a builder's uncommitted change, as parallel builders leave them."""
    one, two = worktree_branch(repo, tmp_path, "one"), worktree_branch(repo, tmp_path, "two")
    edit(one, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    edit(two, "worker.py", numbered(80), agent_id="builder-2")
    return one, two


def merge_into(repo: Path, branch: str, *options: str) -> None:
    git(repo, "-c", "core.hooksPath=/dev/null", "merge", "-q", *options, branch)


@pytest.mark.parametrize("merge", [["--ff-only"], ["--no-ff", "--no-edit"]])
def test_a_pass_on_a_worktree_with_committed_work_doesn_t_cover_merging_another(repo, tmp_path, edit, merge):
    start(repo)
    one, two = parallel_worktrees(repo, tmp_path, edit)
    commit_all(one)
    reviewed(repo, report(PASS, one))
    commit_all(two)
    merge_into(repo, "two", *merge)
    reason = blocked(stop(repo))
    assert "Changed lines since the session started: 80.\nPaths: worker.py." in reason
    assert "140 lines changed since the last VERDICT: PASS" in reason


def test_a_pass_on_a_worktree_changed_only_through_bash_doesn_t_cover_another(repo, tmp_path, edit):
    start(repo)
    one, two = worktree_branch(repo, tmp_path, "one"), worktree_branch(repo, tmp_path, "two")
    edit(one, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    write(two, "worker.py", numbered(80))
    reviewed(repo, report(PASS, two))
    commit_all(one)
    merge_into(repo, "one", "--ff-only")
    assert "140 lines changed since the last VERDICT: PASS" in blocked(stop(repo))


def test_a_pass_on_a_worktree_doesn_t_cover_another_builder_s_work_in_the_main_checkout(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(repo, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    edit(worktree, "worker.py", numbered(80), agent_id="builder-2")
    reviewed(repo, report(PASS, worktree))
    reason = blocked(stop(repo))
    assert "Changed lines since the session started: 60.\nPaths: app.py." in reason
    assert "140 lines changed since the last VERDICT: PASS" in reason


@pytest.mark.parametrize("committed", [False, True])
def test_a_worktree_changed_only_through_bash_needs_a_review(repo, tmp_path, committed):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    write(worktree, "worker.py", numbered(80))
    if committed:
        commit_all(worktree)
    reason = blocked(stop(repo))
    assert f"Unreviewed change in {worktree}:" in reason
    assert "Changed lines since the session started: 80.\nPaths: worker.py." in reason
    reviewed(repo, report(PASS, worktree))
    passes(stop(repo))


def test_a_worktree_changed_after_its_review_needs_another(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    write(worktree, "worker.py", numbered(40))
    assert "40 lines changed since the last VERDICT: PASS" in blocked(stop(repo))
    commit_all(worktree)
    merge_into(repo, "feat", "--ff-only")
    assert "40 lines changed since the last VERDICT: PASS" in blocked(stop(repo))


def test_a_worktree_pass_covers_its_merge_beside_a_worktree_without_changes(repo, tmp_path, edit):
    start(repo)
    one, two = worktree_branch(repo, tmp_path, "one"), worktree_branch(repo, tmp_path, "two")
    edit(one, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    edit(two, "README.md", "# app\n", agent_id="builder-2")
    reviewed(repo, report(PASS, one))
    commit_all(one)
    merge_into(repo, "one", "--ff-only")
    passes(stop(repo))


def test_merging_two_reviewed_worktrees_needs_a_review_of_the_result(repo, tmp_path, edit):
    start(repo)
    one, two = parallel_worktrees(repo, tmp_path, edit)
    reviewed(repo, report(PASS, one))
    reviewed(repo, report(PASS, two))
    commit_all(one)
    commit_all(two)
    merge_into(repo, "one", "--ff-only")
    passes(stop(repo))
    merge_into(repo, "two", "--no-ff", "--no-edit")
    assert "60 lines changed since the last VERDICT: PASS" in blocked(stop(repo))
    reviewed(repo, report(PASS, repo))
    passes(stop(repo))


def test_a_worktree_merged_after_another_needs_a_review_of_the_result(repo, tmp_path, edit):
    start(repo)
    one, two = worktree_branch(repo, tmp_path, "one"), worktree_branch(repo, tmp_path, "two")
    edit(one, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, one))
    commit_all(one)
    merge_into(repo, "one", "--ff-only")
    passes(stop(repo))
    edit(two, "worker.py", numbered(80), agent_id="builder-2")
    reviewed(repo, report(PASS, two))
    commit_all(two)
    merge_into(repo, "two", "--no-ff", "--no-edit")
    assert "60 lines changed since the last VERDICT: PASS" in blocked(stop(repo))
    reviewed(repo, report(PASS, repo))
    passes(stop(repo))


def test_a_worktree_merged_before_any_review_is_covered_by_the_review_of_the_result(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    commit_all(worktree)
    own_commit(repo, "worker.py", numbered(60))
    merge_into(repo, "feat", "--no-ff", "--no-edit")
    reviewed(repo, report(PASS, repo))
    passes(stop(repo))


def test_a_worktree_from_before_the_session_counts_only_what_edit_and_write_changed(repo, tmp_path, edit):
    worktree = worktree_branch(repo, tmp_path)
    write(worktree, "worker.py", numbered(80))
    start(repo)
    passes(stop(repo))
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    assert "Changed lines since the session started: 60.\nPaths: app.py." in blocked(stop(repo))
    reviewed(repo, report(PASS, worktree))
    passes(stop(repo))


def test_a_fix_on_the_merged_state_outranks_the_worktree_s_earlier_pass(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    commit_all(worktree)
    merge_into(repo, "feat", "--ff-only")
    passes(stop(repo))
    reviewed(repo, report(FIX, repo))
    assert "The latest review ended with VERDICT: FIX." in blocked(stop(repo))


def test_a_checkout_s_fix_outranks_an_earlier_pass_from_another_checkout(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    commit_all(worktree)
    merge_into(repo, "feat", "--ff-only")
    write(repo, "app.py", numbered(5, "base") + numbered(60) + numbered(2, "more"))
    reviewed(repo, report(FIX, repo))
    write(repo, "app.py", numbered(5, "base") + numbered(60) + numbered(3, "more"))
    assert "The latest review ended with VERDICT: FIX." in blocked(stop(repo))
    write(repo, "app.py", numbered(5, "base") + numbered(60))
    assert "The latest review ended with VERDICT: FIX." in blocked(stop(repo)), "back on the worktree's reviewed state"


def test_a_pass_after_a_checkout_s_fix_covers_it_from_another_checkout(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    write(repo, "app.py", numbered(5, "base") + numbered(60))
    reviewed(repo, report(FIX, repo))
    edit(worktree, "app.py", numbered(5, "base") + numbered(60, "fixed"), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    commit_all(worktree)
    (repo / "app.py").write_text(numbered(5, "base"))
    merge_into(repo, "feat", "--ff-only")
    passes(stop(repo))
    write(repo, "app.py", numbered(5, "base") + numbered(60, "fixed") + numbered(15, "follow-up"))
    passes(stop(repo))


def unnumbered(data_dir: Path) -> None:
    """The recorded verdicts as the hooks saved them before verdicts were numbered: without `seq`."""
    state_file = data_dir / "state" / "s1" / "main.json"
    state = json.loads(state_file.read_text())
    for record in state[reviewlib.STATE_KEY].values():
        del record["seq"]
    state_file.write_text(json.dumps(state))


def test_a_checkout_s_fix_recorded_before_verdicts_were_numbered_still_blocks(repo, tmp_path, edit, data_dir):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    commit_all(worktree)
    merge_into(repo, "feat", "--ff-only")
    write(repo, "app.py", numbered(5, "base") + numbered(60) + numbered(2, "more"))
    reviewed(repo, report(FIX, repo))
    unnumbered(data_dir)
    write(repo, "app.py", numbered(5, "base") + numbered(60) + numbered(3, "more"))
    assert "The latest review ended with VERDICT: FIX." in blocked(stop(repo))


def test_a_pass_recorded_before_verdicts_were_numbered_still_counts(repo, data_dir):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS)
    unnumbered(data_dir)
    write(repo, "app.py", numbered(80) + numbered(15, "follow-up"))
    passes(stop(repo))


def test_a_verdict_recorded_after_unnumbered_ones_comes_after_them(repo, tmp_path, edit, data_dir):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    write(repo, "app.py", numbered(5, "base") + numbered(60))
    reviewed(repo, report(FIX, repo))
    unnumbered(data_dir)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60, "fixed"), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    assert read_state(data_dir)[reviewlib.STATE_KEY][str(worktree)]["seq"] == 1
    commit_all(worktree)
    (repo / "app.py").write_text(numbered(5, "base"))
    merge_into(repo, "feat", "--ff-only")
    passes(stop(repo))


@pytest.mark.parametrize("merge", [["--ff-only"], ["--no-ff", "--no-edit"]])
def test_a_worktree_pass_doesn_t_cover_the_unreviewed_commit_it_started_from(repo, tmp_path, edit, merge):
    start(repo)
    own_commit(repo, "worker.py", numbered(200))
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(10), agent_id="builder-2")
    reviewed(repo, report(PASS, worktree))
    commit_all(worktree)
    merge_into(repo, "feat", *merge)
    reason = blocked(stop(repo))
    assert "Changed lines since the session started: 210." in reason
    reviewed(repo, report(PASS, repo))
    passes(stop(repo))


def dated_before_the_session(repo: Path) -> None:
    """The start commit dated long before the session, as in a real repo; the fixture makes it in the session's
    first second, which counts it as the session's own."""
    subprocess.run(
        ["git", *GIT_IDENTITY, "-c", "core.hooksPath=/dev/null", "commit", "-q", "--amend", "--no-edit"],
        cwd=repo,
        check=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"},
    )


@pytest.mark.parametrize("merge", [["--ff-only"], ["--no-ff", "--no-edit"]])
def test_a_worktree_reviewed_after_its_commit_doesn_t_cover_the_commit_it_started_from(repo, tmp_path, edit, merge):
    dated_before_the_session(repo)
    start(repo)
    own_commit(repo, "worker.py", numbered(200))
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(10), agent_id="builder-2")
    commit_all(worktree)
    reviewed(repo, report(PASS, worktree))
    merge_into(repo, "feat", *merge)
    assert "Changed lines since the session started: 210." in blocked(stop(repo))


@pytest.mark.parametrize(
    "reviewed_lines",
    [[], ["relative/path"], ["{elsewhere}"], ["{worktree}", "{repo}"]],
    ids=["no line", "relative path", "unknown checkout", "two checkouts"],
)
def test_a_pass_that_names_no_known_checkout_counts_for_none(repo, tmp_path, edit, data_dir, reviewed_lines):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    (tmp_path / "elsewhere").mkdir()
    places = {"worktree": worktree, "repo": repo, "elsewhere": tmp_path / "elsewhere"}
    lines = "".join(f"Reviewed: {line.format(**places)}\n" for line in reviewed_lines)
    message = notice(launch("record_review", reviewer_payload(repo, lines + PASS)))
    assert message.startswith("kit: the reviewer's VERDICT: PASS counts for no checkout.")
    assert reviewlib.STATE_KEY not in read_state(data_dir)
    assert "No reviewer verdict is recorded for this change." in blocked(stop(repo))


def test_a_fix_that_names_no_checkout_drops_every_pass(repo, tmp_path, edit):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    reviewed(repo, report(PASS, worktree))
    passes(stop(repo))
    message = notice(launch("record_review", reviewer_payload(repo, FIX)))
    assert "Every earlier PASS of this session is dropped" in message
    assert "No reviewer verdict is recorded for this change." in blocked(stop(repo))


def test_a_review_recorded_without_the_checkout_it_reviewed_gives_no_credit(repo, data_dir):
    start(repo)
    write(repo, "app.py", numbered(80))
    reviewed(repo, PASS)
    state_file = data_dir / "state" / "s1" / "main.json"
    state = json.loads(state_file.read_text())
    record = state.pop(reviewlib.STATE_KEY)[str(repo)]
    state["reviews"] = {str(repo): record | {"checkouts": 1}}
    state_file.write_text(json.dumps(state))
    assert "No reviewer verdict is recorded for this change." in blocked(stop(repo))


def test_git_failing_on_the_named_worktree_records_nothing(repo, tmp_path, edit, data_dir):
    start(repo)
    worktree = worktree_branch(repo, tmp_path)
    edit(worktree, "app.py", numbered(5, "base") + numbered(60), agent_id="builder-1")
    write(worktree, "secret.txt", "s\n").chmod(0)
    message = notice(launch("record_review", reviewer_payload(repo, report(PASS, worktree))))
    assert message.startswith(f"kit: the reviewer's VERDICT: PASS wasn't recorded for {worktree}: git add failed: ")
    assert reviewlib.STATE_KEY not in read_state(data_dir)


def test_a_branch_rebased_onto_a_teammate_s_commit_counts_only_the_session_s_commits(repo, tmp_path):
    start(repo)
    teammate = teammate_commits(repo, tmp_path, {"teammate.py": numbered(120)})
    worktree = worktree_branch(repo, tmp_path)
    git(worktree, "pull", "-q", "--ff-only", "--no-rebase", str(teammate), "HEAD")
    own_commit(worktree, "app.py", numbered(5, "base") + numbered(60))
    git(repo, "merge", "-q", "--ff-only", "feat")
    assert "Changed lines since the session started: 60.\nPaths: app.py." in blocked(stop(repo))


def nested_repo_without_commits(repo: Path) -> None:
    write(repo, "vendor/lib/README.md", "# lib\n")
    git(repo / "vendor" / "lib", "init", "-q")


def unreadable_untracked_file(repo: Path) -> None:
    write(repo, "secret.txt", "s\n").chmod(0)


GIT_BREAKERS = [nested_repo_without_commits, unreadable_untracked_file]


@pytest.mark.parametrize("break_git", GIT_BREAKERS)
def test_git_failing_in_the_review_check_is_a_finding_beside_the_others(repo, break_git):
    start(repo)
    write(repo, "app.py", numbered(80))
    write(repo, ".github/workflows/ci.yml", "on: push\n")
    break_git(repo)
    reason = blocked(stop(repo))
    assert "Tell the user you changed .github/workflows/ci.yml and why" in reason
    assert "Review check failed:" in reason
    assert "git add failed: " in reason
    assert "so the review can't be checked" in reason


@pytest.mark.parametrize("break_git", GIT_BREAKERS)
def test_git_failing_in_the_review_check_releases_like_any_finding(repo, break_git):
    start(repo)
    write(repo, "app.py", numbered(80))
    break_git(repo)
    for attempt in range(3):
        assert "Review check failed:" in blocked(stop(repo, active=attempt > 0))
    assert notice(stop(repo, active=True)).startswith("kit stop gate: blocked 3 times in a row on the same problems")


@pytest.mark.parametrize("break_git", GIT_BREAKERS)
def test_git_failing_while_recording_a_review_tells_the_user(repo, data_dir, break_git):
    start(repo)
    write(repo, "app.py", numbered(80))
    break_git(repo)
    message = notice(launch("record_review", reviewer_payload(repo, PASS)))
    assert message.startswith(f"kit: the reviewer's VERDICT: PASS wasn't recorded for {repo}: git add failed: ")
    assert "hook failed" not in message
    assert reviewlib.STATE_KEY not in read_state(data_dir)


def test_the_reviewer_hooks_are_registered_for_its_stop_and_its_hand_back():
    assert registered("SubagentStop")["^(reviewer|kit:reviewer|kit-reviewer)$"] == ["record_review"]
    assert registered("PostToolUse")["SubagentHandback"] == ["record_review"]
