import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

KIT_REPO = Path(__file__).resolve().parents[3]
TEMPLATES = KIT_REPO / "templates"
APPLY = KIT_REPO / "plugins" / "kit" / "scripts" / "apply_template.py"
BUILD_AGENTS_MD = KIT_REPO / "plugins" / "kit" / "scripts" / "build_agents_md.py"
STACKS = ("django", "react-vite", "nextjs", "mern")
# The rule stacks /kit:new passes to build_agents_md.py for each template stack.
AGENTS_MD_RULES = {
    "django": ("django", "api-design"),
    "react-vite": ("react",),
    "nextjs": ("nextjs", "react"),
    "mern": ("api-design", "node-express", "mongodb", "react"),
}
FILL_MARKER = re.compile(r"<!-- fill:.*?-->", re.DOTALL)


def betterleaks() -> str | None:
    """The real betterleaks binary; a mise shim can't start inside the tests' environment."""
    found = shutil.which("betterleaks")
    if found is None or Path(found).resolve().name == "mise":
        return None
    return found if subprocess.run([found, "version"], capture_output=True).returncode == 0 else None


def apply(stack: str, dest: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(APPLY), "--templates", str(TEMPLATES), "--stack", stack, "--dest", str(dest), "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


def template_files(*suffixes: str) -> list[Path]:
    return sorted(path for path in TEMPLATES.rglob("*") if path.is_file() and path.name.endswith(suffixes))


@pytest.mark.parametrize("path", template_files(".json"), ids=lambda path: str(path.relative_to(TEMPLATES)))
def test_every_json_template_parses(path):
    json.loads(path.read_text())


@pytest.mark.parametrize("path", template_files(".toml"), ids=lambda path: str(path.relative_to(TEMPLATES)))
def test_every_toml_template_parses(path):
    tomllib.loads(path.read_text())


@pytest.mark.parametrize("stack", STACKS)
def test_each_stack_lays_down_cleanly_with_merged_kit_config(stack, tmp_path):
    report = apply(stack, tmp_path / "app")

    assert {entry["class"] for entry in report["files"]} == {"new"}
    kit = json.loads((tmp_path / "app/.claude/kit.json").read_text())
    assert kit["stack"] == stack
    assert kit["gate"]["review"]["min_lines"] == 50
    assert kit["gate"]["review"]["always"]
    assert kit["gate"]["journeys"]["command"]
    assert kit["docs"] == {"allowed": []}
    assert set(report["fill"]) == {"CLAUDE.md", "README.md"}
    assert len(report["fill"]["CLAUDE.md"]) == 2, "the stack adds its section under the common CLAUDE.md"


@pytest.mark.parametrize("stack", STACKS)
def test_each_stack_gets_the_common_and_stack_ignore_lines_and_its_lefthook_jobs(stack, tmp_path):
    apply(stack, tmp_path / "app")

    ignored = (tmp_path / "app/.gitignore").read_text().splitlines()
    assert {".env*", "!.env.example", ".claude/settings.local.json", ".worktrees/", ".claude/HANDOFF.md"} <= set(
        ignored
    )
    assert len(ignored) == len(set(ignored))
    assert "- .lefthook/*.yml" in (tmp_path / "app/lefthook.yml").read_text()
    assert (tmp_path / f"app/.lefthook/{stack}.yml").is_file()


@pytest.mark.parametrize("stack", STACKS)
def test_a_filled_in_project_passes_the_docs_check_with_claude_md_in_its_limit(stack, tmp_path):
    root = tmp_path / "app"
    report = apply(stack, root)
    for target, sources in report["fill"].items():
        (root / target).write_text("\n".join(Path(source).read_text() for source in sources))
    for path in root.rglob("*.md"):
        if path.name != "_template.md":
            text = FILL_MARKER.sub("Filled in.", path.read_text()).replace("YYYY-MM-DD", "2026-10-02")
            path.write_text(text)
    rule_args = [arg for rule in AGENTS_MD_RULES[stack] for arg in ("--stack", rule)]
    subprocess.run([sys.executable, str(BUILD_AGENTS_MD), "--root", str(root), *rule_args], check=True, timeout=60)
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    result = subprocess.run(
        [sys.executable, str(root / "scripts/docs-check.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout
    claude_md = (root / "CLAUDE.md").read_text()
    assert len(claude_md.splitlines()) <= 150
    assert "@AGENTS.md" not in claude_md, "Claude Code already gets the kit's rules globally; don't load them twice"


@pytest.mark.parametrize("stack", STACKS)
def test_the_first_commit_of_each_stack_passes_the_secret_scan(stack, tmp_path):
    scanner = betterleaks()
    if scanner is None:
        pytest.skip("betterleaks isn't installed")
    root = tmp_path / "app"
    apply(stack, root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    result = subprocess.run(
        [scanner, "git", "--staged", "--redact", "--no-banner", str(root)], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stdout + result.stderr
