# Plan: <task>

Size: big, because <reason>.

## Goal
<What the user or client can do afterwards, in one or two sentences.>

## Scope
- In: <what this task changes>
- Out: <what it deliberately leaves alone>

## Modules touched
- <module>: <what changes>. Skill: `.claude/skills/<module>/SKILL.md`

## Approach
1. <Slice 1: what changes, and the test that proves it>
2. <Slice 2>

## Tests that prove it
- Journeys: <name>: <calls in the frontend's order, with roles and error paths>
- Regression: <for a bug, the test that fails on the current code>
- Unit: <pure logic only>

## Docs to update
- <file>: <the trigger, such as an API change or a new env var>

## Risks
- <what could go wrong>: <how we'd notice it>, <how we prevent or limit it>

## Rollback
<How to undo it: revert the commits, run the migration backwards, switch a flag, restore config.>

## Assumptions and open questions
- Assumption: <a default taken because nothing better was found; say what it was in favor of>
- Question: <question> (for Waqar, or to research)
