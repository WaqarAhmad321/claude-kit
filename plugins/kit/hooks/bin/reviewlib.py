"""The stop gate's review check: a non-trivial change since the session started needs the reviewer's
VERDICT: PASS on the working state as it is now, give or take a small follow-up."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import gatelib
import kitlib
import stopscope

STATE_KEY = "verdicts"
PASS, FIX = "PASS", "FIX"
BLOCK_REASON = (
    "Non-trivial change without an independent review: run the kit:reviewer agent on the current diff, fix what "
    "it reports, and re-run it until it ends with VERDICT: PASS."
)

_VERDICT_LINES = {f"VERDICT: {PASS}": PASS, f"VERDICT: {FIX}": FIX}
_DEFAULT_ALWAYS = (
    "**/auth/**",
    "**/*permission*",
    "**/payments/**",
    "**/billing/**",
    "**/*security*",
    "**/migrations/**",
)
_MAX_PATHS_LISTED = 10
_GIT_TIMEOUT_SECONDS = 60
_BINARY_COUNT = "-"
# HEAD reflog subjects of commands that make a commit here, as opposed to moving HEAD onto an existing commit. A
# rebase step reads `<action> (pick): <subject>`, and a pull's action holds its whole command line.
_MAKES_COMMIT = re.compile(r"(?:commit|cherry-pick|revert|am)\b|.*\((?!(?:start|finish|abort)\))[a-z -]+\): ")
_MAKES_MERGE = re.compile(r"(?:pull|merge)\b.*: Merge made by ")
_MAX_OWN_COMMITS_WALKED = 500
_IDENT_EMAIL = re.compile(r"<([^>]*)>")
_REVIEWED = re.compile(r"Reviewed:\s*(.+)")


@dataclass(frozen=True)
class ReviewConfig:
    enabled: bool = True
    min_lines: int = 50
    follow_up_lines: int = 15
    always: tuple[str, ...] = _DEFAULT_ALWAYS


@dataclass(frozen=True)
class _Problem:
    kind: str
    detail: str


_NONE = _Problem(
    "none",
    "No reviewer verdict is recorded for this change. A verdict counts for the checkout the reviewer's report names "
    "on its `Reviewed:` line, as that checkout was when the verdict came.",
)
_FIX = _Problem("fix", "The latest review ended with VERDICT: FIX.")
_LOST = _Problem("lost", "The reviewed state is gone from the repository, so the change since is unknown.")


@dataclass(frozen=True)
class Unreviewed:
    problem: _Problem
    size: int
    paths: tuple[str, ...]

    @property
    def identity(self) -> list:
        return [self.problem.kind, list(self.paths)]

    def report_lines(self) -> list[str]:
        shown = self.paths[:_MAX_PATHS_LISTED]
        more = len(self.paths) - len(shown)
        listed = ", ".join(shown) + (f" and {more} more" if more else "")
        return [
            BLOCK_REASON,
            f"Changed lines since the session started: {self.size}.",
            f"Paths: {listed}.",
            self.problem.detail,
        ]


def parse_verdict(message) -> str | None:
    """PASS or FIX when the last non-empty line is exactly `VERDICT: PASS` or `VERDICT: FIX`."""
    if not isinstance(message, str):
        return None
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    return _VERDICT_LINES.get(lines[-1]) if lines else None


def review_config(config: dict) -> ReviewConfig:
    gate = config.get("gate")
    review = _setting(gate if isinstance(gate, dict) else {}, "gate.review", {}, _is_object, "an object")
    defaults = ReviewConfig()
    count = "a whole number, 0 or more"
    return ReviewConfig(
        enabled=_setting(review, "gate.review.enabled", defaults.enabled, _is_bool, "true or false"),
        min_lines=_setting(review, "gate.review.min_lines", defaults.min_lines, _is_count, count),
        follow_up_lines=_setting(review, "gate.review.follow_up_lines", defaults.follow_up_lines, _is_count, count),
        always=tuple(_setting(review, "gate.review.always", list(defaults.always), _is_globs, "a list of globs")),
    )


def _setting(section: dict, key_path: str, default, valid: Callable[[object], bool], expected: str):
    value = section.get(key_path.rpartition(".")[2], default)
    if not valid(value):
        raise ValueError(f"{key_path} must be {expected}")
    return value


def _is_object(value) -> bool:
    return isinstance(value, dict)


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_globs(value) -> bool:
    return isinstance(value, list) and all(isinstance(glob, str) and glob.strip() for glob in value)


def reviewed_checkout(
    report: str, checkouts: list[stopscope.RepoChanges], cwd_root: Path | None
) -> stopscope.RepoChanges | None:
    """The checkout a verdict counts for: the one the report names on its `Reviewed:` line, by the absolute path of
    its root. Without that line, the reviewer's own checkout when it is the only one the session knows, so a
    one-checkout session needs no line; with more, nothing tells which one the reviewer was given. None too when
    the line names an unknown checkout, or more than one."""
    roots = {repo.root: repo for repo in checkouts}
    named = {match.group(1).strip("`") for line in report.splitlines() if (match := _REVIEWED.fullmatch(line.strip()))}
    if not named:
        return roots.get(cwd_root) if len(roots) == 1 else None
    path = Path(named.pop())
    return roots.get(path.resolve()) if not named and path.is_absolute() else None


def review_record(repo: stopscope.RepoChanges, verdict: str) -> dict:
    """`verdict` on the checkout's working state as it is now, uncommitted work included."""
    base, tree = session_base_tree(repo), snapshot_tree(repo.root)
    return {"verdict": verdict, "tree": tree, "base": base, "lines": sum(session_lines(repo, base, tree).values())}


def with_verdict(verdicts: dict, root: Path, record: dict) -> dict:
    """The recorded verdicts with `record` as the latest one for its checkout and for its working state, numbered
    after every verdict recorded before it."""
    seq = 1 + max((_seq(old) for old in verdicts.values()), default=0)
    kept = {key: old for key, old in verdicts.items() if key != str(root) and old["tree"] != record["tree"]}
    return {**kept, str(root): {**record, "seq": seq}}


def _seq(record: dict) -> int:
    """The verdict's place in the order they were recorded. One recorded before verdicts were numbered has none and
    counts as 0: older than every numbered one, and no newer than another unnumbered one."""
    return record.get("seq", 0)


def _recorded_after(record: dict, fix: dict | None) -> bool:
    return fix is None or _seq(record) > _seq(fix)


def without_passes(verdicts: dict) -> dict:
    return {key: record for key, record in verdicts.items() if record["verdict"] != PASS}


def unreviewed(repo: stopscope.RepoChanges, settings: ReviewConfig, verdicts: dict) -> Unreviewed | None:
    """Why this repo's change still needs a review, or None when it is trivial or its review still covers it."""
    if not settings.enabled or not repo.changed:
        return None
    base = _base_tree(repo.root, session_base(repo))
    matches = gatelib.watch_matcher(list(settings.always))
    if not _may_need_review(repo, base, settings.min_lines, matches):
        return None
    tree = snapshot_tree(repo.root)
    counts = session_lines(repo, base, tree)
    size = sum(counts.values())
    own = _changed_paths(repo.root, base, tree)
    flagged = sorted(rel for rel in repo.changed if rel in own and matches(rel))
    too_big = size > settings.min_lines
    if not too_big and not flagged:
        return None
    problem = _review_problem(repo, verdicts, base, tree, settings)
    if problem is None:
        return None
    paths = flagged + sorted(set(counts) - set(flagged)) if too_big else flagged
    return Unreviewed(problem, size, tuple(paths))


def _may_need_review(repo, base: str, min_lines: int, matches: Callable[[str], bool]) -> bool:
    """A cheap look before snapshotting the working state: whether the change could be over `min_lines` or touches
    an `always` path. Its count is never below the exact one, since it detects no renames and counts every line of
    an untracked file; a file it can't read means it can't tell."""
    tracked = _line_counts_to_worktree(repo.root, base)
    new_files = gatelib._ls_files(repo.root, "--others", "--exclude-standard")
    # A nested repository shows as its directory, whose files this repo's tree never holds.
    untracked = {rel: 0 if rel.endswith("/") else _line_count(repo.root / rel) for rel in new_files}
    own = {rel: count for rel, count in {**tracked, **untracked}.items() if rel in repo.changed}
    if any(matches(rel) for rel in own):
        return True
    sizes = [count for rel, count in own.items() if not _is_lockfile(rel)]
    return None in sizes or sum(sizes) > min_lines


def _line_counts_to_worktree(root: Path, base: str) -> dict[str, int]:
    out = gatelib._git(root, "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", base, "--")
    counts = {}
    for record in filter(None, out.split("\0")):
        added, deleted, path = record.split("\t", 2)
        counts[path] = _count(added) + _count(deleted)
    return counts


def _line_count(path: Path) -> int | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _review_problem(repo, verdicts: dict, base: str, tree: str, settings: ReviewConfig) -> _Problem | None:
    """None when a PASS covers this working state: one given in this checkout, or in another one reviewed from the
    same base, such as the worktree a branch was built and reviewed in before it was merged here, on exactly this
    state or on one it differs from by a follow-up at most. A FIX as the latest verdict on exactly this state, in any
    checkout, blocks; once this checkout's latest verdict is a FIX, only a PASS recorded after it counts."""
    if any(record["tree"] == tree and record["verdict"] == FIX for record in verdicts.values()):
        return _FIX
    root = str(repo.root)
    own = verdicts.get(root)
    fix = own if own is not None and own["verdict"] == FIX else None
    passes = [
        record
        for key, record in verdicts.items()
        if record["verdict"] == PASS and _recorded_after(record, fix) and (key == root or record["base"] == base)
    ]
    if any(record["tree"] == tree for record in passes):
        return None
    excluded = _out_of_scope(repo)
    follow_ups = [
        _follow_up(repo.root, record, base, tree, excluded)
        for record in passes
        if _tree_exists(repo.root, record["tree"])
    ]
    if follow_ups and min(follow_ups) <= settings.follow_up_lines:
        return None
    if fix is not None:
        return _FIX
    if own is not None and not _tree_exists(repo.root, own["tree"]):
        return _LOST
    if not follow_ups:
        return _NONE
    return _Problem(
        "follow_up",
        f"{min(follow_ups)} lines changed since the last VERDICT: PASS; a review covers at most "
        f"{settings.follow_up_lines} more.",
    )


def _follow_up(root: Path, review: dict, base: str, tree: str, excluded: set[str]) -> int:
    counts = _line_counts(root, _with_arrivals(root, review, base), tree)
    return sum(count for rel, count in counts.items() if rel not in excluded and not _is_lockfile(rel))


def _out_of_scope(repo: stopscope.RepoChanges) -> set[str]:
    """Files dirty at the start that no agent changed since: someone else's work, not this session's."""
    return set(repo.dirty_at_start) - set(repo.changed)


def _with_arrivals(root: Path, review: dict, base: str) -> str:
    """The reviewed state with what arrived from elsewhere since merged in, so a pull after the review isn't a
    follow-up. When that merge conflicts or fails, the reviewed state as it was."""
    reviewed_base = review.get("base")
    if reviewed_base in (None, base) or not _tree_exists(root, reviewed_base):
        return review["tree"]
    merge = ["git", "merge-tree", "--write-tree", f"--merge-base={reviewed_base}", review["tree"], base]
    code, out, _ = kitlib.run(merge, root, timeout=_GIT_TIMEOUT_SECONDS)
    merged = out.partition("\n")[0].strip()
    return merged if code == 0 and merged else review["tree"]


def session_lines(repo: stopscope.RepoChanges, base: str, tree: str) -> dict[str, int]:
    """Changed lines per session-changed file from the session's base tree to `tree`, lockfiles aside."""
    counts = _line_counts(repo.root, base, tree)
    return {rel: count for rel, count in counts.items() if rel in repo.changed and not _is_lockfile(rel)}


def session_base_tree(repo: stopscope.RepoChanges) -> str:
    return _base_tree(repo.root, session_base(repo))


def session_base(repo: stopscope.RepoChanges) -> str | None:
    """The commit the session's own change is measured from: the start commit, or, once HEAD has moved onto commits
    made elsewhere (a pull, a fast-forward, a merge's other side, a branch switch), the latest of those. A commit made
    here (by commit, amend, cherry-pick, revert, a rebase step or a merge) never becomes the base, and neither does
    the session's own commit that arrives from a worktree or `commit-tree`, so both keep counting. A move onto the
    base itself, such as making a worktree at the commit it starts from, leaves the base there."""
    if repo.started_at is None or repo.base_commit is None:
        return repo.base_commit
    own = _own_commit_predicate(repo.root, repo.started_at)
    base, made_here = repo.base_commit, set()
    for move in gatelib.head_moves(repo.root):
        if move.at < repo.started_at:
            continue
        merged = _MAKES_MERGE.match(move.subject)
        if _MAKES_COMMIT.match(move.subject) or merged:
            made_here.add(move.commit)
            arrived = move.parents[1] if len(move.parents) > 1 else None  # A merge's other side came from elsewhere.
        else:
            arrived = move.commit
        if not arrived or arrived in made_here or arrived == base:
            continue
        foreign = _newest_foreign(repo.root, arrived, own)
        # A merge keeps HEAD's own line, so its other side moves the base only by what it brings that's new.
        if foreign and not (merged and gatelib.is_ancestor(repo.root, foreign, base)):
            base = foreign
    return base if gatelib.commit_exists(repo.root, base) else repo.base_commit


def _own_commit_predicate(root: Path, started_at: int) -> Callable[[str, int], bool]:
    """Whether a commit, by its committer email and time, is the session's own: committed with the email git
    commits with in this repo (`user.email`, or `GIT_COMMITTER_EMAIL` when set) at or after the session started.
    When git has no identity here, no commit is."""
    code, out, _ = kitlib.run(["git", "var", "GIT_COMMITTER_IDENT"], root, timeout=10)
    email = _IDENT_EMAIL.search(out) if code == 0 else None
    mine = email.group(1).casefold() if email else ""
    return lambda committer, committed_at: bool(mine) and committer.casefold() == mine and committed_at >= started_at


def _newest_foreign(root: Path, commit: str, own: Callable[[str, int], bool]) -> str | None:
    """The first commit along `commit`'s first parents that isn't the session's own; None when every commit within
    reach is, or `commit` is gone."""
    log = ["git", "log", "--first-parent", f"--max-count={_MAX_OWN_COMMITS_WALKED}", "--format=%H%x1f%ce%x1f%ct"]
    code, out, _ = kitlib.run([*log, commit, "--"], root, timeout=_GIT_TIMEOUT_SECONDS)
    for line in out.splitlines() if code == 0 else []:
        sha, committer, committed_at = line.split("\x1f")
        if not own(committer, int(committed_at)):
            return sha
    return None


def _is_lockfile(rel: str) -> bool:
    return PurePosixPath(rel).name in gatelib.LOCKFILES


def snapshot_tree(root: Path) -> str:
    """The working state (tracked and untracked, not ignored) as a git tree id, built in a copy of the index so
    the real index is never touched."""
    index = root / gatelib._git(root, "rev-parse", "--git-path", "index").strip()
    with tempfile.TemporaryDirectory(prefix="kit-review-") as scratch:
        scratch_index = Path(scratch) / "index"
        if index.is_file():
            # copy2 keeps the index's mtime, which git's racy-clean check compares entries against.
            shutil.copy2(index, scratch_index)
        _git_with_index(root, scratch_index, "add", "--all")
        return _git_with_index(root, scratch_index, "write-tree").strip()


def _git_with_index(root: Path, index: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        check=False,
        cwd=root,
        env={**os.environ, "GIT_INDEX_FILE": str(index)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _base_tree(root: Path, commit: str | None) -> str:
    """The tree of `commit`, or the empty tree when there is no commit to compare with."""
    if commit:
        code, out, _ = kitlib.run(["git", "rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}"], root, timeout=10)
        if code == 0 and out.strip():
            return out.strip()
    code, out, err = gatelib.run_with_input(["git", "hash-object", "-t", "tree", "--stdin"], root, "", 10)
    if code != 0:
        raise RuntimeError(f"git hash-object failed: {err.strip()[:300]}")
    return out.strip()


def _tree_exists(root: Path, tree: str) -> bool:
    code, _, _ = kitlib.run(["git", "cat-file", "-e", f"{tree}^{{tree}}"], root, timeout=10)
    return code == 0


def _changed_paths(root: Path, old: str, new: str) -> set[str]:
    """Every path that differs from tree `old` to tree `new`, both sides of a rename included."""
    out = gatelib._git(root, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", old, new, "--")
    return {rel for rel in out.split("\0") if rel}


def _line_counts(root: Path, old: str, new: str) -> dict[str, int]:
    """Added plus deleted lines per path from tree `old` to tree `new`, under a rename's new path; a binary file
    counts 0."""
    out = gatelib._git(root, "diff", "--numstat", "-z", "-M", "--no-ext-diff", "--no-textconv", old, new, "--")
    fields, counts = iter(out.split("\0")), {}
    for record in fields:
        if not record:
            continue
        added, deleted, path = record.split("\t", 2)
        if not path:  # A rename: its old and new paths follow as fields of their own.
            next(fields, "")
            path = next(fields, "")
        counts[path] = _count(added) + _count(deleted)
    return counts


def _count(value: str) -> int:
    return 0 if value == _BINARY_COUNT else int(value)
