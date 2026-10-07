---
status: accepted
date: 2026-10-03
decision-makers: Waqar Ahmad
---

# Have install.sh update the plugin instead of assuming it loads in place

## Context and Problem Statement

ADR 0001 assumed the `kit` plugin loads in place from `plugins/kit` because its marketplace entry is a relative path (`./plugins/kit`) inside a local-directory marketplace, which Claude Code's docs say should need no `claude plugin update`. `~/.claude/plugins/installed_plugins.json` shows otherwise: `kit@claude-kit` runs from a cache copy under `~/.claude/plugins/cache/claude-kit/kit/<sha>/`, pinned to whichever commit was HEAD the last time it was installed or updated, byte-identical to that commit and stale by dozens of commits after a plain `git pull`. How should a commit to `plugins/kit` actually reach a machine?

## Considered Options

* Keep relying on the documented in-place behaviour and tell people to `git pull` only
* Document the gap and leave `claude plugin update kit@claude-kit` as a manual step after every pull
* Have `install.sh` run `claude plugin update kit@claude-kit` itself, after its existing idempotent install check

## Decision Outcome

Chosen option: `install.sh` now runs `claude plugin update kit@claude-kit` after the install check that used to be the end of the script, printing one line and continuing if that update itself fails (no network, or a Claude Code bug) rather than crashing the rest of the install. Landing a change is `git pull && ./install.sh`, then `/reload-plugins` in a session that was already open; a new session picks up the refreshed cache on its own. This supersedes ADR 0001's "loads in place, no `claude plugin update`" claim, whose status now points here.

### Consequences

* Good, because landing a change stays the single `git pull && ./install.sh` step the old docs described, instead of a second manual command everyone has to remember.
* Good, because `claude plugin list --json`'s `installPath` against `git rev-parse --short=12 HEAD` gives a quick way to confirm an update actually landed.
* Bad, because `claude plugin update` can fail for reasons `install.sh` can't fix (network, a Claude Code bug), so a failure there is now something the script has to report instead of just trusting the plugin is current.
* Bad, because auto-update is off by default for local marketplaces, so this reintroduces an explicit refresh step that Claude Code itself doesn't run on its own.
