---
name: skills-bootstrap
description: Draft module skills v2 for a project (SKILL, ARCHITECTURE, INTERLINKING, FLOWS, LESSONS and the generated CONTRACTS and SCHEMA) into .claude/skills/_draft/ for review, including a migration mode for v1 module skills. Runs only as /kit:skills-bootstrap.
argument-hint: "[module ...] [--v1]"
disable-model-invocation: true
---

# Bootstrap module skills

Draft one v2 module skill per module into `.claude/skills/_draft/<module>/`, from the code, the git history and any v1 skill files. Nothing is live until a reviewed draft moves to `.claude/skills/<module>/`: Claude Code and the kit's guard read only `.claude/skills/<dir>/SKILL.md`, one level down, so a draft neither loads nor guards anything.

Given: $ARGUMENTS

## Who does what
In the main chat, steps 1 and 2 go to one subagent and step 3 to one subagent per module, in parallel (a `kit:builder` each, or a `fork`); step 4 comes back to the main chat for Waqar. Each module agent gets: its module name and paths, the generated files from step 2, the v1 folder if any, the formats below and the templates in `${CLAUDE_SKILL_DIR}/templates/`. In a subagent, or in direct mode, work through the modules yourself.

## 1. Find the modules
- Django: each project app (an `AppConfig` under `apps/` or the repo, not in site-packages). Its paths: the app folder, plus its templates and tests elsewhere.
- React or Next.js: each `src/features/<feature>/` (and its routes under `src/app/` for Next.js).
- Express or Mongoose (MERN server): each model under `server/src/models/` names a module; its paths are that model plus the matching `server/src/{routes,controllers,services}/<resource>.ts` files.
- v1 migration: each `.claude/skills/<dir>/` that has no `paths` frontmatter, or holds a v1-only file (`USER_FLOWS.md`, `BUG_LOG.md`, `API_CONTRACTS.md`, `DB_SCHEMA.md`). A folder with `paths` and an `ARCHITECTURE.md` is already v2. `--v1` forces this mode. Module folder names become kebab-case.

List the modules with their paths and ask which to draft, unless the arguments named them.

## 2. Generate CONTRACTS and SCHEMA
These two are never written by hand. Run in the project, writing into the drafts:
- **CONTRACTS.md** from the OpenAPI schema. Django with drf-spectacular: `uv run python manage.py spectacular --format openapi-json | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/openapi_to_contracts.py - --out .claude/skills/_draft`. Another stack pipes its generated OpenAPI JSON the same way. It groups operations by the first path segment after the shared prefix (`--strip-prefix /api/v1` to set it) and writes `<module>/CONTRACTS.md` with its GENERATED header and the command that regenerates it. Admin and frontend repos get no CONTRACTS of their own; their skills link to the backend's.
- **SCHEMA.md** from the Django models: `KIT_OUT_DIR=.claude/skills/_draft uv run python manage.py shell -v 0 -c "import runpy; runpy.run_path('${CLAUDE_PLUGIN_ROOT}/scripts/django_model_dump.py', run_name='__main__')"`. It writes `<app_label>/SCHEMA.md` per project app; `KIT_APPS=a,b` limits it. Frontend repos get none.
- **SCHEMA.md** from a MERN server's Mongoose models: `KIT_OUT_DIR=.claude/skills/_draft node ${CLAUDE_PLUGIN_ROOT}/scripts/mongoose_model_dump.mjs`, run from the project root after `pnpm install` in `server/`, so the model files and Mongoose import from the server package. It writes `<kebab-case model name>/SCHEMA.md` per model; `KIT_MODELS_DIR` points it at a directory other than `server/src/models`.

When an OpenAPI module name and an app label differ (`trip-stops` against `trips`), move the generated file to the module it belongs to and say so. If the schema or the dump fails, report why and draft the rest; never hand-write a stand-in.

## 3. Draft each module
Read the module's code first, then its callers, `git log --oneline --grep=fix -- <paths>` and, in v1 mode, every v1 file. Write five files from the templates (`SKILL.md.template` becomes `SKILL.md`), following `~/.claude/rules/module-skills.md`:
- **SKILL.md**, 80 lines or fewer: frontmatter `name`, `description` (what the module owns and when to load it) and `paths` (quoted globs from the repo root); the invariants; the settled decisions with their reason; a "Read next" table pointing at the other files and the key code; the verify commands. Links `ARCHITECTURE.md`.
- **ARCHITECTURE.md**, 120 lines or fewer, plain English for a human reader: what it does, who uses it, a mermaid diagram and walk-through of how it works, its data, its connections, the key decisions, its rules and limits, how it's tested, and where the code lives. Link `SCHEMA.md`, `INTERLINKING.md` and `FLOWS.md` instead of repeating them.
- **INTERLINKING.md**: one edge per line to another module: foreign keys and many-to-many, signal → receiver → effect, cache keys, cross-module imports and calls, limits a shipped client sets.
- **FLOWS.md**: each user journey in the order the client calls the API, by operationId or route, naming the journey or test that proves it, or `Test: MISSING`.
- **LESSONS.md**: only bugs that teach a rule, as `- **L-<MOD>-NN** <rule>. *Prevents:* <failure>. *Test:* <test id or MISSING>`. Sources: fix commits and v1 bug logs. No dates, commit hashes, ticket numbers or stories.

Every claim about the code cites `path:symbol` (`apps/properties/services.py:property_approve`), never a line number, and comes from code you read; when the code and a v1 file disagree, the code wins and the draft says nothing the code doesn't show. Present tense, current facts only: no history wording, dates or open work.

### v1 migration mode
| v1 file | Goes to |
|---|---|
| `SKILL.md` | SKILL.md: keep the invariants, settled decisions, read-next and verify commands; drop history ("changes vs R66", dates) |
| `ARCHITECTURE.md` | Source material for the new ARCHITECTURE.md (step 3); system-wide content goes to `docs/architecture/overview.md` |
| `INTERLINKING.md` | INTERLINKING.md, one edge per line |
| `USER_FLOWS.md` | FLOWS.md, in the client's call order, each naming its test or MISSING |
| `BUG_LOG.md` | LESSONS.md: one lesson per bug that teaches a rule; the stories go |
| `API_CONTRACTS.md`, `DB_SCHEMA.md` | Thrown away; step 2 regenerates CONTRACTS.md and SCHEMA.md |
| Underscore files (`_CODING_RULES.md`, `_QA_CHECKLIST.md`, `_GLOBAL_BUG_LOG.md`, `_MODULE_MAP.md`, `_PROJECT_OVERVIEW.md`) | Their repo-specific rules to CLAUDE.md, lessons to the owning module, checklists to FLOWS and journeys, the module map to INTERLINKING and the architecture overview. List where each part went |

Drop what the kit now enforces: mandatory pre-read lists, "update docs last", confirmation blocks, "log every bug", and any instruction to test against production (that one becomes a LESSON saying never to).

## 4. Check and hand over
For each draft, before showing it:
- `SKILL.md` has frontmatter with `paths` and is 80 lines or fewer, and links `ARCHITECTURE.md`.
- `ARCHITECTURE.md` is 120 lines or fewer and has all nine headings from the template.
- `grep -rnE "(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}" .claude/skills/_draft/<module>` finds nothing outside CONTRACTS.md and SCHEMA.md.
- Every LESSON ends with `*Test:*` and an id or MISSING; every FLOW names a test or MISSING.
- CONTRACTS.md and SCHEMA.md start with their GENERATED header.

Then show Waqar each module's SKILL.md and the counts (invariants, edges, flows with and without tests, lessons). On approval, per module: move `_draft/<module>/` to `.claude/skills/<module>/`, replacing the v1 folder, and commit it alone: `docs(skills): add <module> skill` or `docs(skills): move <module> skill to v2`. Remove `_draft/` when it's empty. In a fresh session afterwards, an edit under the module's `paths` should be denied until the skill is loaded.
