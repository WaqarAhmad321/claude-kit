"""PreToolUse Edit|Write|Bash: deny tool calls that would put a credential literal into a tracked file or a command."""

from __future__ import annotations

import base64
import binascii
import itertools
import json
import math
import os
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import kitlib

FAIL_CLOSED = True

_SCANNERS = ("betterleaks", "gitleaks")
# No --redact: the placeholder filter needs each matched value. It stays in this process and is never shown.
_REPORT_ARGS = ("--no-banner", "-f", "json", "-r", "-")
_CONTENT_TIMEOUT = 5
_STAGED_TIMEOUT = 20
_EDITED_FILE_LIMIT = 2_000_000
_ENCODED_BLOB_LIMIT = 200
_SKIPPED_DIRS = frozenset({"node_modules", ".git"})
_ENV_FILE = re.compile(r"\.env(?:\..+)?")
_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec)/"
    r"|(?:^|/)(?:test_[^/]*\.py|conftest\.py)$|_test\.(?:py|go)$|\.(?:test|spec)\.[cm]?[jt]sx?$"
)
# Rules that match on shape or entropy alone rather than a provider's key format; test fixtures may trip them.
_GENERIC_RULES = frozenset({"jwt", "jwt-base64", "curl-auth-header", "curl-auth-user", "authorization-bearer"})

_PLACEHOLDER_MARK = re.compile(r"(?i)x{5,}|\*{3,}|\.\.\.|<[^<>]*>|\$\{[^}]*\}|^\$\w+$|\{\{")
_PLACEHOLDER_WORD = re.compile(
    r"(?i)change[-_]?me|replace[-_]?me|your[\w-]*?here|placeholder|example(?:key)?|sample|dummy|redacted|fake"
)
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")
_DUMMY_PASSWORD = re.compile(
    r"(?i)^(?:pass|secret|postgres|mysql|root|admin|guest|user|test|changeit|default|none|null|true|false)\d*$"
    r"|passw(?:or)?d|passwd"
)
_NOT_A_VALUE = re.compile(r"\s|://|^[$%{/~.]")
# The value side of a type declaration such as `password: string;` names a type, not a credential.
_TYPE_NAME = re.compile(r"(?i)(?:string|number|bigint|boolean|symbol|object|unknown|never|undefined);?")
_KEYED_VALUE = re.compile(
    r"(?P<name>[A-Za-z_][\w.-]*)[\"']?"
    r"(?:[ \t]*(?::=|=>|=|:)[ \t]*(?P<q>[\"'`])(?P<quoted>[^\"'`\n]{1,256})(?P=q)"
    r"|=(?P<bare>[^\s\"'`$#;&|(){}\[\],<>]{1,256}+)(?![(\[])"
    r"|:[ \t]+(?P<yaml>[^\s\"'`#{}\[\],]{1,256})[ \t]*$)",
    re.MULTILINE,
)
_PASSWORD_NAME = re.compile(r"(?i)(?:passw(?:or)?d|passwd|(?:^|[_.-])pass)$")
_NAME_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_SECRET_WORDS = frozenset(
    {"secret", "secrets", "cred", "creds", "credential", "credentials", "passwd", "passphrase", "private"}
    | {"signing", "token", "tokens", "apikey"}
)
_SECRET_LAST_WORDS = frozenset({"key", "secret", "token"})
_SECRET_WORD_PAIRS = frozenset({("auth", "key"), ("api", "key"), ("access", "key")})
_ENCODED = re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2})+")


def is_placeholder(value: str) -> bool:
    if _PLACEHOLDER_MARK.search(value):
        return True
    residue = _PLACEHOLDER_WORD.sub("", value)
    # A placeholder word next to a long random run is camouflage: sk-proj-example<40 random chars> is a key.
    return residue != value and max(map(len, _ALNUM_RUN.findall(residue)), default=0) < 16


def _entropy(value: str) -> float:
    return -sum(n / len(value) * math.log2(n / len(value)) for n in Counter(value).values())


def _is_secret_name(name: str) -> bool:
    """CLIENT_SECRET_VALUE, S3_CREDS, apiKey, AUTH_KEY_ID: split on _, -, . and camelCase, then look at the words."""
    words = [word.lower() for word in _NAME_WORD.findall(name)]
    return bool(words) and (
        words[-1] in _SECRET_LAST_WORDS
        or bool(_SECRET_WORDS.intersection(words))
        or bool(_SECRET_WORD_PAIRS.intersection(itertools.pairwise(words)))
    )


def _plausible_password(value: str) -> bool:
    return (
        len(value) >= 6
        and any(char.isalpha() for char in value)
        and not _NOT_A_VALUE.search(value)
        and not _DUMMY_PASSWORD.search(value)
        and not _TYPE_NAME.fullmatch(value)
        and not is_placeholder(value)
        and _entropy(value) >= 2.5
    )


def _looks_random(value: str) -> bool:
    return (
        len(value) >= 16
        and sum(char.isdigit() for char in value) >= 2
        and any(char.isalpha() for char in value)
        and not _NOT_A_VALUE.search(value)
        and not is_placeholder(value)
        and _entropy(value) >= 3.5
    )


def _not_placeholder(value: str) -> bool:
    return not is_placeholder(value)


@dataclass(frozen=True)
class _Rule:
    name: str
    pattern: re.Pattern
    accepts: Callable[[str], bool] = _not_placeholder


_PROVIDER_RULES = (
    _Rule("aws-access-key-id", re.compile(r"\b(?P<secret>(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16})\b")),
    _Rule(
        "aws-secret-access-key",
        re.compile(
            r"(?i)(?:aws_?secret_?(?:access_?)?key|secret_?access_?key)[\"']?\s*(?::=|=>|=|:)\s*[\"']?"
            r"(?P<secret>[A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"
        ),
    ),
    _Rule(
        "private-key",
        re.compile(r"-----BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----(?P<secret>[A-Za-z0-9+/=\s:,.-]{40,})"),
    ),
    _Rule("github-token", re.compile(r"\b(?P<secret>gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b")),
    _Rule("slack-token", re.compile(r"\b(?P<secret>xox[abposr]-[A-Za-z0-9-]{10,})")),
    _Rule("stripe-live-key", re.compile(r"\b(?P<secret>[sr]k_live_[A-Za-z0-9]{16,})")),
    _Rule("google-api-key", re.compile(r"\b(?P<secret>AIza[A-Za-z0-9_-]{35})")),
    _Rule("openai-api-key", re.compile(r"\b(?P<secret>sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{40,})")),
    _Rule("anthropic-api-key", re.compile(r"\b(?P<secret>sk-ant-[A-Za-z0-9_-]{32,})")),
)
_SHAPE_RULES = (
    _Rule("jwt", re.compile(r"\b(?P<secret>eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,})")),
    # The token class leaves out $, { and <, so "$VAR", "${VAR}" and "<token>" never match.
    _Rule("authorization-bearer", re.compile(r"(?i)authorization:\s*bearer\s+(?P<secret>[A-Za-z0-9._~+/=-]{16,})")),
    _Rule(
        "generic-database-url",
        re.compile(
            r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|redis|rediss|mongodb(?:\+srv)?|amqps?|mssql)://"
            r"[^\s:/@'\"]*:(?P<secret>[^\s@/'\"]+)@"
        ),
        _plausible_password,
    ),
)


@dataclass(frozen=True)
class Finding:
    rule: str
    first_line: int
    last_line: int

    def within(self, spans: list[tuple[int, int]] | None) -> bool:
        return spans is None or any(self.first_line <= last and self.last_line >= first for first, last in spans)


@dataclass(frozen=True)
class _Target:
    text: str
    where: str
    source: str | None = None
    spans: list[tuple[int, int]] | None = None  # Only findings on these lines count; None means all of them.
    fixture: bool = False


@kitlib.hook_main("scan_secrets", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    root = _scan_dir(inp)
    target = _scan_target(inp, root)
    if target is None:
        return
    rules = sorted(
        {
            finding.rule
            for finding in find_in_text(target.text, root, target.source)
            if finding.within(target.spans) and not (target.fixture and is_generic(finding.rule))
        }
    )
    if rules:
        kitlib.pre_tool_decision("deny", _deny_reason(rules, target.where))


def is_generic(rule: str) -> bool:
    return rule.startswith("generic-") or rule in _GENERIC_RULES


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def find_in_text(text: str, cwd: Path, source: str | None = None) -> list[Finding]:
    """What the first external scanner that runs finds in text, plus what the built-in patterns find."""
    for name in _SCANNERS:
        exe = kitlib.find_tool(name, cwd)
        cmd = [exe, "stdin", *_REPORT_ARGS, *_source_attr(name, source)] if exe else None
        report = _run_scanner(cmd, cwd, text, _CONTENT_TIMEOUT) if cmd else None
        if report is not None:
            return [_finding(item) for item in report if _is_real(item)] + pattern_findings(text)
    return pattern_findings(text)


def scan_staged(repo: Path, base: str = "HEAD") -> list[tuple[str, str]]:
    """(rule, file) for each secret the index adds relative to the tree `base`, test-fixture noise left out."""
    found: set[tuple[str, str]] = set()
    for name in _SCANNERS:
        exe = kitlib.find_tool(name, repo)
        cmd = [exe, "git", "--pre-commit", "--staged", *_REPORT_ARGS, "."] if exe else None
        report = _run_scanner(cmd, repo, "", _STAGED_TIMEOUT) if cmd else None
        if report is not None:
            found = {
                (item.get("RuleID") or "unknown-rule", item.get("File") or "?") for item in report if _is_real(item)
            }
            break
    for path, added in staged_additions(repo, base).items():
        found |= {(finding.rule, path) for finding in pattern_findings(added)}
    return sorted((rule, path) for rule, path in found if not (is_generic(rule) and is_test_path(path)))


def pattern_findings(text: str) -> list[Finding]:
    return [*_rule_findings(text, (*_PROVIDER_RULES, *_SHAPE_RULES)), *_keyed_findings(text), *_decoded_findings(text)]


def _rule_findings(text: str, rules: tuple[_Rule, ...], suffix: str = "") -> Iterator[Finding]:
    for rule in rules:
        for match in rule.pattern.finditer(text):
            if rule.accepts(match.group("secret")):
                yield Finding(rule.name + suffix, _line_at(text, match.start()), _line_at(text, match.end()))


def _keyed_findings(text: str) -> Iterator[Finding]:
    """Secret-named keys (DB_PASSWORD, *_TOKEN, apiKey, ...) given a literal value that looks real."""
    for match in _KEYED_VALUE.finditer(text):
        name = match.group("name")
        value = match.group("quoted") or match.group("bare") or match.group("yaml")
        if _PASSWORD_NAME.search(name) and _plausible_password(value):
            rule = "generic-password-assignment"
        elif _is_secret_name(name) and _looks_random(value):
            rule = "generic-secret-assignment"
        else:
            continue
        line = _line_at(text, match.start())
        yield Finding(rule, line, line)


def _decoded_findings(text: str) -> Iterator[Finding]:
    """Provider keys hidden in base64 or hex blobs."""
    for match in itertools.islice(_ENCODED.finditer(text), _ENCODED_BLOB_LIMIT):
        decoded = _decode(match.group(0))
        if decoded:
            line = _line_at(text, match.start())
            for finding in _rule_findings(decoded, _PROVIDER_RULES, suffix="-encoded"):
                yield Finding(finding.rule, line, line)


def _decode(blob: str) -> str | None:
    try:
        if _HEX.fullmatch(blob):
            raw = bytes.fromhex(blob)
        else:
            raw = base64.b64decode(blob.replace("-", "+").replace("_", "/") + "=" * (-len(blob) % 4))
        decoded = raw.decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    printable = sum(char.isprintable() or char.isspace() for char in decoded)
    return decoded if decoded and printable >= 0.95 * len(decoded) else None


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _run_scanner(cmd: list[str], cwd: Path, stdin: str, timeout: int) -> list[dict] | None:
    """The scanner's findings, or None when it couldn't run.

    Exit 1 alone can't mean a leak: a mise shim with no version set also exits 1, printing no report.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        report = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    findings = report or []
    if proc.returncode not in (0, 1) or not isinstance(findings, list) or bool(findings) != (proc.returncode == 1):
        return None
    return findings


def _is_real(item: dict) -> bool:
    secret, rule = str(item.get("Secret") or ""), str(item.get("RuleID") or "")
    return not (is_placeholder(secret) or (is_generic(rule) and _DUMMY_PASSWORD.search(secret)))


def _finding(item: dict) -> Finding:
    first = item.get("StartLine") or 1
    return Finding(item.get("RuleID") or "unknown-rule", first, max(item.get("EndLine") or first, first))


def _source_attr(scanner: str, source: str | None) -> list[str]:
    return ["--set-attr", f"path={source}"] if scanner == "betterleaks" and source else []


def staged_additions(repo: Path, base: str = "HEAD") -> dict[str, str]:
    """Added lines per file between the tree `base` and the index."""
    cmd = ["git", "diff", "--cached", base, "-U0", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/"]
    code, out, err = kitlib.run(cmd, repo)
    if code != 0:
        raise RuntimeError(f"git diff --cached failed: {err.strip()[:300]}")
    added: dict[str, list[str]] = {}
    path, in_header = None, False
    for line in out.splitlines():
        if line.startswith("diff --git "):
            path, in_header = None, True
        elif in_header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            in_header = False
        elif not in_header and path and line.startswith("+"):
            added.setdefault(path, []).append(line[1:])
    return {name: "\n".join(lines) for name, lines in added.items()}


def _scan_target(inp: dict, root: Path) -> _Target | None:
    tool, tool_input = inp.get("tool_name"), inp.get("tool_input") or {}
    if tool == "Bash":
        command = tool_input.get("command")
        return _Target(command, "shell command") if isinstance(command, str) and command.strip() else None
    if tool not in ("Write", "Edit", "NotebookEdit"):
        return None
    path = _target_path(inp)
    if path is not None and (_SKIPPED_DIRS.intersection(path.parts) or _is_ignored_env_file(path)):
        return None
    text, spans = _written_text(tool, tool_input, path)
    if not text.strip():
        return None
    if path is None:
        return _Target(text, "the edited file", spans=spans)
    relative = path.relative_to(root) if path.is_relative_to(root) else path
    return _Target(text, _display_path(path, inp), str(path), spans, is_test_path(relative.as_posix()))


def _target_path(inp: dict) -> Path | None:
    tool_input = inp.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else Path(inp.get("cwd") or os.getcwd()) / path).resolve()


def _written_text(tool: str, tool_input: dict, path: Path | None) -> tuple[str, list[tuple[int, int]] | None]:
    """The text to scan, and which of its lines this call writes (None: all of them)."""
    if tool == "Write":
        return str(tool_input.get("content") or ""), None
    if tool == "NotebookEdit":
        return str(tool_input.get("new_source") or ""), None
    new, old = str(tool_input.get("new_string") or ""), str(tool_input.get("old_string") or "")
    current = _read_edited_file(path)
    if current is None or not old or old not in current:
        return new, None
    return _apply_edit(current, old, new, bool(tool_input.get("replace_all")))


def _read_edited_file(path: Path | None) -> str | None:
    if path is None or not path.is_file() or path.stat().st_size > _EDITED_FILE_LIMIT:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _apply_edit(current: str, old: str, new: str, replace_all: bool) -> tuple[str, list[tuple[int, int]]]:
    """The file as it will be after the edit, so a key split across two edits is seen whole, and the edited lines."""
    pieces, starts, offset, i = [], [], 0, 0
    while (found := current.find(old, i)) >= 0:
        pieces.append(current[i:found])
        offset += found - i
        starts.append(offset)
        pieces.append(new)
        offset += len(new)
        i = found + len(old)
        if not replace_all:
            break
    pieces.append(current[i:])
    after = "".join(pieces)
    return after, [(_line_at(after, start), _line_at(after, start + len(new))) for start in starts]


def _is_ignored_env_file(path: Path) -> bool:
    """An untracked, git-ignored .env is where the deny message tells secrets to go, so writing one is fine."""
    if not _ENV_FILE.fullmatch(path.name) or path.name == ".env.example":
        return False
    directory = kitlib._nearest_existing_dir(path.parent)
    code, _, _ = kitlib.run(["git", "check-ignore", "-q", "--", str(path)], directory, timeout=10)
    return code == 0


def _scan_dir(inp: dict) -> Path:
    cwd = kitlib._nearest_existing_dir(Path(inp.get("cwd") or os.getcwd())) or Path.cwd()
    return kitlib.project_root(str(cwd)) or cwd


def _display_path(path: Path, inp: dict) -> str:
    cwd = Path(inp.get("cwd") or os.getcwd()).resolve()
    return str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path)


def _deny_reason(rules: list[str], where: str) -> str:
    return (
        f"kit: blocked a likely secret in {where} (rule: {', '.join(rules)}). "
        "Keep it out of tracked files and commands: use an environment variable instead "
        '("$API_TOKEN" in shell, read from os.environ in code) or an untracked, git-ignored .env. '
        "If it isn't a real credential, use an obvious placeholder such as <your-token> or changeme."
    )


if __name__ == "__main__":
    main()
