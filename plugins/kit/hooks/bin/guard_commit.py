"""PreToolUse Bash: find every `git commit` the command would run and check it against the rules in commitlib.

This hook sees every Bash call, so a command without a commit returns before anything slow is imported.
"""

from __future__ import annotations

import itertools
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import kitlib
import shellwords
import taskrunners
from shellwords import SimpleCommand, Word
from taskrunners import GIT_COMMIT

FAIL_CLOSED = True

_GIT_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
)
_CHECKED_SUBCOMMANDS = frozenset({"commit", "merge", "commit-tree"})
_RECORDED_SUBCOMMANDS = _CHECKED_SUBCOMMANDS | {"add", "stage"}
_RUN_TIME_CHARS = frozenset("${}*?[]")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_SHELL_OPTIONS_WITH_VALUE = frozenset({"-o", "+o", "-O", "+O"})
_SHELL_SCRIPT_SUFFIXES = frozenset({".sh", ".bash", ".zsh"})
_SHELL_SHEBANG = re.compile(r"#!\S*(?:/|env\s+)(?:ba|z|da|k)?sh\b")
_SOURCE_COMMANDS = frozenset({"source", "."})
_RUNNERS = frozenset({"make", "gmake", "just", "npm", "pnpm", "yarn", "bun", "node", "ruby", "perl"})
_PYTHON = re.compile(r"python[\d.]*")
_ECHO_FLAGS = frozenset({"-n", "-e", "-E", "-ne", "-en"})
_MENTIONS_ONLY = frozenset({"echo", "printf", "grep", "rg", "man", "which", "type", "help"})
_MAX_NESTING = 4
_CONFIRM = "Confirm it, or ask Claude to run git commit directly."


@dataclass(frozen=True)
class _Wrapper:
    """A command that runs another command given on its own command line."""

    with_value: frozenset[str] = frozenset()  # Options that take the next word as their value.
    operands: int = 0  # Words before the command, like timeout's duration or flock's lock file.
    command_options: frozenset[str] = frozenset()  # Options whose value is a shell command string.
    runs_rest_as_string: bool = False  # watch joins the rest into one string for `sh -c`.
    needs_command_option: bool = False  # script and su run a command only through -c.


_WRAPPERS = {
    "timeout": _Wrapper(frozenset({"-s", "--signal", "-k", "--kill-after"}), operands=1),
    "xargs": _Wrapper(
        frozenset({"-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L", "-n", "--max-args", "-P"})
        | frozenset({"--max-procs", "-s", "--max-chars", "--process-slot-var"})
    ),
    "stdbuf": _Wrapper(frozenset({"-i", "-o", "-e"})),
    "setsid": _Wrapper(),
    "flock": _Wrapper(
        frozenset({"-w", "--wait", "--timeout", "-E", "--conflict-exit-code"}),
        operands=1,
        command_options=frozenset({"-c", "--command"}),
    ),
    "ionice": _Wrapper(frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"})),
    "chrt": _Wrapper(
        frozenset({"-T", "--sched-runtime", "-P", "--sched-period", "-D", "--sched-deadline"}), operands=1
    ),
    "taskset": _Wrapper(operands=1),
    "chronic": _Wrapper(),
    "unbuffer": _Wrapper(),
    "coproc": _Wrapper(),
    "function": _Wrapper(operands=1),
    "script": _Wrapper(
        frozenset({"-E", "--echo", "-I", "--log-in", "-O", "--log-out", "-B", "--log-io", "-T", "--log-timing"})
        | frozenset({"-m", "--logging-format", "-o", "--output-limit"}),
        command_options=frozenset({"-c", "--command"}),
        needs_command_option=True,
    ),
    "su": _Wrapper(
        frozenset({"-s", "--shell", "-g", "--group", "-G", "--supp-group", "-w", "--whitelist-environment"}),
        command_options=frozenset({"-c", "--command", "--session-command"}),
        needs_command_option=True,
    ),
    "watch": _Wrapper(frozenset({"-n", "--interval", "-q", "--equexit"}), runs_rest_as_string=True),
}


@dataclass
class GitCall:
    directory: Path | None  # None when a -C value is only known at run time.
    subcommand: str
    args: list[Word]
    stdin: Word | None
    heredocs: list[Word]  # The bodies this call's heredoc markers refer to.


@dataclass
class Chain:
    calls: list[GitCall] = field(default_factory=list)  # git add, commit, merge..., in the order they run.
    written: dict[str, Word] = field(default_factory=dict)  # Files the command writes with `cat > file <<EOF`.
    asks: list[str] = field(default_factory=list)  # Commits kit can't see into, for the user to confirm.
    aliases: dict[str, str] = field(default_factory=dict)  # Aliases the command defines before using them.

    def commit_count(self) -> int:
        return sum(call.subcommand in _CHECKED_SUBCOMMANDS for call in self.calls)


@dataclass(frozen=True)
class _Segment:
    """One simple command, where it runs, and the text it came from."""

    simple: SimpleCommand
    words: list[Word]
    directory: Path
    heredocs: list[Word]
    command: str
    depth: int


@kitlib.hook_main("guard_commit", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    command = (inp.get("tool_input") or {}).get("command")
    if inp.get("tool_name") != "Bash" or not isinstance(command, str):
        return
    chain = parse_chain(command, Path(inp.get("cwd") or os.getcwd()))
    asks = list(chain.asks)
    if chain.commit_count():
        import commitlib  # Deferred so the many commands without a commit stay fast.

        asks += commitlib.check(chain)
    if asks:
        kitlib.pre_tool_decision("ask", " ".join(dict.fromkeys(asks)))


def parse_chain(command: str, cwd: Path) -> Chain:
    chain = Chain()
    _read(chain, command, cwd, depth=0)
    return chain


def _read(chain: Chain, command: str, directory: Path, depth: int) -> None:
    commands, heredocs = shellwords.parse(command)
    previous = None
    for simple in commands:
        if depth < _MAX_NESTING:
            for inner in (text for word in simple.words for text in shellwords.substitutions(word)):
                _read(chain, inner, directory, depth + 1)
        words, script = _unwrapped(shellwords.command_words(simple))
        if script is None:
            script = _shell_script(words) or _alias_expansion(chain, words) or _env_split_string(simple.words)
        if script is not None:
            if depth < _MAX_NESTING:
                _read(chain, script, directory, depth + 1)
        elif words:
            segment = _Segment(simple, words, directory, heredocs, command, depth)
            if not _follow_stdin_shell(chain, segment, previous):
                directory = _record(chain, segment)
        previous = simple


def _record(chain: Chain, segment: _Segment) -> Path:
    """Note what this simple command means for commits; return the directory the next one runs in."""
    words, directory = segment.words, segment.directory
    name = Path(words[0].text).name
    if not words[0].literal:
        if any(word.text == "commit" for word in words[1:]):
            chain.asks.append(_run_time_reason(words, "runs a command whose name is only known at run time"))
    elif name in ("cd", "pushd"):
        return _change_dir(directory, words[1:])
    elif name == "alias":
        chain.aliases |= dict(word.text.split("=", 1) for word in words[1:] if "=" in word.text)
    elif name == "cat" and len(words) == 1 and segment.simple.stdin and segment.simple.stdout:
        if segment.simple.stdout.literal:
            chain.written[str(shellwords.resolve_path(directory, segment.simple.stdout))] = segment.simple.stdin
    elif name == "git":
        _record_git(chain, segment)
    elif name in _RUNNERS or _PYTHON.fullmatch(name):
        recipe = taskrunners.recipe_for(words, directory)
        if recipe:
            _follow_recipe(chain, recipe, segment)
    elif script_file := _executed_file(words):
        _follow_file(chain, script_file, segment, run_by_shell=name in _SHELLS or name in _SOURCE_COMMANDS)
    elif name not in _MENTIONS_ONLY and _names_git_commit(words):
        chain.asks.append(f"kit: this command runs git commit through {name}, which kit can't check. {_CONFIRM}")
    return directory


def _record_git(chain: Chain, segment: _Segment) -> None:
    words = segment.words
    call = _git_call(words, segment.directory, segment.simple.stdin, segment.heredocs)
    if call is None:
        return
    subcommand = words[len(words) - len(call.args) - 1]
    if not subcommand.literal or _RUN_TIME_CHARS.intersection(subcommand.text):
        chain.asks.append(_run_time_reason(words, "runs git with a subcommand only known at run time"))
    elif _defines_commit_alias(call, words):
        chain.asks.append(_run_time_reason(words, "defines a git alias that commits"))
    elif call.subcommand in _RECORDED_SUBCOMMANDS:
        chain.calls.append(call)


def _defines_commit_alias(call: GitCall, words: list[Word]) -> bool:
    """`git -c alias.ci=commit ci` or `git config alias.ci commit`."""
    configs = [value.text for option, value in itertools.pairwise(words) if option.text == "-c"]
    if call.subcommand == "config":
        configs.append(" ".join(word.text for word in call.args))
    return any("alias." in config and "commit" in config for config in configs)


def _names_git_commit(words: list[Word]) -> bool:
    texts = [Path(word.text).name if word.literal else "" for word in words]
    return "git" in texts and "commit" in texts


def _unwrapped(words: list[Word]) -> tuple[list[Word], str | None]:
    """Strip wrappers like `timeout 60` or `xargs -I{}`: the words that run, or the command string they run."""
    while words and words[0].literal and Path(words[0].text).name in _WRAPPERS:
        wrapper = _WRAPPERS[Path(words[0].text).name]
        i, script = _past_wrapper_options(words, 1, wrapper)
        if script is None and wrapper.command_options:
            i, script = _past_wrapper_options(words, i + wrapper.operands, wrapper)  # flock LOCK -c CMD
        elif script is None:
            i += wrapper.operands
        if script is not None:
            return [], script
        if wrapper.needs_command_option:
            return [], None
        if wrapper.runs_rest_as_string:
            return [], " ".join(word.text for word in words[i:])
        words = shellwords.command_words(SimpleCommand(list(words[i:])))
    return words, None


def _past_wrapper_options(words: list[Word], i: int, wrapper: _Wrapper) -> tuple[int, str | None]:
    """The index past the options starting at i, or the command string one of them carries."""
    while i < len(words) and words[i].text.startswith("-") and words[i].text != "-":
        option = words[i].text
        name, has_value, attached = option.partition("=")
        bundle_ends_in_command = not option.startswith("--") and f"-{option[-1]}" in wrapper.command_options
        if name in wrapper.command_options or bundle_ends_in_command:
            value = attached if has_value else (words[i + 1].text if i + 1 < len(words) else "")
            return i, value
        i += 2 if option in wrapper.with_value else 1
        if option == "--":
            break
    return i, None


def _shell_script(words: list[Word]) -> str | None:
    """The command string that `bash -c '...'`, `sh -lc '...'` or `eval ...` runs."""
    if not words:
        return None
    name = Path(words[0].text).name
    if name == "eval":
        return " ".join(word.text for word in words[1:])
    if name not in _SHELLS:
        return None
    rest = iter(words[1:])
    for word in rest:
        option = word.text
        if option in _SHELL_OPTIONS_WITH_VALUE:
            next(rest, None)
        elif option.startswith("-") and not option.startswith("--") and "c" in option:
            script = next(rest, None)
            return script.text if script else None
        elif not option.startswith(("-", "+")):
            return None
    return None


def _env_split_string(words: list[Word]) -> str | None:
    """The command `env -S '...'` splits and runs; shellwords drops it with env's other options."""
    names = [word.text for word in words]
    if "env" not in names:
        return None
    rest = iter(names[names.index("env") + 1 :])
    for option in rest:
        if option in ("-S", "--split-string"):
            return next(rest, None)
        if option.startswith(("-S", "--split-string=")):
            return option.removeprefix("--split-string=").removeprefix("-S")
    return None


def _alias_expansion(chain: Chain, words: list[Word]) -> str | None:
    if not words or not words[0].literal or words[0].text not in chain.aliases:
        return None
    args = (shlex.quote(word.text) if word.literal else f'"{word.text}"' for word in words[1:])
    return " ".join([chain.aliases[words[0].text], *args])


def _follow_stdin_shell(chain: Chain, segment: _Segment, previous: SimpleCommand | None) -> bool:
    """Check the script a shell reads from a heredoc, `< file` or a pipe; False when it isn't such a shell."""
    words, simple = segment.words, segment.simple
    if Path(words[0].text).name not in _SHELLS or not _reads_stdin(words):
        return False
    if simple.stdin is not None:
        if simple.stdin.literal:
            _follow_script(chain, simple.stdin.text, segment, "the heredoc fed to the shell")
        elif "commit" in simple.stdin.text:
            chain.asks.append(f"kit: the shell reads a script built at run time that mentions commit. {_CONFIRM}")
    elif simple.stdin_path is not None:
        _follow_file(chain, simple.stdin_path, segment, run_by_shell=True)
    elif simple.piped and previous is not None:
        _follow_piped(chain, previous, segment)
    return True


def _reads_stdin(words: list[Word]) -> bool:
    rest = iter(words[1:])
    for word in rest:
        option = word.text
        if option in _SHELL_OPTIONS_WITH_VALUE:
            next(rest, None)
        elif option.startswith("-") and not option.startswith("--") and "s" in option:
            return True  # With -s, the words after it are the script's arguments.
        elif not option.startswith(("-", "+")):
            return False
    return True


def _follow_piped(chain: Chain, producer: SimpleCommand, segment: _Segment) -> None:
    words = shellwords.command_words(producer)
    name = Path(words[0].text).name if words else ""
    if words and all(word.literal for word in words) and name in ("echo", "printf"):
        _follow_script(chain, _printed_text(name, [word.text for word in words[1:]]), segment, f"the {name} output")
    elif words and all(word.literal for word in words) and name == "cat" and len(words) > 1:
        for word in words[1:]:
            _follow_file(chain, word, segment, run_by_shell=True)
    elif "commit" in segment.command:
        chain.asks.append(
            f"kit: the shell runs piped input kit can't read, and the command mentions commit. {_CONFIRM}"
        )


def _printed_text(name: str, args: list[str]) -> str:
    if name == "echo":
        text = " ".join(arg for arg in args if arg not in _ECHO_FLAGS)
    else:
        text = "\n".join(args[1:]) if args and "%" in args[0] else " ".join(args)
    return text.replace("\\n", "\n")


def _follow_recipe(chain: Chain, recipe: taskrunners.Recipe, segment: _Segment) -> None:
    if recipe.text is None:
        if recipe.mentions_commit or "commit" in segment.command or "commit" in recipe.label:
            chain.asks.append(f"kit: this command runs {recipe.label}, which kit can't read or resolve. {_CONFIRM}")
    elif not recipe.shell:
        if GIT_COMMIT.search(recipe.text):
            chain.asks.append(f"kit: this command runs {recipe.label}, whose code runs git commit. {_CONFIRM}")
    else:
        inner = _Segment(segment.simple, segment.words, recipe.directory, [], segment.command, segment.depth)
        _follow_script(chain, recipe.text, inner, recipe.label)


def _executed_file(words: list[Word]) -> Word | None:
    """The script file this command runs: `bash x.sh`, `source x.sh`, `. x.sh` or `./x.sh`."""
    name = Path(words[0].text).name
    if name in _SHELLS:
        return next((word for word in words[1:] if not word.text.startswith(("-", "+"))), None)
    if words[0].text in _SOURCE_COMMANDS:
        return words[1] if len(words) > 1 else None
    return words[0] if "/" in words[0].text else None


def _follow_file(chain: Chain, script_file: Word, segment: _Segment, run_by_shell: bool) -> None:
    path = shellwords.resolve_path(segment.directory, script_file)
    text = taskrunners.read_text(path) if script_file.literal else None
    if text is None:
        if "commit" in segment.command or "commit" in path.name:
            chain.asks.append(f"kit: this command runs {path.name}, which kit can't read yet. {_CONFIRM}")
    elif run_by_shell or path.suffix in _SHELL_SCRIPT_SUFFIXES or _SHELL_SHEBANG.match(text):
        _follow_script(chain, text, segment, path.name)
    elif GIT_COMMIT.search(text):
        chain.asks.append(f"kit: this command runs {path.name}, whose code runs git commit. {_CONFIRM}")


def _follow_script(chain: Chain, text: str, segment: _Segment, label: str) -> None:
    """Check a script kit can read like a command of its own; ask when it mentions a commit kit can't follow."""
    found_before = chain.commit_count(), len(chain.asks)
    if segment.depth < _MAX_NESTING:
        _read(chain, text, segment.directory, segment.depth + 1)
    if GIT_COMMIT.search(text) and (chain.commit_count(), len(chain.asks)) == found_before:
        chain.asks.append(f"kit: {label} mentions git commit in a way kit can't follow. {_CONFIRM}")


def _run_time_reason(words: list[Word], what: str) -> str:
    shown = " ".join(word.text for word in words)
    shown = shown if len(shown) <= 80 else shown[:77] + "..."
    return f"kit: `{shown}` {what}, so kit couldn't check that commit. {_CONFIRM}"


def _change_dir(directory: Path, args: list[Word]) -> Path:
    targets = [word for word in args if not word.text.startswith("-") or word.text == "-"]
    if not targets:
        return Path.home()
    return directory if targets[0].text == "-" else shellwords.resolve_path(directory, targets[0])


def _git_call(words: list[Word], directory: Path, stdin: Word | None, heredocs: list[Word]) -> GitCall | None:
    i = 1
    resolved: Path | None = directory
    while i < len(words) and words[i].text.startswith("-"):
        option = words[i].text
        if option == "-C" and i + 1 < len(words) and words[i + 1].text:
            value = words[i + 1]
            # A value only known at run time can't be joined onto `directory`: treat it as unresolvable rather
            # than letting its literal, unexpanded text (e.g. "$target") become a bogus path later code stats.
            resolved = shellwords.resolve_path(resolved, value) if resolved is not None and value.literal else None
        i += 2 if option in _GIT_OPTIONS_WITH_VALUE else 1
    return GitCall(resolved, words[i].text, words[i + 1 :], stdin, heredocs) if i < len(words) else None


if __name__ == "__main__":
    main()
