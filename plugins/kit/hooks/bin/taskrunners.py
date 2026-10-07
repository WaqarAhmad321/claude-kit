"""What `make`, `just`, npm/pnpm/yarn/bun scripts and python/node/ruby/perl would run, so guard_commit can check it.

guard_commit imports this only for commands that start with one of these tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import shellwords
from shellwords import Word

READ_LIMIT = 1_000_000
GIT_COMMIT = re.compile(r"\bgit\b[^\n]*\bcommit\b")

_MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
_JUSTFILES = ("justfile", "Justfile", ".justfile")
_MAKE_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "--directory", "-f", "--file", "--makefile", "-I", "--include-dir", "-o", "--old-file", "-W"}
    | {"--what-if", "--new-file", "--assume-new", "--eval", "-l", "--load-average"}
)
_JUST_OPTIONS_WITH_VALUE = frozenset(
    {"-f", "--justfile", "-d", "--working-directory", "--color", "--shell", "--shell-arg", "--dotenv-filename"}
    | {"--dotenv-path", "--list-heading", "--list-prefix", "--chooser", "--command-color"}
)
_PACKAGE_MANAGER_OPTIONS_WITH_VALUE = frozenset(
    {"--prefix", "-C", "--dir", "--cwd", "-w", "--workspace", "--filter", "-F"}
)
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_NPM_RUN = frozenset({"run", "run-script", "rum", "urn"})
_NPM_LIFECYCLE = {"test": "test", "t": "test", "tst": "test", "start": "start", "stop": "stop", "restart": "restart"}
_PYTHON = re.compile(r"python[\d.]*")
_CODE_OPTIONS = {
    "python": frozenset({"-c"}),
    "node": frozenset({"-e", "--eval", "-p", "--print"}),
    "ruby": frozenset({"-e"}),
    "perl": frozenset({"-e", "-E"}),
}
_INTERPRETER_OPTIONS_WITH_VALUE = frozenset({"-W", "-X", "-r", "--require", "--import", "--loader", "-I", "-M"})
_MAKE_RULE = re.compile(r"^(?P<targets>[^\s:#=][^:=#]*?)\s*::?(?!=)(?P<prerequisites>[^=]*)$")
_RECIPE_PREFIX = re.compile(r"^[@+\-\s]+")
_JUST_RECIPE = re.compile(r"^@?(?P<name>[A-Za-z_][\w-]*)(?P<parameters>[^:=]*?):(?!=)(?P<dependencies>.*)$")
_JUST_NAME = re.compile(r"[A-Za-z_][\w-]*")


@dataclass(frozen=True)
class Recipe:
    label: str
    directory: Path
    text: str | None  # None when kit can't read or resolve it.
    shell: bool = True  # False for code in another language: searched for a commit, not parsed.
    mentions_commit: bool = False  # When text is None: what kit could read still mentions git commit.


def recipe_for(words: list[Word], directory: Path) -> Recipe | None:
    name = Path(words[0].text).name
    if name in ("make", "gmake"):
        return _make(words[1:], directory)
    if name == "just":
        return _just(words[1:], directory)
    if name in _PACKAGE_MANAGERS:
        return _package_script(name, words[1:], directory)
    language = "python" if _PYTHON.fullmatch(name) else name
    return _interpreted(name, _CODE_OPTIONS[language], words[1:], directory)


def read_text(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            return handle.read(READ_LIMIT).decode("utf-8", errors="replace")
    except OSError:
        return None


def _options_and_operands(args: list[Word], with_value: frozenset[str]) -> tuple[dict[str, str], list[Word]]:
    values, operands, rest = {}, [], iter(args)
    for word in rest:
        text = word.text
        if text == "--":
            operands.extend(rest)
        elif not text.startswith("-") or text == "-":
            operands.append(word)
        elif (name := text.partition("=")[0]) in with_value:
            following = next(rest, None) if "=" not in text else None
            values[name] = text.partition("=")[2] if "=" in text else (following.text if following else "")
        elif not text.startswith("--") and text[:2] in with_value:
            values[text[:2]] = text[2:]
    return values, operands


def _make(args: list[Word], directory: Path) -> Recipe | None:
    values, operands = _options_and_operands(args, _MAKE_OPTIONS_WITH_VALUE)
    run_directory = directory / (values.get("-C") or values.get("--directory") or ".")
    named = values.get("-f") or values.get("--file") or values.get("--makefile")
    candidates = [run_directory / named] if named else [run_directory / name for name in _MAKEFILES]
    makefile = next((path for path in candidates if path.is_file()), None)
    if makefile is None:
        return None
    targets = [word.text for word in operands if "=" not in word.text]
    label = " ".join(["make", *targets])
    text = read_text(makefile)
    if text is None:
        return Recipe(label, run_directory, None, mentions_commit=True)
    rules = _make_rules(text)
    targets = targets or [next((target for target in rules if not target.startswith(".")), "")]
    recipe = _dependency_recipes(rules, targets)
    if recipe is None:
        return Recipe(label, run_directory, None, mentions_commit=bool(GIT_COMMIT.search(text)))
    return Recipe(label, run_directory, recipe)


def _make_rules(text: str) -> dict[str, tuple[list[str], list[str]]]:
    """Target -> (prerequisites, recipe lines)."""
    rules: dict[str, tuple[list[str], list[str]]] = {}
    recipe: list[str] | None = None
    for line in text.replace("\\\n", " ").splitlines():
        if line.startswith("\t"):
            if recipe is not None:
                recipe.append(_RECIPE_PREFIX.sub("", line))
            continue
        recipe = None
        rule = _MAKE_RULE.match(line)
        if rule:
            prerequisites, _, inline = rule.group("prerequisites").partition(";")
            recipe = [inline.strip()] if inline.strip() else []
            for target in rule.group("targets").split():
                rules.setdefault(target, (prerequisites.replace("|", " ").split(), recipe))
    return rules


def _dependency_recipes(rules: dict[str, tuple[list[str], list[str]]], targets: list[str]) -> str | None:
    """The recipes the targets run, prerequisites first; None when a requested target has no rule."""
    if any(target not in rules for target in targets):
        return None
    lines, seen, pending = [], set(), list(reversed(targets))
    while pending:
        target = pending.pop()
        if target in seen or target not in rules:
            continue
        seen.add(target)
        prerequisites, recipe = rules[target]
        lines += recipe
        pending += reversed(prerequisites)
    return "\n".join(lines)


def _just(args: list[Word], directory: Path) -> Recipe | None:
    values, operands = _options_and_operands(args, _JUST_OPTIONS_WITH_VALUE)
    named = values.get("-f") or values.get("--justfile")
    justfile = directory / named if named else _find_upward(directory, _JUSTFILES)
    if justfile is None or not justfile.is_file():
        return None
    run_directory = directory / (values.get("-d") or values.get("--working-directory") or justfile.parent)
    text = read_text(justfile)
    recipes = _just_recipes(text) if text is not None else {}
    name = operands[0].text if operands else next(iter(recipes), "")
    label = f"just {name}".strip()
    if text is None or name not in recipes:
        return Recipe(label, run_directory, None, mentions_commit=bool(text and GIT_COMMIT.search(text)))
    return Recipe(label, run_directory, _dependency_recipes(recipes, [name]))


def _just_recipes(text: str) -> dict[str, tuple[list[str], list[str]]]:
    """Recipe -> (dependencies, body lines)."""
    recipes: dict[str, tuple[list[str], list[str]]] = {}
    body: list[str] | None = None
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):
            if body is not None:
                body.append(_RECIPE_PREFIX.sub("", line))
            continue
        if not line.strip():
            continue
        body = None
        recipe = _JUST_RECIPE.match(line)
        if recipe:
            body = []
            recipes.setdefault(recipe.group("name"), (_JUST_NAME.findall(recipe.group("dependencies")), body))
    return recipes


def _package_script(tool: str, args: list[Word], directory: Path) -> Recipe | None:
    values, operands = _options_and_operands(args, _PACKAGE_MANAGER_OPTIONS_WITH_VALUE)
    name = _script_name(tool, [word.text for word in operands])
    if not name:
        return None
    start = directory / (
        values.get("--prefix") or values.get("-C") or values.get("--dir") or values.get("--cwd") or "."
    )
    package = _find_upward(start, ("package.json",))
    if package is None:
        return None
    label = f"{tool} run {name}"
    text = read_text(package)
    try:
        scripts = json.loads(text).get("scripts") if text else None
    except (ValueError, AttributeError):
        scripts = None
    if not isinstance(scripts, dict):
        return Recipe(label, package.parent, None, mentions_commit=bool(text and GIT_COMMIT.search(text)))
    if name not in scripts:
        return None
    # npm runs pre<name> and post<name> around a script.
    parts = [scripts.get(f"pre{name}"), scripts[name], scripts.get(f"post{name}")]
    return Recipe(label, package.parent, "\n".join(str(part) for part in parts if part))


def _script_name(tool: str, operands: list[str]) -> str | None:
    if not operands:
        return None
    if tool == "npm":
        if operands[0] in _NPM_RUN:
            return operands[1] if len(operands) > 1 else None
        return _NPM_LIFECYCLE.get(operands[0])
    return operands[1] if operands[0] == "run" and len(operands) > 1 else operands[0]


def _interpreted(name: str, code_options: frozenset[str], args: list[Word], directory: Path) -> Recipe | None:
    rest = iter(args)
    for word in rest:
        option = word.text
        if option in code_options:
            code = next(rest, None)
            return Recipe(f"{name} {option} '...'", directory, code.text if code else "", shell=False)
        if option == "-m":
            return None
        if option.startswith("-"):
            if option in _INTERPRETER_OPTIONS_WITH_VALUE:
                next(rest, None)
            continue
        path = shellwords.resolve_path(directory, word)
        return Recipe(path.name, directory, read_text(path) if word.literal else None, shell=False)
    return None


def _find_upward(start: Path, names: tuple[str, ...]) -> Path | None:
    for directory in (start, *start.parents):
        for name in names:
            if (directory / name).is_file():
                return directory / name
    return None
