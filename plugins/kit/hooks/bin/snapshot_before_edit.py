"""PreToolUse Edit|Write|NotebookEdit: record the file's lint and format baseline, once per file per session."""

from collections import Counter
from pathlib import Path

import kitlib

FAIL_CLOSED = False


@kitlib.hook_main("snapshot_before_edit", fail_closed=FAIL_CLOSED)
def main() -> None:
    inp = kitlib.read_input()
    path = kitlib._edited_path(inp)
    if path is None:
        return
    root = kitlib.project_root(str(path))
    if root is None or kitlib._is_exempt(root, path) or kitlib.is_non_project(root, path):
        return
    if not _is_checked(root, path):
        return
    key = str(path)
    with kitlib.State(inp) as st:
        if "before" in st["edits"].get(key, {}):
            return
    baseline = _baseline(root, path)
    with kitlib.State(inp) as st:
        st["edits"].setdefault(key, {"seq": 0}).setdefault("before", baseline)


def _is_checked(root: Path, path: Path) -> bool:
    return bool(kitlib.linters_for(root, path)) or kitlib._detect_formatter(root, path) is not None


def _baseline(root: Path, path: Path) -> dict:
    violations = kitlib._violations(root, path)
    counts = Counter(violation.fingerprint for violation in violations)
    return {
        "lint": {kitlib._fingerprint_key(fingerprint): count for fingerprint, count in counts.items()},
        "clean_format": kitlib.format_check(root, path),
        "clean_lint_fixable": not any(violation.fixable for violation in violations),
    }


if __name__ == "__main__":
    main()
