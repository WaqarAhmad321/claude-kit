# Copilot adapter

GitHub Copilot already reads `AGENTS.md` on the surfaces that matter here (VS Code, the cloud/coding
agent, the CLI), but it merges it additively with `.github/copilot-instructions.md` rather than choosing
one — see `docs/guides/using-the-kit-with-other-agents.md`. This adapter is a short pointer, not a copy
of the rules, so nothing here can drift out of sync with `AGENTS.md`.

## Install

```
mkdir -p .github
cp templates/agents/copilot/copilot-instructions.md .github/copilot-instructions.md
```

If the project already has a `.github/copilot-instructions.md` with real content, add the pointer line
instead of overwriting it.
