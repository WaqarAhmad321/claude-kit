"""SessionStart: record where the repo stood at startup (HEAD, pinned so a history rewrite can't lose it, the files
already dirty and its worktrees) and give Claude plain facts about the kit in this repo.

SubagentStart: record the same for the subagent, so its stop checks cover only its own run.
"""

import json
import time
from pathlib import Path

import gatelib
import kitlib
import kitskills

FAIL_CLOSED = False

_CONTEXT_LIMIT = 1500
_MORE_SKILLS_RESERVE = 60
_ENFORCEMENT = (
    "- The kit plugin enforces lint-on-edit, reading a module's skill before editing its files, "
    "secret scanning, and the stop gates."
)


@kitlib.hook_main("session_context", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    root = kitlib.project_root()
    if root is None:
        return
    if inp.get("hook_event_name") == "SubagentStart":
        _record_start(inp, root)
        return
    _record_start({"session_id": inp.get("session_id")}, root)
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": _context(root)}}
    print(json.dumps(output))


def _record_start(state_input: dict, root) -> None:
    # A resume, clear or compact reruns this hook; what was recorded at startup must survive it.
    with kitlib.State(state_input) as st:
        if "initially_dirty" not in st:
            st["initially_dirty"] = sorted(kitskills.dirty_files(root))
        st.setdefault("start_root", str(root))
        st.setdefault("started_at", int(time.time()))
        if "start_worktrees" not in st:
            st["start_worktrees"] = sorted(str(Path(tree["worktree"]).resolve()) for tree in gatelib.worktrees(root))
        if "start_head" not in st:
            st["start_head"] = gatelib.head_commit(root)
            if st["start_head"]:
                label = "-".join(filter(None, (state_input.get("session_id"), state_input.get("agent_id"))))
                gatelib.pin_commit(root, st["start_head"], label)


def _context(root) -> str:
    lines = ["kit plugin facts for this repo:", f"- Git branch: {_branch(root)}", *_journeys_lines(root), _ENFORCEMENT]
    lines += _skill_lines(root, _CONTEXT_LIMIT - len("\n".join(lines)) - 1)
    return kitlib._truncate("\n".join(lines), _CONTEXT_LIMIT)


def _journeys_lines(root) -> list[str]:
    try:
        journeys = gatelib.journeys_config(kitlib.load_config(root))
    except ValueError as error:
        return [f"- .claude/kit.json is invalid ({error}); the stop gate will ask for it to be fixed."]
    return [f"- Journeys command (stop gate): {journeys['command']}"] if journeys else []


def _skill_lines(root, budget: int) -> list[str]:
    skills = kitskills.module_skills(root)
    if not skills:
        return []
    lines = ["- Module skills (read the SKILL.md or invoke the skill before editing matching files):"]
    budget -= len(lines[0]) + _MORE_SKILLS_RESERVE
    for skill in skills:
        entry = f"  - {skill.name}: {', '.join(skill.paths)}"
        if len(entry) + 1 > budget:
            lines.append(f"  - ...and {len(skills) - len(lines) + 1} more in .claude/skills/")
            break
        lines.append(entry)
        budget -= len(entry) + 1
    return lines


def _branch(root) -> str:
    code, out, _ = kitlib.run(["git", "branch", "--show-current"], root, timeout=10)
    if code == 0 and out.strip():
        return out.strip()
    code, out, _ = kitlib.run(["git", "rev-parse", "--short", "HEAD"], root, timeout=10)
    return f"detached HEAD at {out.strip()}" if code == 0 and out.strip() else "unknown"


if __name__ == "__main__":
    main()
