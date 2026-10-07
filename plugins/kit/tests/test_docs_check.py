import re
import shutil
import subprocess
import sys
from pathlib import Path

import doc_name_cases
import pytest

KIT_REPO = Path(__file__).resolve().parents[3]
COMMON = KIT_REPO / "templates" / "common"
SCRIPT = COMMON / "scripts" / "docs-check.py"
FILL_MARKER = re.compile(r"<!-- fill:.*?-->", re.DOTALL)

ADR = """---
status: accepted
date: 2026-10-02
decision-makers: Waqar Ahmad
---

# Use PostGIS

## Context and Problem Statement

We store points.

## Considered Options

* PostGIS

## Decision Outcome

Chosen option: PostGIS, because it does geography.
"""

GOOD_REPO = {
    "README.md": "# App\n\nRun it with make.\n",
    "CLAUDE.md": "# App\n",
    "docs/README.md": "# Docs\n\n- [Deploy](runbooks/deploy.md)\n- [PostGIS](decisions/0001-use-postgis.md)\n",
    "docs/runbooks/deploy.md": "# Deploy\n\nPush to main.\n",
    "docs/decisions/0001-use-postgis.md": ADR,
}


def write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args], capture_output=True, text=True, timeout=60
    )


def problems(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stdout.splitlines() if not line.startswith("docs-check:")]


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "app"
    write(root, GOOD_REPO)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_a_repo_that_meets_the_standard_passes(repo):
    result = run(repo)

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "docs-check: ok, 5 Markdown files checked."


def test_the_common_templates_pass_once_filled_in(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(COMMON, root)
    (root / "CLAUDE.md.template").rename(root / "CLAUDE.md")
    (root / "README.md.template").rename(root / "README.md")
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    unfilled = problems(run(root))

    assert any("template marker" in line for line in unfilled)
    assert any("must be the day it was decided" in line for line in unfilled)

    for path in root.rglob("*.md"):
        if path.name != "_template.md":
            text = FILL_MARKER.sub("Filled in.", path.read_text()).replace("YYYY-MM-DD", "2026-10-02")
            path.write_text(text)
    result = run(root)

    assert result.returncode == 0, result.stdout
    claude_lines = len((root / "CLAUDE.md").read_text().splitlines())
    assert claude_lines <= 100, "the common CLAUDE.md leaves room for the stack section"


def test_missing_required_docs_and_adrs_are_reported(repo):
    for rel in ("CLAUDE.md", "docs/runbooks/deploy.md", "docs/decisions/0001-use-postgis.md"):
        (repo / rel).unlink()
    write(repo, {"docs/README.md": "# Docs\n"})

    result = run(repo)

    assert result.returncode == 1
    assert problems(result) == [
        "CLAUDE.md: is missing. Every repo has it; the kit's templates/common has a starting point.",
        "docs/decisions: has no ADR. Record the first decision as 0001-<title>.md.",
        "docs/runbooks/deploy.md: is missing. Every repo has it; the kit's templates/common has a starting point.",
    ]


def test_a_long_claude_md_is_reported(repo):
    write(repo, {"CLAUDE.md": "# App\n" + "- rule\n" * 150})

    assert problems(run(repo)) == [
        "CLAUDE.md: has 151 lines; keep it to 150. Move module detail into module skills and the rest into docs/."
    ]


def test_markdown_outside_the_docs_layout_is_reported_unless_allowed(repo):
    write(
        repo,
        {
            "notes.md": "x\n",
            "docs/overview.md": "x\n",
            "specs/billing.md": "x\n",
            "apps/billing/README.md": "# Billing\n",
            ".github/ISSUE_TEMPLATE/bug_report.md": "x\n",
            ".claude/skills/billing/SKILL.md": "x\n",
            "node_modules/pkg/notes.md": "x\n",
            ".claude/kit.json": '{"docs": {"allowed": ["specs/**/*.md"]}}',
        },
    )

    flagged = sorted({line.split(":")[0] for line in problems(run(repo))})

    assert flagged == ["docs/overview.md", "notes.md"]


def test_scratch_versioned_dated_and_badly_cased_names_are_reported(repo):
    names = [
        "docs/guides/deploy-plan.md",
        "docs/guides/api-v2.md",
        "docs/guides/launch-2026-09-30.md",
        "docs/guides/Setup_Guide.md",
        "docs/decisions/12-use-redis.md",
        "docs/guides/trip-planner.md",
    ]
    index = "# Docs\n\n- [Deploy](runbooks/deploy.md)\n- [ADR](decisions/0001-use-postgis.md)\n"
    index += "".join(f"- [x]({name.removeprefix('docs/')})\n" for name in names)
    write(repo, {"docs/README.md": index, **dict.fromkeys(names, "# X\n")})

    found = problems(run(repo))

    assert not any(line.startswith("docs/guides/deploy-plan.md") for line in found)
    assert any(line.startswith("docs/guides/api-v2.md: has the word v2") for line in found)
    assert any(line.startswith("docs/guides/launch-2026-09-30.md: has a date") for line in found)
    assert "docs/guides/Setup_Guide.md: must have a kebab-case name, such as calling-hub-operations.md." in found
    assert "docs/decisions/12-use-redis.md: must be named NNNN-kebab-title.md, such as 0003-use-postgis.md." in found
    assert not any(line.startswith("docs/guides/trip-planner.md") for line in found)


def test_a_scratch_word_outside_the_docs_dirs_is_reported_with_its_reason(repo):
    write(repo, {"deploy-plan.md": "# Plan\n"})

    found = problems(run(repo))

    assert (
        "deploy-plan.md: has the word plan in its name, which marks a scratch, dated or versioned file. Name it by "
        "its subject and edit it in place; summaries, plans and status go in the commit, the PR or a PM card."
    ) in found


@pytest.mark.parametrize("rel", doc_name_cases.WELL_NAMED)
def test_a_well_named_doc_passes_as_in_the_docs_guard(repo, rel):
    index = GOOD_REPO["docs/README.md"] + f"- [Doc]({rel.removeprefix('docs/')})\n"
    write(repo, {"docs/README.md": index, rel: ADR if rel.startswith("docs/decisions/") else "# Doc\n"})

    assert not [line for line in problems(run(repo)) if line.startswith(f"{rel}:")]


@pytest.mark.parametrize("rel", doc_name_cases.SCRATCH)
def test_a_scratch_versioned_or_dated_name_is_reported_as_in_the_docs_guard(repo, rel):
    write(repo, {rel: "# Doc\n"})

    assert any(line.startswith(f"{rel}: has ") for line in problems(run(repo)))


def test_every_doc_must_be_linked_from_the_index(repo):
    write(
        repo,
        {
            "docs/guides/onboarding.md": "# Onboarding\n",
            "docs/guides/billing.md": "# Billing\n",
            "docs/README.md": GOOD_REPO["docs/README.md"] + "\nSee [billing][b].\n\n[b]: ./guides/billing.md#top\n",
        },
    )

    assert problems(run(repo)) == [
        "docs/guides/onboarding.md: isn't linked from docs/README.md. Add it under the readers it serves."
    ]


def test_adrs_need_front_matter_and_the_madr_headings_in_order(repo):
    swapped = ADR.replace("## Considered Options", "## Tmp").replace("## Decision Outcome", "## Considered Options")
    write(
        repo,
        {
            "docs/decisions/0002-no-front-matter.md": "# Title\n\n## Context and Problem Statement\n",
            "docs/decisions/0003-swapped.md": swapped.replace("## Tmp", "## Decision Outcome"),
            "docs/decisions/0004-bad-date.md": ADR.replace("2026-10-02", "October 2026").replace(
                "decision-makers: Waqar Ahmad\n", ""
            ),
            "docs/README.md": GOOD_REPO["docs/README.md"]
            + "- [a](decisions/0002-no-front-matter.md)\n- [b](decisions/0003-swapped.md)\n"
            + "- [c](decisions/0004-bad-date.md)\n",
        },
    )

    assert problems(run(repo)) == [
        "docs/decisions/0002-no-front-matter.md: needs MADR front matter: status, date (YYYY-MM-DD) and "
        "decision-makers.",
        "docs/decisions/0003-swapped.md: must keep the MADR headings in order: ## Context and Problem Statement, "
        "## Considered Options, ## Decision Outcome.",
        "docs/decisions/0004-bad-date.md: front matter date 'October 2026' must be the day it was decided, as "
        "YYYY-MM-DD.",
        "docs/decisions/0004-bad-date.md: front matter has no decision-makers.",
    ]


ARCHITECTURE_HEADINGS = (
    "What it does",
    "Who uses it",
    "How it works",
    "Data",
    "Connections",
    "Key decisions",
    "Rules and limits",
    "How it's tested",
    "Where the code lives",
)
GOOD_ARCHITECTURE = "# Billing: architecture\n\n" + "\n\n".join(
    f"## {heading}\n\nx." for heading in ARCHITECTURE_HEADINGS
)


def test_an_architecture_doc_with_every_heading_within_the_line_cap_passes(repo):
    write(repo, {".claude/skills/billing/ARCHITECTURE.md": GOOD_ARCHITECTURE})

    assert run(repo).returncode == 0


def test_an_architecture_doc_missing_a_heading_is_reported(repo):
    write(repo, {".claude/skills/billing/ARCHITECTURE.md": GOOD_ARCHITECTURE.replace("## Data\n\nx.\n\n", "")})

    assert problems(run(repo)) == [".claude/skills/billing/ARCHITECTURE.md: is missing the heading: ## Data."]


def test_a_long_architecture_doc_is_reported(repo):
    write(repo, {".claude/skills/billing/ARCHITECTURE.md": GOOD_ARCHITECTURE + "x\n" * 120})

    assert problems(run(repo)) == [
        ".claude/skills/billing/ARCHITECTURE.md: has 156 lines; keep it to 120. It's for a human reader; move "
        "detail into SKILL.md or the module's other files."
    ]


def test_an_architecture_md_outside_a_module_folder_is_not_checked_as_one(repo):
    write(repo, {".claude/skills/ARCHITECTURE.md": "# Not a module doc\n"})

    assert problems(run(repo)) == []


def test_dates_and_history_words_are_reported_outside_adrs_code_and_the_changelog(repo):
    write(
        repo,
        {
            "docs/runbooks/deploy.md": (
                "# Deploy\n\nAs of 2026-09-30 we deploy by hand.\n\n"
                "It used to run on Heroku, and this was previously manual.\n\n"
                "The key is used to sign tokens. Run `date -d 2026-01-01`.\n\n"
                "```text\n2026-09-30 deployed\n```\n"
            ),
            "CHANGELOG.md": "# Changelog\n\n## 1.0.0 - 2026-09-30\n\nPreviously nothing.\n",
            ".claude/skills/billing/SKILL.md": "---\nname: billing\n---\n\nSince September 2026, invoices round.\n",
        },
    )

    assert problems(run(repo)) == [
        '.claude/skills/billing/SKILL.md:5: says "September 2026". Docs state current facts in the present tense; '
        "dates and history belong in ADRs, the CHANGELOG and git.",
        'docs/runbooks/deploy.md:3: says "2026-09-30". Docs state current facts in the present tense; '
        "dates and history belong in ADRs, the CHANGELOG and git.",
        'docs/runbooks/deploy.md:5: says "It used to". Docs state current facts in the present tense; '
        "dates and history belong in ADRs, the CHANGELOG and git.",
    ]


def test_template_markers_are_reported_except_in_the_handoff_template(repo):
    write(
        repo,
        {
            "README.md": "# App\n\n<!-- fill: what it does -->\n",
            "docs/api/handoffs/_template.md": "# <!-- fill: feature --> handoff\n",
            "docs/README.md": GOOD_REPO["docs/README.md"] + "- [Handoff](api/handoffs/_template.md)\n",
        },
    )

    assert problems(run(repo)) == ["README.md: still has a <!-- fill: template marker. Fill it in or delete it."]


def test_env_vars_the_code_reads_must_be_in_env_example(repo):
    write(
        repo,
        {
            "config/settings/base.py": (
                'SECRET_KEY = env("SECRET_KEY")\nDEBUG = env.bool("DEBUG", default=False)\n'
                'REDIS = os.environ.get("REDIS_URL")\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "x")\n'
            ),
            "src/api.ts": "const base = import.meta.env.VITE_API_URL;\nif (import.meta.env.DEV) {}\n",
            "src/server.ts": 'const port = process.env.PORT ?? 3000;\nconst env = process.env["NODE_ENV"];\n',
            "node_modules/pkg/index.js": "process.env.SOMETHING_ELSE\n",
            ".env.example": "# Settings\nSECRET_KEY=\nDEBUG=true\n# REDIS_URL=redis://localhost:6379/0\n",
        },
    )

    assert problems(run(repo)) == [
        ".env.example: doesn't list PORT, which src/server.ts reads. Add `PORT=` with a safe local value, or empty "
        "for a secret.",
        ".env.example: doesn't list VITE_API_URL, which src/api.ts reads. Add `VITE_API_URL=` with a safe local "
        "value, or empty for a secret.",
    ]


def test_claude_code_kit_and_mise_env_vars_are_exempt(repo):
    write(
        repo,
        {
            "scripts/hook.py": (
                'os.environ.get("CLAUDE_CONFIG_DIR")\nos.environ.get("CLAUDE_PLUGIN_ROOT")\n'
                'os.environ.get("CLAUDE_PLUGIN_DATA")\nos.environ.get("CLAUDE_PROJECT_DIR")\n'
                'os.environ.get("KIT_BYPASS")\nos.environ.get("KIT_DIRECT_MODE")\nos.environ.get("MISE_DATA_DIR")\n'
            ),
        },
    )

    assert problems(run(repo)) == []


def test_a_malformed_kit_json_is_a_problem(repo):
    write(repo, {".claude/kit.json": '{"docs": {"allowed": "docs/**"}}'})

    assert problems(run(repo)) == [".claude/kit.json: docs.allowed must be a list of glob strings; fix it."]


@pytest.mark.parametrize("docs", ["[]", '"docs/**"', "null"])
def test_a_docs_value_that_isnt_an_object_is_a_problem(repo, docs):
    write(repo, {".claude/kit.json": f'{{"docs": {docs}}}'})

    result = run(repo)

    assert problems(result) == [".claude/kit.json: docs must be an object; fix it."]
    assert result.returncode == 1


def test_a_kit_json_that_isnt_an_object_is_a_problem(repo):
    write(repo, {".claude/kit.json": "[]"})

    assert problems(run(repo)) == [".claude/kit.json: must hold a JSON object; fix it."]


def test_a_malformed_docs_ignore_is_a_problem(repo):
    write(repo, {".claude/kit.json": '{"docs": {"ignore": "vendor/**"}}'})

    assert problems(run(repo)) == [".claude/kit.json: docs.ignore must be a list of glob strings; fix it."]


def test_docs_ignore_drops_matching_paths_from_every_check(repo):
    write(
        repo,
        {
            "vendor/sample/README.md": "# Sample\n\n<!-- fill: unfilled -->\n",
            "vendor/sample/config.py": 'TOKEN = os.environ["VENDOR_TOKEN"]\n',
            "src/config.py": 'KEY = os.environ["APP_KEY"]\n',
            ".env.example": "# Settings\n",
            ".claude/kit.json": '{"docs": {"ignore": ["vendor/**"]}}',
        },
    )

    assert problems(run(repo)) == [
        ".env.example: doesn't list APP_KEY, which src/config.py reads. Add `APP_KEY=` with a safe local value, "
        "or empty for a secret."
    ]


def test_it_works_outside_git_and_rejects_a_missing_root(tmp_path):
    root = tmp_path / "plain"
    write(root, GOOD_REPO)
    write(root, {"node_modules/x/notes.md": "x\n"})

    assert run(root).returncode == 0
    assert run(tmp_path / "missing").returncode == 2
