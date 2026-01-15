#!/usr/bin/env python3
"""
Plan subagent assignments from a scan JSON.
Groups files by directory depth and balances token budgets.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional


def load_scan(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to read scan file: {e}", file=sys.stderr)
        sys.exit(1)


def group_by_depth(files: list[dict], depth: int) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for f in files:
        key = group_label(f["path"], depth)
        groups[key].append(f)
    return groups


def group_label(path: str, depth: int) -> str:
    parts = Path(path).parts
    if len(parts) <= depth:
        key = "/".join(parts)
    else:
        key = "/".join(parts[:depth])
    if not key:
        key = "."
    return key


def split_groups(files: list[dict], depth: int, max_depth: int, max_tokens: int) -> list[dict]:
    groups = group_by_depth(files, depth)
    result = []
    for key, items in groups.items():
        total = sum(f.get("tokens", 0) for f in items)
        if total > max_tokens and depth < max_depth:
            result.extend(split_groups(items, depth + 1, max_depth, max_tokens))
        elif total > max_tokens:
            # Fallback: split into individual files to stay under budget.
            for f in sorted(items, key=lambda x: x.get("tokens", 0), reverse=True):
                result.append({
                    "label": f["path"],
                    "tokens": f.get("tokens", 0),
                    "files": [f],
                })
        else:
            result.append({
                "label": key,
                "tokens": total,
                "files": items,
            })
    return result


def assign_groups(groups: list[dict], max_tokens: int) -> list[dict]:
    groups_sorted = sorted(groups, key=lambda x: x.get("tokens", 0), reverse=True)
    buckets: list[dict] = []

    for group in groups_sorted:
        best_bucket = None
        best_bucket_tokens = None
        for bucket in buckets:
            if bucket["tokens"] + group["tokens"] <= max_tokens:
                if best_bucket is None or bucket["tokens"] < best_bucket_tokens:
                    best_bucket = bucket
                    best_bucket_tokens = bucket["tokens"]
        if best_bucket is None:
            buckets.append({"tokens": group["tokens"], "groups": [group]})
        else:
            best_bucket["tokens"] += group["tokens"]
            best_bucket["groups"].append(group)

    return buckets


def format_text(assignments: list[dict]) -> str:
    lines = []
    for idx, bucket in enumerate(assignments, start=1):
        lines.append(f"Group {idx} ({bucket['tokens']:,} tokens)")
        for group in sorted(bucket["groups"], key=lambda x: x["label"]):
            lines.append(f"- {group['label']} ({group['tokens']:,} tokens)")
            for f in sorted(group["files"], key=lambda x: x["path"]):
                lines.append(f"  - {f['path']} ({f['tokens']:,} tokens)")
        lines.append("")
    return "\n".join(lines).rstrip()


def load_changed_list(path: Optional[str]) -> set[str]:
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
    except Exception:
        return set()
    return {line.lstrip("./") for line in lines if line}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan subagent assignments from a scan JSON"
    )
    parser.add_argument(
        "scan",
        help="Path to scan JSON",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=150000,
        help="Max tokens per subagent group (default: 150000)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Initial directory depth to group by (default: 1)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Max directory depth for splitting large groups (default: 3)",
    )
    parser.add_argument(
        "--changed-list",
        default=None,
        help="Path to file with changed files (one per line)",
    )
    parser.add_argument(
        "--changed",
        action="append",
        default=[],
        help="Changed file path (can be repeated)",
    )
    parser.add_argument(
        "--changed-scope",
        choices=["modules", "files"],
        default="modules",
        help="Include modules containing changes or only changed files",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write output to a file instead of stdout",
    )

    args = parser.parse_args()
    scan_path = Path(args.scan)
    data = load_scan(scan_path)

    files = data.get("files", [])
    if not files:
        print("ERROR: Scan file has no files", file=sys.stderr)
        sys.exit(1)

    changed_paths = load_changed_list(args.changed_list)
    changed_paths.update(path.lstrip("./") for path in args.changed if path)

    if changed_paths:
        if args.changed_scope == "files":
            files = [f for f in files if f["path"] in changed_paths]
        else:
            changed_labels = {
                group_label(path, args.depth) for path in changed_paths
            }
            files = [
                f for f in files if group_label(f["path"], args.depth) in changed_labels
            ]

        if not files:
            print("ERROR: No files matched changed paths", file=sys.stderr)
            sys.exit(1)

    groups = split_groups(files, args.depth, args.max_depth, args.max_tokens)
    assignments = assign_groups(groups, args.max_tokens)

    if args.format == "json":
        output = json.dumps({
            "max_tokens": args.max_tokens,
            "changed_scope": args.changed_scope,
            "changed_count": len(changed_paths),
            "assignments": assignments,
        }, indent=2)
    else:
        output = format_text(assignments)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
