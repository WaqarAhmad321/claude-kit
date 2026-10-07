import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "home" / "hooks" / "orchestrator_guard.py"


def run_guard(tmp_path: Path, tool: str = "Bash", tool_input: dict | None = None) -> dict:
    payload = {"tool_name": tool, "tool_input": tool_input or {"command": "ls"}}
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"CLAUDE_CONFIG_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        timeout=10,
    )
    return json.loads(result.stdout)


def test_denial_points_at_the_workflow_rule_instead_of_naming_a_model(tmp_path):
    output = run_guard(tmp_path)
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "model opus" not in reason
    assert "workflow rule" in reason
