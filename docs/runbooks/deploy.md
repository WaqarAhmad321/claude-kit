# Install or update the kit

The kit is deployed by installing it on a machine. Each machine runs from its own clone.

## Install
Needs git 2.31 or later (`install.sh` uses `git rev-parse --path-format`), bash, Python 3.11 or later (the hooks use `tomllib`) and Claude Code (`claude` on the PATH). Projects also use Docker for the Django and MERN test stacks, lefthook for git hooks (`/kit:new` prints an install command if it's missing), and Betterleaks for the git hooks' secret scan. The kit's own tests need uv.

The clone can live in any folder; `install.sh` links from wherever it runs. These steps use `~/Work/personal-projects/claude-kit`.

```bash
git clone https://github.com/WaqarAhmad321/claude-kit ~/Work/personal-projects/claude-kit
~/Work/personal-projects/claude-kit/install.sh      # KIT_SANDBOX=0 to skip the sandbox
```

`install.sh` opens `~/Work` to Claude and to sandboxed commands; set `KIT_WORK_DIR` to an absolute path, such as `KIT_WORK_DIR=~/Code`, to open another folder. A folder from an earlier run stays in `permissions.additionalDirectories` until you remove it from `~/.claude/settings.json`. It also denies sandboxed writes to the live files of the checkout it runs from (`home/`, `plugins/`, `settings/`, `scripts/`, `templates/`, `install.sh`, `.githooks/`, `.claude-plugin/`, `.git/hooks`, `.git/config`), so run it from main.

Machine-only settings, such as the client GitHub organisations auto mode should know about, go in `${XDG_CONFIG_HOME:-~/.config}/claude-kit/settings.local.json`. It lives outside the repo, so it survives worktrees and re-clones but not a new machine: copy it there yourself. `install.sh` merges it after the kit's settings when it exists, adding its entries to the kit's lists. `sandbox` and `autoMode.environment` are kit-owned: each run replaces them with the kit's entries plus this file's, so an entry added there by hand in `~/.claude/settings.json` is lost.

`install.sh`:
- links `home/CLAUDE.md`, `home/rules/` and `home/output-styles/` into `~/.claude`, and `home/hooks/` to `~/.claude/kit-hooks`, backing up anything it replaces
- merges `settings/*.kit.json`, then the machine-only `settings.local.json` above if it exists, into `~/.claude/settings.json`, keeping your other settings and backing up the old file to `~/.claude/backups/`
- registers this repo as the `claude-kit` plugin marketplace, installs the `kit` plugin for your user, and refreshes it on every re-run
- turns the sandbox on (`KIT_SANDBOX=0 ./install.sh` skips that), with `gh` and git's push, fetch, pull and ls-remote run outside it so they can use your GitHub login
- opens your work folder, `~/Work` (or `KIT_WORK_DIR`), to Claude and to sandboxed commands, so helpers work in any repo there without asking. Sandboxed commands still can't write the kit's own live files, which run outside the sandbox. The tradeoff is in [ADR 0010](../decisions/0010-open-the-work-folder-and-run-git-network-commands-outside-the-sandbox.md)
- points this repo's git hooks at `.githooks/`

It is safe to re-run. Start a new Claude Code session afterwards, or run `/reload-plugins` in an open one.

`home/CLAUDE.md` is Waqar's personal profile, and `settings/settings.kit.json` describes his setup for auto mode. Anyone else installing the kit replaces both with their own first.

## Update
Landing a change on main doesn't change the `kit` plugin (its hooks, skills and agents) or the settings on any machine, the one where you change the kit included. Claude Code runs the plugin from a cached copy until `./install.sh` refreshes it, or `claude plugin update kit@claude-kit` for the plugin alone, and a session that was already open needs `/reload-plugins` after that; a new session loads the refreshed copy on its own. Rules, CLAUDE.md, output styles and the orchestrator guard are symlinks into the checkout, so a new session reads them from main at once.

On the machine where you change the kit, land the branch on main (see the worktree flow in [CLAUDE.md](../../CLAUDE.md#main-is-live-work-in-a-worktree)) from a terminal, since the sandbox can't write main's live files. If a sandboxed merge already stopped halfway, run `git restore .` on main first. Then:

```bash
cd ~/Work/personal-projects/claude-kit && ./install.sh
```

The post-commit hook pushes each commit to GitHub. On every other machine:

```bash
cd ~/Work/personal-projects/claude-kit && git pull && ./install.sh
```

Then run `/reload-plugins` in any Claude Code session that was already open.

- Settings changes take effect only after `./install.sh` merges them.
- `install.sh` refreshes the `kit` plugin with `claude plugin update kit@claude-kit` after its install check, printing one line instead of failing the rest of the install if that update can't run. See [ADR 0009](../decisions/0009-have-install-sh-update-the-plugin-instead-of-in-place.md) for why the plugin needs its own refresh step when the files above don't.

### If a plugin change doesn't seem to have landed
`install.sh`'s `claude plugin update` can fail quietly: it prints one line and keeps going. Confirm directly:

```bash
claude plugin list --json       # find the kit@claude-kit entry's installPath
git rev-parse --short=12 HEAD   # compare against the sha at the end of that installPath
```

If they don't match, run `claude plugin update kit@claude-kit` by hand and read what it says, or force a fresh copy:

```bash
claude plugin uninstall kit@claude-kit
claude plugin install kit@claude-kit --scope user
```

Then `/reload-plugins` in any session that was already open.

## Verify
Run from the kit directory:

```bash
claude plugin validate .
claude plugin validate plugins/kit
claude plugin details kit@claude-kit
uvx --with pytest pytest plugins/kit/tests -q
claude -p "list the skills and subagent types you can use" --model sonnet
```

The last command should list the `kit:workflow` and `kit:handoff` skills and the `kit:builder`, `kit:reviewer` and `kit:researcher` agents.

## Roll back
- A bad kit change: `git revert <sha>` in the kit repo and commit. Other machines get it on their next pull.
- Settings: `install.sh` saves `~/.claude/settings.json` to `~/.claude/backups/settings.json.<timestamp>` before each merge, and moves any file it replaces with a symlink to `~/.claude/backups/kit-<timestamp>/`.
- One project: turn the plugin off there, as the [escape hatches](../guides/how-it-works.md#escape-hatches) describe.
- Everywhere: Waqar runs `claude plugin disable kit@claude-kit` himself. A deny rule stops Claude from running it. The orchestrator guard and the style reminder are hooks in `~/.claude/settings.json`, not in the plugin, so they stay on; direct mode lifts the guard.
