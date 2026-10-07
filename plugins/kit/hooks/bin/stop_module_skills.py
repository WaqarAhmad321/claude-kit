"""Stop and SubagentStop: refuse to finish while files changed in this session belong to module skills that weren't
loaded. It catches edits made through Bash, which the PreToolUse guard never sees.

It also ratchets ARCHITECTURE.md coverage: a module skill this session created still needs one to finish; a module
skill this session only touched gets a reminder instead."""

from pathlib import PurePosixPath

import gatelib
import kitlib
import kitskills
import stopscope

FAIL_CLOSED = True

_MAX_FILES_LISTED = 5
_STREAK = "module_skills_streak"
_REPORT_NAME = "module-skills"
_HEADING = "kit: you changed files that belong to module skills you haven't loaded:"
_CLOSING = "Read the skill, re-check your change against it, then finish."

_ARCH_FILE = "ARCHITECTURE.md"
_SKILLS_DIR = ".claude/skills/"
_SKILL_MD = "SKILL.md"
_SKILL_MD_DEPTH = 4  # .claude/skills/<directory>/SKILL.md
_ARCH_STREAK = "module_architecture_streak"
_ARCH_REMINDED = "architecture_reminded"


@kitlib.hook_main("stop_module_skills", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    session = stopscope.load_session(inp)
    if not session.is_main and not stopscope.has_start(session.own):
        # No SubagentStart snapshot: an internal agent (prompt suggestions, /btw) that edits nothing.
        return
    repos = stopscope.repo_changes(session, kitlib.project_root())
    missing = _missing(repos, _skills_seen(session))
    with kitlib.State(inp) as st:
        if missing:
            path = stopscope.write_report(inp, _REPORT_NAME, "\n".join([*_lines(missing, limit=None), _CLOSING]))
            findings = {skill.name: paths for skill, paths in sorted(missing.items(), key=lambda item: item[0].name)}
            release = stopscope.release(st, _STREAK, findings)
            if release:
                if release.first:
                    kitlib.notify_user(
                        f"kit: {release.why}, so Claude may finish, but changed files still belong to module skills "
                        f"that weren't loaded ({stopscope.listed(list(findings))}): {path}"
                    )
                return
            kitlib.stop_block(_main_reason(missing, path) if session.is_main else _reason(missing, path))
        st.pop(_STREAK, None)

        new_modules, old_modules = _architecture_gaps(repos)
        if new_modules:
            names = sorted(skill.name for skill in new_modules)
            release = stopscope.release(st, _ARCH_STREAK, names)
            if release:
                if release.first:
                    kitlib.notify_user(
                        f"kit: {release.why}, so Claude may finish, but new module skill(s) still lack "
                        f"ARCHITECTURE.md ({stopscope.listed(names)})."
                    )
                return
            kitlib.stop_block(_new_module_reason(new_modules))
        st.pop(_ARCH_STREAK, None)

        reminded = {name for state in session.states.values() for name in state.get(_ARCH_REMINDED) or []}
        due = [skill for skill in old_modules if skill.name not in reminded]
        if due:
            st[_ARCH_REMINDED] = sorted({*(st.get(_ARCH_REMINDED) or []), *(skill.name for skill in due)})
            kitlib.notify_user(_architecture_reminder(due))


def _skills_seen(session: stopscope.Session) -> set[str]:
    """The main agent answers for its subagents' work, so a skill any of them loaded counts there."""
    states = session.states.values() if session.is_main else [session.own]
    return {name for state in states for name in state.get("skills_seen") or {}}


def _missing(repos: list, seen: set[str]) -> dict:
    missing: dict = {}
    for repo in repos:
        skills = kitskills.module_skills(repo.root)
        for rel in sorted(repo.changed):
            if kitskills.is_exempt(rel):
                continue
            for skill in kitskills.matching_skills(skills, rel):
                if skill.name not in seen:
                    missing.setdefault(skill, []).append(rel)
    return missing


def _main_reason(missing: dict, path) -> str:
    names = sorted(skill.name for skill in missing)
    count = len({rel for paths in missing.values() for rel in paths})
    summary = f"kit: {count} changed file{'s' if count != 1 else ''} belong to module skills nobody loaded: "
    return stopscope.main_reason(summary + f"{stopscope.listed(names)}.", path)


def _reason(missing: dict, path) -> str:
    return "\n".join([*stopscope.capped(_lines(missing, _MAX_FILES_LISTED), path), _CLOSING])


def _lines(missing: dict, limit: int | None) -> list[str]:
    lines = [_HEADING]
    for skill, paths in sorted(missing.items(), key=lambda item: item[0].name):
        shown = paths if limit is None else paths[:limit]
        more = f" and {len(paths) - len(shown)} more" if len(paths) > len(shown) else ""
        lines.append(f"- {skill.name} ({skill.skill_md}): {', '.join(shown)}{more}")
    return lines


def _architecture_gaps(repos: list) -> tuple[list[kitskills.Skill], list[kitskills.Skill]]:
    """Module skills this session touched that still lack ARCHITECTURE.md, split into (created this session,
    already existed). A skill is new only when no SKILL.md at the base commit has its name, so moving a skill's
    folder doesn't make it new."""
    new, existing = [], []
    for repo in repos:
        gaps = [skill for skill in kitskills.module_skills(repo.root) if _lacks_architecture(repo, skill)]
        names_at_base = _skill_names_at(repo.root, repo.base_commit) if gaps else set()
        for skill in gaps:
            (existing if skill.name in names_at_base else new).append(skill)
    return new, existing


def _lacks_architecture(repo, skill: kitskills.Skill) -> bool:
    prefix = f".claude/skills/{skill.directory}/"
    touched = any(rel.startswith(prefix) for rel in repo.changed)
    return touched and not (repo.root / prefix / _ARCH_FILE).is_file()


def _skill_names_at(root, commit: str | None) -> set[str]:
    if commit is None or not gatelib.commit_exists(root, commit):  # No commit yet: every skill is new.
        return set()
    listed = gatelib._git(root, "ls-tree", "-r", "-z", "--name-only", commit, "--", _SKILLS_DIR)
    names = set()
    for rel in listed.split("\0"):
        parts = PurePosixPath(rel).parts
        if len(parts) == _SKILL_MD_DEPTH and parts[-1] == _SKILL_MD:
            text = gatelib.file_at(root, commit, rel) or ""
            names.add(kitskills.skill_from_text(text, parts[-2]).name)
    return names


def _new_module_reason(skills: list[kitskills.Skill]) -> str:
    names = sorted(skill.name for skill in skills)
    plural = "s" if len(names) != 1 else ""
    return (
        f"kit: new module skill{plural} without ARCHITECTURE.md: {stopscope.listed(names)}. Add "
        ".claude/skills/<module>/ARCHITECTURE.md from the skills-bootstrap template, then finish."
    )


def _architecture_reminder(skills: list[kitskills.Skill]) -> str:
    names = sorted(skill.name for skill in skills)
    plural = "s" if len(names) != 1 else ""
    return f"kit: module skill{plural} missing ARCHITECTURE.md: {stopscope.listed(names)}. Add it in a future change."


if __name__ == "__main__":
    main()
