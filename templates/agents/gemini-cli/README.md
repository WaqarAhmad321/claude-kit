# Gemini CLI adapter

Gemini CLI reads `GEMINI.md` by default, not `AGENTS.md` — unlike Codex, Cursor, OpenCode and Copilot, it
needs a setting before it will pick up the kit's generated `AGENTS.md` (see
`docs/guides/using-the-kit-with-other-agents.md`).

## Install

Copy `settings.json` to `.gemini/settings.json` in the project (merge the `context` key by hand if the
project already has a `.gemini/settings.json`):

```
mkdir -p .gemini
cp templates/agents/gemini-cli/settings.json .gemini/settings.json
```

This points Gemini CLI at `AGENTS.md` instead of `GEMINI.md`, so there's one instructions file to keep in
sync rather than two.
