import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import Future
from pathlib import Path

import gatelib
import pytest
import record_journey_pass
import stop_gate
from conftest import RUFF_PYPROJECT, read_state
from hookrun import BIN, base_payload, commit_all, findings, launch, tool_payload, write

CLEAN = "def f():\n    return 1\n"
LEGACY = "def f():\n    return missing_one\n"
CLOSING = "Fix these in the code (no suppression comments, no config changes), re-run the checks, then finish."
JOURNEYS = {"gate": {"journeys": {"command": "python3 journeys.py", "watch": ["src/**/*.py"]}}}
NO_BASELINE = "typecheck: no baseline (run kit-baseline); type errors not gated"


@pytest.fixture(scope="session")
def mypy_bin() -> str:
    installed = shutil.which("mypy")
    if installed:
        return installed
    if shutil.which("uvx"):
        locate = "import sysconfig; print(sysconfig.get_path('scripts'))"
        proc = subprocess.run(
            ["uvx", "--from", "mypy", "python", "-c", locate], capture_output=True, text=True, timeout=300
        )
        exe = Path(proc.stdout.strip().splitlines()[-1]) / "mypy" if proc.returncode == 0 and proc.stdout else None
        if exe and exe.is_file():
            return str(exe)
    pytest.skip("mypy is not installed and uvx could not provide it")


@pytest.fixture
def gate_repo(ruff_repo) -> Path:
    write(ruff_repo, ".gitignore", ".venv/\n.mypy_cache/\nnode_modules/\n")
    commit_all(ruff_repo)
    return ruff_repo


@pytest.fixture
def mypy_repo(gate_repo, mypy_bin) -> Path:
    write(gate_repo, "pyproject.toml", RUFF_PYPROJECT + "\n[tool.mypy]\nwarn_unused_configs = true\n")
    write(gate_repo, "typed.py", "def label(count: int) -> str:\n    return count\n")
    (gate_repo / ".venv" / "bin" / "mypy").symlink_to(mypy_bin)
    commit_all(gate_repo)
    return gate_repo


def start_session(repo: Path, **options) -> None:
    event = "SubagentStart" if options.get("agent_id") else "SessionStart"
    result = launch("session_context", base_payload(repo, event, **options))
    assert result.returncode == 0, result


def stop(repo: Path, active: bool = False, agent_id: str | None = None, running: tuple[str, ...] = ()):
    event = "SubagentStop" if agent_id else "Stop"
    tasks = [{"id": task_id, "type": "subagent", "status": "running", "description": "work"} for task_id in running]
    payload = base_payload(repo, event, agent_id=agent_id) | {"stop_hook_active": active, "background_tasks": tasks}
    return launch("stop_gate", payload)


def outcome(result) -> str:
    """"pass", "block", or the message shown to the user."""
    assert result.returncode == 0, result
    if not result.stdout:
        return "pass"
    output = json.loads(result.stdout)
    return "block" if output.get("decision") == "block" else output["systemMessage"]


def bash_ran(repo: Path, command: str, response: dict | None = None, **options):
    payload = tool_payload(repo, "PostToolUse", "Bash", {"command": command, "description": "run"}, **options)
    payload["tool_response"] = response or {"stdout": "ok", "stderr": "", "interrupted": False, "isImage": False}
    return launch("record_journey_pass", payload)


def blocked(result) -> str:
    assert result.returncode == 0, result
    output = json.loads(result.stdout)
    assert output["decision"] == "block", output
    return findings(output["reason"])


def notice(result) -> str:
    assert result.returncode == 0, result
    output = json.loads(result.stdout)
    assert "decision" not in output, output
    return output["systemMessage"]


def passes(result) -> None:
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result


def run_baseline(repo: Path):
    cmd = [sys.executable, str(BIN / "kit_baseline.py"), "--root", str(repo)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def test_no_changes_exits_fast(gate_repo):
    start_session(gate_repo)
    started = time.monotonic()
    passes(stop(gate_repo))
    assert time.monotonic() - started < 2


def test_a_tool_edit_with_a_new_violation_blocks(gate_repo, edit):
    write(gate_repo, "app.py", CLEAN)
    commit_all(gate_repo)
    start_session(gate_repo)
    edit(gate_repo, "app.py", CLEAN + "\n\ndef g():\n    return undefined_name\n")
    reason = blocked(stop(gate_repo))
    assert "New lint problems:\napp.py:\n- ruff F821: Undefined name `undefined_name` (line 6)" in reason
    assert reason.endswith(CLOSING)


def test_a_legacy_violation_does_not_block(gate_repo, edit):
    write(gate_repo, "app.py", LEGACY)
    commit_all(gate_repo)
    start_session(gate_repo)
    edit(gate_repo, "app.py", LEGACY + "\n\ndef g():\n    return 2\n")
    passes(stop(gate_repo))


def test_a_bash_change_is_judged_against_head(gate_repo):
    write(gate_repo, "app.py", LEGACY)
    commit_all(gate_repo)
    start_session(gate_repo)
    write(gate_repo, "app.py", LEGACY + "\n\ndef g():\n    return missing_two\n")
    reason = blocked(stop(gate_repo))
    assert "missing_two" in reason
    assert "missing_one" not in reason


def test_a_renamed_file_keeps_its_legacy_violations(gate_repo):
    write(gate_repo, "old.py", LEGACY)
    commit_all(gate_repo)
    start_session(gate_repo)
    subprocess.run(["git", "mv", "old.py", "new.py"], cwd=gate_repo, check=True)
    passes(stop(gate_repo))


def test_an_untracked_new_file_gets_an_empty_baseline(gate_repo):
    write(gate_repo, ".claude/kit.json", json.dumps({"gate": {"review": {"enabled": False}}}))
    commit_all(gate_repo)
    start_session(gate_repo)
    write(gate_repo, "pkg/new_module.py", "import os\n")
    write(gate_repo, "app/migrations/0001_initial.py", "import os\n")
    write(gate_repo, ".claude/helper.py", "import os\n")
    reason = blocked(stop(gate_repo))
    assert "pkg/new_module.py:\n- ruff F401: `os` imported but unused (line 1)" in reason
    assert "migrations" not in reason
    assert ".claude" not in reason


def test_initially_dirty_files_are_excluded(gate_repo):
    write(gate_repo, "wip.py", "import os\n")
    start_session(gate_repo)
    write(gate_repo, "wip.py", "import os\nimport sys\n")
    passes(stop(gate_repo))


def test_journeys_gate_blocks_until_a_green_run(gate_repo, edit):
    write(gate_repo, ".claude/kit.json", json.dumps(JOURNEYS))
    write(gate_repo, "src/app.py", CLEAN)
    commit_all(gate_repo)
    start_session(gate_repo)
    edit(gate_repo, "src/app.py", CLEAN + "\n\nA = 1\n")
    stale = (
        "Run `python3 journeys.py` and make it pass before finishing: "
        "these watched files changed since the last green run: src/app.py"
    )
    assert stale in blocked(stop(gate_repo))
    spoofs = (
        "python3 journeys.py | tail -5",
        "python3 journeys.py; echo done",
        "python3 journeys.py || true",
        "! python3 journeys.py",
        "echo python3 journeys.py",
        "true # python3 journeys.py",
    )
    for spoof in spoofs:
        bash_ran(gate_repo, spoof)
    assert stale in blocked(stop(gate_repo))
    assert bash_ran(gate_repo, "cd src && python3  journeys.py 2>&1").returncode == 0
    passes(stop(gate_repo))
    commit_all(gate_repo)
    passes(stop(gate_repo))
    edit(gate_repo, "src/app.py", CLEAN + "\n\nA = 2\n")
    assert stale in blocked(stop(gate_repo))


def test_a_background_or_interrupted_journey_run_does_not_count(gate_repo):
    write(gate_repo, ".claude/kit.json", json.dumps(JOURNEYS))
    commit_all(gate_repo)
    start_session(gate_repo)
    write(gate_repo, "src/app.py", CLEAN)
    interrupted = {"stdout": "", "stderr": "", "interrupted": True, "isImage": False}
    moved = {"stdout": "Command did not complete within its 120s timeout and was moved to the background", "stderr": ""}
    bash_ran(gate_repo, "python3 journeys.py", interrupted)
    bash_ran(gate_repo, "python3 journeys.py", moved)
    assert "Run `python3 journeys.py`" in blocked(stop(gate_repo))


def test_the_same_findings_block_three_times_then_release_with_one_warning_line(gate_repo):
    start_session(gate_repo)
    write(gate_repo, "app.py", "import os\n")
    for attempt in range(3):
        assert "app.py:\n- ruff F401" in blocked(stop(gate_repo, active=attempt > 0))
    message = notice(stop(gate_repo, active=True))
    assert "\n" not in message
    head, _, report = message.partition("; still unresolved: ")
    assert head == "kit stop gate: blocked 3 times in a row on the same problems, so Claude may finish"
    assert "app.py:\n- ruff F401: `os` imported but unused (line 1)" in Path(report).read_text()
    passes(stop(gate_repo)), "a new turn doesn't reset the count, and the warning isn't repeated"


def test_alternating_findings_block_five_times_then_release_until_a_clean_stop(gate_repo):
    start_session(gate_repo)
    variants = ("import os\n", "import sys\n")
    for attempt in range(5):
        write(gate_repo, "app.py", variants[attempt % 2])
        blocked(stop(gate_repo, active=attempt > 0))
    write(gate_repo, "app.py", variants[1])
    assert notice(stop(gate_repo, active=True)).startswith("kit stop gate: blocked 5 times in a row, so Claude may")
    write(gate_repo, "app.py", variants[0])
    passes(stop(gate_repo)), "a new turn doesn't start a new count"
    write(gate_repo, "app.py", CLEAN)
    passes(stop(gate_repo))
    write(gate_repo, "app.py", variants[0])
    blocked(stop(gate_repo)), "a clean stop starts a new count"


def test_a_main_thread_block_is_three_short_lines_and_the_report_holds_the_rest(gate_repo, data_dir):
    start_session(gate_repo)
    for index in range(30):
        write(gate_repo, f"pkg/module_{index:02}.py", "import os\n")
    write(gate_repo, ".github/workflows/ci.yml", "on: push\n")
    report = data_dir / "reports" / "s1-stop.md"
    assert json.loads(stop(gate_repo).stdout)["reason"].splitlines() == [
        "kit stop gate: new lint problems, gate config changed in 31 files.",
        f"Full report: {report}",
        "Delegate the fix to a subagent that reads the report; don't work through it in the main chat.",
    ]
    text = report.read_text()
    assert "pkg/module_00.py:\n- ruff F401" in text and "pkg/module_29.py:\n- ruff F401" in text
    assert "Tell the user you changed .github/workflows/ci.yml and why; they must approve it." in text


def test_a_subagent_block_keeps_the_findings_but_caps_them_at_forty_lines(gate_repo, data_dir, edit):
    start_session(gate_repo)
    start_session(gate_repo, agent_id="agent-9")
    for index in range(30):
        edit(gate_repo, f"pkg/module_{index:02}.py", "import os\n", agent_id="agent-9", tool="Write")
    lines = json.loads(stop(gate_repo, agent_id="agent-9").stdout)["reason"].splitlines()
    report = data_dir / "reports" / "s1-agent-9-stop.md"
    assert lines[:5] == [
        "kit stop gate: this session's changes don't pass yet.",
        "",
        "New lint problems:",
        "pkg/module_00.py:",
        "- ruff F401: `os` imported but unused (line 1)",
    ]
    assert lines[40:] == [f"...and 23 more lines in the full report: {report}", "", CLOSING]
    assert "pkg/module_29.py:" in report.read_text()


def test_stops_that_defer_a_running_agent_s_files_keep_the_block_count(gate_repo, edit):
    """An orchestrating main thread stops while its agents run. Deferring their files made those stops clean, which
    cleared the count, so the same findings blocked every other stop without end."""
    start_session(gate_repo)
    start_session(gate_repo, agent_id="worker")
    edit(gate_repo, "app.py", "import os\n", agent_id="worker", tool="Write")
    outcomes = [outcome(stop(gate_repo, running=running)) for _ in range(6) for running in ((), ("worker",))]
    assert outcomes[1] == "pass", "the running agent's file is deferred"
    assert outcomes.count("block") == 3
    warnings = [line for line in outcomes if line.startswith("kit stop gate: blocked 3 times in a row")]
    assert len(warnings) == 1 and "\n" not in warnings[0]


def test_findings_that_change_every_turn_block_five_times_then_release(gate_repo):
    start_session(gate_repo)
    variants = ("import os\n", "import sys\n")
    outcomes = []
    for attempt in range(8):
        write(gate_repo, "app.py", variants[attempt % 2])
        outcomes.append(outcome(stop(gate_repo, active=False)))
    assert outcomes[:5] == ["block"] * 5, "a new turn doesn't restart the count"
    assert outcomes[5].startswith("kit stop gate: blocked 5 times in a row, so Claude may finish; still unresolved: ")
    assert outcomes[6:] == ["pass", "pass"]


def test_a_released_subagent_no_longer_holds_back_the_main_stop(gate_repo, edit):
    start_session(gate_repo)
    start_session(gate_repo, agent_id="worker")
    edit(gate_repo, "app.py", "import os\n", agent_id="worker", tool="Write")
    assert [outcome(stop(gate_repo, agent_id="worker")) for _ in range(3)] == ["block"] * 3
    assert outcome(stop(gate_repo, agent_id="worker")).startswith("kit stop gate: blocked 3 times")
    assert outcome(stop(gate_repo, running=("task-unknown",))) == "block", "its stop let it finish, so it's done"


def test_changed_findings_restart_the_same_count_within_five_blocks_and_fixing_them_clears_it(gate_repo, data_dir):
    start_session(gate_repo)
    write(gate_repo, "app.py", "import os\n")
    for attempt in range(3):
        blocked(stop(gate_repo, active=attempt > 0))
    write(gate_repo, "app.py", "import os\nimport sys\n")
    for attempt in range(2):
        assert "`sys` imported but unused" in blocked(stop(gate_repo, active=attempt > 0))
    assert notice(stop(gate_repo, active=True)).startswith("kit stop gate: blocked 5 times in a row")
    write(gate_repo, "app.py", CLEAN)
    passes(stop(gate_repo, active=True))
    assert "stop_gate_streak" not in read_state(data_dir)
    write(gate_repo, "app.py", "import os\n")
    blocked(stop(gate_repo, active=True))


def test_a_gate_config_change_blocks_once_then_notifies(gate_repo):
    start_session(gate_repo)
    write(gate_repo, "pyproject.toml", RUFF_PYPROJECT + 'ignore = ["E501"]\n')
    write(gate_repo, ".github/workflows/ci.yml", "on: push\n")
    reason = blocked(stop(gate_repo))
    assert "Tell the user you changed .github/workflows/ci.yml, pyproject.toml and why; they must approve it." in reason
    assert CLOSING not in reason
    assert "pyproject.toml" in notice(stop(gate_repo, active=True))
    passes(stop(gate_repo))


def test_a_lefthook_script_counts_as_gate_config(gate_repo):
    start_session(gate_repo)
    write(gate_repo, ".lefthook/pre-commit/lint.sh", "exit 0\n")
    assert "Tell the user you changed .lefthook/pre-commit/lint.sh and why" in blocked(stop(gate_repo))


def test_a_lefthook_extends_file_counts_as_gate_config(gate_repo):
    start_session(gate_repo)
    write(gate_repo, ".lefthook/django.yml", "pre-commit: {}\n")
    assert "Tell the user you changed .lefthook/django.yml and why" in blocked(stop(gate_repo))


def test_a_pyproject_change_outside_the_gate_sections_is_not_flagged(gate_repo):
    start_session(gate_repo)
    write(gate_repo, "pyproject.toml", '[project]\nname = "demo"\n\n' + RUFF_PYPROJECT)
    passes(stop(gate_repo))


def test_subagent_stop_reads_the_main_initially_dirty(gate_repo, edit):
    write(gate_repo, "wip.py", "import os\n")
    start_session(gate_repo)
    passes(stop(gate_repo, agent_id="agent-7"))
    edit(gate_repo, "sub.py", CLEAN, agent_id="agent-7", tool="Write")
    passes(stop(gate_repo, agent_id="agent-7"))
    edit(gate_repo, "sub.py", CLEAN + "\n\nvalue = undefined_name\n", agent_id="agent-7")
    reason = blocked(stop(gate_repo, agent_id="agent-7"))
    assert "sub.py:\n- ruff F821" in reason
    assert "wip.py" not in reason


def test_a_subagent_is_not_held_to_a_sibling_s_edits(gate_repo, edit):
    start_session(gate_repo)
    edit(gate_repo, "mine.py", CLEAN, agent_id="agent-1", tool="Write")
    edit(gate_repo, "theirs.py", "value = undefined_name\n", agent_id="agent-2", tool="Write")
    passes(stop(gate_repo, agent_id="agent-1"))
    assert "theirs.py" in blocked(stop(gate_repo))


def test_kit_baseline_records_type_errors_and_the_ratchet_uses_it(mypy_repo):
    result = run_baseline(mypy_repo)
    assert result.returncode == 0, result
    assert "mypy (mypy " in result.stdout and "): 1 type errors" in result.stdout
    baseline = json.loads((mypy_repo / ".claude" / "kit-baseline.json").read_text())
    assert set(baseline) == {"generated_at", "tools", "fingerprints"}
    assert list(baseline["fingerprints"]) == ["typed.py"]
    assert [json.loads(key)[:2] for key in baseline["fingerprints"]["typed.py"]] == [["mypy", "return-value"]]
    commit_all(mypy_repo)
    start_session(mypy_repo)
    write(mypy_repo, "typed.py", "def label(count: int) -> str:\n    return count\n\n\ntotal: int = 'zero'\n")
    reason = blocked(stop(mypy_repo))
    assert "New type errors:\ntyped.py:\n- mypy assignment: Incompatible types in assignment" in reason
    assert "(line 5)" in reason
    assert "return-value" not in reason


def test_kit_baseline_warns_when_the_baseline_grows(mypy_repo):
    assert run_baseline(mypy_repo).returncode == 0
    write(mypy_repo, "typed.py", "def label(count: int) -> str:\n    return count\n\n\ntotal: int = 'zero'\n")
    result = run_baseline(mypy_repo)
    assert "warning: the baseline grew from 1 to 2 type errors" in result.stderr


def test_without_a_baseline_type_errors_are_not_gated_and_it_says_so_once(mypy_repo):
    start_session(mypy_repo)
    write(mypy_repo, "typed.py", "def label(count: int) -> str:\n    return count\n\n\ntotal: int = 'zero'\n")
    assert notice(stop(mypy_repo)) == NO_BASELINE
    passes(stop(mypy_repo))


def test_tsc_ratchet_falls_back_from_build_mode_and_filters_to_changed_files(make_repo, data_dir, tmp_path):
    output = tmp_path / "tsc-output.txt"
    output.write_text("src/app.ts(1,14): error TS2322: Type 'string' is not assignable to type 'number'.\n")
    files = {".gitignore": "node_modules/\n", "tsconfig.json": "{}", "src/app.ts": "export const a = 1;\n"}
    repo = make_repo(files=files | {"src/other.ts": "export const b = 2;\n"})
    fake_tsc(repo / "node_modules" / ".bin" / "tsc", output)
    assert run_baseline(repo).returncode == 0
    commit_all(repo)
    start_session(repo)
    write(repo, "src/app.ts", "export const a = 1;\nexport const c = 3;\n")
    output.write_text(
        "src/app.ts(1,14): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        "src/app.ts(2,3): error TS2345: Argument of type '{ a: string; }' is not assignable.\n"
        "  Types of property 'a' are incompatible.\n"
        "src/other.ts(3,1): error TS2304: Cannot find name 'zzz'.\n"
    )
    reason = blocked(stop(repo))
    assert "src/app.ts:\n- tsc TS2345: Argument of type '{ a: string; }' is not assignable." in reason
    assert "Types of property 'a' are incompatible. (line 2)" in reason
    assert "TS2322" not in reason and "other.ts" not in reason


def fake_tsc(path: Path, output: Path) -> None:
    """Fails `tsc -b` on a project-reference error, as a solution-style config would; `-p` prints `output`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if '--version' in sys.argv:\n    print('Version 9.9.9'); sys.exit(0)\n"
        "if '-b' in sys.argv:\n    print('tsconfig.json(1,1): error TS6306: Referenced project must be composite.'); "
        "sys.exit(1)\n"
        f"sys.stdout.write(open({str(output)!r}).read()); sys.exit(2)\n"
    )
    path.chmod(0o755)


def test_an_invalid_kit_json_is_one_finding_through_the_loop_guard(gate_repo):
    write(gate_repo, ".claude/kit.json", '{"gate": {"journeys": {"command": "make journeys",}}}')
    commit_all(gate_repo)
    start_session(gate_repo)
    write(gate_repo, "app.py", CLEAN)
    reason = blocked(stop(gate_repo))
    assert "Invalid kit config:\n.claude/kit.json is invalid: " in reason
    assert reason.rstrip().endswith("fix it or tell Waqar.")
    assert CLOSING not in reason
    for _ in range(2):
        blocked(stop(gate_repo, active=True))
    report = notice(stop(gate_repo, active=True)).partition("; still unresolved: ")[2]
    assert ".claude/kit.json is invalid" in Path(report).read_text()
    assert bash_ran(gate_repo, "make journeys").stdout == "", "the recorder stays quiet; the gate reports it"


def test_a_malformed_baseline_is_a_finding(gate_repo):
    write(gate_repo, "pyproject.toml", RUFF_PYPROJECT + "\n[tool.mypy]\n")
    write(gate_repo, ".claude/kit-baseline.json", "[]")
    write(gate_repo, ".venv/bin/mypy", "")
    (gate_repo / ".venv" / "bin" / "mypy").chmod(0o755)
    commit_all(gate_repo)
    start_session(gate_repo)
    write(gate_repo, "app.py", CLEAN)
    reason = blocked(stop(gate_repo))
    assert ".claude/kit-baseline.json is invalid: it must be a JSON object" in reason
    assert "regenerate it with kit_baseline.py or tell Waqar." in reason


def test_a_type_checker_timeout_is_a_finding():
    future = Future()
    future.set_exception(subprocess.TimeoutExpired("tsc", 180))
    report = stop_gate.Report()
    job = gatelib.TypeJob("tsc", "tsc", Path("."), ())
    stop_gate._collect_types(report, job, future, {}, "")
    assert report.sections == {"New type errors": ["typecheck timed out (tsc after 180 s)"]}


@pytest.mark.parametrize(
    "command",
    [
        "make journeys",
        "cd api && make journeys 2>&1",
        "CI=1 make journeys > log.txt && echo ok",
        "timeout 600 make journeys",
        "set -o pipefail; make journeys 2>&1 | tail -20",
        "set -euo pipefail\nmake journeys | tee out.log",
        "echo start | make journeys",
        "(cd api && make journeys)",
    ],
)
def test_a_journey_run_counts_when_its_failure_would_fail_the_command(command, tmp_path):
    runs = record_journey_pass.decisive_commands(command, tmp_path)
    assert ["make", "journeys"] in [words for _, words in runs]


@pytest.mark.parametrize(
    "command",
    [
        "! make journeys",
        "echo make journeys",
        ": make journeys",
        "true # make journeys",
        "make journeys -n",
        "make journeys --dry-run",
        "make journeys 2>&1 | tail -20",
        "make journeys; echo done",
        "make journeys\necho done",
        "make journeys || true",
        "false || make journeys",
        "make journeys &",
        "make journeys && echo ok || echo failed",
        "if make journeys; then echo ok; fi",
        "set -o pipefail; set +o pipefail; make journeys | tail",
        "bash -c 'make journeys'",
        "$MAKE journeys",
    ],
)
def test_a_journey_run_does_not_count_when_something_could_hide_its_failure(command, tmp_path):
    runs = record_journey_pass.decisive_commands(command, tmp_path)
    assert ["make", "journeys"] not in [words for _, words in runs]


@pytest.mark.parametrize(
    ("command", "output", "counts"),
    [
        ('make journeys; echo "exit=$?"', "journeys: 3 passed\nexit=0", True),
        ("make journeys; echo $?", "3 passed\n0\n", True),
        ('make journeys\necho "EXIT_CODE=${?}"', "EXIT_CODE=0", True),
        ('make journeys && echo "exit=$?"', "", True),
        ('make journeys; echo "exit=$?"', "1 failed\nexit=2", False),
        ('make journeys; echo "exit=$?"', "exit=0\nlater output", False),
        ('make journeys; echo "exit=0"', "exit=0", False),
        ('make journeys; echo "exit=$?" >&2', "exit=0", False),
        ('make journeys; echo "exit=$?"; true', "exit=0", False),
        ('make journeys | tail -5; echo "exit=$?"', "exit=0", False),
        ('make journeys; echo "exit=$? in $PWD"', "exit=0 in /tmp", False),
        ('false; echo "exit=$?"', "exit=0", False),
    ],
)
def test_an_echoed_exit_status_counts_only_when_the_output_shows_zero(command, output, counts, tmp_path):
    runs = record_journey_pass.decisive_commands(command, tmp_path, output)
    assert (["make", "journeys"] in [words for _, words in runs]) is counts


def test_the_journey_run_is_matched_to_the_repo_it_ran_in(tmp_path):
    runs = record_journey_pass.decisive_commands("cd ../api && make journeys", tmp_path / "web")
    assert runs == [(tmp_path / "web" / ".." / "api", ["make", "journeys"])]


def test_a_journey_config_command_is_compared_word_by_word():
    assert record_journey_pass.journey_words("hurl --test  'j/*.hurl'") == ["hurl", "--test", "j/*.hurl"]
    assert record_journey_pass.journey_words("cd api && make journeys") == ["make", "journeys"]


@pytest.mark.parametrize(
    ("glob", "path", "matches"),
    [
        ("src/**/*.py", "src/app.py", True),
        ("src/**/*.py", "src/a/b/app.py", True),
        ("src/**/*.py", "lib/src/app.py", False),
        ("frontend/src/**", "frontend/src/a/b.tsx", True),
        ("*.hurl", "journeys/login.hurl", True),
        ("backend", "backend/app/models.py", True),
        ("src/*.py", "src/a/app.py", False),
    ],
)
def test_watch_globs_are_gitignore_style(glob, path, matches):
    assert gatelib.watch_matcher([glob])(path) is matches


def test_gate_parts_of_package_json_and_the_journeys_makefile_recipe_count_as_gate_config(gate_repo):
    write(gate_repo, ".claude/kit.json", json.dumps({"gate": {"journeys": {"command": "make journeys"}}}))
    write(gate_repo, "package.json", json.dumps({"name": "demo", "scripts": {"lint": "eslint ."}}))
    write(gate_repo, "Makefile", "journeys:\n\tpython3 -m unittest\n\nbuild:\n\techo build\n")
    commit_all(gate_repo)
    start_session(gate_repo)
    write(gate_repo, "package.json", json.dumps({"name": "demo", "version": "2", "scripts": {"lint": "eslint ."}}))
    write(gate_repo, "Makefile", "journeys:\n\tpython3 -m unittest\n\nbuild:\n\techo build2\n")
    passes(stop(gate_repo))
    write(gate_repo, "package.json", json.dumps({"name": "demo", "scripts": {"lint": "true"}}))
    write(gate_repo, "Makefile", "journeys:\n\ttrue\n\nbuild:\n\techo build2\n")
    assert "Tell the user you changed Makefile, package.json and why" in blocked(stop(gate_repo))


def test_a_noqa_inside_a_python_string_is_not_a_suppression(gate_repo):
    write(gate_repo, "app.py", CLEAN)
    commit_all(gate_repo)
    start_session(gate_repo)
    write(gate_repo, "app.py", CLEAN + '\n\nDOC = "use # noqa sparingly"\n')
    passes(stop(gate_repo))
