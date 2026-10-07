"""Stop, SubagentStop: refuse to finish while this session's changes break the lint or type ratchet,
leave the journeys stale, or change quality-gate config without telling the user. On the main Stop, a
non-trivial change also needs the reviewer's VERDICT: PASS."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

import gatelib
import kitlib
import reviewlib
import stopscope
from lint_edited_file import _describe

FAIL_CLOSED = True

_REPORT_LIMIT = 6000
_LINT, _TYPES, _JOURNEYS = "New lint problems", "New type errors", "Stale journeys"
_CONFIG, _INVALID, _HISTORY = "Gate config changed", "Invalid kit config", "Session history"
_REVIEW = "Unreviewed change"
_REVIEW_FAILED = "Review check failed"
_STREAK = "stop_gate_streak"
_REPORT_NAME = "stop"
_KIT_CONFIG = ".claude/kit.json"
_HEADING = "kit stop gate: this session's changes don't pass yet."
_CLOSING = "Fix these in the code (no suppression comments, no config changes), re-run the checks, then finish."
_NO_BASELINE = "typecheck: no baseline (run kit-baseline); type errors not gated"
_DEFERRED = "Deferred until the background agents still editing them finish; their own stop checks them: {files}"
_REVIEW_SUMMARY = "Non-trivial change ({size}) without an independent PASS review."
_REVIEW_ACTION = "Run the kit:reviewer agent on the current diff; fix FIX findings and re-run until PASS."
_REVIEW_GIT_FAILED = (
    "git failed while reading the working state, so the review can't be checked: {error}. Fix the cause (such as "
    "an unreadable file, or a nested repository without a commit), or tell Waqar."
)


@dataclass
class Report:
    """Findings per section. A section of a repo other than the working directory's names that repo in its heading,
    so every path in it stays relative to its repo root."""

    sections: dict[str, list[str]] = field(default_factory=dict)
    kinds: dict[str, None] = field(default_factory=dict)
    files: set[str] = field(default_factory=set)
    identity: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    user_notices: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)  # Files deferred to the running agents editing them.
    unreviewed_lines: int = 0

    def add(self, kind: str, where: str, lines: list[str], files: list[str], identity) -> None:
        heading = f"{kind} in {where}" if where else kind
        self.sections.setdefault(heading, []).extend(lines)
        self.kinds[kind] = None
        self.files.update(f"{where}/{rel}" for rel in files)
        self.identity.append([kind, where, files, identity])

    def add_violations(self, kind: str, where: str, rel: str, introduced: Counter, found: list) -> None:
        lines = [f"{rel}:"] + [
            _describe(fingerprint, count, [v for v in found if v.fingerprint == fingerprint])
            for fingerprint, count in sorted(introduced.items())
        ]
        self.add(kind, where, lines, [rel], sorted([list(fp), count] for fp, count in introduced.items()))

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()[:16]


@kitlib.hook_main("stop_gate", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    session = stopscope.load_session(inp)
    # SubagentStop also fires for Claude Code's internal agents; an agent that edited nothing is left to the main gate.
    if not session.is_main and not session.own.get("edit_seq"):
        return
    cwd_root = kitlib.project_root()
    report, updates = Report(), {}
    running = _running_subagents(inp)
    deferred = _edits_of_running_agents(session, running)
    repos, configs = stopscope.repo_changes(session, cwd_root), {}
    for repo in repos:
        where = "" if repo.root == cwd_root else str(repo.root)
        repo = _defer(repo, deferred, report)
        if repo.changed or repo.history_findings:
            _add_history(report, repo, where)
            configs[repo.root] = _check_repo(session, repo, where, report, updates)
    # A builder still running in the background hasn't finished the change, so it isn't ready for review yet.
    if session.is_main and not running:
        _check_reviews(session, repos, cwd_root, configs, report)
    _finish(inp, session, report, updates)


def _running_subagents(inp: dict) -> set[str]:
    """The agent ids of subagent tasks still in flight, from the Stop input's `background_tasks`."""
    tasks = [task for task in inp.get("background_tasks") or [] if isinstance(task, dict)]
    return {kitlib._safe_name(str(task.get("id"))) for task in tasks if task.get("type") == "subagent"}


def _edits_of_running_agents(session: stopscope.Session, ids: set[str]) -> set[Path]:
    """On the main Stop, files that subagents still running in the background edited."""
    if not session.is_main or not ids:
        return set()
    running = [session.states[name] for name in ids if name in session.states]
    if not running:
        # A subagent task's id is its agent_id; when none match, fall back to agents whose own stop hasn't passed.
        running = [state for name, state in session.states.items() if name != stopscope.MAIN and _unchecked(state)]
    return {path for state in running for path in stopscope.tool_edited(state)}


def _unchecked(state: dict) -> bool:
    return state.get("edit_seq", 0) > state.get("checked_edit_seq", 0)


def _defer(repo: stopscope.RepoChanges, deferred: set[Path], report: Report) -> stopscope.RepoChanges:
    held = sorted(rel for rel in repo.changed if repo.root / rel in deferred)
    if not held:
        return repo
    report.held.extend(held)
    return replace(repo, changed={rel: base for rel, base in repo.changed.items() if rel not in held})


def _add_history(report: Report, repo: stopscope.RepoChanges, where: str) -> None:
    for finding in repo.history_findings:
        report.add(_HISTORY, where, [finding], [], finding)


def _check_repo(session, repo, where: str, report: Report, updates: dict) -> dict:
    config = _kit_config(repo, where, report)
    candidates = sorted(
        rel for rel in repo.changed if (repo.root / rel).is_file() and not gatelib.is_exempt(repo.root, repo.root / rel)
    )
    jobs, baseline = _typecheck_plan(session, repo, candidates, where, report, updates)
    with ThreadPoolExecutor(max_workers=8) as pool:
        type_runs = [(job, pool.submit(gatelib.run_typecheck, repo.root, job)) for job in jobs]
        lint_runs = [(rel, pool.submit(_lint_file, repo, rel, _snapshot(session, repo, rel))) for rel in candidates]
        journeys = _journeys_finding(session, repo, config)
        for rel, future in lint_runs:
            _collect_lint(report, where, rel, future)
        for job, future in type_runs:
            _collect_types(report, job, future, baseline, where)
    if journeys:
        report.add(_JOURNEYS, where, [journeys[0]], journeys[1], journeys[1])
    _check_gate_config(session, repo, where, report, updates)
    return config


def _kit_config(repo, where: str, report: Report) -> dict:
    try:
        return kitlib.load_config(repo.root)
    except ValueError as error:
        _invalid(report, where, _KIT_CONFIG, error, "fix it")
        return {}


def _invalid(report: Report, where: str, rel: str, error: Exception, remedy: str) -> None:
    report.add(_INVALID, where, [f"{rel} is invalid: {error}; {remedy} or tell Waqar."], [rel], str(error))


def _snapshot(session, repo, rel: str) -> dict | None:
    """The pre-edit lint snapshot, used only where git can't show the file as it was at the start."""
    if repo.has_start and rel not in repo.dirty_at_start:
        return None
    key = str(repo.root / rel)
    for state in (session.own, *session.others()):
        recorded = (state.get("edits") or {}).get(key, {}).get("before", {}).get("lint")
        if recorded is not None:
            return recorded
    return None


def _lint_file(repo, rel: str, recorded: dict | None) -> tuple[Counter, list] | None:
    path = repo.root / rel
    if not kitlib.linters_for(repo.root, path):
        return None
    after = kitlib._violations(repo.root, path)
    if recorded is not None:
        before = Counter({kitlib._fingerprint_from_key(key): count for key, count in recorded.items()})
    else:
        text = gatelib.file_at(repo.root, repo.base_commit, repo.changed[rel])
        found = gatelib.text_violations(repo.root, path, text) if text is not None else []
        before = Counter(violation.fingerprint for violation in found)
    return Counter(violation.fingerprint for violation in after) - before, after


def _collect_lint(report: Report, where: str, rel: str, future) -> None:
    try:
        result = future.result()
    except subprocess.TimeoutExpired as error:
        report.add(_LINT, where, [f"{rel}: lint timed out after {error.timeout:.0f} s"], [rel], "timeout")
        return
    except (RuntimeError, ValueError) as error:
        report.add(_LINT, where, [f"{rel}: {error}"], [rel], "error")
        return
    if result and result[0]:
        report.add_violations(_LINT, where, rel, *result)


def _typecheck_plan(session, repo, candidates, where: str, report: Report, updates: dict) -> tuple[list, dict | None]:
    jobs = gatelib.typecheck_jobs(repo.root, [repo.root / rel for rel in candidates])
    if not jobs:
        return [], None
    try:
        baseline = gatelib.load_baseline(repo.root)
    except ValueError as error:
        _invalid(report, where, gatelib.BASELINE_REL, error, "regenerate it with kit_baseline.py")
        return [], None
    baselined = set((baseline or {}).get("tools", {}))
    shown = session.own.get("typecheck_notice_shown") or updates.get("typecheck_notice_shown")
    if any(job.tool not in baselined for job in jobs) and not shown:
        report.notes.append(_NO_BASELINE)
        updates["typecheck_notice_shown"] = True
    return [job for job in jobs if job.tool in baselined], baseline


def _collect_types(report: Report, job, future, baseline: dict, where: str) -> None:
    try:
        by_file = future.result()
    except subprocess.TimeoutExpired:
        line = f"typecheck timed out ({job.tool} after {gatelib.TYPECHECK_TIMEOUT} s)"
        report.add(_TYPES, where, [line], [], [job.tool, "timeout"])
        return
    except RuntimeError as error:
        report.add(_TYPES, where, [f"typecheck: {error}"], [], [job.tool, "error"])
        return
    for rel, found in sorted(by_file.items()):
        introduced = Counter(v.fingerprint for v in found) - gatelib.baseline_counter(baseline, rel)
        if introduced:
            report.add_violations(_TYPES, where, rel, introduced, found)


def _journeys_finding(session, repo, config: dict) -> tuple[str, list[str]] | None:
    journeys = gatelib.journeys_config(config)
    if journeys is None:
        return None
    matches = gatelib.watch_matcher(journeys["watch"])
    watched = sorted(rel for rel in repo.changed if matches(rel))
    if not watched:
        return None
    # A green run by any agent of the session counts: the hash pins the exact content that passed.
    green = {(state.get("journey_passes") or {}).get(str(repo.root)) for state in session.states.values()} - {None}
    if green and gatelib.watched_fingerprint(repo.root, journeys["watch"]) in green:
        return None
    message = (
        f"Run `{journeys['command']}` and make it pass before finishing: "
        f"these watched files changed since the last green run: {', '.join(watched)}. "
        "Run it as its own command; piping it only counts after `set -o pipefail`."
    )
    return message, watched


def _check_gate_config(session, repo, where: str, report: Report, updates: dict) -> None:
    current = {}
    for rel, base_rel in sorted(repo.changed.items()):
        key = (
            None if rel in repo.dirty_at_start else gatelib.gate_config_key(repo.root, rel, repo.base_commit, base_rel)
        )
        if key is not None:
            current[str(repo.root / rel)] = (rel, key)
    blocked = updates.setdefault("tamper_blocked", dict(session.own.get("tamper_blocked", {})))
    notified = updates.setdefault("tamper_notified", dict(session.own.get("tamper_notified", {})))
    unreported = {path: value for path, value in current.items() if blocked.get(path) != value[1]}
    if unreported:
        rels = [rel for rel, _ in unreported.values()]
        line = f"Tell the user you changed {', '.join(rels)} and why; they must approve it."
        report.add(_CONFIG, where, [line], rels, rels)
        blocked.update({path: key for path, (_, key) in unreported.items()})
        return
    unnotified = {path: value for path, value in current.items() if notified.get(path) != value[1]}
    if unnotified:
        shown = [f"{Path(where).name}/{rel}" if where else rel for rel, _ in unnotified.values()]
        report.user_notices.append(
            f"kit: Claude changed quality-gate config this session: {stopscope.listed(shown)}. Review it."
        )
        notified.update({path: key for path, (_, key) in unnotified.items()})


def _check_reviews(session, repos, cwd_root: Path | None, configs: dict, report: Report) -> None:
    """The review check on every checkout a review can count for, worktrees changed only through Bash included."""
    try:
        checkouts = stopscope.review_checkouts(session, cwd_root, repos)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        report.add(_REVIEW_FAILED, "", [_REVIEW_GIT_FAILED.format(error=error)], [], str(error))
        return
    for repo in checkouts:
        if not repo.changed:
            continue
        where = "" if repo.root == cwd_root else str(repo.root)
        config = configs[repo.root] if repo.root in configs else _kit_config(repo, where, report)
        _check_review(session, repo, where, config, report)


def _check_review(session, repo, where: str, config: dict, report: Report) -> None:
    try:
        settings = reviewlib.review_config(_review_source(repo, config))
    except ValueError as error:
        _invalid(report, where, _KIT_CONFIG, error, "fix it")
        return
    try:
        finding = reviewlib.unreviewed(repo, settings, session.own.get(reviewlib.STATE_KEY) or {})
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        report.add(_REVIEW_FAILED, where, [_REVIEW_GIT_FAILED.format(error=error)], [], str(error))
        return
    if finding:
        report.add(_REVIEW, where, finding.report_lines(), list(finding.paths), finding.identity)
        report.unreviewed_lines += finding.size


def _review_source(repo, config: dict) -> dict:
    """The kit.json the review check reads: as the start commit has it once this session changed it, so a session
    can't turn off or loosen its own review. A start version that doesn't parse leaves the defaults."""
    if _KIT_CONFIG not in repo.changed:
        return config
    text = gatelib.file_at(repo.root, repo.base_commit, repo.changed[_KIT_CONFIG])
    try:
        before = json.loads(text) if text else {}
    except ValueError:
        return {}
    return before if isinstance(before, dict) else {}


def _finish(inp: dict, session: stopscope.Session, report: Report, updates: dict) -> None:
    with kitlib.State(inp) as st:
        st.update(updates)
        if not report.sections:
            # Files held back for a running agent aren't checked yet, so the streak outlives this stop. The user
            # isn't told: an orchestrating main thread would print that on nearly every stop.
            if not report.held:
                st.pop(_STREAK, None)
            st["checked_edit_seq"] = st.get("edit_seq", 0)
            notices = report.notes + report.user_notices
            if notices:
                kitlib.notify_user("\n".join(notices))
            return
        path = stopscope.write_report(inp, _REPORT_NAME, _full_text(report))
        release = stopscope.release(st, _STREAK, report.digest())
        if release:
            # Its stop let it finish, so a later main Stop no longer waits for this agent.
            st["checked_edit_seq"] = st.get("edit_seq", 0)
            if release.first:
                kitlib.notify_user(f"kit stop gate: {release.why}, so Claude may finish; still unresolved: {path}")
            return
        reason = _main_reason(report, path) if session.is_main else _render(report, path)
        payload = {"decision": "block", "reason": reason}
        if report.user_notices:
            payload["systemMessage"] = "\n".join(report.user_notices)
        kitlib._emit(payload)


def _main_reason(report: Report, path: Path) -> str:
    """Three short lines; an unreviewed change on its own says which agent to run."""
    if list(report.kinds) != [_REVIEW]:
        return stopscope.main_reason(_summary(report), path)
    size = f"{report.unreviewed_lines} line{'s' if report.unreviewed_lines != 1 else ''}"
    return "\n".join([_REVIEW_SUMMARY.format(size=size), _REVIEW_ACTION, f"Full report: {path}"])


def _summary(report: Report) -> str:
    kinds = ", ".join(kind.lower() for kind in report.kinds)
    count = len(report.files)
    where = f" in {count} file{'s' if count != 1 else ''}" if count else ""
    return f"kit stop gate: {kinds}{where}."


def _body(report: Report) -> list[str]:
    lines = []
    for heading, section_lines in report.sections.items():
        lines += ["", f"{heading}:", *section_lines]
    return lines


def _tail(report: Report) -> list[str]:
    deferred = [_DEFERRED.format(files=", ".join(report.held))] if report.held else []
    closing = [_CLOSING] if set(report.kinds) & {_LINT, _TYPES, _JOURNEYS} else []
    return [*report.notes, *deferred, *closing]


def _full_text(report: Report) -> str:
    return "\n".join([_HEADING, *_body(report), "", *_tail(report), *report.user_notices])


def _render(report: Report, path: Path) -> str:
    """A subagent's block: the findings themselves, capped, with the rest in the full report."""
    lines = stopscope.capped([_HEADING, *_body(report)], path)
    tail = _tail(report)
    ending = "\n\n" + "\n".join(tail) if tail else ""
    return kitlib._truncate("\n".join(lines), _REPORT_LIMIT - len(ending)) + ending


if __name__ == "__main__":
    main()
