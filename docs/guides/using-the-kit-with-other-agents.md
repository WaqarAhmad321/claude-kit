# Using the kit with other coding agents

The kit's rules live once, in `home/rules/`. Claude Code loads them as `CLAUDE.md` and path-scoped rules. A project that also uses Codex CLI, Cursor, Gemini CLI, OpenCode or GitHub Copilot needs the same rules in a form those agents read: a generated `AGENTS.md`, with lefthook and CI as the layer that actually enforces the checks, whichever agent wrote the diff.

## What each agent picks up

| Agent | Instructions | Skills (SKILL.md) | Hooks | Enforcement |
|---|---|---|---|---|
| Claude Code | `CLAUDE.md`; reads `AGENTS.md` too — natively since v2.1.277 (a setting controls instead-of vs alongside `CLAUDE.md`), or via `@AGENTS.md` import on any version | `.claude/skills/` | This kit's `hooks.json`, full suite | Hooks (kit) + lefthook/CI |
| Codex CLI | `AGENTS.md` natively: global then a project walk from the git root to the cwd, concatenated, capped at `project_doc_max_bytes` (32 KiB default) | `.agents/skills/` (project and user) | Native, `hooks.json` or `config.toml [hooks]`, on by default | lefthook/CI (native hooks available, not configured by the kit) |
| Cursor | `.cursor/rules/*.mdc` is current; also picks up root `AGENTS.md` automatically, applied unconditionally like `CLAUDE.md`. `.cursorrules` is legacy | `.agents/skills/` or `.cursor/skills/` | Native, `.cursor/hooks.json`, broad event set, can block (`beforeShellExecution`) | lefthook/CI (native hooks available, not configured by the kit) |
| Gemini CLI | `GEMINI.md` by default. Reads `AGENTS.md` only if added to `context.fileName` in `settings.json` — not automatic | `.gemini/skills/` or `.agents/skills/` | Native, `settings.json` hooks, shell commands only | lefthook/CI (native hooks available, not configured by the kit) |
| OpenCode | `AGENTS.md` natively, preferred over `CLAUDE.md` in the same directory; falls back to `~/.claude/CLAUDE.md` if neither is found | `.claude/skills/`, `.opencode/skills/` or `.agents/skills/` — reads this repo's Claude Code skills directly | JS/TS plugins (`.opencode/plugin/`), rich event set | lefthook/CI (native plugins available, not configured by the kit) |
| GitHub Copilot | `AGENTS.md` (nearest file wins), merged additively with `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` — all three apply at once | `.github/skills`, `.claude/skills` or `.agents/skills` (project) | Copilot CLI only, `.github/hooks/*.json`; not in VS Code, JetBrains or the cloud agent | lefthook/CI (CLI hooks available, not configured by the kit) |

Only permission rules, hooks and the sandbox are things Claude Code itself enforces (see `docs/architecture/overview.md`); that machinery is specific to Claude Code's plugin system and doesn't run under another agent. `AGENTS.md` is advice there, same as `CLAUDE.md` is advice to Claude Code without the kit's hooks. Lefthook (pre-commit, commit-msg) and CI are the one mechanism common to every agent, so they are what actually holds the line regardless of which agent wrote the diff. See [Recommended project setup](#recommended-project-setup).

## Per-agent notes

### Codex CLI
- Reads `AGENTS.md` with no setup: drop it at the project root (or anywhere in the tree; the nearest file per directory wins, closer-to-cwd files are appended after farther ones). Nothing to install.
- `project_doc_max_bytes` (default 32 KiB) caps how much of the combined `AGENTS.md` chain Codex reads; `build_agents_md.py` warns on stderr if the generated file would exceed it. Raise the config key, or shorten the project section, if that fires.
- Hooks (`hooks.json` or `config.toml [hooks]`) are on by default; events include `PreToolUse`, `PostToolUse`, `SessionStart`/`SessionEnd`, `Stop`. The kit doesn't ship Codex hook scripts — this is a native capability available to configure per project.
- Source: the Codex CLI docs on agents.md, config reference and hooks (developers.openai.com/codex).

### Cursor
- Reads root `AGENTS.md` with no setup, applied unconditionally to every conversation, the same way it treats `CLAUDE.md`. `.cursor/rules/*.mdc` is for *conditional* rules (glob-matched or agent-decided); keep using it for anything narrower than the whole project.
- UNVERIFIED: the exact precedence when `AGENTS.md` and an Always-Apply `.cursor/rules/*.mdc` rule disagree isn't stated in Cursor's docs; both are likely additive (concatenated), not one overriding the other. Also UNVERIFIED (secondary source only): whether `.cursorrules` still loads in Agent mode — migrate off it regardless, Cursor's own docs call it legacy.
- Hooks (`.cursor/hooks.json`) are native and can block a step (`beforeShellExecution` et al.); the kit doesn't configure them.
- Source: cursor.com/docs (rules, skills, hooks).

### Gemini CLI
- Does **not** read `AGENTS.md` by default — this was requested upstream and closed `not_planned` (google-gemini/gemini-cli#12345). Add it yourself: set `context.fileName` in `.gemini/settings.json`. A minimal adapter for this is in `templates/agents/gemini-cli/`.
- Skills live under `.gemini/skills/` or the `.agents/skills/` alias; hooks are native but shell-command only, configured in the same `settings.json`.
- Source: the Gemini CLI docs (`docs/cli/gemini-md.md`, `docs/cli/skills.md`, `docs/hooks/reference.md` in google-gemini/gemini-cli).

### OpenCode
- Reads `AGENTS.md` with no setup, and prefers it over `CLAUDE.md` when both exist in the same directory — so a project that already has both files needs no changes for OpenCode to pick up the kit's rules.
- Skills: OpenCode reads `.claude/skills/` directly, so this kit's Claude Code skills work under OpenCode unmodified; no adapter needed.
- Plugins (`.opencode/plugin/`, JS/TS) are OpenCode's hook equivalent, with a wide event list including `tool.execute.before`/`after`. Not configured by the kit.
- Source: opencode.ai/docs (rules, skills, plugins).

### GitHub Copilot
- Reads `AGENTS.md` on every current surface that matters here (VS Code, the cloud/coding agent, the CLI), and **merges it additively** with `.github/copilot-instructions.md` and any `.github/instructions/*.instructions.md` — unlike Claude Code's either/or default, Copilot applies all of them at once and explicitly tells you not to rely on a precedence order between them.
- UNVERIFIED: JetBrains-specific support wasn't confirmed on an official JetBrains doc.
- Because Copilot merges rather than overrides, don't duplicate the same rule text in both `AGENTS.md` and `copilot-instructions.md` — keep `copilot-instructions.md` either empty of kit rules or a short pointer. A minimal adapter for this is in `templates/agents/copilot/`.
- Skills: `.github/skills`, `.claude/skills` or `.agents/skills` (project). Hooks exist only in Copilot CLI (`.github/hooks/*.json`), not in VS Code, JetBrains or the cloud agent.
- Source: docs.github.com/copilot, the GitHub Changelog (AGENTS.md support, Agent Skills).

### Claude Code's own AGENTS.md handling
Claude Code (v2.1.277+) can read `AGENTS.md` natively. A setting (`/config` → Project instructions, or `pluginConfigs["agents-md@builtin"].options.instructionFiles` in `settings.json`) controls it: `claude-md-or-agents-md` (default — `CLAUDE.md` if present, else `AGENTS.md`), `claude-md-and-agents-md` (both, `CLAUDE.md` first per directory then `AGENTS.md`), `claude-md` (ignore `AGENTS.md`), or `managed-only`. The kit doesn't rely on any of that: Claude Code already gets the kit's rules globally from `home/CLAUDE.md` and `home/rules/`, so `CLAUDE.md` never imports `AGENTS.md` — that would load the same rules a second time into every session. Source: code.claude.com/docs/en/memory.

## Recommended project setup

The commands below run the kit's scripts from its checkout. `${CLAUDE_PLUGIN_ROOT}` is set for the plugin's own hooks, not in your shell, so find the checkout the way the scripts do: the repo `~/.claude/rules` links into (`$CLAUDE_CONFIG_DIR/rules` when that's set), which is the folder you cloned the kit into. Failing that, it's the `installLocation` of the `claude-kit` entry in `~/.claude/plugins/known_marketplaces.json`.

```
KIT="$(git -C "$(readlink -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/rules")" rev-parse --show-toplevel)"
```

1. **Generate `AGENTS.md`** from the kit's rules:
   ```
   python3 "$KIT/plugins/kit/scripts/build_agents_md.py" --root . --stack <stack>
   ```
   Repeat `--stack` for more than one (`django`, `api-design`, `node-express`, `mongodb`, `react`, `nextjs`); a rerun with no `--stack` keeps whatever the file already records. Fill in `## This project` by hand — the generator leaves it alone on every rerun.

2. **Don't import it from `CLAUDE.md`.** Claude Code already gets the kit's rules globally (`home/CLAUDE.md` and `home/rules/`), so `@AGENTS.md` would load a second copy into every session for no reason. `AGENTS.md` is for the other agents, which read it natively or through the adapters below.

3. **Add lefthook and CI as the shared enforcement layer.** `AGENTS.md` is advice to every agent that reads it; nothing here makes an agent other than Claude Code actually follow it. `lefthook.yml` running lint, format and the commit-message check on every commit, and the same checks again in CI, are what hold regardless of which agent produced the diff. (For a new project, the common pack ships the root `lefthook.yml` and each stack pack ships its own `.lefthook/<stack>.yml`, which the root file extends; see `templates/common/` and `templates/<stack>/`.)

4. **Regenerate it locally whenever a kit rule changes**:
   ```
   python3 "$KIT/plugins/kit/scripts/build_agents_md.py" --root . --check
   ```
   Exits 1 if `AGENTS.md` is missing, hand-edited, or stale against a changed kit rule. Run it after changing a rule; it can't run as the project's own CI step, since the generator lives in the claude-kit checkout, which the project's CI runner doesn't have.

## Skills across agents

SKILL.md (a directory with `SKILL.md` plus `name`/`description` frontmatter) has converged into a de facto open standard: Claude Code, Codex, Cursor, Gemini CLI, OpenCode and Copilot all read it, though each looks in its own directory (see the table above). `.agents/skills/` is the one location every agent except Claude Code and OpenCode's `.claude/skills/` path reads natively; OpenCode and Copilot also read `.claude/skills/` directly.

The `vercel-labs/skills` CLI (`npx skills`) installs a skill into every agent it detects on the machine, each into that agent's own directory — its README claims support for 78 agents, including every one in this guide. `npx skills add <source>` takes a GitHub shorthand, a full repo URL, or a local path. To mirror one of this kit's skills into whatever other agents are installed:
```
npx skills add "$KIT/plugins/kit/skills/workflow"
```
UNVERIFIED: the exact behavior of a local-path source (copy vs. symlink, and whether it needs a flag) wasn't independently tested — verify before relying on it for anything beyond a one-off mirror.
