"""scripts/merge-settings.py: the kit's settings, plus an optional local overlay, merge into a user's settings.json
without losing the user's own entries, resolve the work folder to an absolute path, and change nothing on a re-run."""

import json
import os
import subprocess
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[3]
MERGE = KIT_ROOT / "scripts" / "merge-settings.py"
KIT_SETTINGS = KIT_ROOT / "settings" / "settings.kit.json"
SANDBOX_SETTINGS = KIT_ROOT / "settings" / "sandbox.kit.json"

GIT_NETWORK_COMMANDS = ["gh *", "git push *", "git fetch *", "git pull *", "git ls-remote *"]
FORCE_PUSH_DENIES = [
    "Bash(git push --force*)",
    "Bash(git push -f*)",
    "Bash(git push * --force*)",
    "Bash(git push * -f*)",
]


def write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data))
    return path


LIVE_KIT = "/home/someone/Work/claude-kit"
LIVE_KIT_GIT = f"{LIVE_KIT}/.git"
KIT_PATH_VARIABLES = ("KIT_WORK_DIR", "KIT_DIR", "KIT_GIT_DIR")


def run_merge(target: Path, *sources: Path, work_dir: str | None = "/home/someone/Work"):
    env = {key: value for key, value in os.environ.items() if key not in KIT_PATH_VARIABLES}
    env |= {"KIT_DIR": LIVE_KIT, "KIT_GIT_DIR": LIVE_KIT_GIT}
    if work_dir is not None:
        env["KIT_WORK_DIR"] = work_dir
    return subprocess.run(
        [sys.executable, str(MERGE), str(target), *map(str, sources)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


def test_permission_lists_keep_the_users_entries_and_add_the_kits_once(tmp_path):
    target = write_json(
        tmp_path / "settings.json",
        {"permissions": {"additionalDirectories": ["/srv/notes"], "deny": ["Bash(rm -rf /*)", "Bash(git push -f*)"]}},
    )
    kit = write_json(
        tmp_path / "kit.json",
        {"permissions": {"additionalDirectories": ["$KIT_WORK_DIR"], "deny": ["Bash(git push -f*)", "Read(.env)"]}},
    )

    result = run_merge(target, kit)

    assert result.returncode == 0, result.stderr
    permissions = json.loads(target.read_text())["permissions"]
    assert permissions["additionalDirectories"] == ["/srv/notes", "/home/someone/Work"]
    assert permissions["deny"] == ["Bash(rm -rf /*)", "Bash(git push -f*)", "Read(.env)"]


def test_a_rerun_changes_nothing_and_makes_no_new_backup(tmp_path):
    target = write_json(tmp_path / "settings.json", {"theme": "dark", "permissions": {"deny": ["Read(secrets)"]}})

    first = run_merge(target, KIT_SETTINGS, SANDBOX_SETTINGS)
    merged_once = target.read_text()
    backups_after_first = sorted((tmp_path / "backups").iterdir())
    second = run_merge(target, KIT_SETTINGS, SANDBOX_SETTINGS)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert second.stdout.startswith("settings unchanged")
    assert target.read_text() == merged_once
    assert sorted((tmp_path / "backups").iterdir()) == backups_after_first


def test_the_work_folder_resolves_to_an_absolute_path(tmp_path):
    kit = write_json(tmp_path / "kit.json", {"permissions": {"additionalDirectories": ["$KIT_WORK_DIR/"]}})

    result = run_merge(tmp_path / "settings.json", kit, work_dir="~/Code")

    assert result.returncode == 0, result.stderr
    directories = json.loads((tmp_path / "settings.json").read_text())["permissions"]["additionalDirectories"]
    assert directories == [str(Path("~/Code").expanduser())]


def test_an_unset_work_folder_stops_the_merge_and_leaves_the_settings_alone(tmp_path):
    target = write_json(tmp_path / "settings.json", {"theme": "dark"})
    before = target.read_text()
    kit = write_json(tmp_path / "kit.json", {"permissions": {"additionalDirectories": ["$KIT_WORK_DIR"]}})

    result = run_merge(target, kit, work_dir=None)

    assert result.returncode != 0
    assert "KIT_WORK_DIR" in result.stderr
    assert "Traceback" not in result.stderr
    assert target.read_text() == before


def test_a_relative_work_folder_is_refused(tmp_path):
    kit = write_json(tmp_path / "kit.json", {"permissions": {"additionalDirectories": ["$KIT_WORK_DIR"]}})

    result = run_merge(tmp_path / "settings.json", kit, work_dir="Work")

    assert result.returncode != 0
    assert "absolute" in result.stderr
    assert not (tmp_path / "settings.json").exists()


def test_kit_owned_lists_replace_the_old_ones_so_a_dropped_entry_goes_away(tmp_path):
    target = write_json(
        tmp_path / "settings.json",
        {"sandbox": {"excludedCommands": ["docker *", "old-tool *"]}, "autoMode": {"environment": ["Old line"]}},
    )
    kit = write_json(
        tmp_path / "kit.json",
        {"sandbox": {"excludedCommands": ["docker *"]}, "autoMode": {"environment": ["New line"]}},
    )

    result = run_merge(target, kit)

    assert result.returncode == 0, result.stderr
    merged = json.loads(target.read_text())
    assert merged["sandbox"] == {"excludedCommands": ["docker *"]}
    assert merged["autoMode"]["environment"] == ["New line"]


def test_the_local_overlay_adds_to_kit_owned_lists_instead_of_replacing_them(tmp_path):
    target = write_json(tmp_path / "settings.json", {"autoMode": {"environment": ["Stale line"]}})
    kit = write_json(
        tmp_path / "kit.json",
        {"autoMode": {"environment": ["$defaults", "Kit line"]}, "sandbox": {"excludedCommands": ["gh *"]}},
    )
    overlay = write_json(
        tmp_path / "settings.local.json",
        {"autoMode": {"environment": ["Client line"]}, "sandbox": {"excludedCommands": ["gh *", "aws *"]}},
    )

    result = run_merge(target, kit, overlay)

    assert result.returncode == 0, result.stderr
    merged = json.loads(target.read_text())
    assert merged["autoMode"]["environment"] == ["$defaults", "Kit line", "Client line"]
    assert merged["sandbox"]["excludedCommands"] == ["gh *", "aws *"]


def test_a_broken_overlay_stops_the_merge_with_its_name_and_leaves_the_settings_alone(tmp_path):
    target = write_json(tmp_path / "settings.json", {"theme": "dark"})
    before = target.read_text()
    overlay = tmp_path / "settings.local.json"
    overlay.write_text('{"autoMode": {"environment": ["Client line",]}}')

    result = run_merge(target, KIT_SETTINGS, overlay)

    assert result.returncode != 0
    assert str(overlay) in result.stderr
    assert "Traceback" not in result.stderr
    assert target.read_text() == before


def test_a_rerun_with_the_overlay_changes_nothing(tmp_path):
    target = write_json(tmp_path / "settings.json", {"theme": "dark"})
    overlay = write_json(tmp_path / "settings.local.json", {"autoMode": {"environment": ["Client line"]}})

    first = run_merge(target, KIT_SETTINGS, SANDBOX_SETTINGS, overlay)
    merged_once = target.read_text()
    second = run_merge(target, KIT_SETTINGS, SANDBOX_SETTINGS, overlay)

    assert first.returncode == 0, first.stderr
    assert second.stdout.startswith("settings unchanged")
    assert target.read_text() == merged_once
    assert json.loads(merged_once)["autoMode"]["environment"][-1] == "Client line"


def test_the_real_kit_settings_open_the_work_folder_and_keep_git_network_commands_out_of_the_sandbox(tmp_path):
    target = write_json(
        tmp_path / "settings.json",
        {"permissions": {"additionalDirectories": ["/srv/notes"], "deny": ["Bash(npm publish*)"]}},
    )

    result = run_merge(target, KIT_SETTINGS, SANDBOX_SETTINGS)

    assert result.returncode == 0, result.stderr
    merged = json.loads(target.read_text())
    assert merged["permissions"]["additionalDirectories"] == ["/srv/notes", "/home/someone/Work"]
    assert set(FORCE_PUSH_DENIES) <= set(merged["permissions"]["deny"])
    assert "Bash(npm publish*)" in merged["permissions"]["deny"]
    assert set(GIT_NETWORK_COMMANDS) <= set(merged["sandbox"]["excludedCommands"])


def test_the_dash_c_form_of_git_network_commands_is_excluded_and_its_force_push_denied(tmp_path):
    result = run_merge(tmp_path / "settings.json", KIT_SETTINGS, SANDBOX_SETTINGS)

    assert result.returncode == 0, result.stderr
    merged = json.loads((tmp_path / "settings.json").read_text())
    excluded = merged["sandbox"]["excludedCommands"]
    for verb in ("push", "fetch", "pull", "ls-remote"):
        assert {f"git -C * {verb}", f"git -C * {verb} *"} <= set(excluded)
    assert {
        "Bash(git -C * push --force*)",
        "Bash(git -C * push -f*)",
        "Bash(git -C * push * --force*)",
        "Bash(git -C * push * -f*)",
    } <= set(merged["permissions"]["deny"])


def test_sandboxed_commands_cant_write_the_live_kit_but_can_write_its_worktrees(tmp_path):
    result = run_merge(tmp_path / "settings.json", KIT_SETTINGS, SANDBOX_SETTINGS)

    assert result.returncode == 0, result.stderr
    deny_write = json.loads((tmp_path / "settings.json").read_text())["sandbox"]["filesystem"]["denyWrite"]
    assert deny_write == [
        f"{LIVE_KIT}/home",
        f"{LIVE_KIT}/plugins",
        f"{LIVE_KIT}/settings",
        f"{LIVE_KIT}/scripts",
        f"{LIVE_KIT}/templates",
        f"{LIVE_KIT}/install.sh",
        f"{LIVE_KIT}/.githooks",
        f"{LIVE_KIT}/.claude-plugin",
        f"{LIVE_KIT_GIT}/hooks",
        f"{LIVE_KIT_GIT}/config",
    ]
    worktree = Path(LIVE_KIT, ".worktrees", "some-branch", "home", "hooks", "guard.py")
    assert not any(worktree.is_relative_to(entry) for entry in deny_write)


def test_without_the_live_kit_path_the_merge_stops(tmp_path):
    env = {key: value for key, value in os.environ.items() if key not in KIT_PATH_VARIABLES}
    env["KIT_WORK_DIR"] = "/home/someone/Work"

    result = subprocess.run(
        [sys.executable, str(MERGE), str(tmp_path / "settings.json"), str(KIT_SETTINGS), str(SANDBOX_SETTINGS)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "KIT_DIR" in result.stderr
    assert not (tmp_path / "settings.json").exists()
