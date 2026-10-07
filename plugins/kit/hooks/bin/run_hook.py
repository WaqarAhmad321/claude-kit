"""Run a kit hook script by name: `run_hook.py <script_name> [args...]`.

Applies the hook's failure policy to errors the script can't catch itself (a syntax error, a failed
import, a missing file or `main()`), and decides whether KIT_BYPASS may skip the hook.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType

BIN = Path(__file__).resolve().parent
# Known before import, so a script that can't even be imported still fails the right way.
FAIL_CLOSED_HOOKS = frozenset(
    {
        "guard_module_skill",
        "guard_protected_files",
        "guard_protected_bash",
        "guard_docs_location",
        "scan_secrets",
        "guard_commit",
        "stop_gate",
        "stop_module_skills",
    }
)
_STOP_EVENTS = frozenset({"Stop", "SubagentStop"})
_SCRIPT_NAME = re.compile(r"[a-z_][a-z0-9_]*")
_SETTINGS_BYPASS_NOTICE = (
    "kit: KIT_BYPASS is set in a Claude Code settings file, which Claude can edit, so the kit ignored it. "
    "Remove it there; to skip kit hooks, set KIT_BYPASS in the shell that launches Claude Code."
)


def main() -> None:
    name, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    raw = sys.stdin.read()
    sys.stdin = io.StringIO(raw)
    inp = _parse(raw)
    notice = None
    if _bypass_requested(name):
        if not _bypass_from_settings(inp):
            return
        os.environ.pop("KIT_BYPASS")
        notice = _SETTINGS_BYPASS_NOTICE
    module = None
    try:
        module = _load(name)
        sys.argv = [str(module.__file__), *args]
        _run(module, notice, inp)
    except Exception as error:
        _fail(name, error, _fails_closed(name, module), inp)


def _parse(raw: str) -> dict:
    try:
        inp = json.loads(raw) if raw.strip() else {}
    except ValueError:  # The script reports bad input under its own policy.
        return {}
    return inp if isinstance(inp, dict) else {}


def _bypass_requested(name: str) -> bool:
    # Duplicates kitlib.bypassed() on purpose: the bypass has to work when kitlib itself is broken.
    names = {entry.strip() for entry in os.environ.get("KIT_BYPASS", "").split(",")}
    return name in names or "all" in names


def _bypass_from_settings(inp: dict) -> bool:
    return any("KIT_BYPASS" in _settings_env(path) for path in _settings_files(inp))


def _settings_files(inp: dict) -> list[Path]:
    user_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    projects = dict.fromkeys(project for project in (os.environ.get("CLAUDE_PROJECT_DIR"), inp.get("cwd")) if project)
    return [
        user_dir / "settings.json",
        *(
            Path(project) / ".claude" / name
            for project in projects
            for name in ("settings.json", "settings.local.json")
        ),
    ]


def _settings_env(path: Path) -> dict:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # Missing, unreadable or malformed: Claude Code can't apply its env either.
        return {}
    env = settings.get("env") if isinstance(settings, dict) else None
    return env if isinstance(env, dict) else {}


def _load(name: str) -> ModuleType:
    if not _SCRIPT_NAME.fullmatch(name):
        raise ValueError(f"not a hook script name: {name!r}")
    spec = importlib.util.spec_from_file_location(name, BIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(module: ModuleType, notice: str | None, inp: dict) -> None:
    if notice is None:
        module.main()
        return
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            module.main()
    except SystemExit as exit_:
        _release(captured.getvalue(), notice if exit_.code in (None, 0) else None, inp)
        raise
    _release(captured.getvalue(), notice, inp)


def _release(output: str, notice: str | None, inp: dict) -> None:
    """Pass the hook's output through; the first quiet hook of the session carries the notice instead."""
    if not output and notice and _claim_notice(inp):
        output = json.dumps({"systemMessage": notice}) + "\n"
    sys.stdout.write(output)


def _claim_notice(inp: dict) -> bool:
    import kitlib  # The hook itself imported it already.

    marker = kitlib._data_dir() / "state" / kitlib._safe_name(str(inp.get("session_id") or "")) / "bypass-ignored"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=False)
    except FileExistsError:
        return False
    except OSError:  # Nowhere to remember that it was shown, so it's shown each time.
        return True
    return True


def _fails_closed(name: str, module: ModuleType | None) -> bool:
    return name in FAIL_CLOSED_HOOKS or getattr(module, "FAIL_CLOSED", False) is True


def _fail(name: str, error: Exception, fail_closed: bool, inp: dict) -> None:
    detail = f"{type(error).__name__}: {error}"
    if not fail_closed:
        _notify(f"kit: {name} hook failed: {detail}")
    if inp.get("hook_event_name") in _STOP_EVENTS and inp.get("stop_hook_active") is True:
        # This stop was already blocked once; a hook that keeps crashing must not block every stop.
        _notify(
            f"kit: {name} hook failed again ({detail}), so Claude may stop this time. "
            f"Fix the hook, or relaunch Claude Code with KIT_BYPASS={name}."
        )
    sys.stderr.write(
        f"kit: {name} hook failed ({detail}). Fix the hook, or relaunch Claude Code with KIT_BYPASS={name}."
    )
    sys.exit(2)


def _notify(message: str) -> None:
    print(json.dumps({"systemMessage": message}))
    sys.exit(0)


if __name__ == "__main__":
    main()
