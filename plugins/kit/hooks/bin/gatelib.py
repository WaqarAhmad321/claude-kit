"""Helpers shared by the stop gate, the journey recorder and kit_baseline. Python 3 stdlib only."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import guard_protected_files as protected
import kitlib
import kitskills

BASELINE_REL = ".claude/kit-baseline.json"
LINT_TIMEOUT = 30
TYPECHECK_TIMEOUT = 180

LOCKFILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
    }
)
_TS_EXTS = frozenset({".ts", ".tsx", ".mts", ".cts"})
_TOOL_MYPY_TABLE = re.compile(r"^\s*\[tool\.mypy[\].]", re.MULTILINE)
_MYPY_CONFIGS = ("mypy.ini", ".mypy.ini")
_PIN_PREFIX = "refs/kit/start/"
_PIN = re.compile(r"refs/kit/start/(\d+)-.*")
_PIN_TTL_SECONDS = 7 * 24 * 3600
_TSC_ERROR = re.compile(r"^(?P<file>.+?)\((?P<line>\d+),\d+\): error (?P<code>TS\d+): (?P<message>.*)$")
_REFLOG_FORMAT = "%H%x1f%P%x1f%gd%x1f%gs"
_REFLOG_TIME = re.compile(r"@\{(\d+)\}$")


@dataclass(frozen=True)
class HeadMove:
    commit: str
    parents: tuple[str, ...]
    at: int  # Unix seconds
    subject: str


def changed_since(root: Path, commit: str | None) -> dict[str, str]:
    """Paths that differ from `commit` (committed since, staged, unstaged, deleted or untracked), each mapped to
    its path in `commit`, which differs after a rename. With no commit, every file is new."""
    tracked = _diff_names(root, commit) if commit else {rel: rel for rel in _ls_files(root)}
    return tracked | {rel: rel for rel in _ls_files(root, "--others", "--exclude-standard")}


def _diff_names(root: Path, commit: str) -> dict[str, str]:
    out = _git(root, "diff", "--name-status", "-z", "-M", "--no-ext-diff", commit, "--")
    fields, changed = iter(out.split("\0")), {}
    for status in fields:
        if not status:
            continue
        source = next(fields, "")
        changed[next(fields, source) if status[0] in "RC" else source] = source
        if status[0] == "R":
            changed.setdefault(source, source)  # The old path is gone, which is a change too.
    return changed


def _ls_files(root: Path, *options: str) -> list[str]:
    return [rel for rel in _git(root, "ls-files", "-z", *options).split("\0") if rel]


def head_commit(root: Path) -> str | None:
    code, out, _ = kitlib.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], root, timeout=10)
    return out.strip() if code == 0 and out.strip() else None


def commit_exists(root: Path, commit: str) -> bool:
    code, _, _ = kitlib.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], root, timeout=10)
    return code == 0


def is_ancestor(root: Path, commit: str, of: str) -> bool:
    code, _, _ = kitlib.run(["git", "merge-base", "--is-ancestor", commit, of], root, timeout=60)
    return code == 0


def pin_commit(root: Path, commit: str, label: str) -> None:
    """Keep `commit` alive through amends, rebases and gc with a hidden ref; pins older than a week are dropped."""
    now = int(time.time())
    name = re.sub(r"[^A-Za-z0-9_-]", "-", label)
    kitlib.run(["git", "update-ref", f"{_PIN_PREFIX}{now}-{name}", commit], root, timeout=10)
    code, out, _ = kitlib.run(["git", "for-each-ref", "--format=%(refname)", _PIN_PREFIX], root, timeout=10)
    stale = [ref for ref in out.split() if (pin := _PIN.fullmatch(ref)) and int(pin[1]) < now - _PIN_TTL_SECONDS]
    if code == 0 and stale:
        run_with_input(["git", "update-ref", "--stdin"], root, "".join(f"delete {ref}\n" for ref in stale), 10)


def files_committed_since(root: Path, started_at: int, *revisions: str) -> set[str]:
    out = _git(root, "log", f"--since=@{started_at}", "--name-only", "--format=", *revisions)
    return {line for line in out.splitlines() if line}


def stashes_since(root: Path, started_at: int) -> dict[str, list[str]]:
    """Stash entries made since `started_at`, with the files each holds."""
    entries = (line.partition("\t") for line in _git(root, "stash", "list", "--format=%gd%x09%ct").splitlines())
    names = [name for name, _, stamp in entries if stamp.isdigit() and int(stamp) >= started_at]
    return {name: _git(root, "stash", "show", "--include-untracked", "--name-only", name).split() for name in names}


def commits_not_checked_out(root: Path, started_at: int) -> list[str]:
    """Commits made since `started_at` on local branches that no worktree has checked out, newest first."""
    shas = [worktree.get("HEAD", "") for worktree in worktrees(root)]
    heads = [sha for sha in shas if sha.strip("0")]  # An unborn branch reports an all-zero HEAD.
    return _git(root, "rev-list", "--branches", f"--since=@{started_at}", "--not", *heads).split()


def worktrees(root: Path) -> list[dict[str, str]]:
    """Every worktree of `root`'s repository as `git worktree list` gives it: its attributes by name, such as
    `worktree` (its path), `HEAD` and `bare`."""
    found, attributes = [], {}
    for field in _git(root, "worktree", "list", "--porcelain", "-z").split("\0"):
        if field:
            name, _, value = field.partition(" ")
            attributes[name] = value
        elif attributes:
            found.append(attributes)
            attributes = {}
    return found


def head_moves(root: Path) -> list[HeadMove]:
    """HEAD's reflog in `root`'s worktree, oldest first."""
    if head_commit(root) is None:
        return []
    out = _git(root, "reflog", "show", "--date=unix", f"--format={_REFLOG_FORMAT}", "HEAD", "--")
    moves = []
    for line in out.splitlines():
        fields = line.split("\x1f", 3)
        when = _REFLOG_TIME.search(fields[2]) if len(fields) == 4 else None
        if when:
            moves.append(HeadMove(fields[0], tuple(fields[1].split()), int(when.group(1)), fields[3]))
    return moves[::-1]


def branches_containing(root: Path, commit: str) -> list[str]:
    return _git(root, "branch", "--contains", commit, "--format=%(refname:short)").split()


def _git(root: Path, *args: str) -> str:
    code, out, err = kitlib.run(["git", *args], root, timeout=60)
    if code != 0:
        raise RuntimeError(f"git {args[0]} failed: {err.strip()[:300]}")
    return out


def file_at(root: Path, commit: str | None, rel: str) -> str | None:
    if commit is None:
        return None
    code, out, _ = kitlib.run(["git", "show", f"{commit}:{rel}"], root, timeout=30)
    return out if code == 0 else None


def project_files(root: Path) -> list[Path]:
    """Tracked and untracked, not ignored, non-exempt files."""
    paths = {root / rel for rel in _ls_files(root, "--cached", "--others", "--exclude-standard")}
    return sorted(path for path in paths if path.is_file() and not is_exempt(root, path))


def run_with_input(cmd: list[str], cwd: Path, text: str, timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def is_exempt(root: Path, path: Path) -> bool:
    return kitlib._is_exempt(root, path) or path.name in LOCKFILES


def rel_path(root: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(root).as_posix()


def journeys_config(config: dict) -> dict | None:
    gate = config.get("gate")
    journeys = gate.get("journeys") if isinstance(gate, dict) else None
    if not isinstance(journeys, dict):
        return None
    command, watch = journeys.get("command"), journeys.get("watch")
    if not isinstance(command, str) or not command.strip():
        return None
    return {"command": command, "watch": [glob for glob in watch or [] if isinstance(glob, str) and glob.strip()]}


def watch_matcher(globs: list[str]) -> Callable[[str], bool]:
    patterns = [_glob_regex(glob) for glob in globs]
    return lambda rel: any(pattern.fullmatch(rel) for pattern in patterns)


def _glob_regex(glob: str) -> re.Pattern:
    """Gitignore-style: `**` spans directories, a pattern without a slash matches at any depth."""
    glob = glob.strip()
    anchored = "/" in glob.rstrip("/")
    glob = glob.lstrip("/")
    body, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            body.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            body.append(".*")
            i += 2
        elif glob[i] == "*":
            body.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            body.append("[^/]")
            i += 1
        else:
            body.append(re.escape(glob[i]))
            i += 1
    prefix = "" if anchored else "(?:.*/)?"
    # A pattern naming a directory covers everything under it, as in .gitignore.
    return re.compile(prefix + "".join(body).rstrip("/") + "(?:/.*)?")


def watched_fingerprint(root: Path, watch: list[str]) -> str:
    """Hash of the content of every watched file in the working tree. Committing doesn't change it."""
    matches = watch_matcher(watch)
    blobs = {rel: blob for rel, blob in _index_blobs(root).items() if matches(rel)}
    blobs |= _worktree_blobs(root, sorted(rel for rel in kitskills.dirty_files(root) if matches(rel)))
    return hashlib.sha256(json.dumps(sorted(blobs.items())).encode()).hexdigest()


def _index_blobs(root: Path) -> dict[str, str]:
    blobs = {}
    for record in _git(root, "ls-files", "-s", "-z").split("\0"):
        meta, _, rel = record.partition("\t")
        if rel:
            blobs[rel] = meta.split()[1]
    return blobs


def _worktree_blobs(root: Path, rels: list[str]) -> dict[str, str]:
    blobs = dict.fromkeys(rels, "deleted")
    present = [rel for rel in rels if (root / rel).is_file()]
    if present:
        code, out, err = run_with_input(["git", "hash-object", "--stdin-paths"], root, "\n".join(present) + "\n", 30)
        if code != 0:
            raise RuntimeError(f"git hash-object failed: {err.strip()[:300]}")
        blobs.update(zip(present, out.split(), strict=True))
    return blobs


def text_violations(root: Path, path: Path, text: str) -> list:
    """Lint violations and suppression comments of `text`, linted as if it were `path`."""
    found = [
        violation
        for tool in kitlib._detect_linters(root, path)
        for violation in _TEXT_LINTERS[tool.name](tool, path, text)
    ]
    return found + kitlib.suppression_violations(text, path)


def _ruff_text(tool, path: Path, text: str) -> list:
    cmd = [tool.exe, "check", "--output-format", "json", "--no-fix", "--force-exclude", "--no-cache"]
    code, out, err = run_with_input([*cmd, "--stdin-filename", str(path), "-"], tool.cwd, text, LINT_TIMEOUT)
    if code not in (0, 1):
        raise RuntimeError(f"ruff check failed: {(err or out).strip()[:500]}")
    return [
        kitlib._Violation(
            "ruff",
            item.get("code") or "syntax-error",
            item.get("message", ""),
            (item.get("location") or {}).get("row", 0),
            False,
        )
        for item in json.loads(out or "[]")
    ]


def _eslint_text(tool, path: Path, text: str) -> list:
    cmd = [tool.exe, "--format", "json", "--stdin", "--stdin-filename", str(path)]
    code, out, err = run_with_input(cmd, tool.cwd, text, LINT_TIMEOUT)
    if code not in (0, 1):
        raise RuntimeError(f"eslint failed: {(err or out).strip()[:500]}")
    return [
        kitlib._Violation("eslint", item.get("ruleId") or "fatal", item.get("message", ""), item.get("line", 0), False)
        for result in json.loads(out or "[]")
        for item in result.get("messages", [])
    ]


def _oxlint_text(tool, path: Path, text: str) -> list:
    # oxlint can't read stdin, so lint a hidden sibling copy that resolves the same config.
    copy = path.with_name(f".{path.stem}.kit-head{path.suffix}")
    try:
        copy.write_text(text, encoding="utf-8")
        return kitlib._oxlint_violations(tool, copy)
    finally:
        copy.unlink(missing_ok=True)


_TEXT_LINTERS = {"ruff": _ruff_text, "eslint": _eslint_text, "oxlint": _oxlint_text}


@dataclass(frozen=True)
class TypeJob:
    tool: str
    exe: str
    cwd: Path
    files: tuple[Path, ...]


def typecheck_jobs(root: Path, paths: list[Path]) -> list[TypeJob]:
    """One mypy or tsc run per config directory that governs some of `paths`; unconfigured files are skipped."""
    groups: dict[tuple[str, str, Path], list[Path]] = {}
    for path in paths:
        target = _typecheck_target(root, Path(path))
        if target:
            groups.setdefault(target, []).append(Path(path))
    return [TypeJob(tool, exe, cwd, tuple(sorted(files))) for (tool, exe, cwd), files in sorted(groups.items())]


def _typecheck_target(root: Path, path: Path) -> tuple[str, str, Path] | None:
    if path.suffix in kitlib._PY_EXTS:
        config_dir = kitlib._nearest_dir(root, path, _has_mypy_config)
        exe = config_dir and kitlib._nearest_tool("mypy", root, path)
        return ("mypy", exe, config_dir) if exe else None
    if path.suffix in _TS_EXTS:
        config_dir = kitlib._nearest_dir(root, path, lambda directory: (directory / "tsconfig.json").is_file())
        exe = config_dir and _local_tsc(root, config_dir)
        return ("tsc", exe, config_dir) if exe else None
    return None


def _has_mypy_config(directory: Path) -> bool:
    if kitlib._has_any(directory, _MYPY_CONFIGS):
        return True
    pyproject = directory / "pyproject.toml"
    return pyproject.is_file() and bool(
        _TOOL_MYPY_TABLE.search(pyproject.read_text(encoding="utf-8", errors="replace"))
    )


def _local_tsc(root: Path, config_dir: Path) -> str | None:
    ancestors = kitlib._ancestors(root, config_dir / "tsconfig.json")
    return next((exe for exe in (kitlib._local_tool("tsc", d) for d in ancestors) if exe), None)


def run_typecheck(root: Path, job: TypeJob) -> dict[str, list]:
    """Type errors in the job's files, keyed by repo-relative path. Raises TimeoutExpired or RuntimeError."""
    wanted = {rel_path(root, path) for path in job.files}
    by_file: dict[str, list] = {}
    for path, violation in _RUNNERS[job.tool](job):
        rel = rel_path(root, path) if path.resolve().is_relative_to(root) else None
        if rel in wanted:
            by_file.setdefault(rel, []).append(violation)
    return by_file


def _run_mypy(job: TypeJob) -> list[tuple[Path, object]]:
    cmd = [job.exe, "--follow-imports=silent", "-O", "json", *map(str, job.files)]
    code, out, err = kitlib.run(cmd, job.cwd, timeout=TYPECHECK_TIMEOUT)
    if code not in (0, 1):
        raise RuntimeError(f"mypy failed: {_first_line(err or out)}")
    items = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [
        (
            job.cwd / item["file"],
            kitlib._Violation("mypy", item.get("code") or "error", item["message"], item["line"], False),
        )
        for item in items
        if item.get("severity") == "error"
    ]


def _run_tsc(job: TypeJob) -> list[tuple[Path, object]]:
    code, out, err = kitlib.run([job.exe, "-b", "--noEmit", "--pretty", "false"], job.cwd, TYPECHECK_TIMEOUT)
    diagnostics = _parse_tsc(out)
    if code != 0 and _build_config_failed(diagnostics):
        cmd = [job.exe, "--noEmit", "-p", "tsconfig.json", "--pretty", "false"]
        code, out, err = kitlib.run(cmd, job.cwd, TYPECHECK_TIMEOUT)
        diagnostics = _parse_tsc(out)
    if code != 0 and not diagnostics:
        raise RuntimeError(f"tsc failed: {_first_line(out or err)}")
    return [(job.cwd / file, violation) for file, violation in diagnostics]


def _parse_tsc(out: str) -> list[tuple[str, object]]:
    diagnostics: list[tuple[str, list]] = []
    for line in out.splitlines():
        match = _TSC_ERROR.match(line)
        if match:
            diagnostics.append((match["file"], [match["code"], match["message"], int(match["line"])]))
        elif line.startswith(" ") and diagnostics:
            diagnostics[-1][1][1] += " " + line.strip()
    return [(file, kitlib._Violation("tsc", code, message, line, False)) for file, (code, message, line) in diagnostics]


def _build_config_failed(diagnostics: list[tuple[str, object]]) -> bool:
    """`tsc -b` reports project-reference problems against the tsconfig itself, or with no location."""
    return not diagnostics or any(PurePosixPath(file).name.startswith("tsconfig") for file, _ in diagnostics)


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "no output")[:300]


_RUNNERS = {"mypy": _run_mypy, "tsc": _run_tsc}


def tool_version(job: TypeJob) -> str:
    code, out, err = kitlib.run([job.exe, "--version"], job.cwd, timeout=60)
    return _first_line(out or err) if code == 0 else "unknown"


def load_baseline(root: Path) -> dict | None:
    path = root / BASELINE_REL
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not (
        isinstance(data, dict) and isinstance(data.get("tools"), dict) and isinstance(data.get("fingerprints"), dict)
    ):
        raise ValueError("it must be a JSON object with `tools` and `fingerprints`")
    return data


def baseline_counter(baseline: dict, rel: str) -> Counter:
    counts = baseline["fingerprints"].get(rel) or {}
    return Counter({kitlib._fingerprint_from_key(key): count for key, count in counts.items()})


def baseline_size(baseline: dict | None) -> int:
    return sum(sum(counts.values()) for counts in (baseline or {}).get("fingerprints", {}).values())


def gate_config_key(root: Path, rel: str, base_commit: str | None, base_rel: str) -> str | None:
    """Identity of a changed file's gate configuration; None when it holds none or its gate part didn't change."""
    path = root / rel
    if protected._is_gate_file(rel, PurePosixPath(rel).name):
        return _digest(path.read_bytes()) if path.is_file() else "deleted"
    gates = _section_gates(root, rel)
    if gates is None:
        return None
    now = gates(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "")
    before = gates(file_at(root, base_commit, base_rel) or "")
    return None if now == before else _digest(json.dumps(now, sort_keys=True, default=str).encode())


def _section_gates(root: Path, rel: str):
    try:
        return protected._section_gates(root, rel)
    except ValueError:
        return None  # A makefile's gate part depends on kit.json; the stop gate reports an invalid one itself.


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
