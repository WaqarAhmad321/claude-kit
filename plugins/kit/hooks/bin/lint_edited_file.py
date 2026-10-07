"""PostToolUse Edit|Write|NotebookEdit: tidy the edited file and feed back lint problems the edit introduced."""

from collections import Counter
from pathlib import Path

import kitlib

FAIL_CLOSED = False

_MAX_ITEMS = 40
_CLOSING = (
    "These were introduced by your edit. Fix them in the code; don't add suppression comments or change lint config."
)


@kitlib.hook_main("lint_edited_file", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    path = kitlib._edited_path(inp)
    if path is None:
        return
    root = kitlib.project_root(str(path))
    if kitlib.is_non_project(root, path):
        return
    before = _record_edit(inp, path)
    if before is None or root is None or kitlib._is_exempt(root, path) or not path.is_file():
        return
    _tidy(root, path, before)
    after = kitlib._violations(root, path)
    baseline = Counter({kitlib._fingerprint_from_key(key): count for key, count in before["lint"].items()})
    introduced = Counter(violation.fingerprint for violation in after) - baseline
    if introduced:
        kitlib.feedback(_report(root, path, after, introduced))


def _record_edit(inp: dict, path: Path) -> dict | None:
    with kitlib.State(inp) as st:
        st["edit_seq"] += 1
        entry = st["edits"].setdefault(str(path), {"seq": 0})
        entry["seq"] = st["edit_seq"]
        return entry.get("before")


def _tidy(root: Path, path: Path, before: dict) -> None:
    """Autofix and format only files that were clean before, so legacy files are never rewritten wholesale."""
    if before.get("clean_lint_fixable"):
        kitlib.apply_fixes(root, path)
    if before.get("clean_format"):
        kitlib.apply_format(root, path)


def _report(root: Path, path: Path, after: list, introduced: Counter) -> str:
    items = [
        _describe(fingerprint, count, [v for v in after if v.fingerprint == fingerprint])
        for fingerprint, count in sorted(introduced.items())
    ]
    lines = [f"kit: your edit to {path.relative_to(root)} introduced lint problems:", *items[:_MAX_ITEMS]]
    if len(items) > _MAX_ITEMS:
        lines.append(f"- ...and {len(items) - _MAX_ITEMS} more")
    lines.append(_CLOSING)
    return "\n".join(lines)


def _describe(fingerprint: tuple[str, str, str], new_count: int, occurrences: list) -> str:
    tool, code, _ = fingerprint
    line_numbers = ", ".join(str(violation.line) for violation in occurrences)
    if new_count == len(occurrences):
        where = f"line {line_numbers}" if new_count == 1 else f"lines {line_numbers}"
    else:
        where = f"{new_count} new; it occurs at lines {line_numbers}"
    if tool == "suppression":
        return f'- suppression comment "{code}" added ({where})'
    return f"- {tool} {code}: {occurrences[0].message} ({where})"


if __name__ == "__main__":
    main()
