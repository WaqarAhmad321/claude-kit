"""PreToolUse Write|Bash: keep new Markdown files in a repo to its docs layout, so summaries, plans and reports
stay out of it.

Only a file that doesn't exist yet and sits inside a git work tree counts; scratch work and the kit's templates are
left alone. Bash is read best effort: the targets of output redirections and of `tee`; cp, mv, touch and code run by
interpreters aren't followed.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import docnames
import gatelib
import kitlib
import shellwords

FAIL_CLOSED = True

_MARKDOWN_SUFFIXES = frozenset({".md", ".mdx"})
_ALWAYS_ALLOWED_GLOBS = (
    ".claude/**",
    ".github/**",
    "**/node_modules/**",
    "**/fixtures/**",
    "**/__fixtures__/**",
    "**/testdata/**",
    "plugins/**/skills/**",
    "plugins/**/agents/**",
)
_ROOT_ONLY_DOCS = frozenset({"CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE.md"})
_README = "README.md"
_UNEXPANDED = re.compile(r"[$`]")
_TEE = "tee"
_DENY, _ASK = "deny", "ask"
_SEVERITY = {_ASK: 1, _DENY: 2}
_GENERIC_SCRATCHPAD = "your session scratchpad, outside the repo"


@kitlib.hook_main("guard_docs_location", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    verdicts = [verdict for path in _targets(inp) if (verdict := _verdict(path))]
    if not verdicts:
        return
    decision, rel = max(verdicts, key=lambda verdict: _SEVERITY[verdict[0]])
    reason = _deny_reason(rel, _scratchpad(inp)) if decision == _DENY else _ask_reason(rel)
    kitlib.pre_tool_decision(decision, reason)


def _targets(inp: dict) -> list[Path]:
    if inp.get("tool_name") != "Bash":
        written = kitlib._written_path(inp)
        return [written] if written else []
    command = (inp.get("tool_input") or {}).get("command")
    base = Path(inp.get("cwd") or os.getcwd())
    return _bash_targets(command, base) if isinstance(command, str) else []


def _bash_targets(command: str, base: Path) -> list[Path]:
    """Files the command's redirections and `tee` calls write, each resolved from the directory it runs in."""
    targets = []
    for step in shellwords.command_steps(command):
        for word in [*step.written, *_tee_files(step)]:
            path = _resolved(base, word)
            if path:
                targets.append(path)
        base = shellwords.next_directory(step, base)
    return targets


def _tee_files(step: shellwords.CommandStep) -> list[shellwords.Word]:
    words = shellwords.command_words(shellwords.SimpleCommand(list(step.words)))
    if not words or shellwords.literal_basename(words[0]) != _TEE:
        return []
    return shellwords.operands(words[1:])


def _resolved(base: Path, word: shellwords.Word) -> Path | None:
    path = shellwords.resolve_path(base, word)
    if not word.literal and _UNEXPANDED.search(str(path)):
        return None
    return Path(os.path.normpath(path))


def _verdict(path: Path) -> tuple[str, str] | None:
    """The decision and repo-relative path for a new Markdown file in a git work tree; None for anything else."""
    if path.suffix.lower() not in _MARKDOWN_SUFFIXES or os.path.lexists(path):
        return None
    root = kitlib.project_root(str(path))
    resolved = path.resolve()
    if root is None or not resolved.is_relative_to(root) or kitlib.is_non_project(root, resolved):
        return None
    rel = resolved.relative_to(root).as_posix()
    decision = _decision(rel, _allowed_matcher(root))
    return (decision, rel) if decision else None


def _decision(rel: str, allowed: Callable[[str], bool]) -> str | None:
    name = PurePosixPath(rel).name
    if rel in _ROOT_ONLY_DOCS or name == _README or allowed(rel):
        return None
    name_rule = docnames.docs_dir_rule(rel)
    if docnames.scratch_marks(rel) or (name_rule is not None and not name_rule.fullmatch(name)):
        return _DENY
    return None if name_rule is not None else _ASK


@functools.cache
def _allowed_matcher(root: Path) -> Callable[[str], bool]:
    return gatelib.watch_matcher([*_ALWAYS_ALLOWED_GLOBS, *_configured_globs(root)])


def _configured_globs(root: Path) -> list[str]:
    docs = kitlib.load_config(root).get("docs", {})
    allowed = docs.get("allowed", []) if isinstance(docs, dict) else None
    if not isinstance(allowed, list) or not all(isinstance(glob, str) for glob in allowed):
        raise ValueError(f"docs.allowed in {root / '.claude' / 'kit.json'} must be a list of glob strings")
    return [glob for glob in allowed if glob.strip()]


def _scratchpad(inp: dict) -> str:
    scratchpad = inp.get("scratchpad_dir")
    return scratchpad if isinstance(scratchpad, str) and scratchpad else _GENERIC_SCRATCHPAD


def _deny_reason(rel: str, scratchpad: str) -> str:
    return (
        f"kit: new Markdown file blocked: {rel}. Docs live only in README.md, CLAUDE.md, CHANGELOG.md and "
        "docs/{architecture,decisions,api,partners,runbooks,guides}/. Summaries, plans, reports and fix notes go "
        f"in your reply, the PR or a PM card; scratch work goes in {scratchpad}. For lasting docs, update a file "
        "listed in docs/README.md or add docs/decisions/NNNN-title.md, docs/runbooks/<task>.md or "
        "docs/api/handoffs/<feature>.md and index it."
    )


def _ask_reason(rel: str) -> str:
    return (
        f"New Markdown file outside the docs layout: {rel}. Approve only for a new kind of lasting doc, "
        "then add it to docs.allowed in .claude/kit.json."
    )


if __name__ == "__main__":
    main()
