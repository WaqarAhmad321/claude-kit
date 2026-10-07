"""PostToolUse Bash: record a green run of a repository's journeys command for the stop gate."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

import gatelib
import kitlib
import shellwords

FAIL_CLOSED = False

_PIPES = frozenset({"|", "|&"})
_KEEPS_FAILURE = frozenset({"&&", "|", "|&", None})
_UNSAFE_PREFIXES = frozenset({"!", "{", "if", "then", "else", "elif", "do", "while", "until"})
_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?")
_EXIT_STATUS = re.compile(r"\$(?:\?|\{\?\})")


@dataclass(frozen=True)
class _Step:
    words: list
    operator: str | None  # the operator after this simple command
    redirected: bool


@kitlib.hook_main("record_journey_pass", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    tool_input = inp.get("tool_input") or {}
    if inp.get("hook_event_name", "PostToolUse") != "PostToolUse" or inp.get("tool_name", "Bash") != "Bash":
        return
    if not finished_in_foreground(tool_input, inp.get("tool_response")):
        return
    cwd = Path(inp.get("cwd") or os.getcwd())
    output = inp["tool_response"].get("stdout") or ""
    for directory, words in decisive_commands(tool_input.get("command") or "", cwd, output):
        _record_if_journeys(inp, directory, words)


def _record_if_journeys(inp: dict, directory: Path, words: list[str]) -> None:
    root = kitlib.project_root(str(directory))
    if root is None:
        return
    try:
        journeys = gatelib.journeys_config(kitlib.load_config(root))
    except ValueError:
        return  # The stop gate reports an invalid kit.json.
    if journeys is None or words != journey_words(journeys["command"]):
        return
    fingerprint = gatelib.watched_fingerprint(root, journeys["watch"])
    with kitlib.State(inp) as st:
        st.setdefault("journey_passes", {})[str(root)] = fingerprint


def finished_in_foreground(tool_input: dict, response) -> bool:
    """PostToolUse only fires for Bash exit 0, but a backgrounded or interrupted command hasn't finished."""
    if tool_input.get("run_in_background") or not isinstance(response, dict) or response.get("interrupted"):
        return False
    if any("background" in key.lower() and value for key, value in response.items()):
        return False
    output = f"{response.get('stdout') or ''}\n{response.get('stderr') or ''}"
    return "moved to the background" not in output


def journey_words(command: str) -> list[str] | None:
    commands, _ = shellwords.parse(command)
    return _command_words(commands[-1].words) if commands else None


def decisive_commands(command: str, cwd: Path, output: str = "") -> list[tuple[Path, list[str]]]:
    """Each simple command whose failure fails the whole Bash call, with the directory it ran in."""
    steps = _without_zero_status_echo(_steps(command), output)
    found, directory, pipefail = [], cwd, False
    for index, step in enumerate(steps):
        names = [word.text for word in step.words]
        if names[0] == "cd" and len(step.words) == 2 and step.words[1].literal:
            directory = shellwords.resolve_path(directory, step.words[1])
        elif names[0] == "set":
            pipefail = _pipefail_after(names, pipefail)
        elif _is_decisive(steps, index, pipefail):
            command_words = _command_words(step.words)
            if command_words:
                found.append((directory, command_words))
    return found


def _steps(command: str) -> list[_Step]:
    """Simple commands with the operator that follows each; subshell parentheses don't change exit statuses."""
    # shellwords.parse drops the operators, so read its token stream.
    text, _ = shellwords._cut_heredocs(command)
    steps, words, redirected = [], [], False
    tokens = iter(shellwords._tokens(text))
    for token in tokens:
        if isinstance(token, shellwords.Word):
            words.append(token)
        elif token in ("(", ")"):
            continue
        elif token in shellwords._SEPARATORS:
            steps.append(_Step(words, token, redirected))
            words, redirected = [], False
        else:
            next(tokens, None)  # A redirection and its target.
            redirected = True
    steps.append(_Step(words, None, redirected))
    return [step for step in steps if step.words]


def _without_zero_status_echo(steps: list[_Step], output: str) -> list[_Step]:
    """`cmd; echo "exit=$?"` exits 0 whatever cmd did, so it stands for `cmd` alone only when the echo printed 0."""
    if len(steps) < 2 or steps[-2].operator != ";" or not _echoed_zero_status(steps[-1], output):
        return steps
    return [*steps[:-2], replace(steps[-2], operator=None)]


def _echoed_zero_status(step: _Step, output: str) -> bool:
    names = [word.text for word in step.words]
    if step.redirected or names[0] != "echo":
        return False
    printed = " ".join(names[1:])
    expected = _EXIT_STATUS.sub("0", printed)
    if expected == printed or "$" in expected or "`" in expected:
        return False
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == expected.strip()


def _is_decisive(steps: list[_Step], index: int, pipefail: bool) -> bool:
    first = last = index
    while first > 0 and steps[first - 1].operator in _PIPES:
        first -= 1
    while steps[last].operator in _PIPES:
        last += 1
    may_be_skipped = first > 0 and steps[first - 1].operator == "||"
    status_lost_in_pipe = last != index and not pipefail
    later_status_wins = any(step.operator not in _KEEPS_FAILURE for step in steps[last:])
    return not (may_be_skipped or status_lost_in_pipe or later_status_wins)


def _pipefail_after(names: list[str], pipefail: bool) -> bool:
    for flag, option in zip(names[1:], names[2:], strict=False):
        if option == "pipefail" and flag[:1] in "-+" and flag.endswith("o"):
            pipefail = flag.startswith("-")
    return pipefail


def _command_words(words: list) -> list[str] | None:
    """The words from the command name on, or None when a prefix like `!` changes what its exit status means."""
    rest = shellwords.command_words(shellwords.SimpleCommand(list(words)))
    if any(word.text in _UNSAFE_PREFIXES for word in words[: len(words) - len(rest)]):
        return None
    names = [word.text for word in rest]
    if names[:1] == ["timeout"] and len(names) > 2 and _DURATION.fullmatch(names[1]):
        names = names[2:]
    return names
