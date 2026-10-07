"""scan_secrets and guard_commit, run as subprocesses on stdin JSON against temp git repos.

Credential-shaped values are assembled at runtime from fragments, so this file holds none.
"""

import base64
import functools
import json
import os
import random
import re
import shutil
import string
import subprocess
from pathlib import Path

import pytest
from conftest import fake_exe, run_hook

ALNUM = string.ascii_letters + string.digits
NOT_RESOLVED = "{name} isn't installed, or only as a mise shim, which can't start in the tests' stripped PATH"
URLSAFE = ALNUM + "-_"
HERMETIC_GIT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
IDENTITY = {
    "GIT_AUTHOR_NAME": "kit",
    "GIT_AUTHOR_EMAIL": "kit@example.invalid",
    "GIT_COMMITTER_NAME": "kit",
    "GIT_COMMITTER_EMAIL": "kit@example.invalid",
}
CONVENTIONAL_HINT = "must follow Conventional Commits"
ATTRIBUTION_HINT = "carries AI attribution"
UNKNOWN_MESSAGE_HINT = "only built when the command runs"


def _random(length: int, alphabet: str = ALNUM, seed: str = "") -> str:
    rng = random.Random(f"{seed}:{length}:{alphabet}")
    return "".join(rng.choice(alphabet) for _ in range(length))


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def _private_key() -> str:
    body = "\n".join(_random(64, ALNUM + "+/", seed=str(n)) for n in range(6))
    marker = "RSA " + "PRIVATE KEY"
    return f"-----BEGIN {marker}-----\n{body}\n-----END {marker}-----"


CREDENTIALS = {
    "aws-access-key-id": lambda: "AK" + "IA" + _random(16, string.ascii_uppercase + "234567"),
    "private-key": _private_key,
    "github-token": lambda: "gh" + "p_" + _random(36),
    "slack-token": lambda: (
        "xo" + "xb-" + _random(12, string.digits) + "-" + _random(12, string.digits, "b") + "-" + _random(24)
    ),
    "stripe-live-key": lambda: "sk" + "_live_" + _random(24),
    "google-api-key": lambda: "AI" + "za" + _random(35, URLSAFE),
    "openai-api-key": lambda: "sk-" + "proj-" + _random(64, URLSAFE),
    "anthropic-api-key": lambda: "sk-" + "ant-api03-" + _random(93, URLSAFE) + "AA",
    "jwt": lambda: ".".join((_b64({"alg": "HS256", "typ": "JWT"}), _b64({"sub": "42", "name": "kit"}), _random(43))),
}
# Formats every supported scanner flags as well as the built-in patterns.
SCANNED_BY_ALL = ["private-key", "github-token", "slack-token", "stripe-live-key", "google-api-key", "jwt"]
PASSWORD = "S3cure!" + "Pass-2026-prod"


def bearer_curl(token: str) -> str:
    return f'curl -H "Authorization: Bearer {token}" https://api.example.invalid/v1/items'


def embed(secret: str) -> str:
    return f"{secret}\n" if secret.startswith("-----") else f'TOKEN = "{secret}"\n'


@functools.cache
def working_scanner(name: str) -> str | None:
    """The scanner's real binary. A mise shim re-executes mise, which can't start in tool_env's stripped PATH."""
    candidates = [shutil.which(name)]
    if shutil.which("mise"):
        which = subprocess.run(["mise", "which", name], capture_output=True, text=True)
        candidates.append(which.stdout.strip() if which.returncode == 0 else None)
    for candidate in filter(None, candidates):
        if _is_mise_shim(candidate):
            continue
        if subprocess.run([candidate, "version"], capture_output=True).returncode == 0:
            return candidate
    return None


def _is_mise_shim(path: str) -> bool:
    return Path(path).resolve().name == "mise"


def tool_env(tmp_path: Path, tools: dict[str, str | Path]) -> dict[str, str]:
    """PATH with git and only the given tools, and no mise shims, so the hooks see exactly these scanners."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, target in {"git": shutil.which("git"), **tools}.items():
        (bin_dir / name).symlink_to(target)
    return {**HERMETIC_GIT, "PATH": str(bin_dir), "MISE_DATA_DIR": str(tmp_path / "no-mise")}


@pytest.fixture(autouse=True)
def _no_bypass(monkeypatch):
    monkeypatch.delenv("KIT_BYPASS", raising=False)


@pytest.fixture(params=["patterns", "betterleaks", "gitleaks"])
def scanner_env(request, tmp_path) -> dict[str, str]:
    if request.param == "patterns":
        return tool_env(tmp_path, {})
    binary = working_scanner(request.param)
    if binary is None:
        pytest.skip(NOT_RESOLVED.format(name=request.param))
    return tool_env(tmp_path, {request.param: binary})


@pytest.fixture
def patterns_env(tmp_path) -> dict[str, str]:
    return tool_env(tmp_path, {})


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, **HERMETIC_GIT, **IDENTITY}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content)


def stage(repo: Path, files: dict[str, str]) -> None:
    write(repo, files)
    git(repo, "add", "--", *files)


def ignored(repo: Path, rel: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo).returncode == 0


@pytest.fixture
def repo(make_repo) -> Path:
    path = make_repo(files={"README.md": "# demo\n", "src/app.py": "def run():\n    return 1\n"})
    git(path, "add", ".")
    git(path, "commit", "-qm", "chore: init")
    return path


def payload(cwd: Path, tool: str, **tool_input) -> dict:
    return {
        "session_id": "s1",
        "transcript_path": "/dev/null",
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
    }


def decision(result) -> tuple[str, str] | None:
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    output = json.loads(result.stdout)["hookSpecificOutput"]
    return output["permissionDecision"], output["permissionDecisionReason"]


def rules_in(reason: str) -> set[str]:
    return set(re.search(r"\(rule: ([^)]*)\)", reason).group(1).split(", "))


def scan(cwd: Path, env: dict, tool: str, **tool_input):
    return run_hook("scan_secrets", payload(cwd, tool, **tool_input), env=env)


def scan_write(repo: Path, env: dict, rel: str, content: str):
    return decision(scan(repo, env, "Write", file_path=str(repo / rel), content=content))


def guard(cwd: Path, command: str, env: dict):
    return run_hook("guard_commit", payload(cwd, "Bash", command=command), env=env)


def assert_not_echoed(result, secret: str) -> None:
    core = secret.strip().splitlines()[1] if secret.startswith("-----") else secret
    assert core not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", SCANNED_BY_ALL)
def test_write_with_a_credential_is_denied_without_echoing_it(repo, scanner_env, kind):
    secret = CREDENTIALS[kind]()
    result = scan(repo, scanner_env, "Write", file_path=str(repo / "app" / "settings.py"), content=embed(secret))
    verdict, reason = decision(result)
    assert verdict == "deny"
    assert "app/settings.py" in reason
    assert '"$API_TOKEN"' in reason and "environment variable" in reason
    assert "untracked, git-ignored .env" in reason
    assert_not_echoed(result, secret)


def test_edit_new_string_is_scanned(repo, scanner_env):
    secret = CREDENTIALS["stripe-live-key"]()
    result = scan(
        repo, scanner_env, "Edit", file_path=str(repo / "src" / "app.py"), old_string="1", new_string=embed(secret)
    )
    verdict, reason = decision(result)
    assert verdict == "deny"
    assert "src/app.py" in reason
    assert_not_echoed(result, secret)


def test_a_key_split_across_two_edits_is_seen_whole(repo, scanner_env):
    token = CREDENTIALS["github-token"]()
    write(repo, {"src/config.py": f'TOKEN = "{token[:20]}"\n'})
    result = scan(
        repo,
        scanner_env,
        "Edit",
        file_path=str(repo / "src/config.py"),
        old_string='"\n',
        new_string=f'{token[20:]}"\n',
    )
    assert decision(result)[0] == "deny"
    assert_not_echoed(result, token)


def test_an_edit_away_from_an_old_secret_passes(repo, scanner_env):
    write(repo, {"src/config.py": embed(CREDENTIALS["github-token"]()) + "\n\ndef run():\n    return 1\n"})
    result = scan(
        repo, scanner_env, "Edit", file_path=str(repo / "src/config.py"), old_string="return 1", new_string="return 2"
    )
    assert decision(result) is None


def test_notebook_edit_source_is_scanned(repo, patterns_env):
    source = embed(CREDENTIALS["github-token"]())
    result = scan(repo, patterns_env, "NotebookEdit", notebook_path=str(repo / "nb.ipynb"), new_source=source)
    assert decision(result)[0] == "deny"


def test_shell_command_with_a_literal_token_is_denied(repo, scanner_env):
    token = _random(40, seed="bearer")
    result = scan(repo, scanner_env, "Bash", command=bearer_curl(token))
    verdict, reason = decision(result)
    assert verdict == "deny"
    assert "shell command" in reason
    assert_not_echoed(result, token)


def test_env_var_references_and_placeholders_are_allowed(repo, scanner_env):
    for command in (bearer_curl("$API_TOKEN"), bearer_curl("${API_TOKEN}"), 'export API_KEY="$API_KEY"'):
        assert decision(scan(repo, scanner_env, "Bash", command=command)) is None, command
    placeholders = "\n".join(
        (
            "API_KEY=changeme",
            'api_key = "your-key-here"',
            'stripe = "' + "sk" + "_live_" + "x" * 24 + '"',
            "AWS_ACCESS_KEY_ID=" + "AK" + "IA" + "X" * 16,
            'token = "<your-token>"',
            'token = "${API_TOKEN}"',
            "DB_PASSWORD=changeme",
            "DATABASE_URL=postgres://postgres:postgres@db:5432/app",
            "Authorization: Bearer $API_TOKEN",
            "Authorization: Bearer <token>",
        )
    )
    assert scan_write(repo, scanner_env, ".env.example", placeholders) is None


def test_a_placeholder_word_does_not_hide_a_real_key(repo, patterns_env):
    key = "sk-" + "proj-" + "example" + _random(40, seed="camouflage")
    verdict, reason = scan_write(repo, patterns_env, "src/config.py", f'KEY = "{key}"\n')
    assert verdict == "deny"
    assert "openai-api-key" in rules_in(reason)


def test_node_modules_and_git_paths_pass(repo, scanner_env):
    content = embed(CREDENTIALS["github-token"]())
    for rel in ("node_modules/pkg/index.js", ".git/config"):
        assert scan_write(repo, scanner_env, rel, content) is None, rel


@pytest.mark.parametrize("kind", [*CREDENTIALS, "authorization-bearer"])
def test_pattern_fallback_covers_each_credential_format(repo, patterns_env, kind):
    text = bearer_curl(_random(40, seed="bearer")) if kind == "authorization-bearer" else embed(CREDENTIALS[kind]())
    verdict, reason = scan_write(repo, patterns_env, "notes.txt", text)
    assert verdict == "deny"
    assert kind in rules_in(reason)


GENERIC_SECRETS = {
    "postgres url": ("URL = 'postgres://admin:" + PASSWORD + "@db.internal:5432/app'", "generic-database-url"),
    "mysql url": ("mysql://app:" + PASSWORD + "@10.0.0.5/shop", "generic-database-url"),
    "redis url": ("REDIS_URL=redis://:" + PASSWORD + "@cache:6379/0", "generic-database-url"),
    "mongodb url": ("mongodb+srv://app:" + PASSWORD + "@cluster0.example.net/db", "generic-database-url"),
    "amqp url": ("BROKER = 'amqp://worker:" + PASSWORD + "@mq:5672//'", "generic-database-url"),
    "aws secret key": ('aws_secret_access_key = "' + _random(40, ALNUM + "/+", "aws") + '"', "aws-secret-access-key"),
    "api key": ('api_key = "' + _random(32, "0123456789abcdef", "hex") + '"', "generic-secret-assignment"),
    "client secret": (
        'clientSecret: "' + _random(28, string.ascii_lowercase + string.digits, "cs") + '"',
        "generic-secret-assignment",
    ),
    "password": ('DB_PASSWORD = "' + PASSWORD + '"', "generic-password-assignment"),
    "env line": ("export DB_PASSWORD=" + PASSWORD, "generic-password-assignment"),
    "yaml": ("database:\n  password: " + PASSWORD + "\n", "generic-password-assignment"),
}


@pytest.mark.parametrize("case", GENERIC_SECRETS)
def test_high_confidence_generic_secrets_are_denied(repo, patterns_env, case):
    text, rule = GENERIC_SECRETS[case]
    verdict, reason = scan_write(repo, patterns_env, "app/settings.py", text)
    assert verdict == "deny"
    assert rule in rules_in(reason)
    assert PASSWORD not in reason


ORDINARY_CODE = """\
token_url = "https://oauth2.googleapis.com/token"
CACHE_KEY = "user_profile_cache_v2"
sortKey = "createdAt"
pwd = os.getcwd()
password_reset_template = "emails/reset.html"
password = form.cleaned_data["password"]
connect(password=get_password(), token=load_token())
PASSWORD_LABEL = "Enter your password"
PASSWORD_MIN_LENGTH=8
password = "${DB_PASSWORD}"
api_key = os.environ["API_KEY"]
aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
commit = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
PWD=/home/kit/src/app2024
CREDIT_LIMIT = "limit_2024_q3_customer_tier9"
"""


def test_generic_rules_leave_ordinary_code_alone(repo, patterns_env):
    assert scan_write(repo, patterns_env, "app/settings.py", ORDINARY_CODE) is None


TYPE_DECLARATIONS = """\
export interface SignupBody {
  email: string;
  password: string;
  confirmPassword: String;
}

type LoginForm = {
  email: string
  password: string
}
"""


def test_type_declarations_of_password_fields_pass(repo, scanner_env):
    assert scan_write(repo, scanner_env, "web/src/api/types.ts", TYPE_DECLARATIONS) is None


def test_builtin_patterns_run_alongside_the_scanner(repo, scanner_env):
    verdict, reason = scan_write(repo, scanner_env, "app/settings.py", f'DB_PASSWORD = "{PASSWORD}"\n')
    assert verdict == "deny"
    assert "generic-password-assignment" in rules_in(reason)


@pytest.mark.parametrize("encoding", ["base64", "hex"])
def test_encoded_provider_keys_are_decoded(repo, patterns_env, encoding):
    raw = CREDENTIALS["github-token"]().encode()
    blob = base64.b64encode(raw).decode() if encoding == "base64" else raw.hex()
    result = scan(repo, patterns_env, "Write", file_path=str(repo / "src/k.py"), content=f'K = decode("{blob}")\n')
    verdict, reason = decision(result)
    assert verdict == "deny"
    assert "github-token-encoded" in rules_in(reason)
    assert blob not in result.stdout


def test_test_fixtures_may_hold_generic_secrets_but_not_provider_keys(repo, scanner_env):
    fixture = '{"password": "correct-horse-battery", "token": "' + CREDENTIALS["jwt"]() + '"}\n'
    for rel in ("tests/test_login.py", "web/login.test.ts", "tests/fixtures/users.json"):
        assert scan_write(repo, scanner_env, rel, f"USER = {fixture}") is None, rel
    verdict, _ = scan_write(repo, scanner_env, "tests/test_login.py", embed(CREDENTIALS["github-token"]()))
    assert verdict == "deny"


def test_an_ignored_env_file_may_hold_secrets_but_a_tracked_file_may_not(repo, patterns_env):
    content = "GITHUB_TOKEN=" + CREDENTIALS["github-token"]() + "\n"
    if ignored(repo, ".env"):
        pytest.skip("this machine's git excludes already ignore .env")
    assert scan_write(repo, patterns_env, ".env", content)[0] == "deny"
    write(repo, {".gitignore": ".env*\n!.env.example\n"})
    assert scan_write(repo, patterns_env, ".env", content) is None
    assert scan_write(repo, patterns_env, "api/.env.local", content) is None
    assert scan_write(repo, patterns_env, ".env.example", content)[0] == "deny"


@pytest.mark.parametrize("scanner", ["betterleaks", "gitleaks"])
def test_an_installed_scanner_does_the_scanning(repo, tmp_path, scanner):
    """The scanners name the GitHub rule github-pat, the built-in patterns github-token."""
    binary = working_scanner(scanner) or pytest.skip(NOT_RESOLVED.format(name=scanner))
    env = tool_env(tmp_path, {scanner: binary})
    content = embed(CREDENTIALS["github-token"]())
    _, reason = scan_write(repo, env, "src/config.py", content)
    assert "github-pat" in rules_in(reason)
    stage(repo, {"src/config.py": content})
    _, reason = decision(guard(repo, 'git commit -m "feat: add config"', env))
    assert "github-pat in src/config.py" in reason


def test_a_scanner_that_cannot_run_falls_back_to_patterns(repo, tmp_path):
    """A mise shim with no version set exits 1, like a leak, but prints no report."""
    shims = {name: fake_exe(tmp_path / "broken" / name, exit_code=1) for name in ("betterleaks", "gitleaks")}
    result = scan(repo, tool_env(tmp_path, shims), "Bash", command=f"echo {CREDENTIALS['github-token']()}")
    verdict, reason = decision(result)
    assert verdict == "deny"
    assert rules_in(reason) == {"github-token"}


def test_bad_input_fails_closed(patterns_env):
    for script in ("scan_secrets", "guard_commit"):
        result = run_hook(script, stdin="{not json", env=patterns_env)
        assert result.returncode == 2
        assert f"kit: {script} hook failed (JSONDecodeError" in result.stderr


FORBIDDEN_PATHS = [
    ".env",
    "api/.env.local",
    ".claude/settings.local.json",
    "web/.claude/settings.local.json",
    ".claude/worktrees/feature/app.py",
    "certs/server.pem",
    "deploy/id_rsa",
    "e2e/.auth/user.json",
    "playwright/storageState.admin.json",
    ".playwright-mcp/page-snapshot.yml",
]


def test_forbidden_path_patterns():
    import commitlib

    for path in FORBIDDEN_PATHS:
        assert commitlib.is_forbidden(path), path
    for path in (".env.example", "api/.env.example", ".envrc", "docs/env.md", "src/app.py", ".claude/settings.json"):
        assert not commitlib.is_forbidden(path), path


@pytest.mark.parametrize("path", FORBIDDEN_PATHS)
def test_forbidden_staged_path_is_denied(repo, patterns_env, path):
    write(repo, {path: "value\n"})
    if ignored(repo, path):
        pytest.skip(f"this machine's git excludes ignore {path}; test_forbidden_path_patterns covers it")
    stage(repo, {path: "value\n"})
    verdict, reason = decision(guard(repo, 'git commit -m "chore: add config"', patterns_env))
    assert verdict == "deny"
    assert path in reason


def test_env_example_is_allowed(repo, patterns_env):
    stage(repo, {".env.example": "API_TOKEN=changeme\n", "api/.env.example": "DB_URL=\n"})
    assert decision(guard(repo, 'git commit -m "docs: document the env vars"', patterns_env)) is None


def test_git_add_in_the_chain_is_checked_without_touching_the_index(repo, patterns_env):
    write(repo, {".env": "DEBUG=1\n"})
    objects_before = git(repo, "count-objects")
    verdict, reason = decision(guard(repo, 'git add .env src && git commit -m "chore: config"', patterns_env))
    assert verdict == "deny"
    assert ".env" in reason
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert git(repo, "count-objects") == objects_before


def test_commit_all_includes_tracked_modifications(repo, patterns_env):
    stage(repo, {".env": "DEBUG=1\n"})
    git(repo, "commit", "-qm", "chore: tracked env")
    write(repo, {".env": "DEBUG=0\n"})
    assert decision(guard(repo, 'git commit -m "chore: tweak"', patterns_env)) is None
    for command in ('git commit -am "chore: tweak"', 'git commit --all -m "chore: tweak"'):
        verdict, reason = decision(guard(repo, command, patterns_env))
        assert verdict == "deny", command
        assert ".env" in reason


def test_a_racily_clean_edit_is_still_seen(repo, patterns_env):
    """A same-size edit in the index's own timestamp tick: git spots it only by comparing against the index mtime."""
    git(repo, "config", "core.trustctime", "false")  # utime can't rewind ctime, so take ctime out of the stat check.
    stage(repo, {".env": "DEBUG=1\n"})
    git(repo, "commit", "-qm", "chore: tracked env")
    tick = (repo / ".env").stat()
    write(repo, {".env": "DEBUG=0\n"})
    for path in (repo / ".env", repo / ".git" / "index"):
        os.utime(path, ns=(tick.st_atime_ns, tick.st_mtime_ns))
    verdict, _ = decision(guard(repo, 'git commit -am "chore: tweak"', patterns_env))
    assert verdict == "deny"


def test_git_dash_c_and_cd_resolve_the_repo(repo, patterns_env, tmp_path):
    stage(repo, {".env": "DEBUG=1\n"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for command in (f'git -C {repo} commit -m "chore: x"', f'cd {repo} && git commit -m "chore: x"'):
        verdict, reason = decision(guard(elsewhere, command, patterns_env))
        assert verdict == "deny", command
        assert ".env" in reason
    assert decision(guard(elsewhere, 'git commit -m "chore: x"', patterns_env)) is None


GIT_COMMIT_FORMS = [
    'git -c core.hooksPath=/dev/null commit -m "updated stuff"',
    '/usr/bin/git commit -m "updated stuff"',
    'env GIT_AUTHOR_NAME=x git commit -m "updated stuff"',
    'command git commit -m "updated stuff"',
    '\\git commit -m "updated stuff"',
    'git --no-pager commit -m "updated stuff"',
    'git -C {repo} commit -m "updated stuff"',
    'sudo -u "$USER" git commit -m "updated stuff"',
    'nice -n 5 git commit -m "updated stuff"',
    "bash -c 'git commit -m \"updated stuff\"'",
    "sh -lc 'git add . && git commit -m \"updated stuff\"'",
    "eval 'git commit -m \"updated stuff\"'",
    'result=$(git commit -m "updated stuff")',
]


@pytest.mark.parametrize("form", GIT_COMMIT_FORMS)
def test_a_commit_is_found_however_git_is_invoked(repo, patterns_env, form):
    stage(repo, {"docs/notes.md": "notes\n"})
    verdict, reason = decision(guard(repo, form.format(repo=repo), patterns_env))
    assert verdict == "deny"
    assert CONVENTIONAL_HINT in reason


def test_a_command_without_a_commit_never_runs_git(repo, tmp_path):
    """guard_commit sees every Bash call; one without a commit must not touch git (here git isn't even on PATH)."""
    no_git = {**HERMETIC_GIT, "PATH": str(tmp_path / "empty"), "MISE_DATA_DIR": str(tmp_path / "no-mise")}
    for command in ("ls -la && npm test", "git status && git diff", 'echo "git commit -m bad"'):
        result = guard(repo, command, no_git)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), command
    assert guard(repo, 'git commit -m "feat: x"', no_git).returncode == 2


def test_staged_secret_is_denied_without_echoing_it(repo, scanner_env):
    secret = CREDENTIALS["github-token"]()
    stage(repo, {"src/config.py": embed(secret)})
    result = guard(repo, 'git commit -m "feat: add config"', scanner_env)
    verdict, reason = decision(result)
    assert verdict == "deny"
    assert "src/config.py" in reason and "untracked, git-ignored .env" in reason
    assert_not_echoed(result, secret)


def test_secret_added_in_the_chain_is_found(repo, scanner_env):
    write(repo, {"src/config.py": embed(CREDENTIALS["stripe-live-key"]())})
    verdict, _ = decision(guard(repo, 'git add src/config.py && git commit -m "feat: add config"', scanner_env))
    assert verdict == "deny"


def test_staged_generic_secret_in_a_test_fixture_passes(repo, scanner_env):
    stage(
        repo, {"tests/test_login.py": 'USER = {"password": "correct-horse-battery"}\n\ndef test_login():\n    pass\n'}
    )
    assert decision(guard(repo, 'git commit -m "test: cover login"', scanner_env)) is None


def heredoc_m(message: str) -> str:
    return f"git commit -m \"$(cat <<'EOF'\n{message}\nEOF\n)\""


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "feat: add export"',
        "git commit -m 'feat(api)!: drop v1 routes' -m 'BREAKING CHANGE: v1 is gone.'",
        heredoc_m('feat(api): add export\n\nThe body can say don\'t, "quote" and $HOME.'),
        "git commit -F - <<'EOF'\ndocs: explain the export\nEOF",
        "cat > /tmp/kit-msg.txt <<'EOF'\ndocs: explain the export\nEOF\ngit commit -F /tmp/kit-msg.txt",
        "git commit --amend --no-edit",
        "git commit -C HEAD",
    ],
)
def test_acceptable_or_reused_messages_pass(repo, patterns_env, command):
    stage(repo, {"docs/export.md": "# Export\n"})
    assert decision(guard(repo, command, patterns_env)) is None


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "$MSG"',
        'git commit -m "updated stuff $(true)"',
        'git commit -m "feat: built at `date`"',
        heredoc_m("feat: add $FEATURE").replace("<<'EOF'", "<<EOF"),
        'printf "feat: x" | git commit -F -',
        'git commit -F "$MESSAGE_FILE"',
    ],
)
def test_a_message_built_at_run_time_asks(repo, patterns_env, command):
    stage(repo, {"docs/export.md": "# Export\n"})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "ask"
    assert UNKNOWN_MESSAGE_HINT in reason


@pytest.mark.parametrize(
    ("command", "hint"),
    [
        ('git commit -m "Add export"', CONVENTIONAL_HINT),
        ('git commit -m "feature: add export"', CONVENTIONAL_HINT),
        ('git commit -m "feat: ' + "x" * 70 + '"', CONVENTIONAL_HINT),
        (heredoc_m("update the export"), CONVENTIONAL_HINT),
        ("git commit -F - <<'EOF'\nupdate the export\nEOF", CONVENTIONAL_HINT),
        (heredoc_m("feat: add export\n\nCo-Authored-By: Claude <noreply@anthropic.com>"), ATTRIBUTION_HINT),
        ('git commit -m "feat: add export" -m "Generated with [Claude Code](https://claude.com)"', ATTRIBUTION_HINT),
        ('git commit -m "feat: add export" --trailer "Co-authored-by: Bot <bot@example.invalid>"', ATTRIBUTION_HINT),
        ('git commit -m "$SUBJECT" -m "Co-Authored-By: Claude <noreply@anthropic.com>"', ATTRIBUTION_HINT),
    ],
)
def test_bad_messages_are_denied(repo, patterns_env, command, hint):
    stage(repo, {"docs/export.md": "# Export\n"})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "deny"
    assert hint in reason
    if hint == CONVENTIONAL_HINT:
        assert "Example: fix(auth): reject expired refresh tokens" in reason


@pytest.mark.parametrize(
    "command",
    [
        'git -C "$target" commit -m "update stuff"',
        'git -C "$target" add -A && git -C "$target" commit -q -m "update stuff"',
    ],
)
def test_a_commit_in_a_run_time_directory_with_a_bad_literal_message_is_still_denied(repo, patterns_env, command):
    """A -C value only known at run time must not crash the guard (it used to: it joined the unexpanded text
    onto a real path and later code stat'd the bogus result), and a literal message is still checked."""
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "deny"
    assert CONVENTIONAL_HINT in reason


def test_a_commit_in_a_run_time_directory_with_attribution_is_still_denied(repo, patterns_env):
    verdict, reason = decision(
        guard(repo, 'git -C "$target" commit -m "feat: add export" -m "Co-Authored-By: Claude <noreply@x>"', patterns_env)
    )
    assert verdict == "deny"
    assert ATTRIBUTION_HINT in reason


def test_a_commit_in_a_run_time_directory_with_a_good_literal_message_asks(repo, patterns_env):
    verdict, reason = decision(guard(repo, 'git -C "$target" commit -m "feat: add export"', patterns_env))
    assert verdict == "ask"
    assert "directory only known at run time" in reason


def test_a_commit_in_a_run_time_directory_with_an_unresolved_message_asks(repo, patterns_env):
    verdict, reason = decision(guard(repo, 'git -C "$target" commit -m "$message"', patterns_env))
    assert verdict == "ask"
    assert UNKNOWN_MESSAGE_HINT in reason
    assert "directory only known at run time" in reason


def test_a_commit_in_a_run_time_directory_with_a_literal_dash_f_file_asks(repo, patterns_env):
    """-F <literal file> would resolve that path against the unresolvable directory (None / "...") without
    the same guard _message_file has for a non-literal word; it needs its own."""
    verdict, reason = decision(guard(repo, 'git -C "$target" commit -F message.txt', patterns_env))
    assert verdict == "ask"
    assert UNKNOWN_MESSAGE_HINT in reason
    assert "directory only known at run time" in reason


FIX = {"src/app.py": "def run():\n    return 2\n"}
REAL_TESTS = {
    "tests/test_app.py": "from app import run\n\n\ndef test_run():\n    assert run() == 2\n",
    "src/test_app.py": "async def test_run_async():\n    assert True\n",
    "src/app_test.py": "def test_run():\n    assert True\n",
    "web/app.test.tsx": "it('runs', () => {\n  expect(run()).toBe(2);\n});\n",
    "web/app.spec.ts": "describe('run', () => {});\n",
    "web/app.each.test.ts": "test.each([[1, 2]])('adds %i', (a, b) => {});\n",
    "api/items.hurl": "GET http://localhost:8000/items\nHTTP 200\n",
    "tests/matrix/cases.yaml": "- name: empty input\n  expect: 2\n",
}


def test_fix_without_a_test_is_denied(repo, patterns_env):
    stage(repo, {**FIX, "CHANGELOG.md": "- fix\n"})
    verdict, reason = decision(guard(repo, 'git commit -m "fix: return the right value"', patterns_env))
    assert verdict == "deny"
    assert "needs a test" in reason and "src/app.py" in reason
    assert "CHANGELOG.md" not in reason


@pytest.mark.parametrize("test_file", REAL_TESTS)
def test_fix_with_a_test_is_allowed(repo, patterns_env, test_file):
    stage(repo, {**FIX, test_file: REAL_TESTS[test_file]})
    assert decision(guard(repo, 'git commit -m "fix(app): return the right value"', patterns_env)) is None


NO_TEST_CASE = {"tests/test_app.py": "", "tests/test_other.py": "import app\n", "web/app.test.ts": "import './app';\n"}


@pytest.mark.parametrize("test_file", NO_TEST_CASE)
def test_fix_with_a_test_file_but_no_test_case_is_denied(repo, patterns_env, test_file):
    stage(repo, {**FIX, test_file: NO_TEST_CASE[test_file]})
    verdict, reason = decision(guard(repo, 'git commit -m "fix: return the right value"', patterns_env))
    assert verdict == "deny"
    assert "add no test case" in reason


def test_fix_touching_no_source_needs_no_test(repo, patterns_env):
    stage(repo, {"README.md": "# demo, fixed\n", "config/app.yaml": "a: 1\n", "app/migrations/0002_fix.py": "x = 1\n"})
    assert decision(guard(repo, 'git commit -m "fix: correct the typo"', patterns_env)) is None


def test_fix_staged_by_the_chain_is_checked(repo, patterns_env):
    write(repo, {"src/app.py": "def run():\n    return 3\n"})
    verdict, _ = decision(guard(repo, 'git commit -am "fix: return three"', patterns_env))
    assert verdict == "deny"


def test_oversized_commit_asks(repo, patterns_env):
    stage(repo, {"src/big.py": "".join(f"VALUE_{n} = {n}\n" for n in range(700))})
    verdict, reason = decision(guard(repo, 'git commit -m "feat: add the values"', patterns_env))
    assert verdict == "ask"
    assert "700 lines" in reason
    assert reason.endswith("Split into one concern per commit, or confirm this size is intended.")


def test_generated_files_do_not_count_toward_size(repo, patterns_env):
    bulk = "".join(f"line {n}\n" for n in range(5000))
    generated = ("uv.lock", "web/package-lock.json", "app/migrations/0003_bulk.py", "web/__snapshots__/a.snap")
    stage(repo, {**dict.fromkeys(generated, bulk), "docs/CONTRACTS.md": bulk, "openapi.v2.yaml": bulk})
    stage(repo, {"src/app.py": "def run():\n    return 4\n"})
    assert decision(guard(repo, 'git commit -m "build: refresh the lockfiles"', patterns_env)) is None


def lines(count: int, prefix: str) -> str:
    return "".join(f"{prefix}_{n} = {n}\n" for n in range(count))


def test_each_commit_in_a_chain_is_sized_on_its_own(repo, patterns_env):
    write(repo, {"src/a.py": lines(350, "A"), "src/b.py": lines(350, "B")})
    command = 'git add src/a.py && git commit -m "feat: add a" && git add src/b.py && git commit -m "feat: add b"'
    assert decision(guard(repo, command, patterns_env)) is None
    assert git(repo, "diff", "--cached", "--name-only") == ""


def test_each_commit_in_a_chain_is_checked(repo, patterns_env):
    write(repo, {"src/a.py": "A = 1\n", "docs/b.md": "b\n"})
    first = 'git add src/a.py && git commit -m "feat: add a" && git add docs/b.md && '
    verdict, reason = decision(guard(repo, first + 'git commit -m "more stuff"', patterns_env))
    assert (verdict, CONVENTIONAL_HINT in reason) == ("deny", True)
    write(repo, {".env": "DEBUG=1\n"})
    verdict, reason = decision(guard(repo, first + 'git add .env && git commit -m "docs: add b"', patterns_env))
    assert (verdict, ".env" in reason) == ("deny", True)


@pytest.mark.parametrize(
    "command",
    [
        "git show HEAD --stat",
        'echo "git commit -m bad"',
        "git log --grep 'git commit'",
        "git status && git diff --cached",
    ],
)
def test_commands_that_do_not_commit_are_ignored(repo, patterns_env, command):
    stage(repo, {".env": "DEBUG=1\n"})
    result = guard(repo, command, patterns_env)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


WIDER_SECRET_NAMES = [
    "CLIENT_SECRET_VALUE",
    "CREDENTIALS",
    "S3_CREDS",
    "ACCESS_TOKEN_VALUE",
    "SIGNING_KEY_B64",
    "GPG_PASSPHRASE",
    "AUTH_KEY_ID",
    "privateKeyPem",
]


@pytest.mark.parametrize("name", WIDER_SECRET_NAMES)
def test_secret_names_are_matched_on_any_secret_word(repo, patterns_env, name):
    value = _random(40, ALNUM + "/+", "wide")
    verdict, reason = scan_write(repo, patterns_env, "app/settings.py", f'{name} = "{value}"\n')
    assert verdict == "deny"
    assert "generic-secret-assignment" in rules_in(reason)


def test_a_fixtures_folder_outside_the_tests_is_not_relaxed(repo, patterns_env):
    content = f'DB_PASSWORD = "{PASSWORD}"\n'
    assert scan_write(repo, patterns_env, "app/fixtures/config.py", content)[0] == "deny"
    assert scan_write(repo, patterns_env, "app/tests/fixtures/config.py", content) is None


WRAPPED_COMMITS = [
    'timeout 60 git commit -m "updated stuff"',
    'timeout --signal=TERM -k 5 60 git commit -m "updated stuff"',
    'echo x | xargs -I{} git commit -m "updated stuff"',
    'xargs -n 1 git commit -m "updated stuff" < /dev/null',
    'stdbuf -o0 git commit -m "updated stuff"',
    'setsid -w git commit -m "updated stuff"',
    'flock /tmp/kit.lock git commit -m "updated stuff"',
    "flock -w 5 /tmp/kit.lock -c 'git commit -m \"updated stuff\"'",
    'ionice -c3 git commit -m "updated stuff"',
    'ionice -c 2 -n 7 git commit -m "updated stuff"',
    'nohup time git commit -m "updated stuff"',
    'chronic unbuffer git commit -m "updated stuff"',
    "script -qec 'git commit -m \"updated stuff\"' /dev/null",
    'watch -n 5 git commit -m "updated stuff"',
    'doas -u root git commit -m "updated stuff"',
    'sudo -u root -E git commit -m "updated stuff"',
    'timeout 60 nice -n 5 env A=1 git commit -m "updated stuff"',
    'shopt -s expand_aliases; alias gc="git commit"; gc -m "updated stuff"',
    'chrt -o 0 git commit -m "updated stuff"',
    'taskset -c 0 git commit -m "updated stuff"',
    "su -c 'git commit -m \"updated stuff\"' root",
    'coproc git commit -m "updated stuff"',
    'function c { git commit -m "updated stuff"; }; c',
    "env -S 'git commit -m \"updated stuff\"'",
    'command -p git commit -m "updated stuff"',
    'exec git commit -m "updated stuff"',
    'echo | timeout 5 xargs git commit -m "updated stuff"',
]


@pytest.mark.parametrize("command", WRAPPED_COMMITS)
def test_a_wrapper_does_not_hide_a_commit(repo, patterns_env, command):
    stage(repo, {"docs/notes.md": "notes\n"})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "deny"
    assert CONVENTIONAL_HINT in reason


def test_an_aliased_commit_with_a_good_message_passes(repo, patterns_env):
    stage(repo, {"docs/notes.md": "notes\n"})
    command = 'alias gc="git commit"; gc -m "docs: add the notes"'
    assert decision(guard(repo, command, patterns_env)) is None


def test_a_wrapper_cannot_smuggle_an_env_file_into_a_commit(repo, patterns_env):
    write(repo, {".env": "OPENAI_API_KEY=" + CREDENTIALS["openai-api-key"]() + "\n"})
    if ignored(repo, ".env"):
        pytest.skip("this machine's git excludes ignore .env")
    verdict, reason = decision(guard(repo, 'git add -A && timeout 60 git commit -m "chore: stuff"', patterns_env))
    assert verdict == "deny"
    assert ".env" in reason
    assert git(repo, "diff", "--cached", "--name-only") == ""


INDIRECT_GIT = [
    'G=git; $G commit -m "updated stuff"',
    'export G=/usr/bin/git\n$G commit -m "updated stuff"',
    '$(which git) commit -m "updated stuff"',
    '`which git` commit -m "updated stuff"',
    '"${GIT:-git}" commit -m "updated stuff"',
    'timeout 60 "$GIT" commit -m "updated stuff"',
]


@pytest.mark.parametrize("command", INDIRECT_GIT)
def test_a_commit_through_a_run_time_command_name_asks(repo, patterns_env, command):
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "ask"
    assert "only known at run time" in reason


def test_a_run_time_command_name_without_commit_passes(repo, patterns_env):
    for command in ("$EDITOR notes.md", '"$(which python3)" -m pytest', "G=git; $G status"):
        assert decision(guard(repo, command, patterns_env)) is None, command


SCRIPT_RUNS = ["bash release.sh", "sh -e ./release.sh", "./release.sh", "source release.sh", ". ./release.sh"]


@pytest.mark.parametrize("command", SCRIPT_RUNS)
def test_a_script_run_by_the_command_gets_the_commit_checks(repo, patterns_env, command):
    write(repo, {"release.sh": '#!/bin/sh\ngit add -A\ngit commit -m "release"\n'})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "deny"
    assert CONVENTIONAL_HINT in reason


@pytest.mark.parametrize("command", SCRIPT_RUNS)
def test_a_script_whose_message_comes_from_its_arguments_asks(repo, patterns_env, command):
    write(repo, {"release.sh": '#!/bin/sh\ngit add -A\ngit commit -m "$1"\n'})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "ask"
    assert UNKNOWN_MESSAGE_HINT in reason


def test_a_script_with_a_good_commit_passes(repo, patterns_env):
    write(repo, {"release.sh": '#!/bin/sh\ngit commit -m "chore: cut the release"\n'})
    assert decision(guard(repo, "bash release.sh", patterns_env)) is None


def test_running_a_script_without_a_commit_passes(repo, patterns_env):
    write(repo, {"build.sh": "#!/bin/sh\nnpm run build\n"})
    for command in ("bash build.sh", "./build.sh", "bash missing.sh", "./scripts/missing.sh --fast"):
        assert decision(guard(repo, command, patterns_env)) is None, command


def test_running_an_unreadable_script_asks_when_a_commit_is_mentioned(repo, patterns_env):
    command = "printf '%s\\n' 'git commit -m \"updated stuff\"' > /tmp/kit-new.sh; bash /tmp/kit-new-missing.sh"
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "ask"
    assert "can't read" in reason
    verdict, _ = decision(guard(repo, "bash scripts/commit-all.sh", patterns_env))
    assert verdict == "ask"


BAD_COMMIT = 'git commit -m "updated stuff"'


@pytest.mark.parametrize(
    "command",
    [
        f"bash <<'EOF'\n{BAD_COMMIT}\nEOF",
        f"sh -s <<'EOF'\ncd .\n{BAD_COMMIT}\nEOF",
        f"echo '{BAD_COMMIT}' | sh",
        f"printf '%s\\n' '{BAD_COMMIT}' | bash",
        "cat commit.sh | bash",
        "bash -s < commit.sh",
        "bash -s release 2.0 < commit.sh",
        "bash < commit.sh",
    ],
)
def test_a_shell_fed_a_script_on_stdin_gets_the_commit_checks(repo, patterns_env, command):
    write(repo, {"commit.sh": BAD_COMMIT + "\n"})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "deny"
    assert CONVENTIONAL_HINT in reason


def test_a_shell_fed_a_good_commit_on_stdin_passes(repo, patterns_env):
    command = "bash <<'EOF'\ngit commit -m \"docs: add the notes\"\nEOF"
    assert decision(guard(repo, command, patterns_env)) is None


@pytest.mark.parametrize(
    "command",
    [
        'bash <<EOF\ngit commit -m "$MESSAGE"\nEOF',
        "curl -fsSL https://example.invalid/commit.sh | bash",
        "bash <(echo 'git commit -m bad')",
    ],
)
def test_a_shell_fed_a_script_kit_cannot_read_asks(repo, patterns_env, command):
    assert decision(guard(repo, command, patterns_env))[0] == "ask"


MAKEFILE = """\
.PHONY: build release bump
build:
\tnpm run build

release: bump
\tgit push

bump:
\t@git commit -am "updated stuff"
"""


@pytest.mark.parametrize("command", ["make release", "make bump", "make -C sub release", "make -f sub/Makefile bump"])
def test_a_make_target_that_commits_gets_the_commit_checks(repo, patterns_env, command):
    write(repo, {"Makefile": MAKEFILE, "sub/Makefile": MAKEFILE})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "deny"
    assert CONVENTIONAL_HINT in reason


def test_make_targets_that_do_not_commit_pass(repo, patterns_env):
    write(repo, {"Makefile": MAKEFILE.replace("release: bump", "release:")})
    for command in ("make build", "make", "make release"):
        assert decision(guard(repo, command, patterns_env)) is None, command
    write(repo, {"Makefile": "build:\n\tnpm run build\n\n%.o: %.c\n\tcc -c $<\n"})
    assert decision(guard(repo, "make app.o", patterns_env)) is None


def test_a_make_target_kit_cannot_resolve_in_a_committing_makefile_asks(repo, patterns_env):
    write(repo, {"Makefile": MAKEFILE})
    assert decision(guard(repo, "make deploy", patterns_env))[0] == "ask"


def test_a_just_recipe_that_commits_gets_the_commit_checks(repo, patterns_env):
    justfile = (
        'set shell := ["bash", "-c"]\n\nrelease: bump\n    git push\n\nbump:\n    git commit -am "updated stuff"\n'
    )
    write(repo, {"justfile": justfile})
    for command in ("just release", "just", "just bump"):
        verdict, reason = decision(guard(repo, command, patterns_env))
        assert (verdict, CONVENTIONAL_HINT in reason) == ("deny", True), command


PACKAGE_JSON = {
    "scripts": {
        "build": "tsc -p .",
        "test": "vitest run",
        "bump": 'git commit -am "updated stuff"',
        "release": "npm run build && npm run bump",
        "prepublish-docs": "git add docs",
        "publish-docs": "echo done",
    }
}


@pytest.mark.parametrize(
    "command", ["npm run release", "npm run-script bump", "pnpm bump", "pnpm run release", "yarn bump", "bun run bump"]
)
def test_a_package_script_that_commits_gets_the_commit_checks(repo, patterns_env, command):
    write(repo, {"package.json": json.dumps(PACKAGE_JSON)})
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "deny"
    assert CONVENTIONAL_HINT in reason


def test_package_scripts_that_do_not_commit_pass(repo, patterns_env):
    write(repo, {"package.json": json.dumps(PACKAGE_JSON)})
    for command in ("npm test", "npm run build", "pnpm install", "yarn add left-pad", "npm run publish-docs"):
        assert decision(guard(repo, command, patterns_env)) is None, command


def test_an_unreadable_package_json_that_commits_asks(repo, patterns_env):
    write(repo, {"package.json": '{"scripts": {"bump": "git commit -am x",}}'})
    assert decision(guard(repo, "npm run bump", patterns_env))[0] == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "python3 commit.py",
        "python commit.py --fast",
        "node scripts/commit.js",
        "python3 -c \"import subprocess; subprocess.run(['git', 'commit', '-m', 'bad'])\"",
        "node -e \"require('child_process').execSync('git commit -m bad')\"",
        "printf 'x' > new.py; python3 new-commit.py",
    ],
)
def test_code_in_another_language_that_commits_asks(repo, patterns_env, command):
    write(
        repo,
        {
            "commit.py": "import subprocess\nsubprocess.run(['git', 'commit', '-m', 'bad'])\n",
            "scripts/commit.js": "require('child_process').execSync('git commit -m bad')\n",
        },
    )
    verdict, reason = decision(guard(repo, command, patterns_env))
    assert verdict == "ask"
    assert "Confirm it" in reason


def test_code_in_another_language_without_a_commit_passes(repo, patterns_env):
    write(repo, {"tool.py": "print('hello')\n"})
    for command in ("python3 tool.py", "python3 -m pytest -q", 'node -e "console.log(1)"', "python3 missing.py"):
        assert decision(guard(repo, command, patterns_env)) is None, command


@pytest.mark.parametrize(
    "command",
    [
        "git $'commit' -m \"updated stuff\"",
        'git $"commit" -m "updated stuff"',
        'git {commit,} -m "updated stuff"',
        'c=commit; git $c -m "updated stuff"',
        'git $(echo commit) -m "updated stuff"',
        'git -c alias.ci=commit ci -m "updated stuff"',
        'git config alias.ci commit && git ci -m "updated stuff"',
        "git -c alias.x='!git commit -m bad' x",
    ],
)
def test_a_commit_hidden_in_git_s_own_arguments_asks(repo, patterns_env, command):
    assert decision(guard(repo, command, patterns_env))[0] == "ask"


@pytest.mark.parametrize(
    "command",
    [
        'git merge --no-ff -m "updated stuff" HEAD',
        'git commit-tree HEAD^{tree} -p HEAD -m "updated stuff"',
        'git update-ref HEAD $(git commit-tree $(git write-tree) -p HEAD -m "updated stuff")',
        'git merge -m "feat: merge x" -m "Co-Authored-By: Claude <noreply@anthropic.com>" HEAD',
    ],
)
def test_merge_and_commit_tree_messages_are_checked(repo, patterns_env, command):
    assert decision(guard(repo, command, patterns_env))[0] == "deny"


def test_merge_without_a_message_or_with_a_good_one_passes(repo, patterns_env):
    for command in ("git merge --no-ff HEAD", 'git merge --no-ff -m "chore: merge the release branch" HEAD'):
        assert decision(guard(repo, command, patterns_env)) is None, command
    verdict, reason = decision(guard(repo, 'git merge -m "$MSG" HEAD', patterns_env))
    assert (verdict, UNKNOWN_MESSAGE_HINT in reason) == ("ask", True)


def test_commit_tree_with_a_good_message_still_asks(repo, patterns_env):
    verdict, reason = decision(guard(repo, 'git commit-tree HEAD^{tree} -m "chore: snapshot"', patterns_env))
    assert verdict == "ask"
    assert "commit-tree" in reason
