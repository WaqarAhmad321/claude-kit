#!/usr/bin/env python3
"""Check this repo's docs against the kit's docs standard. Stdlib only.

    python3 scripts/docs-check.py [--root DIR]

It needs docnames.py, the naming rules it shares with the kit's docs location guard, in the same directory.
It checks that the required docs exist, CLAUDE.md stays short, every Markdown file sits where the docs layout
puts it under a name that isn't a scratch name, docs/README.md links every doc, ADRs have their front matter and
headings, a module's ARCHITECTURE.md (when one exists) stays short and keeps its headings, no doc outside the
ADRs and the CHANGELOG carries dates or history words, no template marker is left, and every environment variable
the code reads is listed in .env.example. Extra Markdown locations come from the `docs.allowed` globs in
.claude/kit.json; `docs.ignore` there drops matching paths from every check.

Exit codes: 0 when clean, 1 when it found problems, 2 when the root isn't a directory.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import docnames

PROG = "docs-check"
EXIT_PROBLEMS = 1
EXIT_USAGE = 2
CLAUDE_MD_MAX_LINES = 150
REQUIRED_DOCS = ("README.md", "CLAUDE.md", "docs/README.md", "docs/runbooks/deploy.md")
ROOT_DOCS = frozenset(
    {"README.md", "CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE.md"}
)
FREE_TOP_DIRS = frozenset({".claude", ".github"})
SKIPPED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".next",
        "fixtures",
        "__fixtures__",
        "testdata",
    }
)
DOCS_INDEX = "docs/README.md"
HANDOFF_TEMPLATE = "docs/api/handoffs/_template.md"
DATED_DOCS = frozenset({"CHANGELOG.md"})
GENERATED_NAMES = frozenset({"CONTRACTS.md", "SCHEMA.md"})
KIT_CONFIG = ".claude/kit.json"
ENV_EXAMPLE = ".env.example"
SELF = "scripts/docs-check.py"

ADR_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ADR_FIELDS = ("status", "date", "decision-makers")
ADR_HEADINGS = ("## Context and Problem Statement", "## Considered Options", "## Decision Outcome")

ARCHITECTURE_NAME = "ARCHITECTURE.md"
ARCHITECTURE_DIR = ".claude/skills"
ARCHITECTURE_MAX_LINES = 120
ARCHITECTURE_HEADINGS = (
    "## What it does",
    "## Who uses it",
    "## How it works",
    "## Data",
    "## Connections",
    "## Key decisions",
    "## Rules and limits",
    "## How it's tested",
    "## Where the code lives",
)

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_IN_TEXT = re.compile(
    rf"\b(?:(?:19|20)\d{{2}}-\d{{2}}-\d{{2}}|(?:{MONTHS}) \d{{1,2}}, \d{{4}}|\d{{1,2}} (?:{MONTHS}) \d{{4}}"
    rf"|(?:{MONTHS}) \d{{4}})\b"
)
HISTORY_WORDS = re.compile(
    r"\b(?:previously|as of|used to be|(?:we|it|this|that|they|which|who) used to)\b", re.IGNORECASE
)
FILL_MARKER = "<!-- fill:"
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`]*`")
INLINE_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", re.MULTILINE)

CODE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})
ENV_NAME = r"([A-Z][A-Z0-9_]*)"
ENV_READS = (
    re.compile(rf"""\bos\.environ\s*\[\s*["']{ENV_NAME}["']"""),
    re.compile(rf"""\b(?:os\.)?environ\.get\(\s*["']{ENV_NAME}["']"""),
    re.compile(rf"""\bos\.getenv\(\s*["']{ENV_NAME}["']"""),
    re.compile(rf"""\benv(?:\.[a-z_]+)?\(\s*["']{ENV_NAME}["']"""),
    re.compile(rf"""\bprocess\.env\.{ENV_NAME}\b"""),
    re.compile(rf"""\bprocess\.env\[\s*["']{ENV_NAME}["']\s*\]"""),
)
VITE_ENV_READ = re.compile(rf"\bimport\.meta\.env\.{ENV_NAME}\b")
RUNTIME_ENV = frozenset(
    {
        "NODE_ENV",
        "CI",
        "PATH",
        "HOME",
        "PWD",
        "TMPDIR",
        "NEXT_RUNTIME",
        # Claude Code, the kit and mise inject these into the process; no project's .env.example lists them.
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PROJECT_DIR",
        "KIT_BYPASS",
        "KIT_DIRECT_MODE",
        "MISE_DATA_DIR",
    }
)
VITE_BUILTIN_ENV = frozenset({"MODE", "DEV", "PROD", "SSR", "BASE_URL"})
ENV_EXAMPLE_KEY = re.compile(r"^\s*#?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


@dataclass(frozen=True, order=True)
class Problem:
    path: str
    message: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=PROG, description="Check the docs against the kit's docs standard.")
    parser.add_argument("--root", default=".", help="the repo root (default: the git root of the current directory)")
    args = parser.parse_args(argv)
    start = Path(args.root).resolve()
    if not start.is_dir():
        print(f"{PROG}: --root {args.root} is not a directory.", file=sys.stderr)
        return EXIT_USAGE
    root = _repo_root(start)
    files = _project_files(root)
    problems = sorted(set(check(root, files)))
    for problem in problems:
        print(f"{problem.path}: {problem.message}")
    docs = len(_markdown(files))
    if problems:
        print(f"{PROG}: {len(problems)} problem(s) in {docs} Markdown files.")
        return EXIT_PROBLEMS
    print(f"{PROG}: ok, {docs} Markdown files checked.")
    return 0


def check(root: Path, files: list[str]) -> list[Problem]:
    ignored, ignore_problems = _docs_globs(root, "ignore")
    files = [path for path in files if not any(pattern.match(path) for pattern in ignored)]
    markdown = _markdown(files)
    allowed, allowed_problems = _docs_globs(root, "allowed")
    problems = [*ignore_problems, *allowed_problems, *_required(files), *_claude_md_length(root, files)]
    for path in markdown:
        text = _read(root / path)
        problems += _location(path, allowed)
        problems += _name(path)
        problems += _text(path, text)
        if _is_adr(path):
            problems += _adr(path, text)
        if _is_architecture(path):
            problems += _architecture(path, text)
    problems += _index(root, markdown)
    problems += _env_example(root, files)
    return problems


def _repo_root(start: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return start
    return Path(result.stdout.strip())


def _project_files(root: Path) -> list[str]:
    """Tracked and untracked files, minus ignored ones; every file under root outside a git repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not SKIPPED_DIRS.intersection(path.relative_to(root).parts[:-1])
        )
    names = (name for name in result.stdout.decode("utf-8", "replace").split("\0") if name)
    return sorted({name for name in names if (root / name).is_file()})


def _markdown(files: list[str]) -> list[str]:
    return [path for path in files if path.endswith(".md") and not _skipped(path)]


def _skipped(path: str) -> bool:
    return bool(SKIPPED_DIRS.intersection(PurePosixPath(path).parts[:-1]))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _docs_globs(root: Path, key: str) -> tuple[list[re.Pattern[str]], list[Problem]]:
    """The compiled globs under `docs.<key>` in .claude/kit.json: `allowed` exempts a path from the location
    check, `ignore` drops it from every check, including the env var scan."""
    config_path = root / KIT_CONFIG
    if not config_path.is_file():
        return [], []
    try:
        config = json.loads(_read(config_path))
    except json.JSONDecodeError as error:
        return [], [Problem(KIT_CONFIG, f"isn't valid JSON ({error.msg}, line {error.lineno}); fix it.")]
    if not isinstance(config, dict):
        return [], [Problem(KIT_CONFIG, "must hold a JSON object; fix it.")]
    docs = config.get("docs", {})
    if not isinstance(docs, dict):
        return [], [Problem(KIT_CONFIG, "docs must be an object; fix it.")]
    globs = docs.get(key, [])
    if not isinstance(globs, list) or not all(isinstance(glob, str) for glob in globs):
        return [], [Problem(KIT_CONFIG, f"docs.{key} must be a list of glob strings; fix it.")]
    return [glob_to_regex(glob) for glob in globs], []


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """Gitignore-style: `**` spans directories, `*` stays in one segment, a glob without `/` matches at any depth."""
    pattern = glob.strip().lstrip("/") if "/" in glob.strip().rstrip("/") else "**/" + glob.strip()
    out, index = [], 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        elif pattern[index] == "{" and "}" in pattern[index:]:
            end = pattern.index("}", index)
            out.append("(?:" + "|".join(re.escape(part) for part in pattern[index + 1 : end].split(",")) + ")")
            index = end + 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + "(?:/.*)?$")


def _required(files: list[str]) -> list[Problem]:
    present = set(files)
    problems = [
        Problem(path, "is missing. Every repo has it; the kit's templates/common has a starting point.")
        for path in REQUIRED_DOCS
        if path not in present
    ]
    if not any(_is_adr(path) for path in files):
        problems.append(Problem(docnames.DECISIONS_DIR, "has no ADR. Record the first decision as 0001-<title>.md."))
    return problems


def _claude_md_length(root: Path, files: list[str]) -> list[Problem]:
    if "CLAUDE.md" not in files:
        return []
    lines = len(_read(root / "CLAUDE.md").splitlines())
    if lines <= CLAUDE_MD_MAX_LINES:
        return []
    return [
        Problem(
            "CLAUDE.md",
            f"has {lines} lines; keep it to {CLAUDE_MD_MAX_LINES}. Move module detail into module skills "
            "and the rest into docs/.",
        )
    ]


def _location(path: str, allowed: list[re.Pattern[str]]) -> list[Problem]:
    pure = PurePosixPath(path)
    if (
        path in ROOT_DOCS
        or pure.name == "README.md"
        or pure.parts[0] in FREE_TOP_DIRS
        or _in_docs_layout(pure)
        or any(pattern.match(path) for pattern in allowed)
    ):
        return []
    return [
        Problem(
            path,
            "isn't in the docs layout. Move it to docs/<architecture|api|runbooks|guides|partners|decisions>/, "
            "put its content in a commit message, PR or PM card, or add a glob to docs.allowed in .claude/kit.json.",
        )
    ]


def _in_docs_layout(pure: PurePosixPath) -> bool:
    return pure.parent.as_posix() in docnames.DOCS_DIR_RULES or pure.as_posix() == DOCS_INDEX


def _is_adr(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.parent.as_posix() == docnames.DECISIONS_DIR and pure.suffix == ".md" and pure.name != "README.md"


def _name(path: str) -> list[Problem]:
    pure = PurePosixPath(path)
    exempt = pure.name in ROOT_DOCS or pure.name in GENERATED_NAMES or pure.name == "README.md"
    if exempt or pure.parts[0] in FREE_TOP_DIRS or path == HANDOFF_TEMPLATE:
        return []
    marks = docnames.scratch_marks(path)
    problems = []
    if marks:
        reason = f"the word {', '.join(marks.words)}" if marks.words else "a date"
        problems.append(
            Problem(
                path,
                f"has {reason} in its name, which marks a scratch, dated or versioned file. Name it by its "
                "subject and edit it in place; summaries, plans and status go in the commit, the PR or a PM card.",
            )
        )
    rule = docnames.docs_dir_rule(path)
    if rule is None or rule.fullmatch(pure.name):
        return problems
    if _is_adr(path):
        problems.append(Problem(path, "must be named NNNN-kebab-title.md, such as 0003-use-postgis.md."))
    else:
        problems.append(Problem(path, "must have a kebab-case name, such as calling-hub-operations.md."))
    return problems


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code and front matter, with inline code removed."""
    lines = text.splitlines()
    start = _front_matter_end(lines)
    prose, fenced = [], False
    for number, line in enumerate(lines[start:], start=start + 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            prose.append((number, INLINE_CODE.sub("", line)))
    return prose


def _front_matter_end(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index + 1
    return 0


def _text(path: str, text: str) -> list[Problem]:
    problems = []
    if FILL_MARKER in text and path != HANDOFF_TEMPLATE:
        problems.append(Problem(path, f"still has a {FILL_MARKER} template marker. Fill it in or delete it."))
    if _is_adr(path) or path in DATED_DOCS:
        return problems
    for number, line in _prose_lines(text):
        found = DATE_IN_TEXT.search(line) or HISTORY_WORDS.search(line)
        if found:
            problems.append(
                Problem(
                    f"{path}:{number}",
                    f'says "{found.group(0)}". Docs state current facts in the present tense; dates and history '
                    "belong in ADRs, the CHANGELOG and git.",
                )
            )
    return problems


def _adr(path: str, text: str) -> list[Problem]:
    lines = text.splitlines()
    end = _front_matter_end(lines)
    if end == 0:
        return [Problem(path, "needs MADR front matter: status, date (YYYY-MM-DD) and decision-makers.")]
    pairs = (line.partition(":") for line in lines[1 : end - 1])
    fields = {key.strip().lower(): value.strip() for key, _, value in pairs if value.strip()}
    problems = [Problem(path, f"front matter has no {field}.") for field in ADR_FIELDS if field not in fields]
    if "date" in fields and not ADR_DATE.match(fields["date"]):
        problems.append(
            Problem(path, f"front matter date {fields['date']!r} must be the day it was decided, as YYYY-MM-DD.")
        )
    body = [line.rstrip() for line in lines[end:]]
    if not any(line.startswith("# ") for line in body):
        problems.append(Problem(path, "needs a '# <decision title>' heading."))
    positions = [body.index(heading) if heading in body else -1 for heading in ADR_HEADINGS]
    for heading, position in zip(ADR_HEADINGS, positions, strict=True):
        if position < 0:
            problems.append(Problem(path, f"needs the MADR heading '{heading}'."))
    found = [position for position in positions if position >= 0]
    if found != sorted(found):
        problems.append(Problem(path, f"must keep the MADR headings in order: {', '.join(ADR_HEADINGS)}."))
    return problems


def _is_architecture(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.name == ARCHITECTURE_NAME and pure.parent.parent.as_posix() == ARCHITECTURE_DIR


def _architecture(path: str, text: str) -> list[Problem]:
    lines = text.splitlines()
    problems = []
    if len(lines) > ARCHITECTURE_MAX_LINES:
        problems.append(
            Problem(
                path,
                f"has {len(lines)} lines; keep it to {ARCHITECTURE_MAX_LINES}. It's for a human reader; move "
                "detail into SKILL.md or the module's other files.",
            )
        )
    body = [line.rstrip() for line in lines]
    missing = [heading for heading in ARCHITECTURE_HEADINGS if heading not in body]
    if missing:
        plural = "s" if len(missing) != 1 else ""
        problems.append(Problem(path, f"is missing the heading{plural}: {', '.join(missing)}."))
    return problems


def _index(root: Path, markdown: list[str]) -> list[Problem]:
    if DOCS_INDEX not in markdown:
        return []
    index_dir = PurePosixPath(DOCS_INDEX).parent
    text = _read(root / DOCS_INDEX)
    linked = set()
    for target in [*INLINE_LINK.findall(text), *REFERENCE_LINK.findall(text)]:
        if "://" in target or target.startswith(("#", "mailto:")):
            continue
        linked.add(_normalise(index_dir / target.split("#", 1)[0]))
    return [
        Problem(path, f"isn't linked from {DOCS_INDEX}. Add it under the readers it serves.")
        for path in markdown
        if path.startswith("docs/") and path != DOCS_INDEX and path not in linked
    ]


def _normalise(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in (".", ""):
            parts.append(part)
    return "/".join(parts)


def _env_example(root: Path, files: list[str]) -> list[Problem]:
    used = _env_reads(root, files)
    if not used:
        return []
    listed = set(ENV_EXAMPLE_KEY.findall(_read(root / ENV_EXAMPLE))) if ENV_EXAMPLE in files else set()
    return [
        Problem(
            ENV_EXAMPLE,
            f"doesn't list {name}, which {where} reads. Add `{name}=` with a safe local value, or empty for a secret.",
        )
        for name, where in sorted(used.items())
        if name not in listed
    ]


def _env_reads(root: Path, files: list[str]) -> dict[str, str]:
    """Each environment variable the code reads, with the first file that reads it."""
    used: dict[str, str] = {}
    for path in files:
        if PurePosixPath(path).suffix not in CODE_SUFFIXES or _skipped(path) or path == SELF:
            continue
        text = _read(root / path)
        names = [name for pattern in ENV_READS for name in pattern.findall(text) if name not in RUNTIME_ENV]
        names += [name for name in VITE_ENV_READ.findall(text) if name not in VITE_BUILTIN_ENV]
        for name in names:
            used.setdefault(name, path)
    return used


if __name__ == "__main__":
    sys.exit(main())
