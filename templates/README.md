# Templates

Files a project bootstrap lays down when it sets up a new project or adopts an existing one. `/kit:new` and `/kit:adopt` lay them down with `plugins/kit/scripts/apply_template.py`.

## Layout
```
templates/
  common/            files every project gets, whatever the stack
  <stack>/           files for one stack: django, react-vite, nextjs, mern
    README.md        what each file is for and what it assumes (never copied)
    kit.json         becomes .claude/kit.json
    CLAUDE.md.template   a section added to the project's CLAUDE.md
    ...              everything else, at the path it has in the project
  agents/            adapters for other coding agents (Copilot, Gemini CLI); not a stack, copied by hand as each README says
```

Inside `common/` and each stack folder, a file sits at the path it gets in the project, relative to the project root. `templates/mern/server/tsconfig.json` becomes `server/tsconfig.json`, and `templates/mern/.github/workflows/ci.yml` becomes `.github/workflows/ci.yml`. These names are special:

| Template file | In the project |
|---|---|
| `README.md` directly in a pack folder | Not copied. It documents the pack. |
| `kit.json` | `.claude/kit.json`. It sets `stack` to the folder name. |
| `CLAUDE.md.template` | Filled in from the answers and added to the project's `CLAUDE.md`. `plugins/kit/scripts/build_agents_md.py` generates the project's separate `AGENTS.md`, for the other coding agents, from the kit's rules. |
| `README.md.template` (common only) | Filled in from the answers as the project's `README.md` |
| `gitignore.additions` | Lines for the project's `.gitignore`. A new `.gitignore` gets every pack's lines once; an existing one gets only the lines it lacks. |

## Git hooks
`common/lefthook.yml` holds the jobs every project gets: the Betterleaks secret scan, a block on secret and local-settings files, the commit size warning, and the commit-message rules. It pulls in more jobs with `extends: .lefthook/*.yml`, and each stack adds its lint and format jobs in `.lefthook/<stack>.yml`, including the mern pack's `.lefthook/mern.yml`.

## How a bootstrap uses them
1. Copy `common/`, then the stack folder, into the project root. A stack file wins over a common file at the same path.
2. Write `kit.json` to `.claude/kit.json`. If `common/` has one too, merge them, with the stack's keys winning.
3. Add the packs' `gitignore.additions` lines to `.gitignore`.
4. Fill in `CLAUDE.md.template` and `README.md.template` from the answers. In a new project the stack's `CLAUDE.md.template` goes under the common one; in an existing one it is a new section.
5. In an existing project, never overwrite a file. Show the difference and ask, one file at a time.
6. Run the stack README's setup steps (installs, `lefthook install`), then the checks it lists, and report what passed.

## Rules for template files
- Every file is valid as it stands: JSON and YAML parse, configs load, and the workflow passes actionlint. Config files hold no placeholders. Markdown files may hold `<!-- fill: ... -->` markers, which the bootstrap replaces with facts from the answers; the docs check fails while one is left.
- Keep them minimal. A file earns its place by being needed on day one.
- Pin versions: GitHub Actions by commit SHA with the tag in a comment, container images and downloaded tools by version, with a checksum where the tool publishes one.
- No secrets. Test stacks force paid API keys empty, and any value that looks like a credential is a throwaway for the local test stack.
- Comments only for a non-obvious reason, as in code.
- A stack's `README.md` lists every file, what it assumes (package manager, script names, paths, ports), and how it was checked.
