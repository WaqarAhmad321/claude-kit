"""What a stop hook checks: for each repository the agent worked in, the files changed since it started."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path

import gatelib
import kitlib

MAIN = "main"
MAX_SAME_BLOCKS = 3
MAX_BLOCKS = 5
MAX_REASON_LINES = 40

_LOST_HISTORY = (
    "Session history was rewritten; the baseline was lost: the start commit is gone, so every file touched this "
    "session was checked against HEAD instead. Tell the user what you rewrote and why."
)
_STASHED = (
    "Changes were stashed during this session ({name}: {files}), so the checks can't see them. "
    "Restore them with `git stash pop`, or tell the user why they are set aside."
)
_NOT_CHECKED_OUT = (
    "Commits made during this session aren't checked out ({commits} on {branches}), so the checks can't see them. "
    "Check them out, or tell the user why they are left aside."
)


@dataclass(frozen=True)
class Session:
    """Every agent's state in this Claude Code session, and which of them is stopping."""

    agent: str
    states: dict[str, dict]

    @property
    def own(self) -> dict:
        return self.states[self.agent]

    @property
    def is_main(self) -> bool:
        return self.agent == MAIN

    @property
    def main(self) -> dict:
        return self.states.get(MAIN, {})

    def others(self) -> list[dict]:
        return [state for name, state in self.states.items() if name != self.agent]


@dataclass(frozen=True)
class Start:
    root: Path
    head: str | None
    dirty: frozenset[str]
    started_at: int | None
    head_lost: bool
    worktrees: frozenset[str] | None  # The repo's worktree paths at the start; None when they weren't recorded


@dataclass(frozen=True)
class RepoChanges:
    root: Path
    changed: dict[str, str]  # repo-relative path -> its path at base_commit, which differs after a rename
    base_commit: str | None  # where an unedited file's baseline comes from; None when nothing was committed
    dirty_at_start: frozenset[str]
    has_start: bool  # False: no start snapshot for this repo, so only Edit/Write changes are known
    history_findings: tuple[str, ...] = ()
    started_at: int | None = None  # Unix seconds; None without a start snapshot


def load_session(inp: dict) -> Session:
    own_path = kitlib.State(inp).path
    with kitlib.State(inp) as st:
        own = copy.deepcopy(st)
    others = {path.stem: _read_state(path) for path in own_path.parent.glob("*.json") if path != own_path}
    agent = MAIN if not inp.get("agent_id") else own_path.stem
    return Session(agent, {**others, agent: own})


def _read_state(path: Path) -> dict:
    # State files are replaced atomically, so reading one without its lock is safe.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def has_start(state: dict) -> bool:
    return "initially_dirty" in state


@dataclass(frozen=True)
class Release:
    why: str
    first: bool  # The streak's first release, the only one that warns the user.


def release(st: dict, key: str, findings) -> Release | None:
    """None to block on `findings` again; otherwise the stop hook lets Claude finish despite them.

    Until a clean stop clears the streak, the same findings block at most MAX_SAME_BLOCKS times in a row and all
    findings together at most MAX_BLOCKS times, across turns: a new turn (stop_hook_active false) resets neither
    count, because an orchestrating main thread starts a new turn every time a background agent reports.
    """
    streak = st.get(key) or {}
    same = streak.get("same", 0) + 1 if streak.get("findings") == findings else 1
    blocks, released = streak.get("blocks", 0), streak.get("released", False)
    if same > MAX_SAME_BLOCKS:
        why = f"blocked {MAX_SAME_BLOCKS} times in a row on the same problems"
    elif blocks >= MAX_BLOCKS:
        why = f"blocked {MAX_BLOCKS} times in a row"
    else:
        st[key] = {"findings": findings, "same": same, "blocks": blocks + 1, "released": released}
        return None
    st[key] = {"findings": findings, "same": same, "blocks": blocks, "released": True}
    return Release(why, first=not released)


def write_report(inp: dict, hook: str, text: str) -> Path:
    """Save a stop hook's full report as `<data dir>/reports/<session>[-<agent>]-<hook>.md`; week-old ones go."""
    directory = kitlib._data_dir() / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    parts = (inp.get("session_id") or "unknown", inp.get("agent_id"), hook)
    path = directory / f"{'-'.join(kitlib._safe_name(str(part)) for part in parts if part)}.md"
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text.rstrip() + "\n", encoding="utf-8")
    os.replace(temporary, path)
    cutoff = time.time() - kitlib._STATE_TTL_SECONDS
    for old in directory.glob("*.md"):
        with contextlib.suppress(FileNotFoundError):  # The other stop hook runs alongside and prunes too.
            if old.stat().st_mtime < cutoff:
                old.unlink()
    return path


def main_reason(summary: str, report: Path) -> str:
    """A main-thread block, which the user sees in the chat: three short lines, the details in the report."""
    return "\n".join(
        [
            summary,
            f"Full report: {report}",
            "Delegate the fix to a subagent that reads the report; don't work through it in the main chat.",
        ]
    )


def capped(lines: list[str], report: Path) -> list[str]:
    """At most MAX_REASON_LINES lines, then a count of the rest and where to read them."""
    if len(lines) <= MAX_REASON_LINES:
        return lines
    hidden = len(lines) - MAX_REASON_LINES
    return [*lines[:MAX_REASON_LINES], f"...and {hidden} more lines in the full report: {report}"]


def listed(names: list[str], limit: int = 3) -> str:
    shown = ", ".join(names[:limit])
    return f"{shown} and {len(names) - limit} more" if len(names) > limit else shown


def repo_changes(session: Session, cwd_root: Path | None) -> list[RepoChanges]:
    """The main agent answers for every agent's edits; a subagent only for its own. Scratch repos and files, and
    the kit's templates, aren't project code, so they are left out."""
    start = _start(session, cwd_root)
    editors = session.states.values() if session.is_main else [session.own]
    edited = {path for state in editors for path in tool_edited(state) if not kitlib.is_scratch(path)}
    known = {root for root in (start and start.root, cwd_root) if root}
    roots = sorted(root for root in _roots(known, edited) if not kitlib.is_scratch(root))
    return [
        _project_part(_with_start(session, root, start) if start and root == start.root else _edits_only(root, edited))
        for root in roots
    ]


def _project_part(repo: RepoChanges) -> RepoChanges:
    project = {rel: base for rel, base in repo.changed.items() if not kitlib.is_non_project(repo.root, repo.root / rel)}
    return replace(repo, changed=project)


def _start(session: Session, cwd_root: Path | None) -> Start | None:
    own, main = session.own, session.main
    state = own if has_start(own) else main if has_start(main) else None
    root = Path(state["start_root"]) if state and state.get("start_root") else cwd_root
    if state is None or root is None:
        return None
    dirty = set(state["initially_dirty"])
    if state is own and main.get("start_root") == own.get("start_root"):
        dirty |= set(main.get("initially_dirty", []))
    head = state.get("start_head")
    head_lost = bool(head) and not gatelib.commit_exists(root, head)
    if "start_head" not in state or head_lost:
        head = gatelib.head_commit(root)
    worktrees = state.get("start_worktrees")
    known = None if worktrees is None else frozenset(worktrees)
    return Start(root, head, frozenset(dirty), state.get("started_at"), head_lost, known)


def tool_edited(state: dict) -> set[Path]:
    """Files the agent changed through Edit/Write that still exist."""
    edits = state.get("edits") or {}
    return {Path(path) for path, entry in edits.items() if entry.get("seq", 0) > 0 and Path(path).exists()}


def _roots(known: set[Path], edited: set[Path]) -> set[Path]:
    roots = set(known)
    for directory in sorted({path.parent for path in edited}):
        root = next((root for root in roots if directory.is_relative_to(root)), None)
        root = root or kitlib.project_root(str(directory))
        if root:
            roots.add(root)
    return roots


def _with_start(session: Session, root: Path, start: Start) -> RepoChanges:
    others = set() if session.is_main else {path for state in session.others() for path in tool_edited(state)}
    changed = {
        rel: base_rel
        for rel, base_rel in _changed_since_start(root, start).items()
        if rel not in start.dirty and root / rel not in others
    }
    # Files the agent edited itself count even when they were dirty at the start; their baseline is the snapshot.
    own = {gatelib.rel_path(root, path) for path in tool_edited(session.own) if _inside(path, root)}
    changes = {rel: rel for rel in own} | changed
    findings = tuple(_history_findings(root, start))
    return RepoChanges(root, changes, start.head, start.dirty, True, findings, start.started_at)


def _changed_since_start(root: Path, start: Start) -> dict[str, str]:
    changed = gatelib.changed_since(root, start.head)
    if start.head_lost and start.head and start.started_at is not None:
        # Without the start commit, what was committed during the session is known only by commit time.
        changed |= {rel: rel for rel in gatelib.files_committed_since(root, start.started_at, "HEAD")}
    return changed


def _history_findings(root: Path, start: Start) -> list[str]:
    """Session work that the working tree no longer shows but the repo still holds, or a lost baseline."""
    findings = [_LOST_HISTORY] if start.head_lost else []
    if start.started_at is None:
        return findings
    for name, files in gatelib.stashes_since(root, start.started_at).items():
        findings.append(_STASHED.format(name=name, files=", ".join(files) or "no files"))
    commits = gatelib.commits_not_checked_out(root, start.started_at)
    if commits:
        branches = ", ".join(gatelib.branches_containing(root, commits[0])) or "no branch"
        shown = ", ".join(commit[:10] for commit in commits[:3]) + (" and more" if len(commits) > 3 else "")
        findings.append(_NOT_CHECKED_OUT.format(commits=shown, branches=branches))
    return findings


def _edits_only(root: Path, edited: set[Path]) -> RepoChanges:
    rels = {gatelib.rel_path(root, path) for path in edited if _inside(path, root)}
    return RepoChanges(root, {rel: rel for rel in rels}, "HEAD", frozenset(), False)


def review_checkouts(session: Session, cwd_root: Path | None, repos: list[RepoChanges]) -> list[RepoChanges]:
    """The checkouts a review can count for: `repos`, which the stop checks, and every worktree of the start repo. A
    worktree made during the session holds its whole change since then, Bash-made and committed included; one that
    was there at the start holds only what Edit/Write changed in it, since its earlier state wasn't recorded."""
    start = _start(session, cwd_root)
    anchor = start.root if start else cwd_root
    if anchor is None:
        return repos
    found = {repo.root: repo for repo in repos}
    landed = gatelib.head_commit(anchor)
    for worktree in gatelib.worktrees(anchor):
        root = Path(worktree["worktree"]).resolve()
        if "bare" in worktree or root == anchor or not root.is_dir() or kitlib.is_scratch(root):
            continue
        if start and start.worktrees is not None and str(root) not in start.worktrees:
            found[root] = _project_part(_new_worktree(root, landed, start.started_at))
        else:
            found.setdefault(root, _edits_only(root, set()))
    return [found[root] for root in sorted(found)]


def _new_worktree(root: Path, landed: str | None, started_at: int | None) -> RepoChanges:
    """A worktree made during the session: its change since it was made, its oldest HEAD move. Once its HEAD has
    landed in the start checkout's history, only what's uncommitted, since the start repo's check covers the rest."""
    head = gatelib.head_commit(root)
    if head and landed and gatelib.is_ancestor(root, head, landed):
        return RepoChanges(root, gatelib.changed_since(root, head), head, frozenset(), True)
    moves = gatelib.head_moves(root)
    made_at = moves[0].commit if moves else head
    return RepoChanges(root, gatelib.changed_since(root, made_at), made_at, frozenset(), True, (), started_at)


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root)
