import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import cached_plugin_scripts, fake_kit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_template.py"


def run(*args: str, script: Path = SCRIPT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=60)


def write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


@pytest.fixture
def templates(tmp_path) -> Path:
    root = tmp_path / "templates"
    write(
        root,
        {
            "README.md": "# Templates\n",
            "common/README.md": "# Common pack docs\n",
            "common/CLAUDE.md.template": "# <!-- fill: project name -->\n",
            "common/README.md.template": "# <!-- fill: project name -->\n",
            "common/kit.json": json.dumps(
                {"stack": "common", "gate": {"review": {"min_lines": 80, "always": ["**/auth/**"]}}, "keep": 1}
            ),
            "common/docs/README.md": "# Docs\n",
            "common/.gitignore": "common ignores\n",
            "common/scripts/docs-check.py": "print('docs ok')\n",
            "mern/README.md": "# MERN pack docs\n",
            "mern/CLAUDE.md.template": "## MERN\n",
            "mern/kit.json": json.dumps(
                {"gate": {"journeys": {"command": "hurl --test journeys/*.hurl"}, "review": {"always": ["x/**"]}}}
            ),
            "mern/.gitignore": "mern ignores\n",
            "mern/server/tsconfig.json": "{}\n",
            "django/manage.py.txt": "django\n",
        },
    )
    (root / "common/scripts/docs-check.py").chmod(0o755)
    return root


def apply_json(templates: Path, dest: Path, *extra: str) -> dict:
    result = run("--templates", str(templates), "--stack", "mern", "--dest", str(dest), "--json", *extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def classes(report: dict) -> dict[str, str]:
    return {entry["path"]: entry["class"] for entry in report["files"]}


def test_a_fresh_destination_gets_every_file_as_new(templates, tmp_path):
    dest = tmp_path / "app"

    report = apply_json(templates, dest)

    assert classes(report) == {
        ".claude/kit.json": "new",
        ".gitignore": "new",
        "docs/README.md": "new",
        "scripts/docs-check.py": "new",
        "server/tsconfig.json": "new",
    }
    assert (dest / "docs/README.md").read_text() == "# Docs\n"
    assert (dest / "server/tsconfig.json").read_text() == "{}\n"
    assert report["stack"] == "mern" and report["dry_run"] is False


def test_a_stack_file_wins_over_a_common_file_at_the_same_path(templates, tmp_path):
    dest = tmp_path / "app"

    report = apply_json(templates, dest)

    assert (dest / ".gitignore").read_text() == "mern ignores\n"
    sources = {entry["path"]: entry["sources"] for entry in report["files"]}
    assert sources[".gitignore"] == [str(templates / "mern/.gitignore")]


def test_kit_json_merges_deeply_with_the_stack_winning_and_names_the_stack(templates, tmp_path):
    dest = tmp_path / "app"

    report = apply_json(templates, dest)

    assert json.loads((dest / ".claude/kit.json").read_text()) == {
        "stack": "mern",
        "gate": {
            "review": {"min_lines": 80, "always": ["x/**"]},
            "journeys": {"command": "hurl --test journeys/*.hurl"},
        },
        "keep": 1,
    }
    sources = {entry["path"]: entry["sources"] for entry in report["files"]}
    assert sources[".claude/kit.json"] == [str(templates / "common/kit.json"), str(templates / "mern/kit.json")]


def test_kit_json_names_the_stack_even_when_no_pack_has_one(templates, tmp_path):
    (templates / "common/kit.json").unlink()
    (templates / "mern/kit.json").unlink()
    dest = tmp_path / "app"

    apply_json(templates, dest)

    assert json.loads((dest / ".claude/kit.json").read_text()) == {"stack": "mern"}


def test_pack_readmes_and_fill_templates_are_not_copied_but_listed_to_fill(templates, tmp_path):
    dest = tmp_path / "app"

    report = apply_json(templates, dest)

    assert not (dest / "README.md").exists()
    assert not list(dest.rglob("*.template"))
    assert (dest / "docs/README.md").exists()
    assert report["fill"] == {
        "CLAUDE.md": [str(templates / "common/CLAUDE.md.template"), str(templates / "mern/CLAUDE.md.template")],
        "README.md": [str(templates / "common/README.md.template")],
    }


def test_existing_files_are_never_overwritten(templates, tmp_path):
    dest = tmp_path / "app"
    write(dest, {".gitignore": "mern ignores\n", "server/tsconfig.json": '{"strict": true}\n'})

    report = apply_json(templates, dest)

    assert classes(report)[".gitignore"] == "identical"
    assert classes(report)["server/tsconfig.json"] == "differs"
    assert classes(report)["docs/README.md"] == "new"
    assert (dest / "server/tsconfig.json").read_text() == '{"strict": true}\n'


def test_a_path_blocked_by_a_file_or_a_directory_counts_as_differs(templates, tmp_path):
    dest = tmp_path / "app"
    write(dest, {"server": "a file where a directory goes\n"})
    (dest / "docs/README.md").mkdir(parents=True)

    report = apply_json(templates, dest)

    assert classes(report)["server/tsconfig.json"] == "differs"
    assert classes(report)["docs/README.md"] == "differs"
    assert (dest / "server").read_text() == "a file where a directory goes\n"


def test_a_symlinked_directory_leading_outside_the_destination_is_not_written_through(templates, tmp_path):
    dest, outside = tmp_path / "app", tmp_path / "outside"
    outside.mkdir()
    dest.mkdir()
    (dest / "docs").symlink_to(outside)

    report = apply_json(templates, dest)

    assert classes(report)["docs/README.md"] == "differs"
    assert not (outside / "README.md").exists()


def test_a_dry_run_classifies_but_writes_nothing(templates, tmp_path):
    dest = tmp_path / "app"
    write(dest, {".gitignore": "old\n"})

    report = apply_json(templates, dest, "--dry-run")

    assert report["dry_run"] is True
    assert classes(report)[".gitignore"] == "differs"
    assert classes(report)["docs/README.md"] == "new"
    assert sorted(path.name for path in dest.rglob("*")) == [".gitignore"]


def test_a_dry_run_into_a_missing_directory_does_not_create_it(templates, tmp_path):
    dest = tmp_path / "app"

    run("--templates", str(templates), "--stack", "mern", "--dest", str(dest), "--dry-run")

    assert not dest.exists()


def test_the_human_output_lists_every_path_with_its_class(templates, tmp_path):
    dest = tmp_path / "app"
    write(dest, {"server/tsconfig.json": "{ }\n"})

    result = run("--templates", str(templates), "--stack", "mern", "--dest", str(dest))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "new        .claude/kit.json" in lines
    assert "differs    server/tsconfig.json" in lines
    assert "fill       CLAUDE.md from common/CLAUDE.md.template, mern/CLAUDE.md.template" in lines
    assert "Wrote 4 new files; 0 identical, 1 differ." in lines


def test_an_unknown_stack_exits_2_and_lists_the_stacks(templates, tmp_path):
    result = run("--templates", str(templates), "--stack", "rails", "--dest", str(tmp_path / "app"))

    assert result.returncode == 2
    assert "unknown stack 'rails'. Stacks: django, mern." in result.stderr
    assert not (tmp_path / "app").exists()


def test_common_is_not_a_stack(templates, tmp_path):
    result = run("--templates", str(templates), "--stack", "common", "--dest", str(tmp_path / "app"))

    assert result.returncode == 2


def test_the_agent_adapters_are_not_a_stack(templates, tmp_path):
    write(templates, {"agents/copilot/copilot-instructions.md": "See AGENTS.md.\n"})

    listed = run("--templates", str(templates), "--list-stacks", "--json")
    applied = run("--templates", str(templates), "--stack", "agents", "--dest", str(tmp_path / "app"))

    assert json.loads(listed.stdout)["stacks"] == ["django", "mern"]
    assert applied.returncode == 2
    assert not (tmp_path / "app").exists()


def test_file_modes_are_kept(templates, tmp_path):
    dest = tmp_path / "app"

    apply_json(templates, dest)

    assert (dest / "scripts/docs-check.py").stat().st_mode & 0o777 == 0o755
    assert (dest / "docs/README.md").stat().st_mode & 0o111 == 0


def test_tool_caches_inside_a_pack_are_not_copied(templates, tmp_path):
    write(templates, {"common/scripts/__pycache__/x.pyc": "", "mern/server/node_modules/pkg/index.js": ""})
    dest = tmp_path / "app"

    report = apply_json(templates, dest)

    assert not any("__pycache__" in path or "node_modules" in path for path in classes(report))


def test_invalid_kit_json_in_a_pack_exits_2(templates, tmp_path):
    (templates / "mern/kit.json").write_text("{not json")

    result = run("--templates", str(templates), "--stack", "mern", "--dest", str(tmp_path / "app"))

    assert result.returncode == 2
    assert "mern/kit.json isn't valid JSON" in result.stderr


def test_list_stacks_names_the_templates_and_the_stacks(templates):
    result = run("--templates", str(templates), "--list-stacks", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"templates": str(templates), "stacks": ["django", "mern"]}


def test_stack_and_dest_are_required_without_list_stacks(templates):
    result = run("--templates", str(templates), "--stack", "mern")

    assert result.returncode == 2
    assert "--stack and --dest are required" in result.stderr


def kit_with_templates(root: Path, templates: Path) -> Path:
    kit = fake_kit(root)
    shutil.copytree(templates, kit / "templates")
    (kit / "home/rules").mkdir(parents=True)
    return kit


def known_marketplace(config: Path, location: Path) -> None:
    entry = {"source": {"source": "directory", "path": str(location)}, "installLocation": str(location)}
    (config / "plugins/known_marketplaces.json").write_text(json.dumps({"claude-kit": entry}))


def listed_templates(script: Path) -> str:
    result = run("--list-stacks", "--json", script=script)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["templates"]


def test_a_cached_copy_finds_the_templates_in_the_repo_the_rules_link_points_into(templates, claude_config, tmp_path):
    kit = kit_with_templates(tmp_path / "kit", templates)
    (claude_config / "rules").symlink_to(kit / "home/rules")
    scripts = cached_plugin_scripts(claude_config)

    assert listed_templates(scripts / "apply_template.py") == str(kit / "templates")


def test_a_cached_copy_falls_back_to_the_kit_marketplace_install_location(templates, claude_config, tmp_path):
    kit = kit_with_templates(tmp_path / "kit", templates)
    scripts = cached_plugin_scripts(claude_config)
    known_marketplace(claude_config, kit)

    assert listed_templates(scripts / "apply_template.py") == str(kit / "templates")


def test_the_rules_link_wins_over_the_marketplace_install_location(templates, claude_config, tmp_path):
    linked = kit_with_templates(tmp_path / "linked-kit", templates)
    (claude_config / "rules").symlink_to(linked / "home/rules")
    scripts = cached_plugin_scripts(claude_config)
    known_marketplace(claude_config, kit_with_templates(tmp_path / "other-kit", templates))

    assert listed_templates(scripts / "apply_template.py") == str(linked / "templates")


def test_a_running_checkout_uses_its_own_templates(claude_config):
    result = run("--list-stacks", "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["templates"] == str(SCRIPT.parents[3] / "templates")


def test_a_cached_copy_with_no_kit_to_find_exits_2_and_says_where_it_looked(claude_config):
    scripts = cached_plugin_scripts(claude_config)
    (claude_config / "plugins/known_marketplaces.json").write_text("{not json")

    result = run("--list-stacks", script=scripts / "apply_template.py")

    assert result.returncode == 2
    assert "can't find the kit repo" in result.stderr
    assert f"the repo {claude_config / 'rules'} links into (none)" in result.stderr
    assert "known_marketplaces.json (none)" in result.stderr
    assert "Run install.sh from the kit checkout, or pass --templates <kit repo>/templates." in result.stderr


def test_a_kit_without_templates_exits_2(claude_config, tmp_path):
    kit = fake_kit(tmp_path / "kit", {"home/rules/code-style.md": "# Code style\n"})
    (claude_config / "rules").symlink_to(kit / "home/rules")
    scripts = cached_plugin_scripts(claude_config)

    result = run("--list-stacks", script=scripts / "apply_template.py")

    assert result.returncode == 2
    assert f"{kit / 'templates'} is not a directory" in result.stderr


def test_gitignore_additions_make_a_new_gitignore_with_every_pack_line_once(templates, tmp_path):
    write(
        templates,
        {"common/gitignore.additions": ".env*\n!.env.example\n", "mern/gitignore.additions": ".env*\ndist/\n"},
    )
    dest = tmp_path / "app"

    report = apply_json(templates, dest)

    assert classes(report)[".gitignore"] == "new"
    assert (dest / ".gitignore").read_text() == "common ignores\n.env*\n!.env.example\nmern ignores\ndist/\n"
    assert not (dest / "gitignore.additions").exists()


def test_an_existing_gitignore_gets_only_the_missing_lines_appended(templates, tmp_path):
    write(templates, {"common/gitignore.additions": ".env*\n!.env.example\n"})
    dest = tmp_path / "app"
    write(dest, {".gitignore": "node_modules/\n.env*"})

    report = apply_json(templates, dest)

    entry = next(item for item in report["files"] if item["path"] == ".gitignore")
    assert entry["class"] == "append"
    assert entry["append"] == ["common ignores", "!.env.example", "mern ignores"]
    assert (dest / ".gitignore").read_text() == "node_modules/\n.env*\ncommon ignores\n!.env.example\nmern ignores\n"


def test_a_gitignore_holding_every_line_is_identical_and_a_dry_run_appends_nothing(templates, tmp_path):
    write(templates, {"common/gitignore.additions": ".env*\n"})
    dest = tmp_path / "app"
    write(dest, {".gitignore": "mern ignores\n.env*\ncommon ignores\n"})

    assert classes(apply_json(templates, dest))[".gitignore"] == "identical"

    write(dest, {".gitignore": "x\n"})
    report = apply_json(templates, dest, "--dry-run")

    assert classes(report)[".gitignore"] == "append"
    assert (dest / ".gitignore").read_text() == "x\n"


def test_only_limits_the_files_handled_and_the_fill_list(templates, tmp_path):
    dest = tmp_path / "app"

    report = apply_json(templates, dest, "--only", "docs/*", "--only", ".claude/kit.json")

    assert sorted(classes(report)) == [".claude/kit.json", "docs/README.md"]
    assert report["fill"] == {}
    assert sorted(path.relative_to(dest).as_posix() for path in dest.rglob("*") if path.is_file()) == [
        ".claude/kit.json",
        "docs/README.md",
    ]
