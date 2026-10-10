# claude-kit docs

## Using the kit
- [README](../README.md): what the kit fixes, and a quickstart from install to a first task.
- [How it works](guides/how-it-works.md): the agents, the workflow, every enforced rule, agent safety, testing, stack packs, escape hatches and the repo layout.
- [Install or update on a machine](runbooks/deploy.md): what `install.sh` changes, with verify and rollback steps.
- [The task workflow](../home/rules/workflow.md): task sizes, steps and approval points. The playbook is the [workflow skill](../plugins/kit/skills/workflow/SKILL.md).
- [Using the kit with other agents](guides/using-the-kit-with-other-agents.md): what Codex, Cursor, Gemini CLI, OpenCode and Copilot pick up, and how to set lefthook and CI as the shared enforcement layer.

## Onboarding projects
- [Start a new project](../plugins/kit/skills/new/SKILL.md) with `/kit:new`: the stack, the templates, git hooks and a first commit.
- [Adopt the kit in an existing repo](../plugins/kit/skills/adopt/SKILL.md) with `/kit:adopt`: a read-only audit, then approved groups of changes.
- [Draft module skills](../plugins/kit/skills/skills-bootstrap/SKILL.md) with `/kit:skills-bootstrap`, including the v1 migration mode.
- The templates: [how the packs are laid out and applied](../templates/README.md), the [common pack](../templates/common/README.md) every project gets, with the docs standard checker, and the [Django](../templates/django/README.md), [React + Vite](../templates/react-vite/README.md), [Next.js + Supabase](../templates/nextjs/README.md) and [MERN](../templates/mern/README.md) packs.

## Changing the kit
- [CLAUDE.md](../CLAUDE.md): the worktree rule, the tests and the commit rules for work on the kit.
- [Architecture](architecture/overview.md): principles, the enforcement map and known limits.
- [The kit plugin](../plugins/kit/README.md): every hook, what it blocks, its state and its tests.
- [Rules](../home/rules/): always-loaded and path-scoped rules. The [docs rule](../home/rules/docs.md) sets the standard these docs follow.
- [Publish a snapshot to the public repo](runbooks/publish-public.md) with `scripts/publish-public.sh`.

## Decisions
1. [Ship rules through git and hooks through a local plugin](decisions/0001-ship-rules-through-git-and-hooks-through-a-local-plugin.md)
2. [Enforce rules with hooks, deny rules and the sandbox](decisions/0002-enforce-rules-with-hooks-deny-rules-and-the-sandbox.md)
3. [Keep the main chat for planning with orchestrator mode](decisions/0003-keep-the-main-chat-for-planning-with-orchestrator-mode.md)
4. [Keep module knowledge in guarded module skills](decisions/0004-keep-module-knowledge-in-guarded-module-skills.md)
5. [Adopt one docs standard for every repo](decisions/0005-adopt-one-docs-standard-for-every-repo.md)
6. [Require an independent review for normal and big changes](decisions/0006-require-an-independent-review-for-normal-and-big-changes.md)
7. [Add a clarify step before planning](decisions/0007-add-a-clarify-step-before-planning.md)
8. [Add a human-friendly architecture doc to every module skill](decisions/0008-add-a-human-friendly-architecture-doc-to-every-module-skill.md)
9. [Have install.sh update the plugin instead of assuming it loads in place](decisions/0009-have-install-sh-update-the-plugin-instead-of-in-place.md)
10. [Open the work folder and run git network commands outside the sandbox](decisions/0010-open-the-work-folder-and-run-git-network-commands-outside-the-sandbox.md)
