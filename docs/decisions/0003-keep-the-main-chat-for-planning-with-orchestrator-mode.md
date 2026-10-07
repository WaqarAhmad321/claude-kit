---
status: accepted
date: 2026-09-29
decision-makers: Waqar Ahmad
---

# Keep the main chat for planning with orchestrator mode

## Context and Problem Statement

Waqar runs many projects at once and wants the main chat free to talk at any time. Tool calls and code detail in the main chat made it long and tiring to follow. Should the main chat do hands-on work?

## Considered Options

* Let the main chat work directly, as Claude Code does by default
* Advise delegation in the rules
* Enforce delegation with a hook on the main thread

## Decision Outcome

Chosen option: enforce it with a hook, because advice alone kept slipping. `home/hooks/orchestrator_guard.py` denies edits, shell commands, reading, search, web and MCP calls in the main thread, with a reason telling Claude to delegate. Planning tools, agents, memory and plan files stay open. Subagents aren't restricted. Only Waqar switches direct mode on or off, and headless scripts opt out with `KIT_DIRECT_MODE=1`. The hook fails open, so a bug can't lock the chat.

### Consequences

* Good, because the main chat stays short, responsive and at the product level.
* Good, because every piece of work gets a written brief, which makes it reviewable.
* Bad, because it costs more tokens, and even a one-line change needs a subagent.
* Bad, because skills that write files from the main chat have to hand the write to a subagent.
