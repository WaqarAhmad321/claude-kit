import subprocess

import kitskills
import pytest
from hookrun import commit_all, write


def skill(repo, directory: str, frontmatter: str, body: str = "# Rules\n"):
    return write(repo, f".claude/skills/{directory}/SKILL.md", f"---\n{frontmatter}---\n\n{body}")


def test_frontmatter_block_list_with_quotes_and_comments():
    text = (
        "---\nname: billing  # the module\npaths:\n  - \"src/billing/**\"\n  - 'tests/billing/**'\n  - docs/*.md\n---\n"
    )
    assert kitskills.parse_frontmatter(text) == {
        "name": "billing",
        "paths": ["src/billing/**", "tests/billing/**", "docs/*.md"],
    }


def test_frontmatter_flow_list_keeps_brace_groups_whole():
    text = "---\npaths: [\"web/**/*.{ts,tsx}\", 'web/styles/**']\n---\n"
    assert kitskills.parse_frontmatter(text)["paths"] == ["web/**/*.{ts,tsx}", "web/styles/**"]


def test_frontmatter_quoted_values_may_carry_a_comment():
    text = "---\nname: \"billing\"  # module\npaths:\n  - 'src/#hash/**'  # odd dir name\n---\n"
    assert kitskills.parse_frontmatter(text) == {"name": "billing", "paths": ["src/#hash/**"]}


def test_frontmatter_needs_the_opening_and_closing_markers():
    assert kitskills.parse_frontmatter("name: x\n---\n") == {}
    assert kitskills.parse_frontmatter("\n---\nname: x\n---\n") == {}
    assert kitskills.parse_frontmatter("---\nname: x\npaths: src/**\n") == {}


def test_frontmatter_ignores_nested_maps_and_block_scalars():
    text = (
        "---\ndescription: |\n  Use for:\n  - billing work\nmetadata:\n  owner: team\n  tags:\n    - a\n"
        "paths:\n  - src/**\n---\n"
    )
    fields = kitskills.parse_frontmatter(text)
    assert fields["paths"] == ["src/**"]
    assert fields["description"] == "|"


def test_module_skills_read_names_and_paths_and_skip_skills_without_paths(tmp_path):
    skill(tmp_path, "billing", "paths:\n  - src/billing/**\n")
    skill(tmp_path, "web-ui", "name: frontend\npaths: web/**/*.{ts,tsx}, web/styles/**\n")
    skill(tmp_path, "single", "paths: 'lib/**'\n")
    skill(tmp_path, "notes", "name: notes\ndescription: general advice\n")
    write(tmp_path, ".claude/skills/empty-dir/README.md", "not a skill")
    assert kitskills.module_skills(tmp_path) == [
        kitskills.Skill("billing", "billing", ("src/billing/**",)),
        kitskills.Skill("single", "single", ("lib/**",)),
        kitskills.Skill("frontend", "web-ui", ("web/**/*.{ts,tsx}", "web/styles/**")),
    ]
    assert kitskills.module_skills(tmp_path / "nowhere") == []


def test_matching_skills_returns_every_skill_whose_paths_match(tmp_path):
    skill(tmp_path, "billing", "paths:\n  - src/billing/**\n")
    skill(tmp_path, "api", "paths:\n  - src/**/api.py\n")
    skills = kitskills.module_skills(tmp_path)

    def names(rel):
        return [found.name for found in kitskills.matching_skills(skills, rel)]

    assert names("src/billing/api.py") == ["api", "billing"]
    assert names("src/other/api.py") == ["api"]
    assert names("README.md") == []


@pytest.mark.parametrize(
    "glob, path, matches",
    [
        ("src/**/*.ts", "src/a.ts", True),
        ("src/**/*.ts", "src/x/y/a.ts", True),
        ("src/**/*.ts", "lib/src/a.ts", False),
        ("src/**/*.ts", "srcx/a.ts", False),
        ("**/*.ts", "a.ts", True),
        ("**/*.ts", "deep/er/a.ts", True),
        ("src/**", "src/a/b/c.py", True),
        ("src/**", "src", False),
        ("src/", "src/a.py", True),
        ("**", "anything/at/all.txt", True),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("src/*", "src/a/b.py", False),
        ("a/**/b/**", "a/b/c", True),
        ("a/**/b/**", "a/x/y/b/c", True),
        ("a/x**y/b", "a/xzzy/b", True),
        ("a/x**y/b", "a/xz/zy/b", False),
        ("a/**/*.py", "a/.hidden/b.py", True),
        ("src/?.py", "src/a.py", True),
        ("src/?.py", "src/ab.py", False),
        ("src/[ab].py", "src/b.py", True),
        ("src/[!ab].py", "src/c.py", True),
        ("src/[!ab].py", "src/a.py", False),
        ("photos [2024/**", "photos [2024/a.jpg", False),
        ("photos \\[2024/**", "photos [2024/a.jpg", True),
        ("web/**/*.{ts,tsx}", "web/app/page.tsx", True),
        ("web/**/*.{ts,tsx}", "web/app/page.js", False),
        ("{api,web}/**", "web/x.ts", True),
        ("{api,{web,cli}}/*.ts", "cli/x.ts", True),
        ("./src/**", "src/a.py", True),
        ("/src/**", "src/a.py", True),
        ("src/a+b.py", "src/a+b.py", True),
    ],
)
def test_glob_matching(glob, path, matches):
    assert kitskills.glob_matches(glob, path) is matches


@pytest.mark.parametrize(
    "path, exempt",
    [
        (".claude/skills/billing/SKILL.md", True),
        (".claude/kit.json", True),
        ("app/migrations/0001_initial.py", True),
        ("backend/billing/migrations/0002.py", True),
        ("docs/CONTRACTS.md", True),
        ("SCHEMA.md", True),
        ("package-lock.json", True),
        ("web/pnpm-lock.yaml", True),
        ("uv.lock", True),
        ("node_modules/pkg/index.js", True),
        ("web/node_modules/pkg/index.js", True),
        (".git/config", True),
        ("src/billing/models.py", False),
        ("docs/contracts.md", False),
        ("src/migrations.py", False),
        ("", True),
    ],
)
def test_is_exempt(path, exempt):
    assert kitskills.is_exempt(path) is exempt


def test_canonical_name_maps_a_directory_alias_to_the_skill_name(tmp_path):
    skill(tmp_path, "web-ui", "name: frontend\npaths: web/**\n")
    assert kitskills.canonical_name(tmp_path, "web-ui") == "frontend"
    assert kitskills.canonical_name(tmp_path, "frontend") == "frontend"
    assert kitskills.canonical_name(tmp_path, "unknown") == "unknown"
    assert kitskills.canonical_name(None, "web-ui") == "web-ui"


def test_dirty_files_lists_modified_deleted_untracked_and_both_sides_of_a_rename(make_repo):
    repo = make_repo(files={"keep.py": "a\n", "edit me.py": "b\n", "gone.py": "c\n", "old_name.py": "d\n"})
    commit_all(repo)
    write(repo, "edit me.py", "changed\n")
    (repo / "gone.py").unlink()
    write(repo, "src/new_pkg/deep/mod.py", "new\n")
    subprocess.run(["git", "mv", "old_name.py", "new_name.py"], cwd=repo, check=True)
    assert kitskills.dirty_files(repo) == {
        "edit me.py",
        "gone.py",
        "src/new_pkg/deep/mod.py",
        "old_name.py",
        "new_name.py",
    }


def test_dirty_files_raises_outside_git(tmp_path):
    with pytest.raises(RuntimeError, match="git status failed"):
        kitskills.dirty_files(tmp_path)


def test_module_skills_also_match_the_real_path_of_a_symlinked_module(tmp_path):
    root = tmp_path.resolve()
    skill(root, "billing", "paths:\n  - app/billing/**\n  - lib/**\n")
    (root / "real_billing").mkdir()
    (root / "app").mkdir()
    (root / "app" / "billing").symlink_to(root / "real_billing")
    [billing] = kitskills.module_skills(root)
    assert billing.paths == ("app/billing/**", "lib/**")
    assert billing.resolved_paths == ("real_billing/**",)
    assert kitskills.matching_skills([billing], "real_billing/tax.py") == [billing]
    assert kitskills.matching_skills([billing], "real_other/tax.py") == []
