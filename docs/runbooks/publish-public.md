# Publish a snapshot to the public repo

`WaqarAhmad321/claude-kit-private` holds the full history, including commits GitHub's object store still holds after they were deleted and client names that history carries. The public `WaqarAhmad321/claude-kit` gets a fresh, history-free snapshot of `main`'s tracked tree instead, built by `scripts/publish-public.sh`.

## Before running it
Have a local checkout of the public repo ready, with its own remote already configured. The script never clones or creates one.

The names and phrases that must never reach the public repo (client names, private project names) live outside this repo entirely, in a patterns file, one regex per line: `${KIT_PRIVATE_PATTERNS:-${XDG_CONFIG_HOME:-$HOME/.config}/claude-kit/private-patterns.txt}`. Create it (or point `KIT_PRIVATE_PATTERNS` at wherever it lives) before the first run; the script refuses to publish without it. Keeping the list outside the tracked tree means the gate itself never has to spell the names it's checking for.

## Run it
From the private kit's root, on the machine that has the ref you're publishing up to date:

```bash
scripts/publish-public.sh <path to the public repo checkout> [ref]
```

`ref` defaults to `main`; pass a branch or commit to publish something else (an unmerged review branch, a specific tag).

It refuses to run if a pattern from the private-patterns file, or a likely secret, is still in `ref`'s tracked tree. The secret check runs Betterleaks or Gitleaks when one is installed, and the kit's own `scan_secrets.py` patterns otherwise. Past that gate, it archives that tree, replaces everything in the target checkout except its `.git` directory with it, and commits the result there as `chore: sync from private kit <short sha>`, or `feat: initial public release` when the target checkout has no commits yet. A run with nothing new to sync makes no commit.

It never pushes. Run the command it prints:

```bash
git -C <path to the public repo checkout> push
```

## Review before pushing
Read the commit's diff in the target checkout. The gate catches whatever the private-patterns file lists and secret-shaped strings; it doesn't catch every way a private detail could show up in a doc or a comment, so a careless `main` commit can still surface in the snapshot. Keep the patterns file current as new private names come up.
