"""Record the project's current type errors as the stop gate's type-ratchet baseline.

Usage: python3 plugins/kit/hooks/bin/kit_baseline.py [--root <repo>]

Writes .claude/kit-baseline.json: per-file fingerprint counts for mypy and tsc (whichever the project
configures), the tool versions and a timestamp. Commit it; a baseline should only ever shrink.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import gatelib
import kitlib


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = _repo_root(args.root)
    jobs = gatelib.typecheck_jobs(root, gatelib.project_files(root))
    if not jobs:
        print("kit-baseline: no type checker is configured (mypy or tsc); nothing recorded.")
        return 0
    previous = _previous_baseline(root)
    baseline, failed = _collect(root, jobs)
    path = root / gatelib.BASELINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    _print_summary(path, baseline, previous)
    return 1 if failed else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kit_baseline", description="Record the type-ratchet baseline.")
    parser.add_argument("--root", help="repository to baseline (default: the git repository of the current directory)")
    return parser.parse_args(argv)


def _repo_root(requested: str | None) -> Path:
    start = Path(requested or Path.cwd()).expanduser().resolve()
    code, out, err = kitlib.run(["git", "rev-parse", "--show-toplevel"], start, timeout=10)
    if code != 0:
        sys.exit(f"kit-baseline: {start} is not inside a git repository")
    return Path(out.strip()).resolve()


def _previous_baseline(root: Path) -> dict | None:
    try:
        return gatelib.load_baseline(root)
    except ValueError as error:
        print(f"kit-baseline: ignoring the old baseline ({error})", file=sys.stderr)
        return None


def _collect(root: Path, jobs: list) -> tuple[dict, bool]:
    results, failed_tools = [], set()
    for job in jobs:
        try:
            results.append((job, gatelib.run_typecheck(root, job)))
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            print(
                f"kit-baseline: {job.tool} in {job.cwd} failed ({error}); {job.tool} is not baselined", file=sys.stderr
            )
            failed_tools.add(job.tool)
    # A tool baselined for only some of its projects would make the others' legacy errors look new.
    kept = [(job, by_file) for job, by_file in results if job.tool not in failed_tools]
    one_job_per_tool = {job.tool: job for job, _ in kept}
    versions = {tool: gatelib.tool_version(job) for tool, job in one_job_per_tool.items()}
    fingerprints: dict[str, Counter] = {}
    for _, by_file in kept:
        for rel, found in by_file.items():
            fingerprints.setdefault(rel, Counter()).update(kitlib._fingerprint_key(v.fingerprint) for v in found)
    baseline = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tools": versions,
        "fingerprints": {rel: dict(sorted(counts.items())) for rel, counts in sorted(fingerprints.items())},
    }
    return baseline, bool(failed_tools)


def _print_summary(path: Path, baseline: dict, previous: dict | None) -> None:
    print(f"kit-baseline: wrote {path}")
    for tool, version in sorted(baseline["tools"].items()):
        counts = [
            count
            for per_file in baseline["fingerprints"].values()
            for key, count in per_file.items()
            if kitlib._fingerprint_from_key(key)[0] == tool
        ]
        print(f"  {tool} ({version}): {sum(counts)} type errors")
    new_size, old_size = gatelib.baseline_size(baseline), gatelib.baseline_size(previous)
    if previous is not None and new_size > old_size:
        print(
            f"kit-baseline: warning: the baseline grew from {old_size} to {new_size} type errors. "
            "A baseline should only shrink; fix the new errors instead of recording them.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    sys.exit(main())
