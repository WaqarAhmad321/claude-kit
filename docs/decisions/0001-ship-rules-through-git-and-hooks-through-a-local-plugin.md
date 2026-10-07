---
status: superseded by [0009](0009-have-install-sh-update-the-plugin-instead-of-in-place.md)
date: 2026-09-29
decision-makers: Waqar Ahmad
---

# Ship rules through git and hooks through a local plugin

## Context and Problem Statement

Waqar's CLAUDE.md, rules, settings and hooks have to be the same on every machine, versioned, and easy to change. Claude Code reads CLAUDE.md, rules and output styles from `~/.claude`, but a plugin can't ship them: it doesn't load a CLAUDE.md at the plugin root and has no rules component. Hooks, skills and agents are what plugins are built for. How should the kit be packaged?

## Considered Options

* Plain files in `~/.claude`, synced by Syncthing
* One plugin for everything
* A git repo symlinked into `~/.claude` for CLAUDE.md, rules and output styles, plus a local plugin (a marketplace in the same repo) for hooks, skills and agents

## Decision Outcome

Chosen option: the git repo plus a local plugin, because it is the only option that covers both kinds of files. The plugin loads in place from `plugins/kit`, so a commit reaches the next session without `claude plugin update`.

### Consequences

* Good, because one commit, pushed by the post-commit hook, updates every machine after a `git pull`.
* Good, because the plugin's hooks, skills and agents are namespaced, tested in `plugins/kit/tests`, and can be turned off per project.
* Bad, because there are two install mechanisms, symlinks and the plugin, and `install.sh` has to handle both.
* Bad, because Syncthing must stop syncing the files git now owns.
