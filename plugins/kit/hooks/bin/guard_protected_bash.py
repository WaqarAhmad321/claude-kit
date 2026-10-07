"""PreToolUse Bash: ask the user before a command could write a file that can switch the kit off, or disables a plugin.

It checks the same files as guard_protected_files. A best-effort layer above the sandbox, which blocks such writes
at the OS level: it reads the targets of redirections and of the usual file-writing commands, code passed to
interpreters that mentions the kit's or Claude Code's files and could write, and code whose write targets name gate
configuration. It doesn't follow variables it can't see or scripts written in the same command.
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Iterator
from pathlib import Path

import guard_protected_files as protected
import kitlib
import shellwords

FAIL_CLOSED = True

_KIT_SWITCHES = re.compile(r"disableAllHooks|KIT_BYPASS")
_CODE_NEEDLES = re.compile(
    r"\.claude['\"/\\,+)} ]+(settings|hooks|kit-hooks|plugins)|skills_seen|\.cache/claude-kit|kit-baseline"
)
_KIT_OWN_MENTION = re.compile(r"[\w.~/-]*(?:plugins/kit/hooks|home/hooks)[\w./-]*")
_EVERY_ARG_WRITTEN = frozenset(
    {"tee", "sponge", "rm", "unlink", "shred", "rmdir", "chmod", "chown", "chgrp", "truncate", "touch"}
)
_COPY_COMMANDS = frozenset({"cp", "install", "ln", "rsync", "mv"})
_IN_PLACE_EDITORS = frozenset({"sed", "yq"})
_OUTPUT_OPTIONS = {"curl": frozenset({"-o", "--output"}), "wget": frozenset({"-O", "--output-document"})}
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "source", "."})
_INTERPRETER = re.compile(r"(python|pypy)[0-9.]*|node|nodejs|perl|ruby|php|bun|deno")
_GIT_TREE_CHANGING = frozenset(
    {"checkout", "restore", "reset", "stash", "clean", "switch", "pull", "merge", "rebase", "apply", "am"}
    | {"cherry-pick", "revert"}
)
_GIT_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
_PLUGIN_REMOVALS = frozenset({"disable", "uninstall", "remove"})
_MARKETPLACE_REMOVALS = frozenset({"remove", "rm"})
_KNOWN_COMMANDS = (
    _EVERY_ARG_WRITTEN
    | _COPY_COMMANDS
    | _IN_PLACE_EDITORS
    | _OUTPUT_OPTIONS.keys()
    | _SHELLS
    | {"dd", "git", "claude", "eval"}
)
_FILE_WRITERS = _EVERY_ARG_WRITTEN | _COPY_COMMANDS | _SHELLS | {"dd", "eval"}
_ARGUMENTS = r"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*?"  # An argument list, with up to two levels of nested calls.
_WRITE_MODE = _ARGUMENTS + r"""['"][rbtU]*[wax+][rbtwax+U]*['"]"""
_OPEN_FLAGS = r"\bO_(?:WRONLY|RDWR|CREAT|APPEND|TRUNC)\b"
_WRITE_CALLS = {
    "python": re.compile(
        rf"\bopen\s*\({_WRITE_MODE}|{_OPEN_FLAGS}|\.write_(?:text|bytes)\s*\(|\b\w+\.dump\s*\(|\bshutil\.\w+"
        r"|\bos\.(?:remove|replace|renames|removedirs|lchmod|lchown|link|utime)\s*\("
        r"|\.(?:unlink|rename|rmdir|touch|symlink_to|hardlink_to|chmod|chown|truncate|symlink)\s*\("
    ),
    "node": re.compile(
        rf"\bopen(?:Sync)?\s*\({_WRITE_MODE}|{_OPEN_FLAGS}"
        r"|\b(?:writeFile|appendFile|rename|unlink|rmdir|rm|copyFile|cp|truncate|chmod|chown|symlink)(?:Sync)?\s*\("
        r"|\bcreateWriteStream\s*\(|\bBun\.(?:write|spawn\w*|\$)|\bDeno\.(?:write\w*|remove|rename|truncate|copyFile"
        r"|create|symlink|chmod|Command|run)\b"
    ),
    "perl": re.compile(
        rf"""\bopen\b[^;\n]*?['"]\s*[+>]|{_OPEN_FLAGS}|`|\bqx\b"""
        r"|\b(?:unlink|rename|truncate|chmod|chown|symlink|rmdir|rmtree|copy|move)\b"
    ),
}
_SUBPROCESS_CALLS = {
    "python": re.compile(
        r"\b(?:subprocess\.(?:run|call|check_call|check_output|Popen|getoutput|getstatusoutput)"
        r"|os\.(?:system|popen|exec\w*|spawn\w*))\s*\((.*)"
    ),
    "node": re.compile(r"\b(?:execSync|execFileSync|execFile|spawnSync|spawn|exec)\s*\((.*)"),
    "perl": re.compile(r"\b(?:system|exec)\b\s*\(?(.*)"),
}
_STRING_LITERAL = re.compile(r"""(['"])(.*?)(?<!\\)\1""")
_LITERAL_FIRST = re.compile(r"""\s*[\[(]?\s*[fbruFBRU]*['"]""")  # The call's command is a string the hook can read.
_PERL_SWITCHES_WITH_VALUE = frozenset("eEFMmIdxCV")
# `name = value` at the start of a statement, after an optional declaration keyword, with an optional type annotation.
_ASSIGNMENT = re.compile(
    r"(?:^|[;{])[ \t]*(?:(?:const|let|var|my|our|local)\s+)?([$@%]?\w+(?:\.\w+)*)[ \t]*(?::[^=\n;]*)?=(?!=)([^;\n]*)",
    re.MULTILINE,
)
_READ_CALL = re.compile(r"\b(?:open|load|loads|safe_load|parse|read\w*)\s*\(")  # A value read from a file, not a path.
_PAREN_NEXT = re.compile(r"\s*\(")
_STATEMENT_END = re.compile(r"[;\n]")
_LINE_COMMENT = {"python": "#", "perl": "#", "node": "//"}
_TRIPLE_QUOTES = ("'''", '"""')
_CLOSERS = {")": "(", "]": "[", "}": "{"}
_DEVICE_DIRS = (Path("/dev"), Path("/proc"))
_MAX_DEPTH = 4
_MAX_SCRIPT_BYTES = 256 * 1024


@kitlib.hook_main("guard_protected_bash", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    command = (inp.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return
    if _KIT_SWITCHES.search(command):
        _ask("mentions disableAllHooks or KIT_BYPASS, which switch the kit off")
    base = Path(inp.get("cwd") or os.getcwd())
    reason = _review(command, base, depth=0)
    if reason:
        _ask(reason)


def _ask(reason: str) -> None:
    kitlib.pre_tool_decision("ask", f"This command {reason}; the user has to approve it.")


def _review(command: str, base: Path, depth: int) -> str | None:
    """Why `command` needs approval, or None."""
    if depth > _MAX_DEPTH:
        return None
    for step in shellwords.command_steps(command):
        reason = _review_step(step, base, depth)
        if reason:
            return reason
        base = shellwords.next_directory(step, base)
    return None


def _review_step(step: shellwords.CommandStep, base: Path, depth: int) -> str | None:
    for word in step.words + step.written:
        for inner in shellwords.substitutions(word):
            reason = _review(inner, base, depth + 1)
            if reason:
                return reason
    words = shellwords.command_words(shellwords.SimpleCommand(list(step.words)))
    targets = [*step.written, *_command_targets(words, base)]
    return (
        _plugin_removal(words)
        or next((reason for target in targets if (reason := _target_reason(target, base))), None)
        or _code_reason(words, step.stdin, base, depth)
    )


def _command_targets(words: list[shellwords.Word], base: Path) -> list[shellwords.Word]:
    index = _command_index(words)
    if index is None:
        return []
    name, args = shellwords.literal_basename(words[index]), words[index + 1 :]
    if name in _EVERY_ARG_WRITTEN:
        return shellwords.operands(args)
    if name in _COPY_COMMANDS:
        return _copy_targets(name, args, base)
    if name in _IN_PLACE_EDITORS:
        return shellwords.operands(args) if any(_is_in_place_flag(arg.text) for arg in args) else []
    if name in _OUTPUT_OPTIONS:
        return _option_values(args, _OUTPUT_OPTIONS[name])
    if name == "dd":
        return [shellwords.Word(arg.text[3:], arg.literal) for arg in args if arg.text.startswith("of=")]
    if name == "git":
        return _git_targets(args, base)
    return []


def _command_index(words: list[shellwords.Word]) -> int | None:
    """The first word that names a command this guard knows, so wrappers like `timeout 5` or `xargs` don't hide it."""
    return next(
        (
            i
            for i, word in enumerate(words)
            if shellwords.literal_basename(word) in _KNOWN_COMMANDS
            or _INTERPRETER.fullmatch(shellwords.literal_basename(word))
        ),
        None,
    )


def _is_in_place_flag(text: str) -> bool:
    return text.startswith("--in-place") or _short_flags_include(text, "i")


def _short_flags_include(text: str, flag: str) -> bool:
    return text.startswith("-") and not text.startswith("--") and flag in text[1:]


def _option_values(args: list[shellwords.Word], options: frozenset[str]) -> list[shellwords.Word]:
    found = []
    for i, arg in enumerate(args):
        option, inline, value = arg.text.partition("=")
        if option in options:
            found.extend([shellwords.Word(value, arg.literal)] if inline else args[i + 1 : i + 2])
    return found


def _copy_targets(name: str, args: list[shellwords.Word], base: Path) -> list[shellwords.Word]:
    operands = shellwords.operands(args)
    directories = _option_values(args, frozenset({"-t", "--target-directory"}))
    if directories:
        sources, destination = operands, directories[0]
    elif len(operands) >= 2:
        sources, destination = operands[:-1], operands[-1]
    else:
        return []
    into = directories or destination.text.endswith("/") or shellwords.resolve_path(base, destination).is_dir()
    written = (
        [
            shellwords.Word(
                f"{destination.text.rstrip('/')}/{shellwords.literal_basename(source) or source.text}",
                destination.literal,
            )
            for source in sources
        ]
        if into
        else [destination]
    )
    return [*sources, *written] if name == "mv" else written


def _git_targets(args: list[shellwords.Word], base: Path) -> list[shellwords.Word]:
    directory, i = shellwords.Word(str(base)), 0
    while i < len(args) and args[i].text.startswith("-"):
        if args[i].text == "-C" and i + 1 < len(args):
            directory = shellwords.Word(str(shellwords.resolve_path(base, args[i + 1])), args[i + 1].literal)
        i += 2 if args[i].text in _GIT_OPTIONS_WITH_VALUE else 1
    if i >= len(args) or args[i].text not in _GIT_TREE_CHANGING:
        return []
    # These rewrite files anywhere in the work tree, whichever subdirectory they run from.
    where = shellwords.resolve_path(base, directory)
    root = kitlib.project_root(str(where)) or where
    paths = [
        shellwords.Word(str(shellwords.resolve_path(root, arg)), arg.literal)
        for arg in shellwords.operands(args[i + 1 :])
    ]
    return [shellwords.Word(str(root), directory.literal), *paths]


def _plugin_removal(words: list[shellwords.Word]) -> str | None:
    index = _command_index(words)
    if index is None or shellwords.literal_basename(words[index]) != "claude":
        return None
    group, action, marketplace_action = (
        [word.text for word in shellwords.operands(words[index + 1 :])] + ["", "", ""]
    )[:3]
    if group not in ("plugin", "plugins"):
        return None
    removes = action in _PLUGIN_REMOVALS or (action == "marketplace" and marketplace_action in _MARKETPLACE_REMOVALS)
    return "disables or removes a Claude Code plugin or marketplace" if removes else None


def _target_reason(word: shellwords.Word, base: Path) -> str | None:
    path = Path(os.path.normpath(shellwords.resolve_path(base, word)))
    if any(path.is_relative_to(device) for device in _DEVICE_DIRS):
        return None
    for candidate in dict.fromkeys((path, path.resolve())):
        what = protected._guarded_everywhere(candidate) or _holds_protected(candidate) or _gate_config(candidate)
        if what:
            return f"could change {what} ({protected._shown(candidate)})"
    # A path built by $(...) or a variable this hook can't see is only known by its text.
    needles = [*_code_needles(), protected.gate_file_mention()]
    if not word.literal and any(needle.search(word.text) for needle in needles):
        return f"writes to a path that looks like Claude Code, kit or quality-gate configuration ({word.text})"
    return None


def _holds_protected(path: Path) -> str | None:
    """A directory that contains protected files, for `rm -rf`, `mv` or `chmod -R` on the whole thing."""
    config_dirs = protected._claude_config_dirs()
    anchors = [
        ("Claude Code settings", [d / name for d in config_dirs for name in protected._CLAUDE_SETTINGS]),
        ("Claude Code hooks or plugins", [d / name for d in config_dirs for name in protected._CLAUDE_CODE_DIRS]),
        ("the kit's state", protected._kit_state_dirs()),
    ]
    for what, paths in anchors:
        if any(anchor.is_relative_to(path) for anchor in paths):
            return what
    if path.name == ".claude":
        return "Claude Code settings"
    return _holds_kit_files(path)


def _holds_kit_files(path: Path) -> str | None:
    root = protected._live_kit_of(path)
    if root is None or protected._developing_the_kit(path):
        return None
    rel = path.relative_to(root).as_posix()
    prefixes = [glob.split("*")[0].rstrip("/") for glob in protected._KIT_OWN_GLOBS]
    holds = rel == "." or any(prefix == rel or prefix.startswith(f"{rel}/") for prefix in prefixes)
    return "the kit's own hook files" if holds else None


def _gate_config(path: Path) -> str | None:
    root = kitlib.project_root(str(path))
    if root is None or not path.is_relative_to(root) or kitlib.is_non_project(root, path):
        return None
    rel = path.relative_to(root).as_posix()
    return "quality-gate configuration" if protected._is_gate_file(rel, path.name) else None


def _code_reason(words: list[shellwords.Word], stdin, base: Path, depth: int) -> str | None:
    """Shell code run by `bash -c`, `eval` or a script, and code given to an interpreter."""
    index = _command_index(words)
    if index is None:
        return None
    name, args = shellwords.literal_basename(words[index]), words[index + 1 :]
    if name in _SHELLS or name == "eval":
        return _review_shell_code(name, args, stdin, base, depth)
    if not _INTERPRETER.fullmatch(name):
        return None
    scripts = _script_texts(args, base)
    code = [arg.text for arg in args] + ([stdin.text] if stdin else []) + scripts
    kit = any(_mentions_protected(text, base) for text in code)
    gate = any(protected.gate_file_mention().search(text) for text in code)
    if not (kit or gate) or not _code_could_write(name, args, code, base):
        return None
    script = shellwords.operands(args)[0] if scripts else None
    arguments = [arg for arg in args if arg is not script]
    if kit or _writes_gate_config(name, arguments, code, base):
        return "runs code that could change Claude Code, kit or quality-gate configuration"
    return None


def _mentions_protected(text: str, base: Path) -> bool:
    return any(needle.search(text) for needle in _code_needles()) or _mentions_live_kit_files(text, base)


def _mentions_live_kit_files(text: str, base: Path) -> bool:
    """A mention of the kit's hook directories, unless it points into a checkout of the kit other than the live one.
    A path this hook can't place, such as one built from variables, counts."""
    if protected._session_in_live_kit():
        return False
    for mention in _KIT_OWN_MENTION.finditer(text):
        path = Path(os.path.normpath(base / os.path.expanduser(mention.group()))).resolve()
        checkout = protected._kit_checkout_of(path)
        if checkout is None or checkout == protected._live_kit_root():
            return True
    return False


def _code_could_write(name: str, args: list[shellwords.Word], code: list[str], base: Path) -> bool:
    """Whether code given to an interpreter could write, move or delete a file. Code in a language this hook doesn't
    read could do anything."""
    language = _language(name)
    if language is None:
        return True
    text = "\n".join(code)
    return (
        bool(_WRITE_CALLS[language].search(text))
        or _runs_a_writer(text, language, base)
        or (language == "perl" and _perl_in_place(args))
    )


def _language(name: str) -> str | None:
    if name.startswith(("python", "pypy")):
        return "python"
    if name in ("node", "nodejs", "bun", "deno"):
        return "node"
    return "perl" if name == "perl" else None


def _runs_a_writer(code: str, language: str, base: Path) -> bool:
    return any(_writing_subprocesses(code, language, base))


def _writing_subprocesses(code: str, language: str, base: Path) -> Iterator[re.Match]:
    """The calls that start a command that writes files, or one whose command line the code builds out of sight."""
    if language == "node" and "child_process" not in code:
        return
    for call in _SUBPROCESS_CALLS[language].finditer(code):
        arguments = call.group(1)
        literals = [literal.group(2) for literal in _STRING_LITERAL.finditer(arguments)]
        if not _LITERAL_FIRST.match(arguments) or _command_line_writes(literals, base):
            yield call


def _command_line_writes(literals: list[str], base: Path) -> bool:
    if len(literals) == 1:  # One string is a shell command line, as os.system and shell=True take it.
        steps = shellwords.command_steps(literals[0])
    else:
        steps = [shellwords.CommandStep([shellwords.Word(literal) for literal in literals])]
    commands = [shellwords.command_words(shellwords.SimpleCommand(list(step.words))) for step in steps]
    if not any(commands):
        return True
    return any(step.written for step in steps) or any(_writes_files(words, base) for words in commands)


def _writes_files(words: list[shellwords.Word], base: Path) -> bool:
    index = _command_index(words)
    if index is None:
        return False
    name, args = shellwords.literal_basename(words[index]), words[index + 1 :]
    if name in _FILE_WRITERS or _INTERPRETER.fullmatch(name):
        return True
    if name in _IN_PLACE_EDITORS:
        return any(_is_in_place_flag(arg.text) for arg in args)
    if name in _OUTPUT_OPTIONS:
        return any(arg.text.partition("=")[0] in _OUTPUT_OPTIONS[name] for arg in args)
    return name == "git" and bool(_git_targets(args, base))


def _writes_gate_config(name: str, arguments: list[shellwords.Word], code: list[str], base: Path) -> bool:
    """Whether code that could write and mentions a gate file targets it: a write call naming one, directly or
    through a variable set to a path naming one, or one given to the code as an argument. Naming one only to read
    it doesn't count. Code in a language this hook doesn't read counts on the mention alone."""
    language = _language(name)
    if language is None:
        return True
    writes = any(_write_call_names_gate_file(text, language, base) for text in code)
    return writes or _gate_file_argument(arguments, base)


def _write_call_names_gate_file(code: str, language: str, base: Path) -> bool:
    calls = [*_WRITE_CALLS[language].finditer(code), *_writing_subprocesses(code, language, base)]
    if not calls:
        return False
    gate_path = _gate_path_pattern(code)
    return any(gate_path.search(_call_text(code, call, language)) for call in calls)


def _gate_path_pattern(code: str) -> re.Pattern:
    """A gate file's name, or a variable the code sets, however indirectly, to a path naming one."""
    mention = protected.gate_file_mention()
    assignments = [match.groups() for match in _ASSIGNMENT.finditer(code) if not _READ_CALL.search(match.group(2))]
    pattern, names = mention, set()
    while found := {name for name, value in assignments if name not in names and pattern.search(value)}:
        names |= found
        variables = "|".join(re.escape(name) for name in sorted(names))
        pattern = re.compile(rf"{mention.pattern}|(?<![\w$@%.])(?:{variables})(?!\w)")
    return pattern


def _gate_file_argument(arguments: list[shellwords.Word], base: Path) -> bool:
    """A gate file passed on the command line, such as `fmt.py lefthook.yml` or `--out=.claude/kit.json`."""
    for word in arguments:
        text = word.text.partition("=")[2] if word.text.startswith("-") else word.text
        if not word.literal or not text or any(char.isspace() for char in text):
            continue
        if _gate_config(Path(os.path.normpath(shellwords.resolve_path(base, shellwords.Word(text))))):
            return True
    return False


def _call_text(code: str, call: re.Match, language: str) -> str:
    """A write call as written: what it's called on, its name and its argument list; for a call without
    parentheses, the rest of its statement."""
    paren = _call_paren(code, call)
    if paren is None:
        end = _STATEMENT_END.search(code, call.end())
        return code[_receiver_start(code, call.start()) : end.start() if end else len(code)]
    return code[_receiver_start(code, paren) : _closing_paren(code, paren, language)]


def _call_paren(code: str, call: re.Match) -> int | None:
    """The opening parenthesis of the call `call` found: in the match, right after it, or around it on its line,
    as for a write flag given to `os.open`."""
    inside = code.find("(", call.start(), call.end())
    if inside != -1:
        return inside
    after = _PAREN_NEXT.match(code, call.end())
    if after:
        return after.end() - 1
    depth = 0
    for index in range(call.start() - 1, code.rfind("\n", 0, call.start()), -1):
        if code[index] == ")":
            depth += 1
        elif code[index] == "(":
            if depth == 0:
                return index
            depth -= 1
    return None


def _receiver_start(code: str, index: int) -> int:
    """Where the expression a call at `index` hangs off starts, such as `pathlib.Path('.claude', 'kit.json')` before
    `.write_text(`."""
    while index > 0:
        before = code[index - 1]
        if before in ")]":
            opener = _opening_bracket(code, index - 1)
            if opener is None:
                break
            index = opener
        elif before.isalnum() or before in "_.$":
            index -= 1
        else:
            break
    return index


def _opening_bracket(code: str, close: int) -> int | None:
    depth = 0
    for index in range(close, -1, -1):
        if code[index] in ")]":
            depth += 1
        elif code[index] in "([":
            depth -= 1
            if depth == 0:
                return index
    return None


def _closing_paren(code: str, paren: int, language: str) -> int:
    """The end of the argument list opening at `paren`, past strings and comments; the end of the line when it
    doesn't close, so a string the scan misreads can't stretch a call over the rest of the code."""
    stack, index = [], paren
    while index < len(code):
        char = code[index]
        if code.startswith(_LINE_COMMENT[language], index):
            index = code.find("\n", index)
            if index == -1:
                break
        elif char in "'\"`":
            index = _string_end(code, index)
        elif char in "([{":
            stack.append(char)
        elif stack and _CLOSERS.get(char) == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
        index += 1
    end = _STATEMENT_END.search(code, paren)
    return end.start() if end else len(code)


def _string_end(code: str, start: int) -> int:
    """The index of the quote that closes the string opening at `start`, triple quotes included."""
    quote = next((triple for triple in _TRIPLE_QUOTES if code.startswith(triple, start)), code[start])
    index = start + len(quote)
    while index < len(code):
        if code[index] == "\\":
            index += 2
            continue
        if code.startswith(quote, index):
            return index + len(quote) - 1
        index += 1
    return len(code)


def _perl_in_place(args: list[shellwords.Word]) -> bool:
    """Whether perl's switches include -i, read as perl reads them: up to the first argument that isn't a switch."""
    code_next = False
    for arg in args:
        if code_next:
            code_next = False
            continue
        if arg.text == "--" or not arg.text.startswith("-"):
            return False
        for position, switch in enumerate(arg.text[1:], start=1):
            if switch == "i":
                return True
            if switch in _PERL_SWITCHES_WITH_VALUE:
                code_next = switch in "eE" and position == len(arg.text) - 1
                break
    return False


def _review_shell_code(name: str, args: list[shellwords.Word], stdin, base: Path, depth: int) -> str | None:
    if name == "eval":
        scripts = [" ".join(arg.text for arg in args)]
    elif any(_short_flags_include(arg.text, "c") for arg in args):
        scripts = [arg.text for arg in args if not arg.text.startswith("-")][:1]
    else:
        scripts = ([stdin.text] if stdin else []) + _script_texts(args, base)
    return next((reason for script in scripts if (reason := _review(script, base, depth + 1))), None)


def _script_texts(args: list[shellwords.Word], base: Path) -> list[str]:
    operands = shellwords.operands(args)
    path = shellwords.resolve_path(base, operands[0]) if operands and operands[0].literal else None
    try:
        if path is None or not path.is_file() or path.stat().st_size > _MAX_SCRIPT_BYTES:
            return []
    except OSError:  # The first operand of `python -c` is the code itself, which can be too long for a file name.
        return []
    return [path.read_text(encoding="utf-8", errors="replace")]


@functools.cache
def _code_needles() -> list[re.Pattern]:
    needles = [_CODE_NEEDLES]
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        needles.append(re.compile(re.escape(plugin_data)))
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root and not protected._session_in_live_kit():
        needles.append(re.compile(re.escape(plugin_root)))
    return needles


if __name__ == "__main__":
    main()
