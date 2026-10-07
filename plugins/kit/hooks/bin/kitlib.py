"""Shared helpers for the kit plugin's hooks. Python 3 stdlib only."""

from __future__ import annotations

import contextlib
import copy
import fcntl
import functools
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

_FEEDBACK_LIMIT = 8000
_STATE_TTL_SECONDS = 7 * 24 * 3600
_STATE_DEFAULTS = {"edits": {}, "edit_seq": 0, "journey_pass_seq": 0, "skills_seen": {}}

_PY_EXTS = frozenset({".py", ".pyi"})
_JS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"})
_EXEMPT_DIRS = frozenset({".claude", "migrations", "node_modules", ".git", ".venv", "venv"})
_EXEMPT_NAMES = frozenset({"CONTRACTS.md", "SCHEMA.md"})
_SYSTEM_TEMP_DIR = "/tmp"
_KIT_REPO_MARKER = "plugins/kit/.claude-plugin/plugin.json"
_KIT_TEMPLATES_DIR = "templates"

_TOOL_RUFF_TABLE = re.compile(r"^\s*\[tool\.ruff[\].]", re.MULTILINE)
_RUFF_CONFIGS = ("ruff.toml", ".ruff.toml")
_OXLINT_CONFIGS = (".oxlintrc.json", ".oxlintrc.jsonc", "oxlint.config.ts")
_ESLINT_CONFIGS = (
    *(f"eslint.config.{ext}" for ext in ("js", "mjs", "cjs", "ts", "mts", "cts")),
    *(f".eslintrc{ext}" for ext in ("", ".js", ".cjs", ".json", ".yaml", ".yml")),
)
_PRETTIER_CONFIGS = (
    *(f".prettierrc{ext}" for ext in ("", ".json", ".json5", ".yaml", ".yml", ".toml")),
    *(f".prettierrc.{ext}" for ext in ("js", "cjs", "mjs", "ts", "cts", "mts")),
    *(f"prettier.config.{ext}" for ext in ("js", "cjs", "mjs", "ts", "cts", "mts")),
)
_JS_FORMATTERS = (
    ("biome", ("biome.json", "biome.jsonc")),
    ("oxfmt", (".oxfmtrc.json", ".oxfmtrc.jsonc")),
    ("prettier", _PRETTIER_CONFIGS),
)

_FORMAT_CHECK_ARGS = {
    "ruff": ["format", "--check", "--force-exclude", "--no-cache"],
    "prettier": ["--check"],
    "oxfmt": ["--check"],
    "biome": ["format"],
}
_FORMAT_WRITE_ARGS = {
    "ruff": ["format", "--force-exclude", "--no-cache"],
    "prettier": ["--write"],
    "oxfmt": ["--write"],
    "biome": ["format", "--write"],
}
# An import or variable added one edit before its first use must survive the autofix.
_FIX_ARGS = {
    "ruff": ["check", "--fix-only", "--no-unsafe-fixes", "--unfixable", "F401,F841", "--force-exclude", "--no-cache"],
    "oxlint": ["--fix", "-A", "no-unused-vars"],
    "eslint": ["--fix"],
}

_PY_SUPPRESSIONS = {
    "# noqa": re.compile(r"#\s*noqa\b", re.IGNORECASE),
    "# ruff: noqa": re.compile(r"#\s*ruff\s*:\s*noqa\b", re.IGNORECASE),
    "# flake8: noqa": re.compile(r"#\s*flake8\s*:\s*noqa\b", re.IGNORECASE),
    "# ruff: disable": re.compile(r"#\s*ruff\s*:\s*disable\b", re.IGNORECASE),
    "# type: ignore": re.compile(r"#\s*type:\s*ignore\b"),
    "# mypy: ignore-errors": re.compile(r"#\s*mypy\s*:\s*ignore-errors\b"),
    "# pyright: ignore": re.compile(r"#\s*pyright\s*:\s*ignore\b"),
    "# pragma: no cover": re.compile(r"#\s*pragma:\s*no\s*cover\b"),
}
_JS_SUPPRESSIONS = {
    "eslint-disable": re.compile(r"\beslint-disable(?![-\w])"),
    "eslint-disable-next-line": re.compile(r"\beslint-disable-next-line\b"),
    "eslint-disable-line": re.compile(r"\beslint-disable-line\b"),
    "oxlint-disable": re.compile(r"\boxlint-disable(?![-\w])"),
    "oxlint-disable-next-line": re.compile(r"\boxlint-disable-next-line\b"),
    "oxlint-disable-line": re.compile(r"\boxlint-disable-line\b"),
    "@ts-ignore": re.compile(r"@ts-ignore\b"),
    "@ts-expect-error": re.compile(r"@ts-expect-error\b"),
    "@ts-nocheck": re.compile(r"@ts-nocheck\b"),
}
_STOP_EVENTS = frozenset({"Stop", "SubagentStop"})


@functools.cache
def read_input() -> dict:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("hook input is not a JSON object")
    return payload


def project_root(path: str | None = None) -> Path | None:
    start = Path(path) if path else Path(read_input().get("cwd") or os.getcwd())
    directory = _nearest_existing_dir(start)
    return _git_toplevel(directory) if directory else None


@functools.cache  # A hook process is short-lived, and its guards ask about the same directories repeatedly.
def _git_toplevel(directory: Path) -> Path | None:
    code, out, _ = run(["git", "rev-parse", "--show-toplevel"], directory, timeout=10)
    top = out.strip()
    return Path(top).resolve() if code == 0 and top else None


def load_config(root: Path) -> dict:
    config_path = Path(root) / ".claude" / "kit.json"
    if not config_path.is_file():
        return {}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"{config_path} must hold a JSON object")
    return config


def bypassed(hook: str) -> bool:
    names = {name.strip() for name in os.environ.get("KIT_BYPASS", "").split(",")}
    return hook in names or "all" in names


def _nearest_existing_dir(path: Path) -> Path | None:
    path = path.expanduser().absolute()
    return next((candidate for candidate in (path, *path.parents) if candidate.is_dir()), None)


def _edited_path(inp: dict) -> Path | None:
    written = _written_path(inp)
    return written.resolve() if written else None


def _edited_rel_paths(inp: dict, root: Path) -> list[str]:
    """The edited file relative to `root`, as written and with symlinks resolved; a guard checks both."""
    written = _written_path(inp)
    found: list[str] = []
    for path in (written, written.resolve()) if written else ():
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else None
        if rel and rel not in found:
            found.append(rel)
    return found


def _written_path(inp: dict) -> Path | None:
    tool_input = inp.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(inp.get("cwd") or os.getcwd()) / path
    return Path(os.path.normpath(path))


def _is_exempt(root: Path, path: Path) -> bool:
    if not path.is_relative_to(root):
        return True
    parent_parts = path.relative_to(root).parts[:-1]
    return path.name in _EXEMPT_NAMES or any(part in _EXEMPT_DIRS for part in parent_parts)


def session_project() -> str | None:
    """The directory the session started in: $CLAUDE_PROJECT_DIR, else the hook input's cwd."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or read_input().get("cwd")


def is_kit_repo(root: Path) -> bool:
    return (Path(root) / _KIT_REPO_MARKER).is_file()


def is_non_project(root: Path | None, path: Path) -> bool:
    """Scratch work under a temp directory, or a starter file in the kit repo's templates/: not project code, so
    no lint, stop check or gate-config check covers it. `root` is the repo holding `path`, if any."""
    if is_scratch(path):
        return True
    return root is not None and is_kit_repo(root) and Path(path).is_relative_to(Path(root) / _KIT_TEMPLATES_DIR)


def is_scratch(path: Path) -> bool:
    path = Path(path)
    return any(form.is_relative_to(directory) for form in (path, path.resolve()) for directory in scratch_dirs())


@functools.cache
def scratch_dirs() -> tuple[Path, ...]:
    candidates = (read_input().get("scratchpad_dir"), os.environ.get("TMPDIR"), _SYSTEM_TEMP_DIR)
    return _scratch_dirs(Path(session_project() or os.getcwd()), candidates)


def _scratch_dirs(project: Path, candidates) -> tuple[Path, ...]:
    """The absolute temp directories among `candidates`, minus any that holds the session's project: a project
    checked out under /tmp is still project code, and a TMPDIR of / or $HOME exempts nothing."""
    projects = {project.expanduser().absolute(), project.expanduser().resolve()}
    found: set[Path] = set()
    for candidate in candidates:
        if not candidate or not Path(candidate).is_absolute():
            continue
        forms = {Path(candidate), Path(candidate).resolve()}
        if not any(place.is_relative_to(form) for place in projects for form in forms):
            found |= forms
    return tuple(sorted(found))


def _data_dir() -> Path:
    """The plugin data directory, or ~/.cache/claude-kit when it's unset or can't be written."""
    configured = os.environ.get("CLAUDE_PLUGIN_DATA")
    if configured and _can_create(Path(configured) / "state"):
        return Path(configured)
    return Path.home() / ".cache" / "claude-kit"


def _can_create(path: Path) -> bool:
    existing = _nearest_existing_dir(path)
    return existing is not None and os.access(existing, os.W_OK | os.X_OK)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", value) or "unknown"


class State:
    """`with State(inp) as st:` gives a dict persisted per session and agent under an exclusive lock."""

    def __init__(self, inp: dict):
        self.session_dir = _data_dir() / "state" / _safe_name(inp.get("session_id") or "unknown")
        self.path = self.session_dir / f"{_safe_name(inp.get('agent_id') or 'main')}.json"
        self._lock_file = None
        self._data: dict = {}

    def __enter__(self) -> dict:
        if not self.session_dir.exists():
            self.session_dir.mkdir(parents=True, exist_ok=True)
            _prune_old_sessions(self.session_dir.parent, keep=self.session_dir)
        # The lock lives in its own file because saving replaces the JSON file's inode.
        self._lock_file = open(self.path.with_suffix(".lock"), "a")
        fcntl.flock(self._lock_file, fcntl.LOCK_EX)
        self._data = self._load()
        return self._data

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            # Hooks exit through sys.exit() (feedback, stop_block, ...) while still inside the block.
            if exc_type is None or issubclass(exc_type, SystemExit):
                self._save()
        finally:
            self._lock_file.close()
        return False

    def _load(self) -> dict:
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        for key, default in _STATE_DEFAULTS.items():
            data.setdefault(key, copy.deepcopy(default))
        return data

    def _save(self) -> None:
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)


def _prune_old_sessions(state_dir: Path, keep: Path) -> None:
    cutoff = time.time() - _STATE_TTL_SECONDS
    for session_dir in state_dir.iterdir():
        if session_dir == keep or not session_dir.is_dir() or session_dir.stat().st_mtime >= cutoff:
            continue
        with contextlib.suppress(FileNotFoundError):  # Another hook pruned it at the same time.
            shutil.rmtree(session_dir)


def pre_tool_decision(decision: str, reason: str) -> NoReturn:
    if decision not in ("deny", "ask", "allow"):
        raise ValueError(f"unknown PreToolUse decision: {decision}")
    _emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }
    )


def feedback(reason: str) -> NoReturn:
    sys.stderr.write(_truncate(reason, _FEEDBACK_LIMIT))
    sys.exit(2)


def stop_block(reason: str) -> NoReturn:
    _emit({"decision": "block", "reason": reason})


def notify_user(msg: str) -> NoReturn:
    _emit({"systemMessage": msg})


def _emit(payload: dict) -> NoReturn:
    print(json.dumps(payload))
    sys.exit(0)


def _truncate(text: str, limit: int) -> str:
    marker = "\n[truncated]"
    return text if len(text) <= limit else text[: limit - len(marker)] + marker


def hook_main(name: str, fail_closed: bool) -> Callable[[Callable], Callable]:
    def decorate(main: Callable) -> Callable:
        @functools.wraps(main)
        def wrapper(*args, **kwargs):
            if bypassed(name):
                return None
            try:
                return main(*args, **kwargs)
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"
                if not fail_closed:
                    notify_user(f"kit: {name} hook failed ({detail}); skipped it. KIT_BYPASS={name} silences it.")
                if _is_repeated_stop():
                    notify_user(
                        f"kit: {name} hook failed again ({detail}), so Claude may stop this time. "
                        f"Fix the hook, or relaunch Claude Code with KIT_BYPASS={name}."
                    )
                sys.stderr.write(
                    f"kit: {name} hook failed ({detail}). Fix the hook, or relaunch Claude Code with KIT_BYPASS={name}."
                )
                sys.exit(2)

        return wrapper

    return decorate


def _is_repeated_stop() -> bool:
    """A stop that a hook already blocked once; a hook that keeps crashing must not block every stop."""
    try:
        inp = read_input()
    except ValueError:  # Unreadable input can't show a repeat, so the crash blocks.
        return False
    return inp.get("hook_event_name") in _STOP_EVENTS and inp.get("stop_hook_active") is True


def run(cmd: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def find_tool(name: str, root: Path) -> str | None:
    return _local_tool(name, Path(root)) or _path_tool(name)


def _local_tool(name: str, directory: Path) -> str | None:
    for subdir in ("node_modules/.bin", ".venv/bin", "venv/bin"):
        candidate = directory / subdir / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _path_tool(name: str) -> str | None:
    mise_data = Path(os.environ.get("MISE_DATA_DIR") or Path.home() / ".local" / "share" / "mise")
    return shutil.which(name) or shutil.which(name, path=str(mise_data / "shims"))


def _ancestors(root: Path, path: Path) -> list[Path]:
    """The file's directory and each parent up to root, nearest first."""
    if not path.is_relative_to(root):
        return []
    directory, found = path.parent, []
    while directory.is_relative_to(root):
        found.append(directory)
        if directory == root:
            break
        directory = directory.parent
    return found


def _nearest_tool(name: str, root: Path, path: Path) -> str | None:
    for directory in _ancestors(root, path):
        found = _local_tool(name, directory)
        if found:
            return found
    return _path_tool(name)


@dataclass(frozen=True)
class _Tool:
    name: str
    exe: str
    cwd: Path


@dataclass(frozen=True)
class _Violation:
    tool: str
    code: str
    message: str
    line: int
    fixable: bool

    @property
    def fingerprint(self) -> tuple[str, str, str]:
        return (self.tool, self.code, _normalize_message(self.message))


def _normalize_message(message: str) -> str:
    # Numbers in messages (lengths, counts, "from line 12") move with unrelated edits.
    return re.sub(r"\b\d+\b", "N", " ".join(message.split()))


def _fingerprint_key(fingerprint: tuple[str, str, str]) -> str:
    return json.dumps(list(fingerprint))


def _fingerprint_from_key(key: str) -> tuple[str, str, str]:
    return tuple(json.loads(key))


def _has_any(directory: Path, names: tuple[str, ...]) -> bool:
    return any((directory / name).is_file() for name in names)


def _package_json_has(directory: Path, key: str) -> bool:
    package = directory / "package.json"
    if not package.is_file():
        return False
    data = json.loads(package.read_text(encoding="utf-8"))
    return isinstance(data, dict) and key in data


def _has_ruff_config(directory: Path) -> bool:
    if _has_any(directory, _RUFF_CONFIGS):
        return True
    pyproject = directory / "pyproject.toml"
    return pyproject.is_file() and bool(
        _TOOL_RUFF_TABLE.search(pyproject.read_text(encoding="utf-8", errors="replace"))
    )


def _has_oxlint(directory: Path) -> bool:
    return _has_any(directory, _OXLINT_CONFIGS) or _local_tool("oxlint", directory) is not None


def _has_eslint_config(directory: Path) -> bool:
    return _has_any(directory, _ESLINT_CONFIGS) or _package_json_has(directory, "eslintConfig")


def _nearest_dir(root: Path, path: Path, matches: Callable[[Path], bool]) -> Path | None:
    return next((directory for directory in _ancestors(root, path) if matches(directory)), None)


def _tool_at(name: str, config_dir: Path | None, root: Path, path: Path) -> _Tool | None:
    exe = _nearest_tool(name, root, path) if config_dir else None
    return _Tool(name, exe, config_dir) if exe else None


def _detect_linters(root: Path, path: Path) -> list[_Tool]:
    root, path = Path(root).resolve(), Path(path).resolve()
    if path.suffix in _PY_EXTS:
        tools = [_tool_at("ruff", _nearest_dir(root, path, _has_ruff_config), root, path)]
    elif path.suffix in _JS_EXTS:
        oxlint = _tool_at("oxlint", _nearest_dir(root, path, _has_oxlint), root, path)
        tools = [oxlint or _tool_at("eslint", _nearest_dir(root, path, _has_eslint_config), root, path)]
    else:
        tools = []
    return [tool for tool in tools if tool]


def _detect_formatter(root: Path, path: Path) -> _Tool | None:
    root, path = Path(root).resolve(), Path(path).resolve()
    if path.suffix in _PY_EXTS:
        return _tool_at("ruff", _nearest_dir(root, path, _has_ruff_config), root, path)
    if path.suffix not in _JS_EXTS:
        return None
    for directory in _ancestors(root, path):
        for name, configs in _JS_FORMATTERS:
            if _has_any(directory, configs) or (name == "prettier" and _package_json_has(directory, "prettier")):
                return _tool_at(name, directory, root, path)
    return None


def linters_for(root: Path, path: Path) -> list[str]:
    return [tool.name for tool in _detect_linters(root, path)]


def _violations(root: Path, path: Path) -> list[_Violation]:
    """Every lint violation and suppression comment in the file, with line numbers."""
    path = Path(path)
    if not path.is_file():
        return []
    found = [
        violation
        for tool in _detect_linters(root, path)
        for violation in _LINT_RUNNERS[tool.name](tool, path.resolve())
    ]
    return found + _suppressions(path)


def _suppressions(path: Path) -> list[_Violation]:
    return suppression_violations(path.read_text(encoding="utf-8", errors="replace"), path)


def suppression_violations(text: str, path: Path) -> list[_Violation]:
    """Suppression comments in `text`, read as the language of `path`: Python comments only, or JS/TS lines."""
    suffix = Path(path).suffix
    if suffix in _PY_EXTS:
        lines, patterns = _python_comments(text), _PY_SUPPRESSIONS
    elif suffix in _JS_EXTS:
        lines, patterns = list(enumerate(text.splitlines(), start=1)), _JS_SUPPRESSIONS
    else:
        return []
    return [
        _Violation("suppression", kind, "", line_number, False)
        for line_number, line in lines
        for kind, pattern in patterns.items()
        for _ in pattern.finditer(line)
    ]


def _python_comments(text: str) -> list[tuple[int, str]]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        # Code mid-edit may not tokenize; scanning every line then can only over-count.
        return list(enumerate(text.splitlines(), start=1))
    return [(token.start[0], token.string) for token in tokens if token.type == tokenize.COMMENT]


def _ruff_violations(tool: _Tool, path: Path) -> list[_Violation]:
    cmd = [tool.exe, "check", "--output-format", "json", "--no-fix", "--force-exclude", "--no-cache", str(path)]
    code, out, err = run(cmd, tool.cwd)
    if code not in (0, 1):
        raise RuntimeError(f"ruff check failed: {(err or out).strip()[:500]}")
    return [
        _Violation(
            "ruff",
            item.get("code") or "syntax-error",
            item.get("message", ""),
            (item.get("location") or {}).get("row", 0),
            (item.get("fix") or {}).get("applicability") == "safe",
        )
        for item in json.loads(out or "[]")
    ]


def _oxlint_violations(tool: _Tool, path: Path) -> list[_Violation]:
    _, out, err = run([tool.exe, "--format", "json", str(path)], tool.cwd)
    report = _json_from_output(out, err, "oxlint")
    diagnostics = report.get("diagnostics", []) if isinstance(report, dict) else report
    # oxlint's JSON doesn't say which diagnostics are fixable, so any of them might be.
    return [
        _Violation("oxlint", item.get("code") or "oxlint", item.get("message", ""), _oxlint_line(item), True)
        for item in diagnostics
    ]


def _oxlint_line(diagnostic: dict) -> int:
    labels = diagnostic.get("labels") or [{}]
    return (labels[0].get("span") or {}).get("line", 0)


def _eslint_violations(tool: _Tool, path: Path) -> list[_Violation]:
    code, out, err = run([tool.exe, "--format", "json", str(path)], tool.cwd)
    if code not in (0, 1):
        raise RuntimeError(f"eslint failed: {(err or out).strip()[:500]}")
    return [
        _Violation("eslint", item.get("ruleId") or "fatal", item.get("message", ""), item.get("line", 0), "fix" in item)
        for result in json.loads(out or "[]")
        for item in result.get("messages", [])
    ]


def _json_from_output(out: str, err: str, tool: str):
    # Some tools print a plain notice (e.g. "No files found to lint") before the JSON.
    lines = out.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith(("{", "["))), None)
    if start is None:
        raise RuntimeError(f"{tool} printed no JSON: {(err or out).strip()[:500]}")
    return json.loads("\n".join(lines[start:]))


_LINT_RUNNERS = {"ruff": _ruff_violations, "oxlint": _oxlint_violations, "eslint": _eslint_violations}


def format_check(root: Path, path: Path) -> bool | None:
    tool = _detect_formatter(root, path)
    if tool is None:
        return None
    if not Path(path).exists():
        return True
    code, _, _ = run([tool.exe, *_FORMAT_CHECK_ARGS[tool.name], str(Path(path).resolve())], tool.cwd)
    return code == 0


def apply_format(root: Path, path: Path) -> bool:
    tool = _detect_formatter(root, path)
    if tool is None or not Path(path).is_file():
        return False
    code, _, _ = run([tool.exe, *_FORMAT_WRITE_ARGS[tool.name], str(Path(path).resolve())], tool.cwd)
    return code == 0


def apply_fixes(root: Path, path: Path) -> None:
    if not Path(path).is_file():
        return
    for tool in _detect_linters(root, path):
        # Exit codes are ignored here: the re-lint that follows reports whatever a fix left behind.
        run([tool.exe, *_FIX_ARGS[tool.name], str(Path(path).resolve())], tool.cwd)
