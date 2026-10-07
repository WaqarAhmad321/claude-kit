"""SubagentStop, and PostToolUse on SubagentHandback: record the kit reviewer's verdict with the working state of the
checkout it reviewed, for the stop gate's review check. A reviewer that reports through SubagentHandback (auto
mode) ends its SubagentStop with closing text instead of the report, so the report is read from the hand-back too."""

import subprocess
from typing import NoReturn

import kitlib
import reviewlib
import stopscope

FAIL_CLOSED = False

REVIEWER_TYPES = frozenset({"reviewer", "kit:reviewer", "kit-reviewer"})
_HANDBACK_TOOL = "SubagentHandback"
_NOT_RECORDED = (
    "kit: the reviewer's VERDICT: {verdict} wasn't recorded for {root}: {error}. The stop gate reports it too."
)
_UNATTRIBUTED = (
    "kit: the reviewer's VERDICT: {verdict} counts for no checkout. Its report needs a `Reviewed:` line with the "
    "absolute path of the one checkout of this session it reviewed; only a session with one checkout may leave the "
    "line out.{also}"
)
_PASSES_DROPPED = " Every earlier PASS of this session is dropped, since this FIX may be about any of them."


@kitlib.hook_main("record_review", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    if inp.get("agent_type") not in REVIEWER_TYPES:
        return
    report = _report(inp)
    verdict = reviewlib.parse_verdict(report)
    if verdict is None:
        return
    # The review belongs to the main thread, which answers for the whole session's change at its Stop.
    main_input = {"session_id": inp.get("session_id")}
    session = stopscope.load_session(main_input)
    cwd_root = kitlib.project_root()
    checkouts = stopscope.review_checkouts(session, cwd_root, stopscope.repo_changes(session, cwd_root))
    repo = reviewlib.reviewed_checkout(report, checkouts, cwd_root)
    if repo is None:
        _unattributed(main_input, verdict)
    try:
        record = reviewlib.review_record(repo, verdict)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        kitlib.notify_user(_NOT_RECORDED.format(verdict=verdict, root=repo.root, error=error))
    with kitlib.State(main_input) as st:
        st[reviewlib.STATE_KEY] = reviewlib.with_verdict(st.get(reviewlib.STATE_KEY) or {}, repo.root, record)


def _unattributed(main_input: dict, verdict: str) -> NoReturn:
    if verdict == reviewlib.FIX:
        with kitlib.State(main_input) as st:
            st[reviewlib.STATE_KEY] = reviewlib.without_passes(st.get(reviewlib.STATE_KEY) or {})
    also = _PASSES_DROPPED if verdict == reviewlib.FIX else ""
    kitlib.notify_user(_UNATTRIBUTED.format(verdict=verdict, also=also))


def _report(inp: dict) -> str | None:
    event = inp.get("hook_event_name")
    if event == "SubagentStop":
        return inp.get("last_assistant_message")
    if event == "PostToolUse" and inp.get("tool_name") == _HANDBACK_TOOL:
        return (inp.get("tool_input") or {}).get("message")
    return None


if __name__ == "__main__":
    main()
