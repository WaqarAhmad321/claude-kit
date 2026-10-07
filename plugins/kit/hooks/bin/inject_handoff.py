"""SessionStart after a compaction or resume: give Claude the handoff notes in the project's `.claude/HANDOFF.md`."""

import datetime
import json
from pathlib import Path

import kitlib

FAIL_CLOSED = False

_SOURCES = frozenset({"compact", "resume"})
_HANDOFF_REL_PATH = Path(".claude") / "HANDOFF.md"
_NOTES_LIMIT_CHARS = 6000


@kitlib.hook_main("inject_handoff", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    if inp.get("source") not in _SOURCES:
        return
    project = kitlib.session_project()
    if not project:
        return
    path = Path(project) / _HANDOFF_REL_PATH
    if not path.is_file():
        return
    notes = path.read_text(encoding="utf-8", errors="replace").strip()
    if not notes:
        return
    context = f"{_header(path)}\n{_capped(notes, path)}"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))


def _header(path: Path) -> str:
    saved_at = datetime.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="minutes")
    return f"Handoff notes from {path}, written before a compaction or resume (last saved {saved_at}):"


def _capped(notes: str, path: Path) -> str:
    if len(notes) <= _NOTES_LIMIT_CHARS:
        return notes
    marker = f"\n[truncated; the rest is in {path}]"
    return notes[: _NOTES_LIMIT_CHARS - len(marker)] + marker


if __name__ == "__main__":
    main()
