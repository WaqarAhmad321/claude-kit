import re
import shutil
import subprocess
import sys
from pathlib import Path

import build_agents_md as agents_md
import pytest
from conftest import SCRIPTS, cached_plugin_scripts, fake_kit

UNIVERSAL_TITLES = ("## Code style", "## Verification and testing", "## Task workflow", "## Git commits", "## Docs")
PROJECT_NOTES = "## This project\n\nRun `make up`, then `make journeys`.\n"
KIT_RULES = SCRIPTS.parents[2] / "home" / "rules"


def generate(root: Path, *args: str) -> int:
    return agents_md.main(["--root", str(root), *args])


def agents_file(root: Path) -> str:
    return (root / "AGENTS.md").read_text(encoding="utf-8")


def test_generates_every_universal_rule_between_the_markers(tmp_path):
    assert generate(tmp_path) == 0
    text = agents_file(tmp_path)
    begin, end = text.index(agents_md.BEGIN_PREFIX), text.index(agents_md.END_MARKER)
    block = text[begin:end]
    assert text.startswith(agents_md.BEGIN_PREFIX)
    assert all(title in block for title in UNIVERSAL_TITLES)
    assert "## This project" in text[end:]
    assert "paths:" not in block
    assert "## Django / DRF" not in block


def test_leaves_out_what_only_claude_code_can_act_on(tmp_path):
    generate(tmp_path, "--stack", "django")
    text = agents_file(tmp_path)
    assert not re.search(r"(?<!claude-)kit:", text)
    assert "Who does what" not in text
    assert "main chat" not in text.lower()
    assert "rules load when" not in text
    assert "Size every task before starting, then follow the steps for its size.\n" in text
    assert "Open items go to the PM board, not into repo files.\n" in text


def test_leaves_out_the_models_section_too(tmp_path):
    generate(tmp_path)
    text = agents_file(tmp_path)
    assert "## Models" not in text
    assert "Opus: planning" not in text


def test_rerun_is_byte_for_byte_identical_and_keeps_the_recorded_stacks(tmp_path, capsys):
    generate(tmp_path, "--stack", "react", "--stack", "django")
    first = agents_file(tmp_path)
    capsys.readouterr()
    assert generate(tmp_path) == 0
    assert agents_file(tmp_path) == first
    assert capsys.readouterr().out == ""
    assert "--stack django --stack react" in first.splitlines()[0]
    assert first.index("## Django / DRF") < first.index("## React / TypeScript")


def test_keeps_the_project_section_and_restores_a_hand_edited_block(tmp_path):
    generate(tmp_path)
    fresh = agents_file(tmp_path)
    notes = fresh.replace(agents_md.PROJECT_SECTION.rstrip("\n"), PROJECT_NOTES.rstrip("\n"))
    (tmp_path / "AGENTS.md").write_text(notes.replace("## Code style", "## Code style (edited)"), encoding="utf-8")
    generate(tmp_path)
    assert agents_file(tmp_path) == notes
    assert agents_file(tmp_path).endswith(PROJECT_NOTES)


def test_adopts_an_existing_agents_md_as_the_project_section(tmp_path):
    (tmp_path / "AGENTS.md").write_text("\n\n# Legacy notes\n\nUse pnpm.\n", encoding="utf-8")
    generate(tmp_path)
    text = agents_file(tmp_path)
    assert text.startswith(agents_md.BEGIN_PREFIX)
    assert text.endswith(f"{agents_md.END_MARKER}\n\n# Legacy notes\n\nUse pnpm.\n")
    generate(tmp_path)
    assert agents_file(tmp_path) == text


def test_check_passes_on_a_current_file_and_writes_nothing(tmp_path):
    generate(tmp_path, "--stack", "nextjs")
    before = agents_file(tmp_path)
    assert generate(tmp_path, "--check") == 0
    assert agents_file(tmp_path) == before


def test_check_fails_on_a_missing_file(tmp_path, capsys):
    assert generate(tmp_path, "--check") == 1
    assert "is missing" in capsys.readouterr().err
    assert not (tmp_path / "AGENTS.md").exists()


def test_check_fails_when_the_block_was_edited_by_hand(tmp_path, capsys):
    generate(tmp_path)
    edited = agents_file(tmp_path).replace("Default to no comments.", "Comment freely.")
    (tmp_path / "AGENTS.md").write_text(edited, encoding="utf-8")
    assert generate(tmp_path, "--check") == 1
    assert "out of date" in capsys.readouterr().err
    assert agents_file(tmp_path) == edited


def test_check_fails_when_a_kit_rule_changes(tmp_path, monkeypatch, capsys):
    rules = tmp_path / "rules"
    shutil.copytree(KIT_RULES, rules)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(agents_md, "kit_rules_dir", lambda: rules)
    generate(project)
    code_style = rules / "code-style.md"
    code_style.write_text(code_style.read_text(encoding="utf-8") + "- Prefer composition.\n", encoding="utf-8")
    assert generate(project, "--check") == 1
    assert "--root" in capsys.readouterr().err
    generate(project)
    assert "- Prefer composition." in agents_file(project)


def test_every_stack_together_stays_within_the_caps(tmp_path):
    stacks = [arg for stack in agents_md.STACK_RULES for arg in ("--stack", stack)]
    assert generate(tmp_path, *stacks) == 0
    text = agents_file(tmp_path)
    block = text[: text.index(agents_md.END_MARKER) + len(agents_md.END_MARKER)]
    assert len(block.encode("utf-8")) <= agents_md.MAX_BLOCK_BYTES
    assert len(text.encode("utf-8")) < agents_md.CODEX_DEFAULT_MAX_BYTES


def test_warns_on_stderr_when_the_file_would_truncate_in_codex(tmp_path, capsys):
    generate(tmp_path)
    fresh = agents_file(tmp_path)
    huge_notes = "## This project\n\n" + ("Local context. " * 3000) + "\n"
    (tmp_path / "AGENTS.md").write_text(
        fresh.replace(agents_md.PROJECT_SECTION.rstrip("\n"), huge_notes.rstrip("\n")), encoding="utf-8"
    )
    capsys.readouterr()
    assert generate(tmp_path) == 0
    err = capsys.readouterr().err
    assert f"Codex reads only the first {agents_md.CODEX_DEFAULT_MAX_BYTES}" in err


def test_refuses_a_block_over_the_byte_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(agents_md, "MAX_BLOCK_BYTES", 50)
    assert generate(tmp_path) == 1
    assert "over the 50-byte cap" in capsys.readouterr().err
    assert not (tmp_path / "AGENTS.md").exists()


@pytest.mark.parametrize(
    "broken",
    [
        f"{agents_md.BEGIN_PREFIX} -->\nrules\n",
        f"{agents_md.END_MARKER}\nrules\n{agents_md.BEGIN_PREFIX} -->\n",
        f"{agents_md.BEGIN_PREFIX} -->\n{agents_md.END_MARKER}\n{agents_md.BEGIN_PREFIX} -->\n{agents_md.END_MARKER}\n",
    ],
)
def test_refuses_to_guess_when_the_markers_are_broken(tmp_path, capsys, broken):
    (tmp_path / "AGENTS.md").write_text(broken, encoding="utf-8")
    assert generate(tmp_path) == 1
    assert "exactly one claude-kit begin marker" in capsys.readouterr().err
    assert agents_file(tmp_path) == broken


def test_rule_section_strips_frontmatter_demotes_headings_and_names_untitled_rules():
    rule = (
        "---\npaths:\n  - '**/*.py'\n---\nIntro. Ask `kit:reviewer` for a review.\n\n## Commit style\n- Small.\n\n"
        "## Who does what\n- Main chat plans.\n### Builders\n- Build.\n\n## After\n- Kept.\n"
    )
    assert agents_md.rule_section("git-commits", rule) == (
        "## Git commits\n\nIntro.\n### Commit style\n- Small.\n### After\n- Kept."
    )


def test_rule_section_drops_a_claude_code_import_line():
    rule = "---\npaths:\n  - '**/*.md'\n---\nIntro.\n\n@AGENTS.md\n\n## Commit style\n- Small.\n"
    assert agents_md.rule_section("docs", rule) == "## Docs\n\nIntro.\n### Commit style\n- Small."


def test_rule_section_drops_a_main_chat_sentence_outside_who_does_what():
    rule = "Intro.\n\n## Asking questions\n- Ask first. Subagents return the question to the main chat.\n"
    assert agents_md.rule_section("workflow", rule) == (
        "## Workflow\n\nIntro.\n### Asking questions\n- Ask first."
    )


def test_runs_as_a_script(tmp_path):
    script = SCRIPTS / "build_agents_md.py"
    result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--stack", "django"],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    check = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert check.returncode == 0, check.stderr
    missing_root = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path / "nowhere")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert missing_root.returncode == 1
    assert "not a directory" in missing_root.stderr


def run_script(script: Path, project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--root", str(project)], capture_output=True, text=True, timeout=60, check=False
    )


def test_a_cached_copy_reads_the_rules_from_the_repo_the_rules_link_points_into(claude_config, tmp_path):
    kit = fake_kit(tmp_path / "kit")
    shutil.copytree(KIT_RULES, kit / "home/rules")
    (kit / "home/rules/code-style.md").write_text("# Code style\n\n- From the linked kit.\n", encoding="utf-8")
    (claude_config / "rules").symlink_to(kit / "home/rules")
    scripts = cached_plugin_scripts(claude_config)

    result = run_script(scripts / "build_agents_md.py", tmp_path)

    assert result.returncode == 0, result.stderr
    assert "- From the linked kit." in agents_file(tmp_path)


def test_a_cached_copy_with_no_kit_to_find_exits_1_and_says_where_it_looked(claude_config, tmp_path):
    scripts = cached_plugin_scripts(claude_config)

    result = run_script(scripts / "build_agents_md.py", tmp_path)

    assert result.returncode == 1
    assert "build_agents_md: can't find the kit repo" in result.stderr
    assert not (tmp_path / "AGENTS.md").exists()
