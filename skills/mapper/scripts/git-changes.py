#!/usr/bin/env python3
"""
List changed files from git for incremental mapping.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def git_available() -> bool:
    return shutil.which("git") is not None


def is_git_repo(root: Path) -> bool:
    if (root / ".git").exists():
        return True
    if not git_available():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def group_label(path: str, depth: int) -> str:
    parts = Path(path).parts
    if len(parts) <= depth:
        key = "/".join(parts)
    else:
        key = "/".join(parts[:depth])
    if not key:
        key = "."
    return key


def git_changed_files(root: Path, range_spec: str, since_commit: str, since_date: str, include_untracked: bool) -> list[str]:
    if not is_git_repo(root):
        print("ERROR: Not a git repo", file=sys.stderr)
        sys.exit(1)

    if range_spec:
        cmd = ["git", "-C", str(root), "diff", "--name-only", range_spec]
    elif since_commit:
        cmd = ["git", "-C", str(root), "diff", "--name-only", f"{since_commit}..HEAD"]
    elif since_date:
        cmd = [
            "git",
            "-C",
            str(root),
            "log",
            f"--since={since_date}",
            "--name-only",
            "--pretty=format:",
        ]
    else:
        cmd = ["git", "-C", str(root), "diff", "--name-only", "HEAD~1..HEAD"]

    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        msg = e.stderr.strip() or e.stdout.strip() or "git command failed"
        print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    if include_untracked:
        try:
            untracked = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            files.extend([line.strip() for line in untracked.stdout.splitlines() if line.strip()])
        except Exception:
            pass

    seen = set()
    deduped = []
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def write_output(output: str, out_path: Optional[Path]) -> None:
    if out_path is None:
        print(output)
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List changed files from git"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Repo path (default: current directory)",
    )
    parser.add_argument(
        "--range",
        dest="range_spec",
        default=None,
        help="Git diff range (e.g. abc123..HEAD)",
    )
    parser.add_argument(
        "--since-commit",
        default=None,
        help="Base commit (defaults to HEAD~1 if nothing else set)",
    )
    parser.add_argument(
        "--since-date",
        default=None,
        help="Since date (e.g. '2024-01-01')",
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Include untracked files",
    )
    parser.add_argument(
        "--group-depth",
        type=int,
        default=1,
        help="Group files by path depth (default: 1)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write output to a file instead of stdout",
    )

    args = parser.parse_args()
    root = Path(args.path).resolve()

    if not root.exists():
        print(f"ERROR: Path does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    files = git_changed_files(
        root=root,
        range_spec=args.range_spec,
        since_commit=args.since_commit,
        since_date=args.since_date,
        include_untracked=args.include_untracked,
    )

    if args.format == "json":
        groups = {}
        for path in files:
            label = group_label(path, args.group_depth)
            groups.setdefault(label, []).append(path)
        output = json.dumps({
            "root": str(root),
            "range": args.range_spec or args.since_commit or args.since_date or "HEAD~1..HEAD",
            "files": files,
            "groups": groups,
        }, indent=2)
    else:
        output = "\n".join(files)

    out_path = Path(args.out) if args.out else None
    write_output(output, out_path)


if __name__ == "__main__":
    main()
