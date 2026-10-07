"""PostCompact: append the compaction summary to `~/.claude/compact-log/<session_id>.md`, so the user sees what
compaction kept."""

import datetime
import os
import re
from pathlib import Path

import kitlib

FAIL_CLOSED = False

_LOG_REL_DIR = Path(".claude") / "compact-log"
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_LOG_DIR_MODE = 0o700
_LOG_FILE_MODE = 0o600


@kitlib.hook_main("log_compact", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    session_id, summary = inp.get("session_id"), inp.get("compact_summary")
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        return
    if not isinstance(summary, str) or not summary.strip():
        return
    _append(Path.home() / _LOG_REL_DIR / f"{session_id}.md", _entry(summary, inp.get("trigger")))


def _entry(summary: str, trigger) -> str:
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return f"## {stamp} (trigger: {trigger or 'unknown'})\n\n{summary.strip()}\n\n"


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(mode=_LOG_DIR_MODE, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, _LOG_FILE_MODE)
    with open(descriptor, "a", encoding="utf-8") as log:
        log.write(text)


if __name__ == "__main__":
    main()
