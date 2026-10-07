"""PreToolUse Edit|Write|NotebookEdit: deny editing a module's files until this agent has loaded the module's skill."""

import kitlib
import kitskills

FAIL_CLOSED = True


@kitlib.hook_main("guard_module_skill", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    path = kitlib._edited_path(inp)
    root = kitlib.project_root(str(path)) if path else None
    if root is None:
        return
    skills = kitskills.module_skills(root)
    # Through a symlinked module directory the path as written and the resolved path differ; either can match.
    matches = {
        rel: kitskills.matching_skills(skills, rel)
        for rel in kitlib._edited_rel_paths(inp, root)
        if not kitskills.is_exempt(rel)
    }
    matched = {skill.name: skill for found in matches.values() for skill in found}
    if not matched:
        return
    try:
        seen = _skills_seen(inp)
    except OSError as error:
        # Without state no skill read can ever be recorded, so a deny here would block the module for good.
        kitlib.notify_user(
            f"kit: the module-skill guard can't use its state ({type(error).__name__}: {error}), "
            f"so it allowed this edit. Make {kitlib._data_dir()} writable to turn the guard back on."
        )
    missing = [skill for name, skill in matched.items() if name not in seen]
    if missing:
        rel = next(rel for rel, found in matches.items() if found)
        kitlib.pre_tool_decision("deny", _reason(rel, missing))


def _skills_seen(inp: dict) -> dict:
    with kitlib.State(inp) as st:
        return dict(st["skills_seen"])


def _reason(rel: str, missing: list[kitskills.Skill]) -> str:
    steps = [f"- Read {skill.skill_md} (or invoke the {skill.name} skill)" for skill in missing]
    header = f"kit: {rel} belongs to a module whose skill you haven't loaded in this session."
    return "\n".join([header, *steps, "...then retry the edit."])


if __name__ == "__main__":
    main()
