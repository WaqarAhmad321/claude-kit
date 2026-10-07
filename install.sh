#!/usr/bin/env bash
# Install claude-kit into ~/.claude on this machine. Safe to re-run.
#   KIT_SANDBOX=0 ./install.sh          skips enabling the sandbox
#   KIT_WORK_DIR=~/Code ./install.sh    opens ~/Code to Claude and sandboxed commands instead of ~/Work
# Merges ${XDG_CONFIG_HOME:-~/.config}/claude-kit/settings.local.json after the kit's settings, if it exists.
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
BACKUP="$CLAUDE_HOME/backups/kit-$(date +%Y%m%d-%H%M%S)"

link() {
  local src=$1 dst=$2
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then return; fi
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    mkdir -p "$BACKUP"
    mv "$dst" "$BACKUP/"
    echo "backed up $dst to $BACKUP/"
  fi
  ln -s "$src" "$dst"
  echo "linked $dst -> $src"
}

mkdir -p "$CLAUDE_HOME"
link "$KIT/home/CLAUDE.md" "$CLAUDE_HOME/CLAUDE.md"
link "$KIT/home/rules" "$CLAUDE_HOME/rules"
link "$KIT/home/output-styles" "$CLAUDE_HOME/output-styles"
# Kit hook scripts go to kit-hooks: ~/.claude/hooks is Syncthing-synced and holds herdr's file.
link "$KIT/home/hooks" "$CLAUDE_HOME/kit-hooks"

settings=("$KIT/settings/settings.kit.json")
[ "${KIT_SANDBOX:-1}" = "1" ] && settings+=("$KIT/settings/sandbox.kit.json")
# Private additions such as client names; kept outside the repo, so they survive worktrees and re-clones.
overlay="${XDG_CONFIG_HOME:-$HOME/.config}/claude-kit/settings.local.json"
[ -f "$overlay" ] && settings+=("$overlay")
# The sandbox settings deny writes to this checkout's live files, since ~/.claude runs them unsandboxed.
kit_git_dir="$(git -C "$KIT" rev-parse --path-format=absolute --git-common-dir)"
KIT_WORK_DIR="${KIT_WORK_DIR:-$HOME/Work}" KIT_DIR="$KIT" KIT_GIT_DIR="$kit_git_dir" \
  python3 "$KIT/scripts/merge-settings.py" "$CLAUDE_HOME/settings.json" "${settings[@]}"

# Exits 0 when the JSON array on stdin has an entry whose key $1 equals $2.
json_lists() {
  python3 -c 'import json, sys; sys.exit(not any(e.get(sys.argv[1]) == sys.argv[2] for e in json.load(sys.stdin)))' "$1" "$2"
}
claude plugin marketplace list --json | json_lists name claude-kit || claude plugin marketplace add "$KIT"
claude plugin list --json | json_lists id kit@claude-kit || claude plugin install kit@claude-kit --scope user

# The plugin install above is a no-op once it's already installed, and Claude Code runs it from a cache copy
# that doesn't follow this checkout's HEAD on its own (see docs/decisions/0009), so a re-run has to refresh it.
claude plugin update kit@claude-kit >/dev/null 2>&1 \
  || echo "could not refresh the kit plugin automatically; run: claude plugin update kit@claude-kit" >&2

# Git owns rules and CLAUDE.md; stop Syncthing from also syncing them.
stignore="$CLAUDE_HOME/.stignore"
if [ -f "$stignore" ]; then
  sed -i -e '\#^!/rules$#d' -e '\#^!/CLAUDE.md$#d' "$stignore"
  grep -qx '/skills/synced' "$stignore" || sed -i 's#^!/skills$#/skills/synced\n!/skills#' "$stignore"
fi

git -C "$KIT" config core.hooksPath .githooks
echo "claude-kit installed. Start a new Claude Code session, or run /reload-plugins in an open one, to load it."
