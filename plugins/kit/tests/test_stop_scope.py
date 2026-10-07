import json
import subprocess
from pathlib import Path

import pytest
from conftest import read_state
from hookrun import GIT_IDENTITY, base_payload, findings, launch, tool_payload, write

CLEAN = "def f():\n    return 1\n"
VIOLATION = "def f():\n    return undefined_name\n"
SKILL = "---\nname: billing\npaths:\n  - src/billing/**\n---\n\n# Billing\n"
JOURNEYS = {"gate": {"journeys": {"command": "make journeys", "watch": ["src/**/*.py"]}, "review": {"enabled": False}}}


@pytest.fixture
def repo(ruff_repo_at) -> Path:
    files = {
        ".claude/skills/billing/SKILL.md": SKILL,
        ".claude/kit.json": json.dumps(JOURNEYS),
        "src/billing/invoice.py": CLEAN,
        "src/app.py": CLEAN,
    }
    return ruff_repo_at("repo", files)


def start(cwd: Path, agent_id: str | None = None) -> None:
    event = "SubagentStart" if agent_id else "SessionStart"
    assert launch("session_context", base_payload(cwd, event, agent_id=agent_id)).returncode == 0


def tool_edit(cwd: Path, path: Path, content: str, agent_id: str | None = None) -> None:
    payload = tool_payload(cwd, "PreToolUse", "Write", {"file_path": str(path)}, agent_id=agent_id)
    launch("snapshot_before_edit", payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    launch("lint_edited_file", payload | {"hook_event_name": "PostToolUse"})


def stop_hooks(cwd: Path, agent_id: str | None = None, running: list[str] = ()) -> dict:
    event = "SubagentStop" if agent_id else "Stop"
    tasks = [{"id": task_id, "type": "subagent", "status": "running", "description": "work"} for task_id in running]
    payload = base_payload(cwd, event, agent_id=agent_id) | {"stop_hook_active": False, "background_tasks": tasks}
    return {hook: _verdict(launch(hook, payload)) for hook in ("stop_gate", "stop_module_skills")}


def _verdict(result) -> str:
    assert result.returncode == 0, result
    if not result.stdout:
        return "pass"
    output = json.loads(result.stdout)
    return findings(output["reason"]) if output.get("decision") == "block" else f"notice: {output['systemMessage']}"


def read_skill(cwd: Path, repo: Path, agent_id: str | None = None) -> None:
    path = str(repo / ".claude" / "skills" / "billing" / "SKILL.md")
    launch("record_skill_seen", tool_payload(cwd, "PostToolUse", "Read", {"file_path": path}, agent_id=agent_id))


def journeys_ran(cwd: Path, agent_id: str | None = None, command: str = "make journeys", stdout: str = "OK") -> None:
    payload = tool_payload(cwd, "PostToolUse", "Bash", {"command": command}, agent_id=agent_id)
    response = {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False}
    assert launch("record_journey_pass", payload | {"tool_response": response}).returncode == 0


def git(repo: Path, *args: str) -> str:
    command = ["git", *GIT_IDENTITY, "-c", "core.hooksPath=/dev/null", *args]
    return subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True).stdout


def git_commit(repo: Path) -> None:
    git(repo, "commit", "-qam", "feat: x")


def start_pins(repo: Path) -> list[str]:
    return git(repo, "for-each-ref", "--format=%(refname)", "refs/kit/start/").split()


def test_session_start_records_the_start_commit(repo, data_dir):
    start(repo)
    state = read_state(data_dir)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert (state["start_root"], state["start_head"]) == (str(repo), head)


def test_a_bash_edit_that_was_committed_is_still_checked(repo):
    start(repo)
    write(repo, "src/billing/invoice.py", VIOLATION)
    git_commit(repo)
    verdicts = stop_hooks(repo)
    assert "src/billing/invoice.py:\n- ruff F821" in verdicts["stop_gate"]
    assert "- billing (.claude/skills/billing/SKILL.md): src/billing/invoice.py" in verdicts["stop_module_skills"]


def test_a_change_committed_and_then_reverted_is_not_a_change(repo):
    start(repo)
    write(repo, "src/app.py", VIOLATION)
    git_commit(repo)
    write(repo, "src/app.py", CLEAN)
    git_commit(repo)
    assert stop_hooks(repo) == {"stop_gate": "pass", "stop_module_skills": "pass"}


def test_a_bash_violation_is_not_absorbed_by_a_later_edit_s_snapshot(repo):
    start(repo)
    write(repo, "src/app.py", VIOLATION)
    tool_edit(repo, repo / "src" / "app.py", VIOLATION + "\n\nA = 1\n")
    assert "src/app.py:\n- ruff F821" in stop_hooks(repo)["stop_gate"]


def test_a_file_dirty_at_the_start_keeps_its_snapshot_baseline(repo):
    write(repo, "tools/wip.py", VIOLATION)
    start(repo)
    tool_edit(repo, repo / "tools" / "wip.py", VIOLATION + "\n\nA = 1\n")
    assert stop_hooks(repo)["stop_gate"] == "pass"
    tool_edit(repo, repo / "tools" / "wip.py", VIOLATION + "\n\nA = missing_two\n")
    assert "missing_two" in stop_hooks(repo)["stop_gate"]


def test_the_main_stop_accepts_what_its_subagents_satisfied(repo):
    start(repo)
    start(repo, agent_id="worker")
    read_skill(repo, repo, agent_id="worker")
    tool_edit(repo, repo / "src" / "billing" / "invoice.py", CLEAN + "\n\nB = 1\n", agent_id="worker")
    journeys_ran(repo, agent_id="worker")
    assert stop_hooks(repo, agent_id="worker") == {"stop_gate": "pass", "stop_module_skills": "pass"}
    assert stop_hooks(repo) == {"stop_gate": "pass", "stop_module_skills": "pass"}


def test_a_subagent_still_answers_for_its_own_work(repo):
    start(repo)
    start(repo, agent_id="reader")
    start(repo, agent_id="editor")
    read_skill(repo, repo, agent_id="reader")
    tool_edit(repo, repo / "src" / "billing" / "invoice.py", CLEAN + "\n\nB = 1\n", agent_id="editor")
    verdicts = stop_hooks(repo, agent_id="editor")
    assert "billing" in verdicts["stop_module_skills"]
    assert "Run `make journeys`" in verdicts["stop_gate"]
    journeys_ran(repo, agent_id="reader")
    assert stop_hooks(repo) == {"stop_gate": "pass", "stop_module_skills": "pass"}


def test_edits_in_a_sibling_repo_are_checked(repo, ruff_repo_at):
    sibling = ruff_repo_at("sibling", {"lib.py": CLEAN})
    start(repo)
    tool_edit(repo, sibling / "lib.py", VIOLATION)
    assert f"New lint problems in {sibling}:\nlib.py:\n- ruff F821" in stop_hooks(repo)["stop_gate"]


def test_a_session_started_outside_git_still_checks_edited_repos(ruff_repo_at, tmp_path):
    child = ruff_repo_at("projects/child", {"lib.py": CLEAN})
    parent = tmp_path / "projects"
    start(parent)
    tool_edit(parent, child / "lib.py", VIOLATION)
    assert f"New lint problems in {child}:\nlib.py:\n- ruff F821" in stop_hooks(parent)["stop_gate"]


def test_moving_a_file_out_of_a_module_counts_as_changing_the_module(repo):
    start(repo)
    subprocess.run(["git", "mv", "src/billing/invoice.py", "src/invoice.py"], cwd=repo, check=True)
    assert "src/billing/invoice.py" in stop_hooks(repo)["stop_module_skills"]


def test_session_start_pins_the_start_commit_and_drops_week_old_pins(repo):
    git(repo, "update-ref", "refs/kit/start/1000000000-old-session", "HEAD")
    start(repo)
    pins = start_pins(repo)
    assert len(pins) == 1 and pins[0].endswith("-s1")
    assert git(repo, "rev-parse", pins[0]) == git(repo, "rev-parse", "HEAD")


def test_amending_and_garbage_collecting_the_start_commit_hides_nothing(repo):
    start(repo)
    write(repo, "src/app.py", VIOLATION)
    git(repo, "commit", "-qa", "--amend", "--no-edit")
    git(repo, "reflog", "expire", "--expire=now", "--all")
    git(repo, "gc", "--prune=now", "-q")
    verdicts = stop_hooks(repo)
    assert "src/app.py:\n- ruff F821" in verdicts["stop_gate"]
    assert "rewritten" not in verdicts["stop_gate"]


def test_a_lost_start_commit_is_a_finding_and_everything_touched_counts(repo):
    write(repo, "README.md", "demo\n")
    git(repo, "add", "README.md")
    git_commit(repo)
    start(repo)
    for pin in start_pins(repo):
        git(repo, "update-ref", "-d", pin)
    write(repo, "src/billing/invoice.py", CLEAN + "\n\nB = 1\n")
    git(repo, "commit", "-qa", "--amend", "--no-edit")
    git(repo, "reflog", "expire", "--expire=now", "--all")
    git(repo, "gc", "--prune=now", "-q")
    verdicts = stop_hooks(repo)
    assert "Session history:\nSession history was rewritten; the baseline was lost" in verdicts["stop_gate"]
    assert "src/billing/invoice.py" in verdicts["stop_module_skills"]


def test_changes_stashed_during_the_session_are_a_finding(repo):
    start(repo)
    write(repo, "src/app.py", VIOLATION)
    write(repo, "src/new.py", CLEAN)
    git(repo, "stash", "-u")
    reason = stop_hooks(repo)["stop_gate"]
    assert "Changes were stashed during this session (stash@{0}: src/app.py, src/new.py)" in reason
    git(repo, "stash", "pop")
    assert "src/app.py:\n- ruff F821" in stop_hooks(repo)["stop_gate"]


def test_a_stash_from_before_the_session_is_not_a_finding(repo, data_dir):
    write(repo, "src/app.py", CLEAN + "\n\nX = 1\n")
    git(repo, "stash")
    start(repo)
    state_file = data_dir / "state" / "s1" / "main.json"
    state = json.loads(state_file.read_text())
    state_file.write_text(json.dumps(state | {"started_at": state["started_at"] + 5}))
    assert stop_hooks(repo) == {"stop_gate": "pass", "stop_module_skills": "pass"}


def test_session_commits_left_on_another_branch_are_a_finding(repo):
    start(repo)
    branch = git(repo, "branch", "--show-current").strip()
    write(repo, "src/app.py", VIOLATION)
    git_commit(repo)
    git(repo, "checkout", "-q", "HEAD~1")
    reason = stop_hooks(repo)["stop_gate"]
    assert "Commits made during this session aren't checked out (" in reason
    assert f" on {branch}), so the checks can't see them." in reason


def test_commits_checked_out_in_another_worktree_are_not_a_finding(repo, tmp_path):
    start(repo)
    worktree = tmp_path / "agent-worktree"
    git(repo, "worktree", "add", "-q", "-b", "agent", str(worktree))
    write(worktree, "src/app.py", CLEAN + "\n\nX = 1\n")
    git(worktree, "commit", "-qam", "feat: agent work")
    assert stop_hooks(repo) == {"stop_gate": "pass", "stop_module_skills": "pass"}


def test_a_subagent_s_echoed_green_status_satisfies_the_main_stop(repo):
    start(repo)
    start(repo, agent_id="worker")
    read_skill(repo, repo, agent_id="worker")
    tool_edit(repo, repo / "src" / "billing" / "invoice.py", CLEAN + "\n\nB = 1\n", agent_id="worker")
    journeys_ran(repo, "worker", 'make journeys; echo "exit=$?"', "journeys: 3 passed\nsome warning\nexit=0")
    assert stop_hooks(repo) == {"stop_gate": "pass", "stop_module_skills": "pass"}


def test_an_echoed_failing_status_does_not_satisfy_the_journeys(repo):
    start(repo)
    write(repo, "src/app.py", CLEAN + "\n\nA = 1\n")
    journeys_ran(repo, command='make journeys; echo "exit=$?"', stdout="1 failed\nexit=2")
    assert "Run `make journeys`" in stop_hooks(repo)["stop_gate"]


def test_the_main_stop_defers_files_a_running_background_agent_edited(repo):
    start(repo)
    start(repo, agent_id="worker")
    tool_edit(repo, repo / "src" / "app.py", VIOLATION, agent_id="worker")
    assert stop_hooks(repo, running=["worker"])["stop_gate"] == "pass", "deferred, and the user isn't told each stop"
    assert "src/app.py:\n- ruff F821" in stop_hooks(repo, agent_id="worker")["stop_gate"]
    assert "src/app.py:\n- ruff F821" in stop_hooks(repo)["stop_gate"], "once the agent is done, the main Stop checks"


def test_unmatched_task_ids_defer_only_agents_whose_own_stop_has_not_passed(repo):
    start(repo)
    for agent in ("busy", "done"):
        start(repo, agent_id=agent)
    tool_edit(repo, repo / "src" / "app.py", CLEAN + "\n\nA = missing_one\n", agent_id="busy")
    tool_edit(repo, repo / "tools" / "lib.py", CLEAN, agent_id="done")
    assert stop_hooks(repo, agent_id="done")["stop_gate"] == "pass"
    write(repo, "tools/lib.py", VIOLATION)
    verdict = stop_hooks(repo, running=["task-unknown"])["stop_gate"]
    assert "their own stop checks them: src/app.py" in verdict
    assert "tools/lib.py:\n- ruff F821" in verdict
