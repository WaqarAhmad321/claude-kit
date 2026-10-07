"""Write a project's AGENTS.md from the kit's rules, so Codex, Cursor, Gemini CLI, OpenCode and Copilot follow them too.

Usage: python3 plugins/kit/scripts/build_agents_md.py [--root <project>] [--stack <name> ...] [--check]

The rules block sits between the claude-kit begin and end markers. Everything outside the markers is the
project's own section and is kept as it is. Without --stack, a rerun keeps the stacks recorded in the begin
marker. --check writes nothing and exits 1 when AGENTS.md is missing or differs from what the rules give.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import kit_root

AGENTS_FILE = "AGENTS.md"
UNIVERSAL_RULES = ("code-style", "verification", "workflow", "git-commits", "docs")
STACK_RULES = ("django", "api-design", "node-express", "mongodb", "react", "nextjs")
# Codex stops reading project docs after project_doc_max_bytes, 32 KiB by default.
CODEX_DEFAULT_MAX_BYTES = 32 * 1024
# A safety margin under Codex's default, so the generated block alone never risks truncation even before the
# project's own "## This project" section is added; _warn_if_codex_truncates catches that section pushing the
# whole file over the real limit.
MAX_BLOCK_BYTES = CODEX_DEFAULT_MAX_BYTES - 4 * 1024

BEGIN_PREFIX = "<!-- claude-kit:begin"
END_MARKER = "<!-- claude-kit:end -->"
_STACK_IN_MARKER = re.compile(r"--stack ([a-z0-9-]+)")
_HEADING = re.compile(r"^(#{1,6}) ")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_LIST_MARKER = re.compile(r"^(\s*(?:[-*]|\d+\.)\s+)?(.*)$")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\"])")
_LEADING_BLANK_LINES = re.compile(r"\A(?:[ \t]*\n)+")
_BLANK_LINES_BEFORE_SUBHEADING = re.compile(r"\n\n+(?=### )")
# Claude Code's own memory-import syntax ("@path/to/file.md" on its own line); other agents don't read it.
_CLAUDE_IMPORT_LINE = re.compile(r"^@\S")

# Claude Code's own machinery: the kit's agents and skills, its main chat, and rules that load by path. Other agents
# can't act on these, and in AGENTS.md every rule is always loaded.
CLAUDE_ONLY_SECTIONS = frozenset({"Who does what", "Models"})
CLAUDE_ONLY_TERMS = ("kit:", "rules load when", "main chat")

PROJECT_SECTION = """\
## This project

<!-- Notes every agent needs here: what the app is, how to run it, the check and journeys commands, and the
modules with their skills. The kit keeps this section as it is when it regenerates the rules above. -->
"""

INTRO = """\
# Working rules

Every coding agent in this repo follows these rules: Claude Code, Codex, Cursor, Gemini CLI, OpenCode and Copilot. They come from Waqar's claude-kit; the project's own notes follow them.

- This file is advice. The git hooks (lefthook) and CI enforce the checks for every agent: never skip them (`--no-verify`) or weaken them.
- Whoever writes a change doesn't review it. A separate agent session or a person reviews it against the plan.
"""


class AgentsFileError(Exception):
    """AGENTS.md can't be updated safely; the message says why and what to do."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"build_agents_md: {root} is not a directory; pass the project root with --root", file=sys.stderr)
        return 1
    path = root / AGENTS_FILE
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    try:
        requested = args.stack if args.stack else recorded_stacks(current)
        stacks = [stack for stack in STACK_RULES if stack in requested]
        expected = build(current, render_block(kit_rules_dir(), stacks))
    except kit_root.KitRootError as error:
        print(f"build_agents_md: {error}, or run this script from a kit checkout.", file=sys.stderr)
        return 1
    except AgentsFileError as error:
        print(f"build_agents_md: {error}", file=sys.stderr)
        return 1
    if args.check:
        return _check(path, current, expected, stacks)
    if current != expected:
        path.write_text(expected, encoding="utf-8")
        print(f"build_agents_md: wrote {path}")
    _warn_if_codex_truncates(path, expected)
    return 0


def kit_rules_dir() -> Path:
    return kit_root.find_kit_root(Path(__file__)) / "home" / "rules"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="build_agents_md", description="Write AGENTS.md from the kit rules.")
    parser.add_argument(
        "--root", default=".", help="project root that holds AGENTS.md (default: the current directory)"
    )
    parser.add_argument(
        "--stack",
        action="append",
        choices=STACK_RULES,
        help="add a stack rule; repeat for more (default: the stacks AGENTS.md already records)",
    )
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if AGENTS.md is missing or stale")
    return parser.parse_args(argv)


def _check(path: Path, current: str | None, expected: str, stacks: list[str]) -> int:
    if current == expected:
        return 0
    state = "is missing" if current is None else "is out of date with the kit rules"
    fix = " ".join(["python3", str(Path(__file__).resolve()), "--root", str(path.parent), *_stack_args(stacks)])
    print(f"build_agents_md: {path} {state}. Regenerate it with: {fix}", file=sys.stderr)
    return 1


def _warn_if_codex_truncates(path: Path, text: str) -> None:
    size = len(text.encode("utf-8"))
    if size > CODEX_DEFAULT_MAX_BYTES:
        print(
            f"build_agents_md: {path} is {size} bytes; Codex reads only the first {CODEX_DEFAULT_MAX_BYTES} "
            "unless project_doc_max_bytes is raised. Shorten the project section.",
            file=sys.stderr,
        )


def recorded_stacks(current: str | None) -> list[str]:
    if current is None:
        return []
    begin = next((line for line in current.splitlines() if line.startswith(BEGIN_PREFIX)), "")
    return [stack for stack in _STACK_IN_MARKER.findall(begin) if stack in STACK_RULES]


def build(current: str | None, block: str) -> str:
    """The whole AGENTS.md: the project's text before and after the markers is kept, the block between is replaced."""
    if current is None or not current.strip():
        return _join("", block, PROJECT_SECTION)
    lines = current.splitlines(keepends=True)
    begins = [i for i, line in enumerate(lines) if line.startswith(BEGIN_PREFIX)]
    ends = [i for i, line in enumerate(lines) if line.strip() == END_MARKER]
    if not begins and not ends:
        return _join("", block, current)
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise AgentsFileError(
            f"{AGENTS_FILE} needs exactly one claude-kit begin marker followed by one end marker. "
            "Fix the markers by hand, or delete the old rules block and rerun."
        )
    return _join("".join(lines[: begins[0]]), block, "".join(lines[ends[0] + 1 :]))


def _join(before: str, block: str, after: str) -> str:
    parts = [_LEADING_BLANK_LINES.sub("", part).rstrip() for part in (before, block, after)]
    return "\n\n".join(part for part in parts if part) + "\n"


def render_block(rules_dir: Path, stacks: list[str]) -> str:
    """The generated rules block, markers included. `stacks` are STACK_RULES names in STACK_RULES order."""
    sections = [INTRO.rstrip("\n")]
    for name in (*UNIVERSAL_RULES, *stacks):
        rule_path = rules_dir / f"{name}.md"
        if not rule_path.is_file():
            raise AgentsFileError(f"the kit rule {rule_path} is missing; check the kit checkout")
        sections.append(rule_section(name, rule_path.read_text(encoding="utf-8")))
    command = " ".join(["build_agents_md.py", *_stack_args(stacks)])
    begin = (
        f"{BEGIN_PREFIX} GENERATED by claude-kit `{command}` from the kit rules. "
        "Change the rules and rerun it; don't edit this block by hand. -->"
    )
    block = "\n\n".join([begin, *sections, END_MARKER]) + "\n"
    size = len(block.encode("utf-8"))
    if size > MAX_BLOCK_BYTES:
        raise AgentsFileError(
            f"the rules block is {size} bytes, over the {MAX_BLOCK_BYTES}-byte cap. Drop a stack, or shorten the rules."
        )
    return block


def _stack_args(stacks: list[str]) -> list[str]:
    return [arg for stack in stacks for arg in ("--stack", stack)]


def rule_section(name: str, text: str) -> str:
    """One rule as an AGENTS.md section: no frontmatter, headings one level down, Claude-only text left out."""
    body = _FRONTMATTER.sub("", text, count=1)
    lines = _drop_claude_only(body.splitlines())
    if not any(_heading_level(line) == 1 for line in lines):
        lines = [f"# {name.replace('-', ' ').capitalize()}", "", *lines]
    demoted = [f"#{line}" if _heading_level(line) else line for line in lines]
    return _squeeze_blank_lines(demoted)


def _heading_level(line: str) -> int:
    heading = _HEADING.match(line)
    return len(heading[1]) if heading else 0


def _drop_claude_only(lines: list[str]) -> list[str]:
    kept, skipped_level = [], 0
    for line in lines:
        if _CLAUDE_IMPORT_LINE.match(line.lstrip()):
            continue
        level = _heading_level(line)
        if level and (not skipped_level or level <= skipped_level):
            skipped_level = level if line.lstrip("#").strip() in CLAUDE_ONLY_SECTIONS else 0
        if skipped_level:
            continue
        if any(term in line for term in CLAUDE_ONLY_TERMS):
            line = _without_claude_only_sentences(line)
        if line is not None:
            kept.append(line)
    return kept


def _without_claude_only_sentences(line: str) -> str | None:
    """The line with every sentence that names a Claude-only term removed, or None when nothing is left."""
    marker, text = _LIST_MARKER.match(line).groups()
    sentences = [s for s in _SENTENCE_BREAK.split(text) if not any(term in s for term in CLAUDE_ONLY_TERMS)]
    if not sentences:
        return None
    return f"{marker or ''}{' '.join(sentences)}"


def _squeeze_blank_lines(lines: list[str]) -> str:
    """One blank line at most, and none before a subheading, which keeps every stack within the line cap."""
    text = "\n".join(line.rstrip() for line in lines)
    text = _BLANK_LINES_BEFORE_SUBHEADING.sub("\n", re.sub(r"\n{3,}", "\n\n", text))
    return text.strip("\n")


if __name__ == "__main__":
    sys.exit(main())
