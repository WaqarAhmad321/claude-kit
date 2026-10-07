"""A small POSIX-shell reader for hooks: split a Bash tool command into simple commands and words.

It knows quoting, $(...)/${...}/backticks, heredocs, redirections and command separators, and marks every
word the shell would expand, so callers can tell a literal from something only known at run time.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

_OPERATORS = ("<<<", "&>>", "&&", "||", ";;", "|&", "<<", ">>", "&>", ">&", "<&", ">|", "<>", *"&|;()<>")
_SEPARATORS = frozenset({"&&", "||", ";;", "|&", "&", "|", ";", "(", ")"})
_PIPES = frozenset({"|", "|&"})
_WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>", ">&"})
_FD_TARGET = re.compile(r"\d+-?|-")
_PROCESS_SUBSTITUTIONS = ("<(", ">(")
_WORD_BREAK = frozenset(" \t\n;&|()<>")
_HEREDOC_OP = re.compile(r"(?<!<)<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
_HEREDOC_MARK = "__KIT_HEREDOC_{}__"
_HEREDOC_REF = re.compile(r"__KIT_HEREDOC_(\d+)__")
_CAT_HEREDOC = re.compile(r"\$\(\s*cat\s*<<__KIT_HEREDOC_(\d+)__\s*\)")
_EXPANSION = re.compile(r"\$[A-Za-z_{(0-9@*#?$!-]|`")
_VARIABLE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?$!-])")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_KEYWORDS = frozenset("! { if then else elif do while until".split())
_WRAPPERS = {  # Commands that run the rest of the line, with their options that take a separate value.
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "sudo": frozenset({"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "--close-from"})
    | frozenset({"-D", "--chdir", "-r", "--role", "-t", "--type", "-U", "--other-user", "-T", "--command-timeout"}),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "exec": frozenset({"-a"}),
    "command": frozenset(),
    "builtin": frozenset(),
    "nohup": frozenset(),
}


@dataclass(frozen=True)
class Word:
    text: str
    literal: bool = True  # False when the shell would expand part of it: $VAR, $(...), backticks.


@dataclass
class SimpleCommand:
    words: list[Word] = field(default_factory=list)
    stdin: Word | None = None  # A heredoc body or here-string.
    stdout: Word | None = None
    stdin_path: Word | None = None  # The file of a `< file` redirection.
    piped: bool = False  # Its stdin is the previous command's output.


@dataclass
class CommandStep:
    """A simple command with the target of every output redirection it makes, where parse() keeps only the last."""

    words: list[Word] = field(default_factory=list)
    written: list[Word] = field(default_factory=list)
    stdin: Word | None = None  # A heredoc body or here-string.


def parse(command: str) -> tuple[list[SimpleCommand], list[Word]]:
    """The simple commands in order, and the heredoc bodies that `<<` redirections and value() refer to."""
    text, heredocs = _cut_heredocs(command)
    return _simple_commands(text, heredocs), heredocs


def value(word: Word, heredocs: list[Word]) -> str | None:
    """The word after expansion when that's knowable now: a literal, or a `$(cat <<'EOF' ... EOF)` substitution."""
    if word.literal:
        return word.text
    substitution = _CAT_HEREDOC.fullmatch(word.text)
    body = heredocs[int(substitution.group(1))] if substitution else None
    return body.text if body and body.literal else None


def _cut_heredocs(command: str) -> tuple[str, list[Word]]:
    """Swap each heredoc for a numbered marker and pull its body out, so what's left tokenizes line by line."""
    lines, kept, bodies, i = command.split("\n"), [], [], 0
    while i < len(lines):
        line, operators = _mark_heredocs(lines[i], len(bodies))
        kept.append(line)
        i += 1
        for operator in operators:
            body, i = _heredoc_body(lines, i, operator.group(3), strip_tabs=operator.group(1) == "-")
            quoted = bool(operator.group(2))
            bodies.append(Word(body, literal=quoted or not _EXPANSION.search(body)))
    return "\n".join(kept), bodies


def _mark_heredocs(line: str, first: int) -> tuple[str, list[re.Match]]:
    operators = list(_HEREDOC_OP.finditer(line))
    numbers = iter(range(first, first + len(operators)))
    return _HEREDOC_OP.sub(lambda _: "<<" + _HEREDOC_MARK.format(next(numbers)), line), operators


def _heredoc_body(lines: list[str], start: int, delimiter: str, strip_tabs: bool) -> tuple[str, int]:
    body = []
    for i in range(start, len(lines)):
        line = lines[i].lstrip("\t") if strip_tabs else lines[i]
        if line == delimiter:
            return "\n".join(body), i + 1
        body.append(line)
    return "\n".join(body), len(lines)


def _tokens(text: str) -> list[Word | str]:
    tokens, i = [], 0
    while i < len(text):
        char = text[i]
        operator = next((op for op in _OPERATORS if text.startswith(op, i)), None)
        if char in " \t":
            i += 1
        elif text.startswith("\\\n", i):
            i += 2
        elif char == "\n":
            tokens.append(";")
            i += 1
        elif char == "#":
            i = _find(text, "\n", i)
        elif text.startswith(_PROCESS_SUBSTITUTIONS, i):
            end = _closing(text, i + 1)
            tokens.append(Word(text[i : end + 1], literal=False))
            i = end + 1
        elif operator:
            tokens.append(operator)
            i += len(operator)
        else:
            word, i = _read_word(text, i)
            if not (word.text.isdigit() and text[i : i + 1] in ("<", ">")):  # Drop fd numbers, as in 2>&1.
                tokens.append(word)
    return tokens


def _find(text: str, char: str, start: int) -> int:
    index = text.find(char, start)
    return len(text) if index < 0 else index


def _read_word(text: str, i: int) -> tuple[Word, int]:
    parts, literal = [], True
    while i < len(text) and text[i] not in _WORD_BREAK:
        char = text[i]
        if char == "\\":
            parts.append(text[i + 1 : i + 2].replace("\n", ""))
            i += 2
        elif char == "'":
            end = _find(text, "'", i + 1)
            parts.append(text[i + 1 : end])
            i = end + 1
        elif char == '"':
            segment, i, plain = _read_double_quoted(text, i + 1)
            parts.append(segment)
            literal &= plain
        elif char in "$`":
            segment, i = _read_expansion(text, i)
            parts.append(segment)
            literal &= segment == "$"
        else:
            parts.append(char)
            i += 1
    return Word("".join(parts), literal), i


def _read_double_quoted(text: str, i: int) -> tuple[str, int, bool]:
    parts, literal = [], True
    while i < len(text) and text[i] != '"':
        char = text[i]
        if char == "\\" and text[i + 1 : i + 2] in ('"', "\\", "$", "`", "\n"):
            parts.append(text[i + 1].replace("\n", ""))
            i += 2
        elif char in "$`":
            segment, i = _read_expansion(text, i)
            parts.append(segment)
            literal &= segment == "$"
        else:
            parts.append(char)
            i += 1
    return "".join(parts), i + 1, literal


def _read_expansion(text: str, i: int) -> tuple[str, int]:
    """The raw text of the $(...), ${...}, $NAME or `...` starting at i."""
    if text[i] == "`":
        end = _find(text, "`", i + 1)
        return text[i : end + 1], end + 1
    if text[i + 1 : i + 2] in ("(", "{"):
        end = _closing(text, i + 1)
        return text[i : end + 1], end + 1
    variable = _VARIABLE.match(text, i)
    return (variable.group(0), variable.end()) if variable else ("$", i + 1)


def _closing(text: str, start: int) -> int:
    opener = text[start]
    closer = ")" if opener == "(" else "}"
    depth, i = 0, start
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "'":
            i = _find(text, "'", i + 1) + 1
            continue
        if char == '"':
            _, i, _ = _read_double_quoted(text, i + 1)
            continue
        depth += (char == opener) - (char == closer)
        if depth == 0:
            return i
        i += 1
    return len(text) - 1


def _simple_commands(text: str, heredocs: list[Word]) -> list[SimpleCommand]:
    commands, current = [], SimpleCommand()
    tokens = iter(_tokens(text))
    for token in tokens:
        if isinstance(token, Word):
            current.words.append(token)
        elif token in _SEPARATORS:
            commands.append(current)
            current = SimpleCommand(piped=token in _PIPES)
        else:
            _redirect(current, token, next(tokens, None), heredocs)
    commands.append(current)
    return [command for command in commands if command.words]


def _redirect(command: SimpleCommand, operator: str, target: Word | str | None, heredocs: list[Word]) -> None:
    if not isinstance(target, Word):
        return
    if operator == "<<":
        marker = _HEREDOC_REF.fullmatch(target.text)
        command.stdin = heredocs[int(marker.group(1))] if marker else None
    elif operator == "<<<":
        command.stdin = target
    elif operator == "<":
        command.stdin_path = target
    elif operator in (">", ">|", ">>"):
        command.stdout = target


def command_steps(command: str) -> list[CommandStep]:
    """The simple commands in order, each with every file its redirections write. Heredoc bodies are cut out
    first, so a body line that starts with `>` isn't read as a redirection."""
    text, heredocs = _cut_heredocs(command)
    steps, current = [], CommandStep()
    tokens = iter(_tokens(text))
    for token in tokens:
        if isinstance(token, Word):
            current.words.append(token)
        elif token in _SEPARATORS:
            steps.append(current)
            current = CommandStep()
        else:
            _redirect_step(current, token, next(tokens, None), heredocs)
    steps.append(current)
    return [step for step in steps if step.words or step.written]


def _redirect_step(step: CommandStep, operator: str, target: Word | str | None, heredocs: list[Word]) -> None:
    if not isinstance(target, Word):
        return
    if operator in _WRITE_REDIRECTS and not _FD_TARGET.fullmatch(target.text):
        step.written.append(target)
    elif operator == "<<":
        marker = _HEREDOC_REF.fullmatch(target.text)
        step.stdin = heredocs[int(marker.group(1))] if marker else None
    elif operator == "<<<":
        step.stdin = target


def next_directory(step: CommandStep, base: Path) -> Path:
    """Where the commands after `step` run: the literal target of a `cd` or `pushd` step, else `base`."""
    words = command_words(SimpleCommand(list(step.words)))
    if not words or words[0].text not in ("cd", "pushd"):
        return base
    targets = operands(words[1:])
    if not targets:
        return Path.home()
    return resolve_path(base, targets[0]) if targets[0].literal else base


def operands(args: list[Word]) -> list[Word]:
    """The arguments that aren't options."""
    return [arg for arg in args if not arg.text.startswith("-")]


def literal_basename(word: Word) -> str:
    """The last path segment of a literal word (`/usr/bin/git` gives `git`, and the `.` builtin stays `.`);
    empty when the shell would expand the word."""
    if not word.literal:
        return ""
    return word.text if word.text == "." else PurePosixPath(word.text).name


def command_words(command: SimpleCommand) -> list[Word]:
    """The words from the command name on, past assignments, keywords and wrappers such as `sudo -u x`."""
    words, i = command.words, 0
    while i < len(words):
        text = words[i].text
        if text in _KEYWORDS or _ASSIGNMENT.match(text):
            i += 1
        elif text in _WRAPPERS:
            i = _past_options(words, i + 1, _WRAPPERS[text])
        else:
            break
    return words[i:]


def _past_options(words: list[Word], i: int, with_value: frozenset[str]) -> int:
    while i < len(words) and words[i].text.startswith("-"):
        option = words[i].text
        i += 2 if option in with_value else 1
        if option == "--":
            break
    return i


def substitutions(word: Word) -> list[str]:
    """The command text inside each outermost $(...), `...`, <(...) and >(...) of the word."""
    if word.literal:
        return []
    text, found, i = word.text, [], 0
    while i < len(text):
        if text.startswith(("$(", *_PROCESS_SUBSTITUTIONS), i):
            end = _closing(text, i + 1)
            found.append(text[i + 2 : end])
        elif text[i] == "`":
            end = _find(text, "`", i + 1)
            found.append(text[i + 1 : end])
        else:
            end = i
        i = end + 1
    return found


def resolve_path(base: Path, word: Word) -> Path:
    """Where a path argument points, with ~ and any $VAR this process can see expanded."""
    text = word.text if word.literal else os.path.expandvars(word.text)
    return base / os.path.expanduser(text)
