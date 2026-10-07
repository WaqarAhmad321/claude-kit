---
name: new
description: Start a new project from the kit's templates, with the docs skeleton, quality gates, git hooks and a first commit. Runs only as /kit:new.
argument-hint: "[directory] [stack] [name]"
disable-model-invocation: true
---

# New project

Set up a new project from the kit's templates: the common pack and one stack pack, a CLAUDE.md and README.md written from the answers, an AGENTS.md generated from the kit's rules for other coding agents, git with lefthook, and a first commit that passes the hooks.

Given: $ARGUMENTS

## Who does what
Questions stay in the main chat. There the orchestrator guard blocks shell commands and file writes, so send step 2 to an Explore agent, and steps 4 to 10 to one subagent: a `fork`, which inherits this conversation, or else a `kit:builder` briefed with the answers and told to follow steps 4 to 10 of `${CLAUDE_SKILL_DIR}/SKILL.md`, commit included. In a subagent, or in direct mode, do them yourself.

The template tool is `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/apply_template.py`. It finds the kit's templates itself, never overwrites a file, and prints the templates directory with `--list-stacks`.

## Steps
1. **Answers.** You need:
   - the directory (default: the current one)
   - the stack: `django`, `react-vite`, `nextjs` or `mern`
   - the project name, and one line on what it does and for whom
   - whether the API has external consumers: a partner, another team or a public API
   - whether there's a mobile app

   Take what the arguments and the prompt give.
2. **Check.** Run the template tool with `--list-stacks`. If the chosen stack isn't listed, say so and offer the stacks that are. The directory must be new, or empty apart from `.git` and a lone README or LICENSE. If it holds anything else, stop and point to `/kit:adopt`. If it sits inside another git repository, stop and ask: a new project gets its own.
3. **Ask** for what's still missing, in one message, offering the listed stacks. In a headless run (`claude -p`) no one can answer, so stop and name the missing answers instead.
4. **Lay down the templates** before `git init`: inside a git repository the kit asks before every gate-config write.
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/apply_template.py --stack <stack> --dest <directory> --json`
   Keep its output: the files it wrote are the ones you commit, and `fill` names the templates for step 5. Everything should be `new`. For a file that `differs`, such as an existing LICENSE, keep the existing one and say so.
5. **Fill in.**
   - `CLAUDE.md`: the common `CLAUDE.md.template`, then the stack's `CLAUDE.md.template` under it (both paths are in `fill`). At most 150 lines.
   - `README.md`: from `README.md.template`, folding in anything an existing README said. Take the run and check commands from the stack section.
   - `AGENTS.md`: generate it with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_agents_md.py --root <directory>` and the stack's rules: `--stack django --stack api-design` for django, `--stack react` for react-vite, `--stack nextjs --stack react` for nextjs (add `--stack api-design` when other clients call its route handlers), `--stack api-design --stack node-express --stack mongodb --stack react` for mern. Then replace the comment under its `## This project` heading with what the app is and its run, check and journeys commands, as CLAUDE.md and the README give them.
   - Replace every fill marker in CLAUDE.md, README.md and every file step 4 wrote, except `docs/api/handoffs/_template.md`, with facts from the answers. Where a fact isn't known yet, write "Not decided yet." Leave no guidance text behind. In each ADR set `date` to today and `decision-makers` to Waqar Ahmad unless told otherwise. ADR 0002 records the stack the answers chose; its options are the stacks offered.
   - The API docs: keep `docs/api/` when the project serves an API (django, mern, or a nextjs app whose route handlers other clients call). For a frontend-only app, delete `docs/api/` and its section in `docs/README.md`, so the index stays true.
   - External API consumers or a mobile app: add to CLAUDE.md's "Rules for this repo": "Shipped clients depend on this API: a breaking change to its shape, status codes or auth needs a compatible path (a new version path or an opt-in header) until they have moved." Name the clients in `docs/api/README.md`.
6. **Git.** `git init -b main` if there's no `.git`, then `lefthook install`. It refuses when `core.hooksPath` is set: say so, and don't unset it. If lefthook is missing, give an install command and go on: `mise use -g lefthook@2.1.16`, `go install github.com/evilmartians/lefthook/v2@v2.1.16`, brew, the AUR's `lefthook-bin`, or in a Python project `uv add --dev lefthook==2.1.16`.
7. **Type baseline.** If the project configures mypy or tsc and the tool resolves (`mypy` in `.venv/bin` or on PATH, `node_modules/.bin/tsc`), run `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/bin/kit_baseline.py --root <directory>`. A brand-new project has no dependencies yet, so this usually waits: put it in the next steps.
8. **Docs check.** In the project, run `python3 scripts/docs-check.py`. Fix what it reports in the files you wrote, and rerun it until it passes.
9. **First commit.**
   - Stage by explicit path: the files step 4 wrote (minus a deleted `docs/api/`), `CLAUDE.md`, `AGENTS.md`, `README.md`, and `.claude/kit-baseline.json` if step 7 wrote it.
   - The subject is a lowercase Conventional Commit of at most 72 characters, such as `chore: start <name> from the kit's <stack> templates`. The body says why. No attribution lines of any kind.
   - Let the lefthook hooks run; never `--no-verify`. The stack's lint jobs skip themselves until the dependencies exist, and the size job's warning is expected on this first commit. Fix what the others report and commit again. If a job fails only because its tool isn't installed (betterleaks), leave the files staged, don't commit, and put the install and the first commit at the top of the next steps.
10. **Report** what was set up, naming the gate configs it added (`.claude/kit.json`, `lefthook.yml` and `.lefthook/`, lint and type configs, CI workflows), what passed, and what didn't run. Then the next steps:
    - The stack's setup: the "Setup for a new project" steps in `<templates>/<stack>/README.md`, where `<templates>` is the directory `--list-stacks` printed. That README isn't copied into the project.
    - Record the type baseline once the dependencies are installed (step 7's command), and commit `.claude/kit-baseline.json`.
    - Replace the pack's example journeys, role matrix and specs with the app's own, in the client's call order, and run them green. Until then the journeys gate in `.claude/kit.json` blocks finishing a change under its `watch` globs; remove `gate.journeys` if the first slices need to land before any journey can pass, and add it back once they do.
    - Run `/kit:skills-bootstrap` once the first modules exist.
    - Push to a new remote, protect `main`, and turn on the CI checks as required.
    - With external API consumers or a mobile app: the oasdiff check guards `docs/api/openapi.yaml` from the first release.
