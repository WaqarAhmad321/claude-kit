"""scripts/publish-public.sh: refuses to export a tree carrying a private pattern or a likely secret, and
otherwise syncs a ref's tracked files into a checkout of the public repo as one commit, replacing whatever was
there."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from hookrun import commit_all

KIT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = KIT_ROOT / "scripts" / "publish-public.sh"
HOOKS_BIN = KIT_ROOT / "plugins" / "kit" / "hooks" / "bin"
# A generic-shaped fixture, not a real credential: the script must flag it outside a test path.
LEAKED_CONFIG = 'DB_PASSWORD = "Sup3rSecretValue2026!"\n'
# Stands in for a real client name: the patterns file itself, never the repo, is where that belongs.
PRIVATE_PATTERN = "acme-client"


def _init_main(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=repo, check=True)


def fake_source(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    """A minimal kit checkout: this script plus the real scan_secrets.py/kitlib.py it imports, so the gate
    behaves exactly as it does on the real repo."""
    repo = tmp_path / "kit"
    _init_main(repo)
    (repo / "scripts").mkdir()
    shutil.copy(SCRIPT, repo / "scripts" / SCRIPT.name)
    (repo / "scripts" / SCRIPT.name).chmod(0o755)
    bin_dir = repo / "plugins" / "kit" / "hooks" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("kitlib.py", "scan_secrets.py"):
        shutil.copy(HOOKS_BIN / name, bin_dir / name)
    for rel, content in (extra or {}).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    commit_all(repo)
    return repo


def fake_target(tmp_path: Path, name: str = "pub") -> Path:
    target = tmp_path / name
    _init_main(target)
    (target / "keepme.txt").write_text("a file main no longer tracks\n")
    commit_all(target)
    return target


def write_patterns(path: Path, *patterns: str) -> Path:
    path.write_text("\n".join(patterns) + "\n" if patterns else "")
    return path


@pytest.fixture
def patterns_file(tmp_path) -> Path:
    """A fake private pattern, standing in for a real one kept outside this repo."""
    return write_patterns(tmp_path / "private-patterns.txt", PRIVATE_PATTERN)


def run_script(source: Path, target: Path, *args: str, patterns_file: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "KIT_PRIVATE_PATTERNS": str(patterns_file)}
    return subprocess.run(
        ["bash", str(source / "scripts" / SCRIPT.name), str(target), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def commit_count(repo: Path) -> int:
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
    return len(log.splitlines())


def test_refuses_on_a_private_pattern(tmp_path, patterns_file):
    source = fake_source(tmp_path, {"docs/notes.md": f"a note about the {PRIVATE_PATTERN} migration\n"})
    target = fake_target(tmp_path)

    result = run_script(source, target, patterns_file=patterns_file)

    assert result.returncode != 0, result.stdout
    assert "private pattern" in result.stderr
    assert commit_count(target) == 1


def test_refuses_on_a_likely_secret(tmp_path, patterns_file):
    source = fake_source(tmp_path, {"src/config.py": LEAKED_CONFIG})
    target = fake_target(tmp_path)

    result = run_script(source, target, patterns_file=patterns_file)

    assert result.returncode != 0, result.stdout
    assert "secret scan" in result.stderr
    assert "src/config.py" in result.stderr
    assert commit_count(target) == 1


def test_refuses_without_a_patterns_file(tmp_path):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    target = fake_target(tmp_path)
    missing = tmp_path / "nowhere" / "private-patterns.txt"

    result = run_script(source, target, patterns_file=missing)

    assert result.returncode != 0, result.stdout
    assert str(missing) in result.stderr
    assert "create it" in result.stderr
    assert commit_count(target) == 1


def test_refuses_on_an_empty_patterns_file(tmp_path):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    target = fake_target(tmp_path)
    empty = write_patterns(tmp_path / "empty-patterns.txt")

    result = run_script(source, target, patterns_file=empty)

    assert result.returncode != 0, result.stdout
    assert str(empty) in result.stderr
    assert "create it" in result.stderr


def test_falls_back_to_xdg_config_home_when_the_env_override_is_unset(tmp_path):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    target = fake_target(tmp_path)
    xdg = tmp_path / "xdg"
    patterns_dir = xdg / "claude-kit"
    patterns_dir.mkdir(parents=True)
    write_patterns(patterns_dir / "private-patterns.txt", PRIVATE_PATTERN)
    # XDG_CONFIG_HOME is also where git keeps its global identity on this machine; override it for the commit
    # author too, so the test doesn't depend on the real one still being reachable.
    identity = {"GIT_AUTHOR_NAME": "kit", "GIT_AUTHOR_EMAIL": "kit@example.invalid"}
    identity |= {"GIT_COMMITTER_NAME": "kit", "GIT_COMMITTER_EMAIL": "kit@example.invalid"}
    env = {k: v for k, v in os.environ.items() if k != "KIT_PRIVATE_PATTERNS"} | {"XDG_CONFIG_HOME": str(xdg)}
    env |= identity

    result = subprocess.run(
        ["bash", str(source / "scripts" / SCRIPT.name), str(target)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr


def test_exports_mains_tracked_tree_as_one_commit(tmp_path, patterns_file):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    target = fake_target(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "main"], cwd=source, capture_output=True, text=True, check=True
    ).stdout.strip()

    result = run_script(source, target, patterns_file=patterns_file)

    assert result.returncode == 0, result.stderr
    assert f"git -C {target} push" in result.stdout
    assert not (target / "keepme.txt").exists()
    assert (target / "README.md").read_text() == "# kit\n"
    log = subprocess.run(["git", "log", "--oneline"], cwd=target, capture_output=True, text=True).stdout
    assert log.splitlines()[0] == f"{log.splitlines()[0].split()[0]} chore: sync from private kit {sha}"
    assert len(log.splitlines()) == 2


def test_a_second_run_with_nothing_changed_makes_no_commit(tmp_path, patterns_file):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    target = fake_target(tmp_path)
    run_script(source, target, patterns_file=patterns_file)
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=target, capture_output=True, text=True).stdout

    result = run_script(source, target, patterns_file=patterns_file)

    assert result.returncode == 0, result.stderr
    assert "nothing changed" in result.stderr
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=target, capture_output=True, text=True).stdout
    assert after == before


def test_refuses_when_the_target_is_not_a_git_checkout(tmp_path, patterns_file):
    source = fake_source(tmp_path)

    result = run_script(source, tmp_path / "not-a-repo", patterns_file=patterns_file)

    assert result.returncode != 0
    assert "isn't a git checkout" in result.stderr


def test_exports_the_given_ref_rather_than_the_checked_out_branch(tmp_path, patterns_file):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    subprocess.run(["git", "checkout", "-qb", "release"], cwd=source, check=True)
    (source / "README.md").write_text("# kit, released\n")
    commit_all(source)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=source, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "release"], cwd=source, capture_output=True, text=True, check=True
    ).stdout.strip()
    target = fake_target(tmp_path)

    result = run_script(source, target, "release", patterns_file=patterns_file)

    assert result.returncode == 0, result.stderr
    assert (target / "README.md").read_text() == "# kit, released\n"
    assert f"chore: sync from private kit {sha}" in subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=target, capture_output=True, text=True
    ).stdout


def test_a_private_pattern_only_on_another_ref_does_not_block_the_given_ref(tmp_path, patterns_file):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    subprocess.run(["git", "checkout", "-qb", "messy"], cwd=source, check=True)
    (source / "notes.md").write_text(f"a note about the {PRIVATE_PATTERN} migration\n")
    commit_all(source)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=source, check=True)
    target = fake_target(tmp_path)

    result = run_script(source, target, patterns_file=patterns_file)

    assert result.returncode == 0, result.stderr


def test_makes_an_initial_release_commit_when_the_target_has_no_commits_yet(tmp_path, patterns_file):
    source = fake_source(tmp_path, {"README.md": "# kit\n"})
    target = tmp_path / "pub"
    _init_main(target)

    result = run_script(source, target, patterns_file=patterns_file)

    assert result.returncode == 0, result.stderr
    log = subprocess.run(["git", "log", "--oneline"], cwd=target, capture_output=True, text=True).stdout
    assert log.splitlines()[0].split(maxsplit=1)[1] == "feat: initial public release"
    assert len(log.splitlines()) == 1
