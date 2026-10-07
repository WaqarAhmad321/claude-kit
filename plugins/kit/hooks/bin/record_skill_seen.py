"""Record which skills this agent has loaded, and forget raw SKILL.md reads after compaction (`--compact`).

PostToolUse Read of a whole project SKILL.md marks it as "read", PostToolUse Skill as "skill", and a typed
`/skill` (UserPromptExpansion) as "prompt".
"""

import os
import sys
from pathlib import Path

import kitlib
import kitskills

FAIL_CLOSED = False

_READ_DEFAULT_LIMIT = 2000  # Lines the Read tool returns when no limit is given.


@kitlib.hook_main("record_skill_seen", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    if "--compact" in sys.argv[1:]:
        _forget_reads(inp)
        return
    seen = _seen_skill(inp)
    if seen is None:
        return
    name, via = seen
    with kitlib.State(inp) as st:
        # A read never downgrades a skill invocation, whose content survives compaction.
        if via != "read" or name not in st["skills_seen"]:
            st["skills_seen"][name] = via


def _forget_reads(inp: dict) -> None:
    # Compaction re-attaches invoked skills but not the content of a plain Read.
    with kitlib.State(inp) as st:
        st["skills_seen"] = {name: via for name, via in st["skills_seen"].items() if via != "read"}


def _seen_skill(inp: dict) -> tuple[str, str] | None:
    if inp.get("hook_event_name") == "UserPromptExpansion":
        if inp.get("expansion_type") == "mcp_prompt":
            return None
        return _invoked(inp.get("command_name"), "prompt", inp.get("cwd"))
    tool_input = inp.get("tool_input") or {}
    if inp.get("tool_name") == "Skill":
        return _invoked(tool_input.get("skill"), "skill", inp.get("cwd"))
    if inp.get("tool_name") == "Read":
        return _read_skill_md(tool_input, inp.get("cwd"))
    return None


def _invoked(raw, via: str, cwd: str | None) -> tuple[str, str] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    namespace, _, name = raw.strip().lstrip("/").rpartition(":")
    root = kitlib.project_root()
    if namespace and not _is_project_namespace(namespace, cwd, root):
        # `someplugin:orders` is a plugin's skill, not this repo's .claude/skills/orders.
        return None
    return kitskills.canonical_name(root, name), via


def _is_project_namespace(namespace: str, cwd: str | None, root: Path | None) -> bool:
    """A directory-qualified name (`apps/web:deploy`) whose directory is this repo's root."""
    return root is not None and (Path(cwd or os.getcwd()) / namespace).resolve() == root


def _read_skill_md(tool_input: dict, cwd: str | None) -> tuple[str, str] | None:
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        return None
    # Not resolved: skill directories are often symlinks into a shared skills checkout.
    path = Path(os.path.abspath(Path(cwd or os.getcwd()) / Path(raw).expanduser()))
    if path.name != "SKILL.md" or path.parts[-4:-2] != (".claude", "skills"):
        return None
    project = path.parents[3]
    if kitlib.project_root(str(project)) != project.resolve() or not path.is_file():
        return None
    if not _covers_whole_file(tool_input, path):
        return None
    return kitskills.read_skill_file(path).name, "read"


def _covers_whole_file(tool_input: dict, path: Path) -> bool:
    # Read's offset is the line to start at, so 0 and 1 both start at the top.
    if tool_input.get("offset") not in (None, 0, 1):
        return False
    lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return (tool_input.get("limit") or _READ_DEFAULT_LIMIT) >= lines


if __name__ == "__main__":
    main()
