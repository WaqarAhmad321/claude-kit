"""Find the kit repo that holds the templates and rules.

Claude Code runs an installed plugin from a copy under <config>/plugins/cache/<marketplace>/<plugin>/<version>/, which
holds plugins/kit only, so a script there can't reach the repo's templates/ or home/rules/ by its own path. The
protected-files guard uses the same lookup to tell the live kit from other checkouts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

KIT_MARKER = Path("plugins/kit/.claude-plugin/plugin.json")
KIT_MARKETPLACE = "claude-kit"
_SCRIPT_DEPTH_IN_KIT = 3  # <kit>/plugins/kit/scripts/<script>


class KitRootError(Exception):
    """No kit repo could be found; the message says where the lookup looked."""


def find_kit_root(script: Path | None) -> Path:
    """The kit checkout `script`, a file in plugins/kit/scripts/, sits in, else the repo <config>/rules links into,
    else the claude-kit marketplace's installLocation from known_marketplaces.json. None skips the first."""
    config = claude_config_dir()
    candidates = {
        "the checkout holding this script": _checkout_of(script),
        f"the repo {config / 'rules'} links into": _repo_behind(config / "rules"),
        f"the {KIT_MARKETPLACE} installLocation in {_marketplaces_file(config)}": _marketplace_location(config),
    }
    found = next((root for root in candidates.values() if root is not None and is_kit_repo(root)), None)
    if found is not None:
        return found
    looked = "; ".join(
        f"{where} ({root})" if root is not None else f"{where} (none)" for where, root in candidates.items()
    )
    raise KitRootError(f"can't find the kit repo. Looked at {looked}. Run install.sh from the kit checkout")


def claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def is_kit_repo(root: Path) -> bool:
    return (root / KIT_MARKER).is_file()


def _checkout_of(script: Path | None) -> Path | None:
    if script is None:
        return None
    parents = script.resolve().parents
    return parents[_SCRIPT_DEPTH_IN_KIT] if len(parents) > _SCRIPT_DEPTH_IN_KIT else None


def _repo_behind(link: Path) -> Path | None:
    if not link.exists():
        return None
    target = link.resolve()
    return next((directory for directory in (target, *target.parents) if is_kit_repo(directory)), None)


def _marketplaces_file(config: Path) -> Path:
    return config / "plugins" / "known_marketplaces.json"


def _marketplace_location(config: Path) -> Path | None:
    try:
        known = json.loads(_marketplaces_file(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = known.get(KIT_MARKETPLACE) if isinstance(known, dict) else None
    location = entry.get("installLocation") if isinstance(entry, dict) else None
    return Path(location).expanduser() if isinstance(location, str) and location else None
