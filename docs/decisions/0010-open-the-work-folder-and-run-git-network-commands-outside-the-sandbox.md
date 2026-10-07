---
status: accepted
date: 2026-10-07
decision-makers: Waqar Ahmad
---

# Open the work folder and run git network commands outside the sandbox

## Context and Problem Statement

Waqar runs auto mode with the sandbox on. Most of his permission prompts came from helpers retrying a failed command outside the sandbox, for two reasons:

* Sandboxed commands could write only to the session's working directories, so work in another repo, his notes folder or this kit failed.
* GitHub commands (`gh`, `git push`, `fetch`, `pull`, `ls-remote`) need his login, which a sandboxed command can't reach. The kit already took most of them out of the sandbox, but an exclusion matches the command's text: `cd <dir> && git push` stays sandboxed because of the `cd`, and `git -C <dir> push` doesn't start with `git push`.

How do we stop these prompts once, from the kit's settings, without an `/add-dir` in every session?

## Considered Options

* `/add-dir` or `--add-dir` in each session
* `sandbox.filesystem.allowWrite` for `~/Work`
* `permissions.additionalDirectories` for `~/Work`, plus `sandbox.excludedCommands` for the GitHub commands in their plain and `git -C <dir>` forms

## Decision Outcome

Chosen option: the third.

* `install.sh` writes the absolute path of `$KIT_WORK_DIR`, by default `$HOME/Work`, into `permissions.additionalDirectories`. Claude Code documents `~` expansion for sandbox paths but not for this key, so the kit doesn't rely on it. An additional directory is a working directory for Claude's file tools and is writable for sandboxed commands. `allowWrite` would cover the sandbox only.
* `sandbox.excludedCommands` lists `gh`, `git push`, `git fetch`, `git pull` and `git ls-remote`, each also as `git -C * <verb>` with and without arguments. A pattern with two wildcards doesn't match the bare command, hence both forms.
* The force-push deny rules get the same `git -C` forms, so the `-C` exclusion doesn't open a way around them.
* Helpers reach another folder with `git -C <dir>` or absolute paths, one plain command per call (rule 8 of `home/rules/agents-parallelism.md`).
* `sandbox.filesystem.denyWrite` lists the live parts of the checkout `install.sh` runs from, as exact absolute paths: `home/`, `plugins/`, `settings/`, `scripts/`, `templates/`, `install.sh`, `.githooks/`, `.claude-plugin/`, and the repo's `.git/hooks` and `.git/config` (which could point `core.hooksPath` or `core.fsmonitor` elsewhere). `~/.claude/kit-hooks` links into `home/hooks/`, so it's covered. `denyWrite` blocks writes "including paths inside a directory that is otherwise writable", so it holds inside the work folder. `.worktrees/` stays writable for kit development.

The kit's ask guards on its own and Claude Code's settings files stay as they are.

### Consequences

* Good, because helpers work in any repo under `~/Work` without a prompt, and GitHub commands reach Waqar's login on the first try.
* Good, because the settings come from the kit, so every machine gets them from `install.sh`.
* Bad, because a sandboxed command, which runs with no prompt and no classifier review, can now write anywhere under `~/Work` except the kit's live files. That includes other repos' `.git/hooks`, `.git/config` and `.claude/` settings. Claude Code's protected paths cover only the session's own working directory (and `.claude` config in the folders above it), and on Linux `denyWrite` skips wildcard entries, so no single entry closes this for every repo. A hook or `core.fsmonitor` planted that way runs outside the sandbox the next time an excluded git command (`git push`, `git status`) or Waqar runs git in that repo.
* Bad, because landing a branch on main has to run outside the sandbox, in a terminal. A sandboxed `git merge --ff-only` there stops at the first denied file ("unable to unlink old"), after updating the files before it and without moving main, so a plain retry then refuses; `git restore .` on main clears the half-done update first.
* Bad, because excluded commands run with Waqar's full access. They still go through the permission flow, so auto mode's classifier reviews each one, but `git -C * push *` and its siblings also match other git commands that have the verb as a separate word, such as a commit whose message says "push", and those run outside the sandbox once the classifier allows them.
* Bad, because `additionalDirectories` merges as a union: a folder from an earlier `KIT_WORK_DIR` stays until it's removed from `~/.claude/settings.json` by hand.
