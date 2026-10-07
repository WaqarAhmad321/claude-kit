---
name: handoff
description: Write the handoff note, .claude/HANDOFF.md, so the next session can pick up the task cold. Use at the end of a big task, before stopping mid-task, or when Waqar asks for a handoff.
---

# Handoff note

Write `.claude/HANDOFF.md` at the project root, replacing any old one. The next session reads it with no other context, so it has to stand on its own.

## Who writes it
In the main chat, the orchestrator guard blocks file writes, so spawn a `fork` agent to write the file: a fork inherits this conversation. Where forks aren't available, draft the note in full and give it to a `kit:builder` to check against the repo and write. In a subagent, or in direct mode, write it yourself.

## Rules
- Take facts from the conversation and the repo (`git status`, `git log`), not from memory. Run the verify commands if you can, and say which fail.
- Present tense, current state only. No story of how you got here, no dates.
- Keep it under about 60 lines. Link to plans, ADRs and PM board cards instead of copying them.
- The note is a working file for the next session, not a repo doc. If `git check-ignore .claude/HANDOFF.md` finds no match, add the path to `.git/info/exclude`.

## Shape
```markdown
# Handoff: <task>

## Goal
<What done looks like, in one or two sentences.>

## Done
- <what works now, and how it was verified>

## In progress
- <what is half built: the files and the state they're in>

## Next steps
1. <the next concrete action>

## Decisions
- <decision>: <why>

## Open questions
- <question> (for Waqar, or to research)

## Verify
<the commands that show the current state: checks, tests, journeys>
```
