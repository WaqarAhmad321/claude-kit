"""PreToolUse Edit|Write|NotebookEdit: ask the user before an edit changes quality-gate configuration."""

import configparser
import fnmatch
import functools
import importlib
import json
import os
import re
import shlex
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import kitlib
import kitskills

# kit_root lives with the plugin's scripts, which find the kit checkout the same way.
sys.path.append(str(Path(__file__).resolve().parents[2] / "scripts"))
kit_root = importlib.import_module("kit_root")

FAIL_CLOSED = True

_ROOT_GLOBS = (".claude/kit.json", ".claude/kit-baseline*", ".github/workflows/*", "conftest.py")
# lefthook reads its main config as lefthook.<ext> or .lefthook.<ext> (at the root or under .config/, which a
# name-only match already covers), and merges an extra lefthook-local.<ext> / .lefthook-local.<ext> the same way.
_LEFTHOOK_CONFIG_EXTENSIONS = (".yml", ".yaml", ".toml", ".json", ".jsonc")
_LEFTHOOK_CONFIG_BASENAMES = ("lefthook", ".lefthook", "lefthook-local", ".lefthook-local")
_NAME_GLOBS = (
    "ruff.toml",
    ".ruff.toml",
    ".oxlintrc.json",
    ".oxlintrc.jsonc",
    "eslint.config.*",
    ".eslintrc*",
    "biome.json",
    "biome.jsonc",
    "tsconfig*.json",
    *(f"{base}{ext}" for base in _LEFTHOOK_CONFIG_BASENAMES for ext in _LEFTHOOK_CONFIG_EXTENSIONS),
    ".pre-commit-config.yaml",
    "mypy.ini",
    ".mypy.ini",
    "pytest.ini",
    ".flake8",
    "tox.ini",
)
# lefthook runs the scripts in its default `source_dir` (.lefthook/<git hook>/<script>) and `source_dir_local`
# (.lefthook-local/), and the kit's lefthook.yml also extends .lefthook/<stack>.yml, so every file under them counts.
_LEFTHOOK_SOURCE_DIRS = frozenset({".lefthook", ".lefthook-local"})
_CLAUDE_SETTINGS = ("settings.json", "settings.local.json")
_PYPROJECT_TOOLS = ("ruff", "mypy", "importlinter", "pytest")
_GATE_SCRIPT = re.compile(r"(lint|test|typecheck|type-check|types|tsc|check)([:-].*)?")
_MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")
_MAKE_DIRECTORY_OPTIONS = frozenset({"-C", "--directory"})
_MAKE_FILE_OPTIONS = frozenset({"-f", "--file", "--makefile"})
_MAKE_OPTIONS_WITH_VALUE = _MAKE_DIRECTORY_OPTIONS | _MAKE_FILE_OPTIONS | {"-I", "--include-dir", "-o", "-W"}
_SHELL_OPERATORS = frozenset({"&&", "||", ";", "|"})
_MAKE_RULE = re.compile(r"(?P<targets>[^\s:#=][^:#=]*?)\s*::?(?![=:])(?P<rest>.*)")
_CLAUDE_CODE_DIRS = ("hooks", "kit-hooks", "plugins")
_KIT_OWN_GLOBS = ("plugins/kit/hooks/**", "settings/*.json", "home/hooks/**", "install.sh")
_MENTION_SEPARATOR = r"""['"/\\,+)} ]+"""  # Between path parts written apart: '.github', 'workflows'.


@kitlib.hook_main("guard_protected_files", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    written = kitlib._written_path(inp)
    if written is None:
        return
    # A symlink gives a file a second name (~/.claude/hooks points into the kit repo), so both names count.
    for path in dict.fromkeys((written, written.resolve())):
        what = _guarded_everywhere(path)
        if what:
            _ask(what, _shown(path))
    path = written.resolve()
    root = kitlib.project_root(str(path))
    if root is None or kitlib.is_non_project(root, path):
        return
    tool_input = inp.get("tool_input") or {}
    for rel in kitlib._edited_rel_paths(inp, root):
        if _changes_gate_config(root, rel, path, tool_input):
            _ask("quality-gate configuration", rel)


def _ask(what: str, shown: str) -> None:
    kitlib.pre_tool_decision("ask", f"This changes {what} ({shown}); the user has to approve it.")


def _guarded_everywhere(path: Path) -> str | None:
    """Files that could switch the kit off, in or out of a repo: Claude Code's settings, hooks and plugins,
    the kit's state, and the kit's own hook files."""
    config_dirs = _claude_config_dirs()
    if _is_claude_settings(path, config_dirs):
        return "Claude Code settings"
    if _within(path, [directory / name for directory in config_dirs for name in _CLAUDE_CODE_DIRS]):
        return "Claude Code hooks or plugins"
    if _within(path, _kit_state_dirs()):
        return "the kit's state"
    if _is_kit_own_file(path) and not _developing_the_kit(path):
        return "the kit's own hook files"
    return None


def _is_claude_settings(path: Path, config_dirs: list[Path]) -> bool:
    if path.name in _CLAUDE_SETTINGS and (path.parent.name == ".claude" or path.parent in config_dirs):
        return True
    # ~/.claude/settings.json may be a symlink into a repo, such as the kit's own settings/ directory.
    return path in {(directory / name).resolve() for directory in config_dirs for name in _CLAUDE_SETTINGS}


def _claude_config_dirs() -> list[Path]:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return _with_resolved([Path.home() / ".claude", *([Path(configured)] if configured else [])])


def _kit_state_dirs() -> list[Path]:
    configured = os.environ.get("CLAUDE_PLUGIN_DATA")
    return _with_resolved([Path.home() / ".cache" / "claude-kit", *([Path(configured)] if configured else [])])


def _is_kit_own_file(path: Path) -> bool:
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root and _within(path, _with_resolved([Path(plugin_root)])):
        return True
    root = _live_kit_of(path)
    return root is not None and any(
        kitskills.glob_matches(glob, path.relative_to(root).as_posix()) for glob in _KIT_OWN_GLOBS
    )


def _developing_the_kit(path: Path) -> bool:
    """The session itself runs in the live kit, which holds `path`, so changing the kit is the task."""
    return _session_in_live_kit() and _within(path, [_live_kit_root()])


def _session_in_live_kit() -> bool:
    project = kitlib.session_project()
    session_root = kitlib.project_root(project) if project else None
    return session_root is not None and session_root == _live_kit_root()


@functools.cache
def _live_kit_root() -> Path | None:
    """The kit checkout Claude Code runs, as kit_root finds it: the checkout holding the plugin, else the repo
    <config>/rules links into, else the claude-kit marketplace's installLocation. Other checkouts and worktrees of
    the kit are ordinary files."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    try:
        root = kit_root.find_kit_root(Path(plugin_root) / "scripts" / "kit_root.py" if plugin_root else None)
    except kit_root.KitRootError:
        return None
    return root.resolve()


def _live_kit_of(path: Path) -> Path | None:
    root = _kit_checkout_of(path)
    return root if root is not None and root == _live_kit_root() else None


def _kit_checkout_of(path: Path) -> Path | None:
    root = kitlib.project_root(str(path))
    return root if root is not None and kitlib.is_kit_repo(root) and path.is_relative_to(root) else None


def _with_resolved(paths: list[Path]) -> list[Path]:
    return list(dict.fromkeys(form for path in paths for form in (path, path.resolve())))


def _within(path: Path, directories: list[Path]) -> bool:
    return any(path.is_relative_to(directory) for directory in directories)


def _shown(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home).as_posix()}" if path.is_relative_to(home) else str(path)


def _changes_gate_config(root: Path, rel: str, path: Path, tool_input: dict) -> bool:
    if _is_gate_file(rel, PurePosixPath(rel).name):
        return True
    gates = _section_gates(root, rel)
    return gates is not None and _section_changed(path, tool_input, gates)


def _is_gate_file(rel: str, name: str) -> bool:
    """Files that are quality-gate configuration as a whole. The stop gate's tamper check uses this too."""
    parts = PurePosixPath(rel).parts
    claude_settings = len(parts) >= 2 and parts[-2] == ".claude" and name in _CLAUDE_SETTINGS
    # The directory itself counts too, for `rm -rf .lefthook` in the Bash guard.
    lefthook_scripts = not _LEFTHOOK_SOURCE_DIRS.isdisjoint(parts)
    # fnmatch's `*` crosses `/`, so `.github/workflows/*` covers nested workflow files too.
    return (
        claude_settings
        or lefthook_scripts
        or any(fnmatch.fnmatchcase(rel, glob) for glob in _ROOT_GLOBS)
        or any(fnmatch.fnmatchcase(name, glob) for glob in _NAME_GLOBS)
    )


@functools.cache
def gate_file_mention() -> re.Pattern:
    """Text naming a whole-file gate config, for write targets the Bash guard can't resolve to a path (a shell word it
    can't expand, a write call in code): each file name from _is_gate_file's lists, and its directories, with the
    parts of a directory path apart or joined."""
    names = [*_NAME_GLOBS, *(PurePosixPath(glob).name for glob in _ROOT_GLOBS if not glob.endswith("/*"))]
    directories = [*_LEFTHOOK_SOURCE_DIRS, *(glob.removesuffix("/*") for glob in _ROOT_GLOBS if glob.endswith("/*"))]
    patterns = [_glob_text(name) + r"(?![\w-])" for name in names]
    patterns += [
        _MENTION_SEPARATOR.join(map(re.escape, directory.split("/"))) + r"(?![\w.-])" for directory in directories
    ]
    return re.compile(r"(?<![\w-])(?:" + "|".join(patterns) + ")")


def _glob_text(glob: str) -> str:
    return "[\\w.-]*".join(re.escape(part) for part in glob.split("*"))


def _section_gates(root: Path, rel: str) -> Callable[[str], object] | None:
    """For files that are only partly gate configuration: a function giving their gate part."""
    name = PurePosixPath(rel).name
    by_name = {"pyproject.toml": _pyproject_gates, "setup.cfg": _setup_cfg_gates, "package.json": _package_json_gates}
    if name in by_name:
        return by_name[name]
    if name in _MAKEFILE_NAMES or name.endswith(".mk"):
        return _journey_recipe_gates(root, rel)
    return None


def _section_changed(path: Path, tool_input: dict, gates: Callable[[str], object]) -> bool:
    before = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    return gates(before) != gates(_content_after(before, tool_input))


def _content_after(before: str, tool_input: dict) -> str:
    if "content" in tool_input:
        return tool_input["content"] or ""
    old, new = tool_input.get("old_string") or "", tool_input.get("new_string") or ""
    if not old:
        return before or new
    # Replacing every occurrence covers whichever one a single Edit changes; no occurrence means the Edit fails.
    return before.replace(old, new)


def _pyproject_gates(text: str) -> dict | None:
    try:
        tool = tomllib.loads(text).get("tool", {})
    except tomllib.TOMLDecodeError:
        return None
    return {name: tool.get(name) for name in _PYPROJECT_TOOLS}


def _setup_cfg_gates(text: str) -> dict | None:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    return {
        section: dict(parser[section])
        for section in parser.sections()
        if section == "flake8" or section == "mypy" or section.startswith("mypy-")
    }


def _package_json_gates(text: str) -> dict | None:
    try:
        package = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(package, dict):
        return None
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    gate_scripts = {name: command for name, command in scripts.items() if _GATE_SCRIPT.fullmatch(name)}
    return {"eslintConfig": package.get("eslintConfig"), "scripts": gate_scripts}


def _journey_recipe_gates(root: Path, rel: str) -> Callable[[str], object] | None:
    """Best effort: when `gate.journeys.command` runs make, the recipes it runs from this makefile."""
    command = ((kitlib.load_config(root).get("gate") or {}).get("journeys") or {}).get("command")
    invocation = _make_invocation(command) if isinstance(command, str) else None
    if invocation is None:
        return None
    makefiles, targets = invocation
    return functools.partial(_make_recipes, targets=targets) if rel in makefiles else None


def _make_invocation(command: str) -> tuple[set[str], tuple[str, ...]] | None:
    try:
        words = shlex.split(command)
    except ValueError:  # Unbalanced quotes: the command can't run make either.
        return None
    start = next((index for index, word in enumerate(words) if PurePosixPath(word).name in ("make", "gmake")), None)
    if start is None:
        return None
    directory, makefile, targets = "", None, []
    args = iter(words[start + 1 :])
    for word in args:
        if word in _SHELL_OPERATORS:
            break
        option, inline, value = word.partition("=") if word.startswith("--") else (word, "", "")
        if option in _MAKE_OPTIONS_WITH_VALUE:
            value = value if inline else next(args, "")
            directory = value if option in _MAKE_DIRECTORY_OPTIONS else directory
            makefile = value if option in _MAKE_FILE_OPTIONS else makefile
        elif not word.startswith("-") and "=" not in word:
            targets.append(word)
    names = (makefile,) if makefile else _MAKEFILE_NAMES
    return {PurePosixPath(directory, name).as_posix() for name in names}, tuple(targets)


def _make_recipes(text: str, targets: tuple[str, ...]) -> dict[str, list[str]]:
    """The rule lines of `targets` (or the default goal) and of every prerequisite they pull in."""
    rules, prerequisites, order = _make_rules(text)
    pending, found = list(targets) or order[:1], {}
    while pending:
        target = pending.pop()
        if target in found or target not in rules:
            continue
        found[target] = rules[target]
        pending.extend(prerequisites.get(target, []))
    return found


def _make_rules(text: str) -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    rules: dict[str, list[str]] = {}
    prerequisites: dict[str, list[str]] = {}
    order: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("\t"):
            for target in current:
                rules[target].append(line)
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rule = _MAKE_RULE.fullmatch(line)
        current = rule.group("targets").split() if rule else []
        rest = rule.group("rest") if rule else ""
        for target in current:
            if target not in rules and not target.startswith("."):
                order.append(target)
            rules.setdefault(target, []).append(line)
            if "=" not in rest:
                prerequisites.setdefault(target, []).extend(rest.split(";")[0].replace("|", " ").split())
    return rules, prerequisites, order


if __name__ == "__main__":
    main()
