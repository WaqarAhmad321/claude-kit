"""The rules guard_commit enforces on each `git commit`, judged on a scratch copy of the index.

Per repo, the command's `git add`s and commits are replayed in order into a scratch index and object store, so
each commit is checked on exactly what it would contain, and the real index and objects are never touched.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import kitlib
import scan_secrets
import shellwords
from shellwords import Word

if TYPE_CHECKING:
    from guard_commit import Chain, GitCall

_MAX_LINES = 600
_MAX_SUBJECT = 72
_CONVENTIONAL = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([\w./-]+\))?!?: .+")
_ATTRIBUTION = re.compile(
    r"(?im)^\s*co-authored-by\s*[:=]"
    r"|\bgenerated\s+(?:with|by|using)\b[^\n]*\b(?:claude|anthropic|chatgpt|openai|copilot|gemini|ai)\b"
    r"|noreply@anthropic\.com"
)
_TEST_FILE = re.compile(r"(?:^|/)tests/|(?:^|/)test_[^/]*\.py$|_test\.py$|\.(?:test|spec)\.[cm]?[jt]sx?$|\.hurl$")
_MATRIX_FILE = re.compile(r"(?:^|/)tests/matrix/[^/]+\.ya?ml$")
_TEST_CASE = re.compile(
    r"^\s*(?:async\s+)?def\s+test_\w*"
    r"|(?<![.\w])(?:it|test|describe)(?:\.\w+)*\s*\("
    r"|^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\S",
    re.MULTILINE,
)
_SOURCE_SUFFIXES = frozenset(
    ".py .pyi .js .jsx .ts .tsx .mjs .cjs .mts .cts .vue .svelte "
    ".go .rs .java .kt .rb .php .cs .c .h .cpp .hpp .swift .scala .sh".split()
)
_TEST_LOCATIONS = "tests/, test_*.py, *_test.py, *.test.ts(x), *.spec.ts(x), *.hurl, tests/matrix/*.yaml"
_INTERACTIVE_ADD = frozenset({"-p", "--patch", "-i", "--interactive", "-e", "--edit"})
_UNKNOWN_MESSAGE = (
    "kit: the commit message is only built when the command runs ($(...), backticks, $VAR or piped input), "
    "so kit couldn't check it for Conventional Commits or AI attribution. Confirm it, or ask Claude to pass "
    "the message literally."
)
_PLUMBING_COMMIT = (
    "kit: this command commits through git commit-tree, so kit can't check the files that commit holds. "
    "Confirm it, or ask Claude to use git commit."
)
_UNRESOLVED_DIRECTORY = (
    "kit: this command runs git -C a directory only known at run time, so kit couldn't check the files it "
    "would stage or commit. Confirm it, or ask Claude to pass the directory literally."
)
_MESSAGE_ONLY_SUBCOMMANDS = frozenset({"merge", "commit-tree"})


def _glob(pattern: str) -> re.Pattern:
    regex = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"/\*\*", "/.*").replace(r"\*", "[^/]*")
    return re.compile(regex)


_FORBIDDEN = [
    _glob(pattern)
    for pattern in (
        "**/.env",
        "**/.env.*",
        "**/.claude/settings.local.json",
        ".claude/worktrees/**",
        "**/*.pem",
        "**/id_rsa*",
        "**/.auth/**",
        "**/storageState*.json",
        "**/.playwright-mcp/**",
    )
]
_ALLOWED = [_glob("**/.env.example")]
_SIZE_EXEMPT = [
    _glob(pattern)
    for pattern in (
        "**/*.lock",
        "**/*.lockb",
        "**/package-lock.json",
        "**/npm-shrinkwrap.json",
        "**/pnpm-lock.yaml",
        "**/go.sum",
        "**/migrations/**",
        "**/__snapshots__/**",
        "**/*.snap",
        "**/CONTRACTS.md",
        "**/SCHEMA.md",
        "**/openapi*.yaml",
        "**/openapi*.yml",
    )
]

_SHORT_WITH_VALUE = {"m": "messages", "F": "message_file", "C": "reuse", "c": "reuse", "t": None}
_SHORT_WITH_ATTACHED_VALUE = frozenset("uS")
_LONG_WITH_VALUE = {
    "--message": "messages",
    "--file": "message_file",
    "--trailer": "trailers",
    "--reuse-message": "reuse",
    "--reedit-message": "reuse",
    "--fixup": "reuse",
    "--squash": "reuse",
    "--template": None,
    "--author": None,
    "--date": None,
    "--cleanup": None,
    "--pathspec-from-file": None,
}


@dataclass
class CommitArgs:
    stage_all: bool = False
    reuses_message: bool = False
    messages: list[Word] = field(default_factory=list)
    message_file: Word | None = None
    trailers: list[Word] = field(default_factory=list)
    pathspecs: list[Word] = field(default_factory=list)


@dataclass(frozen=True)
class Change:
    status: str
    path: str


@dataclass
class Preview:
    """One repo's scratch index; `base` is the tree the next commit's changes are measured against."""

    repo: Path
    base: str

    def add(self, call: GitCall) -> None:
        """Failures are ignored: a path the command creates later can't be staged yet, and git reports it anyway."""
        texts = [word.text for word in call.args]
        if all(word.literal for word in call.args) and not _INTERACTIVE_ADD.intersection(texts):
            _scratch_add(call.directory, *texts)

    def stage(self, args: CommitArgs, call: GitCall) -> None:
        if args.stage_all:
            _scratch_add(self.repo, "-u")
        if args.pathspecs and all(word.literal for word in args.pathspecs):
            _scratch_add(call.directory, "-u", "--", *(word.text for word in args.pathspecs))

    def commit(self) -> None:
        self.base = _git(self.repo, "write-tree").strip()


def check(chain: Chain) -> list[str]:
    """Deny on the first broken rule; return what the user should be asked to confirm."""
    asks = [
        ask
        for call in chain.calls
        if call.subcommand in _MESSAGE_ONLY_SUBCOMMANDS
        for ask in _check_message(call, chain)
    ]
    commits_and_adds = [call for call in chain.calls if call.subcommand not in _MESSAGE_ONLY_SUBCOMMANDS]
    # A call whose -C directory is only known at run time can't be staged or previewed against a real repo, so
    # it never reaches _calls_by_repo; a literal message on it is still checked, same as merge and commit-tree.
    for call in commits_and_adds:
        if call.directory is None and call.subcommand == "commit":
            asks += [*_check_message(call, chain), _UNRESOLVED_DIRECTORY]
    resolvable = [call for call in commits_and_adds if call.directory is not None]
    for repo, calls in _calls_by_repo(resolvable).items():
        with _preview(repo) as preview:
            for call in calls:
                if call.subcommand == "commit":
                    asks += _check_commit(call, chain, preview)
                else:
                    preview.add(call)
    return asks


def _check_message(call: GitCall, chain: Chain) -> list[str]:
    """The message rules for `git merge -m` and `git commit-tree`, which don't commit the staged files."""
    args = _commit_args(call.args)
    asks = [_PLUMBING_COMMIT] if call.subcommand == "commit-tree" else []
    if call.subcommand == "commit-tree" and not (args.messages or args.message_file):
        args.message_file = Word("-")
    if not (args.messages or args.message_file):
        return asks
    message = _message(args, call, chain)
    if message is None:
        _deny_attribution("\n".join(word.text for word in args.messages))
        return [*asks, _UNKNOWN_MESSAGE]
    _deny_bad_subject(_subject(message))
    _deny_attribution(message)
    return asks


def _calls_by_repo(calls: list[GitCall]) -> dict[Path, list[GitCall]]:
    roots: dict[Path, Path | None] = {}
    grouped: dict[Path, list[GitCall]] = {}
    for call in calls:
        if call.directory not in roots:
            roots[call.directory] = kitlib.project_root(str(call.directory))
        if roots[call.directory]:
            grouped.setdefault(roots[call.directory], []).append(call)
    return {repo: group for repo, group in grouped.items() if any(c.subcommand == "commit" for c in group)}


def _check_commit(call: GitCall, chain: Chain, preview: Preview) -> list[str]:
    """Deny on the first broken rule; return what the user should be asked to confirm."""
    args = _commit_args(call.args)
    preview.stage(args, call)
    repo, base = preview.repo, preview.base
    changes = _staged_changes(repo, base)
    _deny_forbidden_paths(changes)
    _deny_staged_secrets(repo, base)
    asks = []
    message = _message(args, call, chain)
    if message is None:
        _deny_attribution("\n".join(word.text for word in [*args.messages, *args.trailers]))
        if args.messages or args.message_file:
            asks.append(_UNKNOWN_MESSAGE)
    else:
        subject = _subject(message)
        _deny_bad_subject(subject)
        _deny_attribution("\n".join([message, *(word.text for word in args.trailers)]))
        _deny_untested_fix(subject, changes, repo, base)
    lines = _changed_lines(repo, base)
    if lines > _MAX_LINES:
        asks.append(
            f"kit: this commit changes {lines} lines (over {_MAX_LINES}, not counting lockfiles, migrations, "
            "snapshots, generated docs and binaries). Split into one concern per commit, or confirm this size "
            "is intended."
        )
    preview.commit()
    return asks


def _commit_args(args: list[Word]) -> CommitArgs:
    parsed = CommitArgs()
    words = iter(args)
    for word in words:
        text = word.text
        if text == "--":
            parsed.pathspecs.extend(words)
        elif text.startswith("--"):
            name, has_value, value = text.partition("=")
            parsed.stage_all |= name == "--all"
            if name in _LONG_WITH_VALUE:
                _record(parsed, _LONG_WITH_VALUE[name], Word(value, word.literal) if has_value else next(words, None))
        elif text.startswith("-") and len(text) > 1:
            _short_options(parsed, word, words)
        else:
            parsed.pathspecs.append(word)
    return parsed


def _short_options(parsed: CommitArgs, word: Word, words: Iterator[Word]) -> None:
    text = word.text
    for after, flag in enumerate(text[1:], start=2):
        if flag == "a":
            parsed.stage_all = True
        elif flag in _SHORT_WITH_VALUE:
            rest = text[after:]
            _record(parsed, _SHORT_WITH_VALUE[flag], Word(rest, word.literal) if rest else next(words, None))
            return
        elif flag in _SHORT_WITH_ATTACHED_VALUE:
            return


def _record(parsed: CommitArgs, kind: str | None, value: Word | None) -> None:
    if value is None or kind is None:
        return
    if kind == "reuse":
        parsed.reuses_message = True
    elif kind == "message_file":
        parsed.message_file = value
    else:
        getattr(parsed, kind).append(value)


def _message(args: CommitArgs, call: GitCall, chain: Chain) -> str | None:
    """The commit message as git will see it, or None when it can't be known before the command runs."""
    if args.reuses_message:
        return None
    if args.messages:
        parts = [shellwords.value(word, call.heredocs) for word in args.messages]
        return None if None in parts else "\n\n".join(parts)
    return _message_file(args.message_file, call, chain) if args.message_file else None


def _message_file(word: Word, call: GitCall, chain: Chain) -> str | None:
    if word.text == "-":
        source = call.stdin
    elif not word.literal or call.directory is None:
        return None
    else:
        path = shellwords.resolve_path(call.directory, word)
        source = chain.written.get(str(path))
        if source is None:
            return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    return source.text if source and source.literal else None


def _subject(message: str) -> str:
    return next((line.rstrip() for line in message.splitlines() if line.strip()), "")


def _git(repo: Path, *args: str) -> str:
    code, out, err = kitlib.run(["git", *args], repo)
    if code != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {err.strip()[:300]}")
    return out


@contextlib.contextmanager
def _preview(repo: Path) -> Iterator[Preview]:
    index = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    objects = _git(repo, "rev-parse", "--path-format=absolute", "--git-path", "objects").strip()
    code, head, _ = kitlib.run(["git", "rev-parse", "--verify", "-q", "HEAD^{tree}"], repo)
    base = head.strip() if code == 0 else _git(repo, "hash-object", "-t", "tree", "--stdin").strip()
    with tempfile.TemporaryDirectory(prefix="kit-commit-") as scratch:
        preview_index = Path(scratch) / "index"
        if index.is_file():
            # copy2 keeps the index's mtime, which git's racy-clean check needs to spot same-size edits.
            shutil.copy2(index, preview_index)
        (Path(scratch) / "objects").mkdir()
        scratch_env = {
            "GIT_INDEX_FILE": str(preview_index),
            "GIT_OBJECT_DIRECTORY": str(Path(scratch) / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects,
        }
        with _environment(scratch_env):
            yield Preview(repo, base)


@contextlib.contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _scratch_add(directory: Path, *args: str) -> None:
    kitlib.run(["git", "-c", "core.hooksPath=/dev/null", "add", *args], directory)


def _staged_changes(repo: Path, base: str) -> list[Change]:
    fields = _git(repo, "diff", "--cached", base, "--name-status", "-z").split("\0")
    changes, i = [], 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        path_index = i + 2 if status[0] in "RC" else i + 1
        changes.append(Change(status[0], fields[path_index]))
        i = path_index + 1
    return changes


def _changed_lines(repo: Path, base: str) -> int:
    fields = _git(repo, "diff", "--cached", base, "--numstat", "-z").split("\0")
    total, i = 0, 0
    while i < len(fields) and fields[i]:
        added, deleted, path = fields[i].split("\t", 2)
        if path:
            i += 1
        else:  # A rename: the pre- and post-image paths follow as their own fields.
            path, i = fields[i + 2], i + 3
        if added != "-" and not any(pattern.fullmatch(path) for pattern in _SIZE_EXEMPT):
            total += int(added) + int(deleted)
    return total


def _deny_forbidden_paths(changes: list[Change]) -> None:
    forbidden = sorted({change.path for change in changes if change.status != "D" and is_forbidden(change.path)})
    if forbidden:
        kitlib.pre_tool_decision(
            "deny",
            f"kit: refusing to commit files that must never be committed: {', '.join(forbidden)}. "
            "Leave them out of the commit (don't `git add` them; `git rm --cached <path>` if already staged) "
            "and make sure .gitignore covers them.",
        )


def is_forbidden(path: str) -> bool:
    return not any(p.fullmatch(path) for p in _ALLOWED) and any(p.fullmatch(path) for p in _FORBIDDEN)


def _deny_staged_secrets(repo: Path, base: str) -> None:
    findings = scan_secrets.scan_staged(repo, base)
    if findings:
        where = ", ".join(f"{rule} in {path}" for rule, path in findings)
        kitlib.pre_tool_decision(
            "deny",
            f"kit: the staged changes contain likely secrets ({where}). Keep them out of tracked files: use an "
            'environment variable instead ("$API_TOKEN" in shell, read from os.environ in code) or an '
            "untracked, git-ignored .env, re-stage, and commit again. Rotate the credential if it was ever real.",
        )


def _deny_bad_subject(subject: str) -> None:
    if _CONVENTIONAL.match(subject) and len(subject) <= _MAX_SUBJECT:
        return
    shown = subject if len(subject) <= 100 else subject[:97] + "..."
    kitlib.pre_tool_decision(
        "deny",
        f'kit: the commit subject "{shown}" ({len(subject)} chars) must follow Conventional Commits and fit in '
        f"{_MAX_SUBJECT} characters: type(scope): summary, where type is one of feat, fix, docs, style, "
        "refactor, perf, test, build, ci, chore, revert. Example: fix(auth): reject expired refresh tokens",
    )


def _deny_attribution(message: str) -> None:
    if _ATTRIBUTION.search(message):
        kitlib.pre_tool_decision(
            "deny",
            'kit: the commit message carries AI attribution (a Co-Authored-By line or a "Generated with ..." '
            "trailer). The owner's rule: commit messages hold only the change description. Remove those lines "
            "and commit again.",
        )


def _deny_untested_fix(subject: str, changes: list[Change], repo: Path, base: str) -> None:
    kind = _CONVENTIONAL.match(subject)
    if not kind or kind.group(1) != "fix":
        return
    sources = [change.path for change in changes if _is_source(change.path)]
    if not sources or _adds_a_test_case(repo, base):
        return
    shown = ", ".join(sources[:5]) + (f" and {len(sources) - 5} more" if len(sources) > 5 else "")
    kitlib.pre_tool_decision(
        "deny",
        f"kit: a fix: commit needs a test. The staged changes touch source code ({shown}) but add no test case "
        f"({_TEST_LOCATIONS}): a def test_..., it(...), test(...) or describe(...) block, or a Hurl request. "
        "Add a test that fails without the fix and passes with it, stage it, and commit again.",
    )


def _adds_a_test_case(repo: Path, base: str) -> bool:
    for path, added in scan_secrets.staged_additions(repo, base).items():
        if _MATRIX_FILE.search(path):
            if any(line.strip() and not line.lstrip().startswith("#") for line in added.splitlines()):
                return True
        elif _TEST_FILE.search(path) and _TEST_CASE.search(added):
            return True
    return False


def _is_source(path: str) -> bool:
    posix = PurePosixPath(path)
    return posix.suffix in _SOURCE_SUFFIXES and "migrations" not in posix.parts[:-1] and not _TEST_FILE.search(path)
