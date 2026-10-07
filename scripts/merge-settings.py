"""Merge kit-owned settings into a Claude Code settings.json.

The kit files, and the optional local overlay after them, first combine with each other: dicts recursively
and lists as an ordered union. The result merges into the target the same way, so machine-specific entries
survive, except that keys listed in OWNED are replaced wholesale, so an entry the kit drops goes away there too.

Entries in PATH_LISTS may name environment variables, which install.sh sets, and `~`; they're written out as
absolute paths, since Claude Code expands neither variables nor, for additionalDirectories, `~`.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

OWNED = frozenset({("autoMode", "environment"), ("sandbox",)})
PATH_LISTS = (("permissions", "additionalDirectories"), ("sandbox", "filesystem", "denyWrite"))


def merge(base, kit, owned, path=()):
    for key, value in kit.items():
        here = (*path, key)
        if here in owned or key not in base:
            base[key] = value
        elif isinstance(value, dict) and isinstance(base[key], dict):
            merge(base[key], value, owned, here)
        elif isinstance(value, list) and isinstance(base[key], list):
            base[key] = base[key] + [item for item in value if item not in base[key]]
        else:
            base[key] = value
    return base


def absolute_path(entry):
    expanded = os.path.expanduser(os.path.expandvars(entry))
    if "$" in expanded:
        sys.exit(f"merge-settings: {entry!r} names an unset variable; run install.sh, which sets it.")
    if not os.path.isabs(expanded):
        sys.exit(f"merge-settings: {entry!r} resolves to {expanded!r}; it has to be an absolute path.")
    return os.path.normpath(expanded)


def resolve_paths(kit):
    for *parents, key in PATH_LISTS:
        section = kit
        for parent in parents:
            section = section.get(parent, {})
        if key in section:
            section[key] = [absolute_path(entry) for entry in section[key]]
    return kit


def load(path):
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        sys.exit(f"merge-settings: {path} isn't valid JSON ({error}); fix it and run install.sh again.")


def main(target, *kit_files):
    target = Path(target).expanduser()
    kit = {}
    for kit_file in kit_files:
        merge(kit, load(Path(kit_file)), owned=frozenset())
    resolve_paths(kit)
    current = load(target) if target.exists() else {}
    merged = merge(json.loads(json.dumps(current)), kit, OWNED)
    if merged == current:
        print(f"settings unchanged: {target}")
        return
    if target.exists():
        backup_dir = target.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(target, backup_dir / f"settings.json.{time.strftime('%Y%m%d-%H%M%S')}")
    target.write_text(json.dumps(merged, indent=2) + "\n")
    print(f"settings merged: {target}")


if __name__ == "__main__":
    main(*sys.argv[1:])
