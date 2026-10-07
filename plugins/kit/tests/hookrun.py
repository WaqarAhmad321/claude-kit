"""Run kit hooks the way Claude Code does, through run_hook.py with the hook input on stdin."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "hooks" / "bin"
LAUNCHER = BIN / "run_hook.py"
HOOKS_JSON = BIN.parent / "hooks.json"
GIT_IDENTITY = ["-c", "user.name=kit", "-c", "user.email=kit@example.com", "-c", "commit.gpgsign=false"]
MAIN_REASON_LINES = 3
_FULL_REPORT = re.compile(r"^Full report: (?P<path>.+)$", re.MULTILINE)


def launch(script: str, payload: dict | None = None, *args: str, stdin: str | None = None, env=None, launcher=LAUNCHER):
    return subprocess.run(
        [sys.executable, str(launcher), script, *args],
        input=json.dumps(payload or {}) if stdin is None else stdin,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=120,
    )


def decision(result) -> dict:
    assert result.returncode == 0, result
    return json.loads(result.stdout)["hookSpecificOutput"]


def findings(reason: str) -> str:
    """A stop block's findings: a subagent's reason holds them; a main-thread reason is three short lines that point
    to the full report."""
    report = _FULL_REPORT.search(reason)
    if report is None:
        return reason
    assert len(reason.splitlines()) <= MAIN_REASON_LINES, reason
    return Path(report["path"]).read_text(encoding="utf-8").rstrip("\n")


def registered(event: str) -> dict[str | None, list[str]]:
    """The kit scripts hooks.json runs on `event`, by matcher."""
    scripts: dict[str | None, list[str]] = {}
    for group in json.loads(HOOKS_JSON.read_text())["hooks"][event]:
        scripts.setdefault(group.get("matcher"), []).extend(hook["args"][1] for hook in group["hooks"])
    return scripts


def commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", *GIT_IDENTITY, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"], cwd=repo, check=True
    )


def add_worktree(repo: Path, path: Path, branch: str) -> Path:
    """Another checkout of `repo`'s committed files, as `git worktree add` makes one."""
    commit_all(repo)
    subprocess.run(["git", "worktree", "add", "-q", "-b", branch, str(path)], cwd=repo, check=True)
    return path.resolve()


def write(repo: Path, rel: str, content: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def base_payload(repo: Path, event: str, session: str = "s1", agent_id: str | None = None) -> dict:
    payload = {"session_id": session, "transcript_path": "/dev/null", "cwd": str(repo), "hook_event_name": event}
    if agent_id:
        payload |= {"agent_id": agent_id, "agent_type": "general-purpose"}
    return payload


def tool_payload(repo: Path, event: str, tool: str, tool_input: dict, **options) -> dict:
    return base_payload(repo, event, **options) | {"tool_name": tool, "tool_input": tool_input}


def edit_input(repo: Path, rel: str, tool: str = "Edit", extra: dict | None = None, **options) -> dict:
    return tool_payload(repo, "PreToolUse", tool, {"file_path": str(repo / rel), **(extra or {})}, **options)
