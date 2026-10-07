import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hookrun import commit_all

BIN = Path(__file__).resolve().parents[1] / "hooks" / "bin"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(BIN), str(SCRIPTS)]

RUFF_PYPROJECT = """\
[tool.ruff]
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I"]
"""


def fake_exe(path: Path, stdout: str = "", exit_code: int = 0) -> Path:
    """An executable that prints fixed output, standing in for a linter binary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({stdout!r})\nsys.exit({exit_code})\n")
    path.chmod(0o755)
    return path


def run_hook(script: str, payload: dict | None = None, stdin: str | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, str(BIN / f"{script}.py")],
        input=json.dumps(payload) if stdin is None else stdin,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=120,
    )


def edit_payload(repo: Path, rel: str, session: str = "s1", agent_id: str | None = None, tool: str = "Edit") -> dict:
    payload = {
        "session_id": session,
        "transcript_path": "/dev/null",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(repo / rel)},
    }
    if agent_id:
        payload |= {"agent_id": agent_id, "agent_type": "general-purpose"}
    return payload


@pytest.fixture(autouse=True)
def _no_session_project(monkeypatch):
    """Hooks take the session's project from CLAUDE_PROJECT_DIR; a Claude Code session running the tests sets it."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


@pytest.fixture(scope="session")
def ruff_bin() -> str:
    installed = shutil.which("ruff")
    if installed:
        return installed
    if shutil.which("uvx"):
        locate = "from ruff.__main__ import find_ruff_bin; print(find_ruff_bin())"
        proc = subprocess.run(
            ["uvx", "--from", "ruff", "python", "-c", locate], capture_output=True, text=True, timeout=300
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[-1]
    pytest.skip("ruff is not installed and uvx could not provide it")


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    directory = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(directory))
    monkeypatch.delenv("KIT_BYPASS", raising=False)
    return directory


@pytest.fixture
def make_repo(tmp_path):
    def make(name: str = "repo", files: dict[str, str] | None = None) -> Path:
        repo = tmp_path / name
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        for rel, content in (files or {}).items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(content)
        return repo.resolve()

    return make


@pytest.fixture
def ruff_repo_at(make_repo, ruff_bin, data_dir):
    """A committed repo linted by ruff, at `name` under the test's temp directory."""

    def make(name: str, files: dict[str, str] | None = None) -> Path:
        repo = make_repo(name, files={"pyproject.toml": RUFF_PYPROJECT, ".gitignore": ".venv/\n", **(files or {})})
        (repo / ".venv" / "bin").mkdir(parents=True)
        (repo / ".venv" / "bin" / "ruff").symlink_to(ruff_bin)
        commit_all(repo)
        return repo

    return make


@pytest.fixture
def ruff_repo(make_repo, ruff_bin, data_dir) -> Path:
    repo = make_repo(files={"pyproject.toml": RUFF_PYPROJECT})
    link = repo / ".venv" / "bin" / "ruff"
    link.parent.mkdir(parents=True)
    link.symlink_to(ruff_bin)
    return repo


@pytest.fixture
def edit():
    """Simulate one Claude edit: the PreToolUse snapshot, the file write, then the PostToolUse lint."""

    def simulate(repo: Path, rel: str, new_content: str, **payload_options):
        payload = edit_payload(repo, rel, **payload_options)
        pre = run_hook("snapshot_before_edit", payload)
        assert (pre.returncode, pre.stdout, pre.stderr) == (0, "", ""), pre
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(new_content)
        return run_hook("lint_edited_file", {**payload, "hook_event_name": "PostToolUse"})

    return simulate


@pytest.fixture
def claude_config(tmp_path, monkeypatch) -> Path:
    """An empty Claude Code config directory, so a kit-root lookup never reads the real ~/.claude."""
    directory = tmp_path / "claude-config"
    directory.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(directory))
    return directory


def fake_kit(root: Path, files: dict[str, str] | None = None) -> Path:
    """A directory the kit-root lookup accepts as a kit repo."""
    for rel, content in {"plugins/kit/.claude-plugin/plugin.json": '{"name": "kit"}', **(files or {})}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)
    return root


def cached_plugin_scripts(config: Path) -> Path:
    """The plugin's scripts as Claude Code's copy under <config>/plugins/cache holds them: plugins/kit only."""
    scripts = config / "plugins/cache/claude-kit/kit/0123456789ab/scripts"
    shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    return scripts


def read_state(data_dir: Path, session: str = "s1", agent: str = "main") -> dict:
    return json.loads((data_dir / "state" / session / f"{agent}.json").read_text())
