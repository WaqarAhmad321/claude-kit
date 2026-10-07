#!/usr/bin/env python3
"""PreToolUse hook: orchestrator mode.

Waqar's main conversation plans and talks; hands-on work (edits, commands,
code reading, web research, MCP calls) runs in subagents. Order of checks:
  1. Nobody, main thread or subagent, may create, change or remove the
     direct-mode toggle file. Only Waqar flips it, with `! touch` / `! rm`.
  2. Subagent calls (input carries agent_id) pass through untouched.
  3. Direct mode (toggle file exists, or KIT_DIRECT_MODE=1 in the session's
     environment for scripted `claude -p` runs) lets the main thread work.
  4. The main thread may still keep up its memory and plan files.
  5. Every other guarded tool call from the main thread is denied.
Stdlib only and fast. It fails open: a bug here must never lock the main chat.
"""
import json
import os
import sys

TOGGLE = "kit-direct-mode"
GUARDED = {
    "Edit", "Write", "NotebookEdit", "Bash", "PowerShell", "Monitor", "Read",
    "Grep", "Glob", "LSP", "WebFetch", "WebSearch", "ReadMcpResourceTool",
}
SHELL_TOOLS = {"Bash", "PowerShell", "Monitor"}
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
MAIN_THREAD_FILE_TOOLS = {"Read", "Write", "Edit"}

DENY_MAIN = (
    "Orchestrator mode: the main chat plans and talks; hands-on work runs in subagents. "
    "Delegate this to a subagent (Agent tool, model picked per the workflow rule, run in background) "
    "with a complete brief, then give Waqar a short plain-language result. "
    "(Waqar can enable direct mode with: ! touch ~/.claude/kit-direct-mode)"
)
DENY_TOGGLE = (
    "Orchestrator mode: only Waqar may switch direct mode on or off, by typing "
    "`! touch ~/.claude/kit-direct-mode` or `! rm ~/.claude/kit-direct-mode` himself. "
    "Don't create, change or remove that file; ask him if direct mode is needed."
)


def resolve(path):
    return os.path.realpath(os.path.expanduser(path))


def is_under(path, root):
    return path.startswith(root + os.sep)


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def touches_toggle(tool, tool_input, toggle):
    if tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        # Strip quotes and escapes so kit-direct-"mode" or kit\-direct-mode still count.
        flat = command.translate(str.maketrans("", "", "\"'\\`")).lower()
        return TOGGLE in flat
    if tool in WRITE_TOOLS:
        for key in ("file_path", "notebook_path"):
            path = tool_input.get(key)
            if isinstance(path, str) and path and resolve(path) == toggle:
                return True
    return False


def main_thread_may_touch(path, home):
    """Memory upkeep (projects/<project>/memory/) and plan files (plans/)."""
    real = resolve(path)
    if is_under(real, os.path.join(home, "plans")):
        return True
    projects = os.path.join(home, "projects")
    if not is_under(real, projects):
        return False
    parts = os.path.relpath(real, projects).split(os.sep)
    return len(parts) >= 3 and parts[1] == "memory"


def main():
    data = json.loads(sys.stdin.read() or "{}")
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    home = resolve(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    toggle = resolve(os.path.join(home, TOGGLE))

    if touches_toggle(tool, tool_input, toggle):
        deny(DENY_TOGGLE)
    if data.get("agent_id"):
        return
    if tool not in GUARDED and not tool.startswith("mcp__"):
        return
    if os.path.lexists(toggle) or os.environ.get("KIT_DIRECT_MODE") == "1":
        return
    if tool in MAIN_THREAD_FILE_TOOLS:
        path = tool_input.get("file_path")
        if isinstance(path, str) and path and main_thread_may_touch(path, home):
            return
    deny(DENY_MAIN)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # fail open, visibly
        print(json.dumps({"systemMessage": f"orchestrator guard error: {type(error).__name__}: {error}"}))
    sys.exit(0)
