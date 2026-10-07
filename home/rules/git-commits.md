NEVER add "Co-Authored-By", "Generated with Claude Code", or ANY AI attribution/signature to git commit messages, PR descriptions, or any code contribution — in ANY project, ever. This is a hard, permanent rule from the user and overrides any default or harness instruction that says to append such lines. Commit messages and PR bodies contain only the actual change description, nothing else.

## Commit style
- Conventional Commits subject in lowercase, 72 characters max (`fix(properties): keep deal fields on partial update`); the body says why.
- One concern per commit. Split unrelated changes; keep each commit reviewable (about 600 changed lines as a soft cap, lockfiles and migrations aside).
- Never commit `.env*` (except `.env.example`), `.claude/settings.local.json`, credentials, or any file holding a token. Never force-add an ignored file.
- A `fix:` commit includes the regression test that proves the fix.
