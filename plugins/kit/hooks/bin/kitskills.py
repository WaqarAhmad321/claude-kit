"""Map project files to the module skills in `.claude/skills/*/SKILL.md` whose `paths` globs match them."""

from __future__ import annotations

import dataclasses
import functools
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import kitlib

_LOCKFILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
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
        "flake.lock",
        "mix.lock",
    }
)
_GENERATED_DOCS = frozenset({"CONTRACTS.md", "SCHEMA.md"})
_EXEMPT_DIRS = frozenset({".claude", ".git", "node_modules", "migrations"})

_KEY_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.*)$")
_BRACE_GROUP = re.compile(r"\{([^{}]*,[^{}]*)\}")
_GLOB_CHARS = re.compile(r"[*?\[{\\]")


@dataclass(frozen=True)
class Skill:
    name: str
    directory: str
    paths: tuple[str, ...]
    resolved_paths: tuple[str, ...] = ()

    @property
    def skill_md(self) -> str:
        return f".claude/skills/{self.directory}/SKILL.md"


def find_skills(root: Path) -> list[Skill]:
    """Every skill under `root/.claude/skills/`, module skill or not, sorted by directory."""
    skills_dir = Path(root) / ".claude" / "skills"
    if not skills_dir.is_dir():
        return []
    return [_read_skill(path) for path in sorted(skills_dir.glob("*/SKILL.md")) if path.is_file()]


def read_skill_file(path: Path) -> Skill:
    return _read_skill(Path(path))


def module_skills(root: Path) -> list[Skill]:
    return [
        dataclasses.replace(skill, resolved_paths=_resolved_globs(Path(root), skill.paths))
        for skill in find_skills(root)
        if skill.paths
    ]


def matching_skills(skills: list[Skill], rel_path: str) -> list[Skill]:
    return [
        skill for skill in skills if any(glob_matches(glob, rel_path) for glob in (*skill.paths, *skill.resolved_paths))
    ]


def canonical_name(root: Path | None, invoked: str) -> str:
    """The skill's `name` for an invocation by name or by directory, which both work for project skills."""
    for skill in find_skills(root) if root else []:
        if invoked in (skill.name, skill.directory):
            return skill.name
    return invoked


def is_exempt(rel_path: str) -> bool:
    parts = PurePosixPath(rel_path).parts
    if not parts:
        return True
    name, directories = parts[-1], parts[:-1]
    return name in _LOCKFILES or name in _GENERATED_DOCS or any(part in _EXEMPT_DIRS for part in directories)


def dirty_files(root: Path) -> set[str]:
    """Repo-relative paths that are modified, deleted or untracked; untracked directories are listed file by file."""
    cmd = ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"]
    code, out, err = kitlib.run(cmd, Path(root), timeout=30)
    if code != 0:
        raise RuntimeError(f"git status failed: {err.strip()[:300]}")
    return {entry[3:] for entry in out.split("\0") if len(entry) > 3}


def glob_matches(glob: str, rel_path: str) -> bool:
    return any(pattern.fullmatch(rel_path) for pattern in _compiled(glob))


def _resolved_globs(root: Path, globs: tuple[str, ...]) -> tuple[str, ...]:
    """The globs again with their literal leading directories resolved, so a symlinked module's real files match."""
    found = []
    for expanded in (variant for glob in globs for variant in _expand_braces(glob.strip())):
        prefix, rest = _literal_prefix(expanded.removeprefix("./").lstrip("/"))
        real = (root / prefix).resolve() if prefix else None
        if real is None or real == root / prefix or not real.is_relative_to(root):
            continue
        found.append("/".join(part for part in (real.relative_to(root).as_posix(), rest) if part))
    return tuple(dict.fromkeys(found))


def _literal_prefix(glob: str) -> tuple[str, str]:
    parts = glob.split("/")
    literal = next((index for index, part in enumerate(parts) if _GLOB_CHARS.search(part)), len(parts))
    return "/".join(parts[:literal]), "/".join(parts[literal:])


def _read_skill(skill_md: Path) -> Skill:
    return skill_from_text(skill_md.read_text(encoding="utf-8", errors="replace"), skill_md.parent.name)


def skill_from_text(text: str, directory: str) -> Skill:
    """The skill a SKILL.md with this text declares from `.claude/skills/<directory>/`."""
    fields = parse_frontmatter(text)
    name = fields.get("name")
    return Skill(
        name=name if isinstance(name, str) and name else directory,
        directory=directory,
        paths=tuple(_as_globs(fields.get("paths"))),
    )


def parse_frontmatter(text: str) -> dict[str, str | list[str]]:
    """Top-level `key: value` pairs and `- item` lists from YAML frontmatter; enough for `name` and `paths`."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return {}
    fields: dict[str, str | list[str]] = {}
    key = None
    for line in lines[1:]:
        if line.rstrip() == "---":
            return fields
        entry = _KEY_LINE.match(line)
        item = _LIST_ITEM.match(line)
        if entry:
            key = entry.group(1)
            fields[key] = _scalar(entry.group(2)) or []
        elif item and isinstance(fields.get(key), list):
            fields[key].append(_unquote(_strip_comment(item.group(1))))
    # Without a closing marker the YAML doesn't parse, and Claude Code loads the skill with no fields.
    return {}


def _scalar(raw: str) -> str | list[str]:
    value = _strip_comment(raw)
    if value.startswith("[") and value.endswith("]"):
        return [_unquote(part) for part in _split_commas(value[1:-1])]
    return _unquote(value)


def _strip_comment(raw: str) -> str:
    value = raw.strip()
    quoted = re.match(r"""(["']).*?\1""", value)
    if quoted:
        return quoted.group(0)
    return re.sub(r"\s+#.*$", "", value)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _as_globs(value) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [_unquote(part) for part in _split_commas(value)]
    else:
        items = []
    return [item.strip() for item in items if item.strip()]


def _split_commas(text: str) -> list[str]:
    """Split on commas outside quotes and brace groups, so `src/*.{ts,tsx}` stays one glob."""
    parts, current, depth, quote = [], [], 0, None
    for char in text:
        if quote:
            quote = None if char == quote else quote
        elif char in ("'", '"'):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth = max(depth - 1, 0)
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


@functools.cache
def _compiled(glob: str) -> tuple[re.Pattern, ...]:
    patterns = (_glob_regex(expanded) for expanded in _expand_braces(glob.strip()))
    return tuple(pattern for pattern in patterns if pattern is not None)


def _expand_braces(glob: str) -> list[str]:
    group = _BRACE_GROUP.search(glob)
    if group is None:
        return [glob]
    head, tail = glob[: group.start()], glob[group.end() :]
    return [expanded for option in group.group(1).split(",") for expanded in _expand_braces(head + option + tail)]


def _glob_regex(glob: str) -> re.Pattern | None:
    """Anchored at the project root; `*` stays inside one segment and a `**` segment spans zero or more."""
    glob = glob.removeprefix("./").lstrip("/")
    if glob.endswith("/"):
        glob += "**"
    parts = glob.split("/")
    pieces = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            pieces.append(".+" if last else "(?:[^/]+/)*")
            continue
        segment = _segment_regex(part)
        if segment is None:
            return None
        pieces.append(segment if last else segment + "/")
    return re.compile("".join(pieces)) if glob else None


def _segment_regex(part: str) -> str | None:
    pieces, index = [], 0
    while index < len(part):
        char = part[index]
        if char == "*":
            while index + 1 < len(part) and part[index + 1] == "*":
                index += 1
            pieces.append("[^/]*")
        elif char == "?":
            pieces.append("[^/]")
        elif char == "[":
            bracket = _bracket_regex(part, index)
            if bracket is None:
                return None
            regex, index = bracket
            pieces.append(regex)
        elif char == "\\" and index + 1 < len(part):
            index += 1
            pieces.append(re.escape(part[index]))
        else:
            pieces.append(re.escape(char))
        index += 1
    return "".join(pieces)


def _bracket_regex(part: str, start: int) -> tuple[str, int] | None:
    """`[abc]`, `[!abc]` or `[^abc]` starting at `start`; None when unclosed, which makes the glob match nothing."""
    body_start = start + 1
    negate = part[body_start : body_start + 1] in ("!", "^")
    if negate:
        body_start += 1
    close = part.find("]", body_start + 1)
    if close == -1:
        return None
    body = "".join("\\" + char if char in "\\[]&~|^" else char for char in part[body_start:close])
    return ("[^/" + body + "]" if negate else "[" + body + "]"), close
