#!/usr/bin/env python3
"""Lay the kit's common templates and one stack pack into a project, never overwriting a file.

Files from templates/common/ and then templates/<stack>/ go to the same path in the destination; a stack file wins
over a common file at the same path. The packs' kit.json files are merged into .claude/kit.json. Each target is
classed `new` (written), `identical` (left alone) or `differs` (left alone and listed, so the caller can show the
difference and ask). A pack's own README.md is never copied. CLAUDE.md.template and README.md.template are listed
under "fill" for the caller to write from the project's answers. The packs' gitignore.additions lines go into
.gitignore: a new file gets them all, and an existing one is classed `append` and gets only the lines it lacks.
--only limits the run to matching paths, so a caller can apply one approved group at a time.

    python3 apply_template.py --list-stacks
    python3 apply_template.py --stack mern --dest ~/Work/new-app
    python3 apply_template.py --stack django --dest . --dry-run --json
    python3 apply_template.py --stack django --dest . --only .gitignore --only 'docs/*'
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import kit_root

PROG = "apply_template"
EXIT_INPUT_ERROR = 2
COMMON_PACK = "common"
# Per-agent instruction adapters, copied by hand as their READMEs describe; never laid down as a stack.
AGENT_ADAPTERS = "agents"
NOT_STACKS = frozenset({COMMON_PACK, AGENT_ADAPTERS})
PACK_README = "README.md"
PACK_KIT_JSON = "kit.json"
KIT_JSON_TARGET = ".claude/kit.json"
FILL_TEMPLATES = {"CLAUDE.md.template": "CLAUDE.md", "README.md.template": "README.md"}
GITIGNORE_ADDITIONS = "gitignore.additions"
GITIGNORE_TARGET = ".gitignore"
TOOL_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"})

HELP_EPILOG = """\
Templates: --templates, else templates/ in the kit repo: the kit checkout this script sits in (a plugin from a
local-directory marketplace loads in place), else the repo ~/.claude/rules links into, else the claude-kit
marketplace's installLocation in known_marketplaces.json. A plugin Claude Code copied into plugins/cache/ holds only
plugins/kit, so from there the script's own folder has no templates.

Exit codes: 0 on success, including when files differ; 2 for an unknown stack or unusable templates.
"""


class TemplateError(Exception):
    """The templates or the arguments can't be used; the message says what to do."""


class FileClass(StrEnum):
    NEW = "new"
    IDENTICAL = "identical"
    DIFFERS = "differs"
    APPEND = "append"


@dataclass(frozen=True)
class Planned:
    path: str
    sources: tuple[Path, ...]
    content: bytes
    mode_source: Path | None
    lines: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Classified:
    planned: Planned
    file_class: FileClass
    missing_lines: tuple[str, ...] = ()


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.list_stacks and (args.stack is None or args.dest is None):
        parser.error("--stack and --dest are required unless --list-stacks is given")
    try:
        templates = _templates_dir(args.templates)
        stacks = list_stacks(templates)
        if args.list_stacks:
            _print_stacks(templates, stacks, as_json=args.json)
            return 0
        dest = Path(args.dest).expanduser().resolve()
        results, fill = apply(templates, args.stack, dest, dry_run=args.dry_run, only=args.only)
    except TemplateError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    _print_results(templates, args.stack, dest, results, fill, dry_run=args.dry_run, as_json=args.json)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Lay the kit's templates into a project without overwriting anything.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stack", help="the stack pack to apply, such as django or mern")
    parser.add_argument("--dest", help="the project root to lay the files into; created when missing")
    parser.add_argument("--templates", help="the templates directory (default: the kit repo's templates/, see below)")
    parser.add_argument("--dry-run", action="store_true", help="classify every file but write nothing")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--list-stacks", action="store_true", help="print the templates directory and its stacks")
    parser.add_argument(
        "--only",
        action="append",
        metavar="GLOB",
        help="handle only the target paths matching this fnmatch glob (repeatable; * also matches /)",
    )
    return parser


def _templates_dir(requested: str | None) -> Path:
    if requested:
        path = Path(requested).expanduser().resolve()
        if not path.is_dir():
            raise TemplateError(f"--templates {requested} is not a directory.")
        return path
    try:
        templates = kit_root.find_kit_root(Path(__file__)) / "templates"
    except kit_root.KitRootError as error:
        raise TemplateError(f"{error}, or pass --templates <kit repo>/templates.") from error
    if not templates.is_dir():
        raise TemplateError(f"{templates} is not a directory. Pass --templates <kit repo>/templates.")
    return templates


def list_stacks(templates: Path) -> list[str]:
    return sorted(
        entry.name
        for entry in templates.iterdir()
        if entry.is_dir() and entry.name not in NOT_STACKS and not entry.name.startswith((".", "_"))
    )


def apply(
    templates: Path, stack: str, dest: Path, dry_run: bool, only: list[str] | None = None
) -> tuple[list[Classified], dict[str, list[Path]]]:
    stacks = list_stacks(templates)
    if stack not in stacks:
        raise TemplateError(f"unknown stack {stack!r}. Stacks: {', '.join(stacks) or 'none'}.")
    if dest.exists() and not dest.is_dir():
        raise TemplateError(f"--dest {dest} is a file, not a directory.")
    packs = [pack for pack in (templates / COMMON_PACK, templates / stack) if pack.is_dir()]
    planned = [item for item in _plan(packs, stack) if _selected(item.path, only)]
    results = [_classify(dest, item) for item in planned]
    if not dry_run:
        for result in results:
            if result.file_class is FileClass.NEW:
                _write(dest, result.planned)
            elif result.file_class is FileClass.APPEND:
                _append_lines(dest / result.planned.path, result.missing_lines)
    fill = {target: sources for target, sources in _fill_sources(packs).items() if _selected(target, only)}
    return results, fill


def _selected(path: str, only: list[str] | None) -> bool:
    return not only or any(fnmatch.fnmatchcase(path, glob) for glob in only)


def _plan(packs: list[Path], stack: str) -> list[Planned]:
    by_path: dict[str, Planned] = {}
    for pack in packs:
        for source in _pack_files(pack):
            rel = source.relative_to(pack).as_posix()
            by_path[rel] = Planned(rel, (source,), source.read_bytes(), source)
    by_path[KIT_JSON_TARGET] = _kit_json(packs, stack)
    gitignore = _gitignore(packs)
    if gitignore is not None:
        by_path[GITIGNORE_TARGET] = gitignore
    return [by_path[rel] for rel in sorted(by_path)]


def _pack_files(pack: Path) -> list[Path]:
    special = {PACK_README, PACK_KIT_JSON, GITIGNORE_ADDITIONS, *FILL_TEMPLATES}
    return [
        path
        for path in sorted(pack.rglob("*"))
        if path.is_file()
        and not TOOL_CACHE_DIRS.intersection(path.relative_to(pack).parts[:-1])
        and path.relative_to(pack).as_posix() not in special
    ]


def _kit_json(packs: list[Path], stack: str) -> Planned:
    sources = tuple(pack / PACK_KIT_JSON for pack in packs if (pack / PACK_KIT_JSON).is_file())
    merged: dict = {}
    for source in sources:
        merged = deep_merge(merged, _read_json_object(source))
    config = {"stack": stack, **{key: value for key, value in merged.items() if key != "stack"}}
    content = (json.dumps(config, indent=2) + "\n").encode()
    return Planned(KIT_JSON_TARGET, sources, content, None)


def _gitignore(packs: list[Path]) -> Planned | None:
    """The .gitignore lines of every pack, in pack order without repeats; None when no pack has additions."""
    if not any((pack / GITIGNORE_ADDITIONS).is_file() for pack in packs):
        return None
    sources = tuple(
        path for pack in packs for path in (pack / GITIGNORE_TARGET, pack / GITIGNORE_ADDITIONS) if path.is_file()
    )
    lines = tuple(dict.fromkeys(line for source in sources for line in _ignore_lines(source)))
    content = "".join(f"{line}\n" for line in lines).encode()
    return Planned(GITIGNORE_TARGET, sources, content, None, lines)


def _ignore_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TemplateError(f"{path} isn't valid JSON ({error}); fix the template.") from error
    if not isinstance(value, dict):
        raise TemplateError(f"{path} must hold a JSON object; fix the template.")
    return value


def deep_merge(base: dict, override: dict) -> dict:
    """Objects merge key by key; any other value in `override`, lists included, replaces the one in `base`."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _classify(dest: Path, planned: Planned) -> Classified:
    target = dest / planned.path
    if target.is_file() and planned.lines is not None and not _blocked(dest, target):
        present = set(_ignore_lines(target))
        missing = tuple(line for line in planned.lines if line not in present)
        return Classified(planned, FileClass.APPEND if missing else FileClass.IDENTICAL, missing)
    if target.is_file():
        same = target.read_bytes() == planned.content
        return Classified(planned, FileClass.IDENTICAL if same else FileClass.DIFFERS)
    if target.exists() or target.is_symlink() or _blocked(dest, target):
        return Classified(planned, FileClass.DIFFERS)
    return Classified(planned, FileClass.NEW)


def _blocked(dest: Path, target: Path) -> bool:
    """A file sits where a parent directory has to go, or a symlinked parent leads outside the destination."""
    between = [parent for parent in target.parents if parent.is_relative_to(dest) and parent != dest]
    if any(parent.exists() and not parent.is_dir() for parent in between):
        return True
    return not target.resolve().is_relative_to(dest.resolve())


def _write(dest: Path, planned: Planned) -> None:
    target = dest / planned.path
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(planned.content)
    if planned.mode_source is not None:
        shutil.copymode(planned.mode_source, target)


def _append_lines(target: Path, lines: tuple[str, ...]) -> None:
    existing = target.read_bytes()
    separator = b"" if not existing or existing.endswith(b"\n") else b"\n"
    with target.open("ab") as handle:
        handle.write(separator + "".join(f"{line}\n" for line in lines).encode())


def _fill_sources(packs: list[Path]) -> dict[str, list[Path]]:
    fill: dict[str, list[Path]] = {}
    for template, target in FILL_TEMPLATES.items():
        sources = [pack / template for pack in packs if (pack / template).is_file()]
        if sources:
            fill[target] = sources
    return fill


def _print_stacks(templates: Path, stacks: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps({"templates": str(templates), "stacks": stacks}, indent=2))
        return
    print(f"Templates: {templates}")
    print(f"Stacks: {', '.join(stacks) or 'none'}")


def _print_results(
    templates: Path,
    stack: str,
    dest: Path,
    results: list[Classified],
    fill: dict[str, list[Path]],
    dry_run: bool,
    as_json: bool,
) -> None:
    if as_json:
        report = {
            "templates": str(templates),
            "stack": stack,
            "dest": str(dest),
            "dry_run": dry_run,
            "files": [
                {
                    "path": result.planned.path,
                    "class": result.file_class.value,
                    "sources": [str(source) for source in result.planned.sources],
                    **({"append": list(result.missing_lines)} if result.missing_lines else {}),
                }
                for result in results
            ],
            "fill": {target: [str(source) for source in sources] for target, sources in fill.items()},
        }
        print(json.dumps(report, indent=2))
        return
    print(f"Templates: {templates} (stack {stack})")
    print(f"Destination: {dest}")
    for result in results:
        extra = f" (+{len(result.missing_lines)} lines)" if result.missing_lines else ""
        print(f"{result.file_class.value:<10} {result.planned.path}{extra}")
    for target, sources in fill.items():
        shown = ", ".join(source.relative_to(templates).as_posix() for source in sources)
        print(f"{'fill':<10} {target} from {shown}")
    counts = {file_class: sum(r.file_class is file_class for r in results) for file_class in FileClass}
    new = counts[FileClass.NEW]
    summary = f"{counts[FileClass.IDENTICAL]} identical, {counts[FileClass.DIFFERS]} differ"
    if counts[FileClass.APPEND]:
        summary += f", {counts[FileClass.APPEND]} to append to"
    if dry_run:
        print(f"Dry run, nothing written: {new} new, {summary}.")
    else:
        print(f"Wrote {new} new files; {summary}.")
    if counts[FileClass.DIFFERS]:
        print("Files that differ were left as they are: show each difference and ask before changing it.")


if __name__ == "__main__":
    sys.exit(main())
