"""install.sh: it's safe to re-run, and a re-run has to refresh the kit plugin itself, since Claude Code runs it
from a cache copy that doesn't follow this checkout's HEAD on its own (see docs/decisions/0009)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

KIT_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SH = KIT_ROOT / "install.sh"
LOCAL_OVERLAY = Path("claude-kit") / "settings.local.json"

_FAKE_CLAUDE = """\
#!{python}
import json, os, sys

with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
if args == ["plugin", "marketplace", "list", "--json"]:
    print(json.dumps([{{"name": "claude-kit"}}]))
elif args == ["plugin", "list", "--json"]:
    already = os.environ.get("FAKE_CLAUDE_INSTALLED") == "1"
    print(json.dumps([{{"id": "kit@claude-kit"}}] if already else []))
elif args[:3] == ["plugin", "update", "kit@claude-kit"]:
    sys.exit(int(os.environ.get("FAKE_CLAUDE_UPDATE_EXIT", "0")))
sys.exit(0)
"""


def fake_kit(root: Path) -> Path:
    """A copy of install.sh and the sibling files it reads, isolated from the real checkout, so the test never
    touches this worktree's own git config or ~/.claude."""
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "install.sh").write_text(INSTALL_SH.read_text())
    (root / "install.sh").chmod(0o755)
    for rel in ("home/CLAUDE.md",):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("# fake\n")
    for rel in ("home/rules", "home/output-styles", "home/hooks", ".githooks"):
        (root / rel).mkdir(parents=True)
    (root / ".githooks" / "post-commit").write_text("#!/bin/sh\nexit 0\n")
    (root / ".githooks" / "post-commit").chmod(0o755)
    for name in ("scripts", "settings"):
        _copy_tree(KIT_ROOT / name, root / name)
    return root


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        target = dst / path.relative_to(src)
        if path.is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.write_bytes(path.read_bytes())


def run_install(
    tmp_path: Path,
    *,
    already_installed: bool,
    update_exit: int,
    env_extra: dict | None = None,
    overlay: dict | None = None,
) -> subprocess.CompletedProcess:
    kit = fake_kit(tmp_path / "kit")
    config_home = tmp_path / "config"
    if overlay is not None:
        (config_home / LOCAL_OVERLAY).parent.mkdir(parents=True)
        (config_home / LOCAL_OVERLAY).write_text(json.dumps(overlay))
    claude_home = tmp_path / "claude-home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude_bin = bin_dir / "claude"
    claude_bin.write_text(_FAKE_CLAUDE.format(python=sys.executable))
    claude_bin.chmod(0o755)
    log = tmp_path / "claude-calls.log"
    log.write_text("")
    env = {
        **{key: value for key, value in os.environ.items() if key != "KIT_WORK_DIR"},
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CLAUDE_CONFIG_DIR": str(claude_home),
        "FAKE_CLAUDE_LOG": str(log),
        "FAKE_CLAUDE_INSTALLED": "1" if already_installed else "0",
        "FAKE_CLAUDE_UPDATE_EXIT": str(update_exit),
        "XDG_CONFIG_HOME": str(config_home),
        **(env_extra or {}),
    }
    env = {key: value for key, value in env.items() if value is not None}
    result = subprocess.run(
        ["bash", str(kit / "install.sh")], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    result.calls = log.read_text()
    return result


@pytest.mark.parametrize("already_installed", [True, False])
def test_a_rerun_refreshes_the_plugin_whether_or_not_it_was_just_installed(tmp_path, already_installed):
    result = run_install(tmp_path, already_installed=already_installed, update_exit=0)

    assert result.returncode == 0, result.stderr
    assert "plugin update kit@claude-kit" in result.calls
    assert "could not refresh" not in result.stderr


def test_a_failing_update_prints_one_line_instead_of_failing_the_install(tmp_path):
    result = run_install(tmp_path, already_installed=True, update_exit=1)

    assert result.returncode == 0, result.stderr
    assert (
        result.stderr.strip()
        == "could not refresh the kit plugin automatically; run: claude plugin update kit@claude-kit"
    )
    assert "claude-kit installed." in result.stdout


def installed_settings(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "claude-home" / "settings.json").read_text())


def test_the_work_folder_defaults_to_work_under_home(tmp_path):
    home = tmp_path / "home"

    result = run_install(tmp_path, already_installed=True, update_exit=0, env_extra={"HOME": str(home)})

    assert result.returncode == 0, result.stderr
    assert installed_settings(tmp_path)["permissions"]["additionalDirectories"] == [str(home / "Work")]


def test_kit_work_dir_picks_another_work_folder(tmp_path):
    work = tmp_path / "Code"

    result = run_install(tmp_path, already_installed=True, update_exit=0, env_extra={"KIT_WORK_DIR": str(work)})

    assert result.returncode == 0, result.stderr
    assert installed_settings(tmp_path)["permissions"]["additionalDirectories"] == [str(work)]


def test_sandboxed_commands_cant_write_the_live_checkout_or_its_hooks(tmp_path):
    result = run_install(tmp_path, already_installed=True, update_exit=0)

    assert result.returncode == 0, result.stderr
    kit = tmp_path / "kit"
    deny_write = [Path(entry) for entry in installed_settings(tmp_path)["sandbox"]["filesystem"]["denyWrite"]]
    live_paths = {kit / "home", kit / "install.sh", kit / ".githooks", kit / ".git" / "hooks", kit / ".git" / "config"}
    assert live_paths <= set(deny_write)
    kit_hooks = (tmp_path / "claude-home" / "kit-hooks").resolve()
    assert any(kit_hooks.is_relative_to(entry) for entry in deny_write)
    assert not any((kit / ".worktrees" / "branch").is_relative_to(entry) for entry in deny_write)


def test_without_the_sandbox_no_write_denies_are_added(tmp_path):
    result = run_install(tmp_path, already_installed=True, update_exit=0, env_extra={"KIT_SANDBOX": "0"})

    assert result.returncode == 0, result.stderr
    assert "sandbox" not in installed_settings(tmp_path)


def test_the_local_overlay_is_merged_after_the_kit_settings(tmp_path):
    overlay = {"autoMode": {"environment": ["Client line"]}, "permissions": {"deny": ["Bash(npm publish*)"]}}

    result = run_install(tmp_path, already_installed=True, update_exit=0, overlay=overlay)

    assert result.returncode == 0, result.stderr
    settings = installed_settings(tmp_path)
    assert settings["autoMode"]["environment"][0] == "$defaults"
    assert settings["autoMode"]["environment"][-1] == "Client line"
    assert "Bash(npm publish*)" in settings["permissions"]["deny"]
    assert "Bash(git push --force*)" in settings["permissions"]["deny"]


def test_the_overlay_is_read_from_dot_config_when_xdg_config_home_is_unset(tmp_path):
    home = tmp_path / "home"
    overlay = home / ".config" / LOCAL_OVERLAY
    overlay.parent.mkdir(parents=True)
    overlay.write_text(json.dumps({"autoMode": {"environment": ["Client line"]}}))

    result = run_install(
        tmp_path, already_installed=True, update_exit=0, env_extra={"XDG_CONFIG_HOME": None, "HOME": str(home)}
    )

    assert result.returncode == 0, result.stderr
    assert installed_settings(tmp_path)["autoMode"]["environment"][-1] == "Client line"


def test_without_an_overlay_only_the_kit_settings_are_merged(tmp_path):
    result = run_install(tmp_path, already_installed=True, update_exit=0)

    assert result.returncode == 0, result.stderr
    kit_environment = json.loads((KIT_ROOT / "settings" / "settings.kit.json").read_text())["autoMode"]["environment"]
    assert installed_settings(tmp_path)["autoMode"]["environment"] == kit_environment
