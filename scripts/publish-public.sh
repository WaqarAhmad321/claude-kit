#!/usr/bin/env bash
# Sync one ref of this kit into a fresh, history-free commit on a checkout of the public repo.
# Usage: scripts/publish-public.sh <path to the public repo checkout> [ref]
set -euo pipefail

die() {
    echo "publish-public: $1" >&2
    exit 1
}

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || {
    echo "usage: $(basename "$0") <path to the public repo checkout> [ref]" >&2
    exit 2
}

target=$1
ref=${2:-main}
kit_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
patterns_file="${KIT_PRIVATE_PATTERNS:-${XDG_CONFIG_HOME:-$HOME/.config}/claude-kit/private-patterns.txt}"

[ -d "$target/.git" ] || die "$target isn't a git checkout."
[ -s "$patterns_file" ] || die "no private patterns at $patterns_file; create it (one regex per line) first."

set +e
# Checked against the ref's own committed tree, not the working copy.
git -C "$kit_root" grep -qi -E -f "$patterns_file" "$ref" -- .
grep_status=$?
set -e
case "$grep_status" in
    0) die "a private pattern from $patterns_file is still in $ref's tracked tree; remove it before publishing." ;;
    1) ;;
    *) die "the private-pattern grep failed (exit $grep_status)." ;;
esac

python3 - "$kit_root" "$ref" <<'PYEOF' || die "the secret scan found something in the tracked tree; fix it before publishing."
import subprocess
import sys
from pathlib import Path

root, ref = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / "plugins" / "kit" / "hooks" / "bin"))
import scan_secrets

tracked = subprocess.run(
    ["git", "-C", str(root), "ls-tree", "-r", "--name-only", ref], capture_output=True, text=True, check=True
).stdout.splitlines()

findings = set()
for rel in tracked:
    blob = subprocess.run(
        ["git", "-C", str(root), "show", f"{ref}:{rel}"], capture_output=True, check=True
    ).stdout
    text = blob.decode("utf-8", errors="replace")
    for finding in scan_secrets.find_in_text(text, root, rel):
        if not (scan_secrets.is_generic(finding.rule) and scan_secrets.is_test_path(rel)):
            findings.add((rel, finding.rule))

if findings:
    for rel, rule in sorted(findings):
        print(f"{rel}: {rule}", file=sys.stderr)
    sys.exit(1)
PYEOF

sha=$(git -C "$kit_root" rev-parse --short "$ref")

tmp_tar=$(mktemp)
trap 'rm -f "$tmp_tar"' EXIT
git -C "$kit_root" archive "$ref" -o "$tmp_tar"

# A sync replaces the whole tree, so a file the ref no longer tracks must disappear here too.
find "$target" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
tar -xf "$tmp_tar" -C "$target"

if git -C "$target" rev-parse --verify -q HEAD >/dev/null; then
    message="chore: sync from private kit $sha"
else
    message="feat: initial public release"
fi

# The whole point is one commit of everything that changed, deletions included, not a hand-picked diff.
git -C "$target" add -A
if git -C "$target" diff --cached --quiet; then
    echo "publish-public: nothing changed since the last sync." >&2
    exit 0
fi

git -C "$target" commit -q -m "$message"

echo "Synced. Review the commit, then push it yourself:"
echo "  git -C $target push"
