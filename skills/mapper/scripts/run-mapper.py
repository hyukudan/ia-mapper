#!/usr/bin/env python3
"""
Run a mapper workflow end-to-end with preflight and mode selection.
"""

import argparse
import json
import sys
from pathlib import Path
from subprocess import run


def run_cmd(cmd: list[str], cwd: Path) -> None:
    result = run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"ERROR: Command failed: {' '.join(cmd)}", file=sys.stderr)
        sys.exit(result.returncode)


def load_scan_summary(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def prompt_continue(message: str) -> bool:
    try:
        response = input(message).strip().lower()
    except EOFError:
        return False
    return response in {"y", "yes"}


def build_steps(mode: str) -> list[str]:
    if mode == "risk":
        return ["risk", "merge"]
    if mode == "quick":
        return ["scan", "plan"]
    if mode == "update":
        return ["changes", "scan", "plan", "risk", "merge"]
    return ["scan", "plan", "risk", "merge"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a Mapper workflow with preflight"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Repo path (default: current directory)",
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "full", "update", "risk"],
        default="full",
        help="Workflow mode (default: full)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation",
    )
    parser.add_argument(
        "--confirm-threshold",
        type=int,
        default=500000,
        help="Prompt if previous scan tokens exceed this (default: 500000)",
    )
    parser.add_argument(
        "--scan-arg",
        action="append",
        default=[],
        help="Extra args to pass to scan-codebase.py (repeatable)",
    )
    parser.add_argument(
        "--plan-arg",
        action="append",
        default=[],
        help="Extra args to pass to plan-assignments.py (repeatable)",
    )
    parser.add_argument(
        "--changed-list",
        default=None,
        help="Path to changed files list (default: .claude/mapper/changed.txt)",
    )
    parser.add_argument(
        "--changed-range",
        default=None,
        help="Git diff range (e.g. abc123..HEAD)",
    )
    parser.add_argument(
        "--changed-since-commit",
        default=None,
        help="Base commit for changes",
    )
    parser.add_argument(
        "--changed-since-date",
        default=None,
        help="Since date for changes",
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Include untracked files in changes",
    )
    parser.add_argument(
        "--changed-scope",
        choices=["files", "modules"],
        default="modules",
        help="Scope for update mode (default: modules)",
    )

    args = parser.parse_args()
    root = Path(args.path).resolve()
    scripts = Path(__file__).resolve().parent

    scan_path = root / ".claude" / "mapper" / "scan.json"
    assignments_path = root / ".claude" / "mapper" / "assignments.txt"
    changed_path = Path(args.changed_list) if args.changed_list else root / ".claude" / "mapper" / "changed.txt"
    risk_path = root / "docs" / "RISK_SIGNALS.md"
    map_path = root / "docs" / "CODEBASE_MAP.md"

    steps = build_steps(args.mode)

    previous = load_scan_summary(scan_path) if scan_path.exists() else {}
    prev_tokens = previous.get("total_tokens")

    print("Mapper preflight")
    print(f"- Mode: {args.mode}")
    print(f"- Steps: {', '.join(steps)}")
    if prev_tokens is not None:
        print(f"- Previous scan tokens: {prev_tokens:,}")
    else:
        print("- Previous scan: none")

    if not args.yes and prev_tokens and prev_tokens > args.confirm_threshold:
        if not prompt_continue("Proceed with this run? [y/N]: "):
            print("Aborted.")
            sys.exit(1)

    if "changes" in steps:
        change_cmd = [
            sys.executable,
            str(scripts / "git-changes.py"),
            str(root),
        ]
        if args.changed_range:
            change_cmd.extend(["--range", args.changed_range])
        if args.changed_since_commit:
            change_cmd.extend(["--since-commit", args.changed_since_commit])
        if args.changed_since_date:
            change_cmd.extend(["--since-date", args.changed_since_date])
        if args.include_untracked:
            change_cmd.append("--include-untracked")
        change_cmd.extend(["--out", str(changed_path)])
        run_cmd(change_cmd, root)

    if "scan" in steps:
        scan_cmd = [
            sys.executable,
            str(scripts / "scan-codebase.py"),
            str(root),
            "--format",
            "json",
            "--out",
            str(scan_path),
        ]
        if args.mode == "update":
            scan_cmd.extend(["--changed-list", str(changed_path)])
        scan_cmd.extend(args.scan_arg)
        run_cmd(scan_cmd, root)

    if "plan" in steps:
        plan_cmd = [
            sys.executable,
            str(scripts / "plan-assignments.py"),
            str(scan_path),
            "--out",
            str(assignments_path),
        ]
        if args.mode == "update":
            plan_cmd.extend(["--changed-list", str(changed_path), "--changed-scope", args.changed_scope])
        plan_cmd.extend(args.plan_arg)
        run_cmd(plan_cmd, root)

    if "risk" in steps:
        risk_cmd = [
            sys.executable,
            str(scripts / "risk-signals.py"),
            str(scan_path),
            "--out",
            str(risk_path),
        ]
        run_cmd(risk_cmd, root)

    if "merge" in steps:
        if map_path.exists() and risk_path.exists():
            merge_cmd = [
                sys.executable,
                str(scripts / "merge-risk-signals.py"),
                "--map",
                str(map_path),
                "--risk",
                str(risk_path),
            ]
            run_cmd(merge_cmd, root)
        else:
            print("Skip merge: docs/CODEBASE_MAP.md or docs/RISK_SIGNALS.md missing")

    print("Done.")
    print(f"- Scan: {scan_path}")
    print(f"- Assignments: {assignments_path}")
    if risk_path.exists():
        print(f"- Risk signals: {risk_path}")
    if map_path.exists():
        print(f"- Map: {map_path}")


if __name__ == "__main__":
    main()
