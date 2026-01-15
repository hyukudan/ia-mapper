#!/usr/bin/env python3
"""
Generate a reduced "skeleton" view of files from a scan JSON.
Designed to reduce tokens while preserving structure and signatures.
"""

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import Optional


KEEP_PATTERNS = [
    r"^\s*(import|from)\b",
    r"^\s*(export|module\.exports|exports\.)\b",
    r"^\s*(class|def|function|fn|struct|enum|interface|type|trait|impl)\b",
    r"^\s*(const|let|var)\s+[A-Za-z_][A-Za-z0-9_]*\b",
    r"^\s*(package|use)\b",
    r"^\s*#include\b",
    r"^\s*#define\b",
    r"^\s*@[A-Za-z_][A-Za-z0-9_]*",
    r"^\s*if __name__ == ['\"]__main__['\"]:\s*$",
]

COMMENT_PREFIXES = ("#", "//", "/*", "*", "--")
DEFAULT_CONFIG_FILES = [".mapper.json", ".claude/mapper/config.json"]


def load_scan(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to read scan file: {e}", file=sys.stderr)
        sys.exit(1)


def load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def find_config(root: Path, override_path: Optional[str], disabled: bool) -> Optional[Path]:
    if disabled:
        return None
    if override_path:
        path = Path(override_path)
        if path.is_absolute():
            return path if path.exists() else None
        candidate = root / path
        return candidate if candidate.exists() else None
    for name in DEFAULT_CONFIG_FILES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def normalize_patterns(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def coerce_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def coerce_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def read_text(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def matches_any(line: str, patterns: list) -> bool:
    import re

    for pattern in patterns:
        if re.search(pattern, line):
            return True
    return False


def is_comment(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in COMMENT_PREFIXES)


def build_keep_mask(lines: list[str], keep_head: int, keep_tail: int, comment_backtrack: int) -> list[bool]:
    count = len(lines)
    keep = [False] * count

    for i in range(min(keep_head, count)):
        keep[i] = True
    for i in range(max(0, count - keep_tail), count):
        keep[i] = True

    for idx, line in enumerate(lines):
        if matches_any(line, KEEP_PATTERNS):
            keep[idx] = True
            back = idx - 1
            remaining = comment_backtrack
            while back >= 0 and remaining > 0 and is_comment(lines[back]):
                keep[back] = True
                back -= 1
                remaining -= 1

    return keep


def render_lines(lines: list[str], keep: Optional[list[bool]], line_numbers: bool) -> list[str]:
    if keep is None:
        return [format_line(i, line, line_numbers) for i, line in enumerate(lines)]

    rendered = []
    last_kept = False
    for idx, line in enumerate(lines):
        if keep[idx]:
            rendered.append(format_line(idx, line, line_numbers))
            last_kept = True
        else:
            if last_kept:
                rendered.append("...")
            last_kept = False
    if rendered and rendered[-1] == "...":
        rendered.pop()
    return rendered


def format_line(index: int, line: str, line_numbers: bool) -> str:
    if not line_numbers:
        return line
    return f"{index + 1:>5} | {line}"


def filter_paths(paths: list[dict], include: list[str], exclude: list[str]) -> list[dict]:
    result = []
    for entry in paths:
        path = entry.get("path", "")
        if include and not any(fnmatch.fnmatch(path, pattern) for pattern in include):
            continue
        if exclude and any(fnmatch.fnmatch(path, pattern) for pattern in exclude):
            continue
        result.append(entry)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate skeleton views of files from a scan JSON"
    )
    parser.add_argument(
        "scan",
        help="Path to scan JSON",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output markdown path (default: .claude/mapper/skeleton.md)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (default: auto-detect .mapper.json)",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore config file",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=None,
        help="Include full file if lines <= this (default: 400)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Include full file if bytes <= this (default: 200000)",
    )
    parser.add_argument(
        "--keep-head",
        type=int,
        default=None,
        help="Always keep first N lines (default: 30)",
    )
    parser.add_argument(
        "--keep-tail",
        type=int,
        default=None,
        help="Always keep last N lines (default: 20)",
    )
    parser.add_argument(
        "--comment-backtrack",
        type=int,
        default=None,
        help="Keep up to N comment lines above matched lines (default: 2)",
    )
    parser.add_argument(
        "--min-keep-lines",
        type=int,
        default=None,
        help="Fallback to full if skeleton keeps fewer than this many lines (default: 40)",
    )
    parser.add_argument(
        "--min-keep-ratio",
        type=float,
        default=None,
        help="Fallback to full if kept lines ratio is below this (default: 0.05)",
    )
    parser.add_argument(
        "--line-numbers",
        action="store_true",
        default=None,
        help="Include line numbers",
    )
    parser.add_argument(
        "--only-skeleton",
        action="store_true",
        default=None,
        help="Always skeletonize (never include full files)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="Glob pattern of files to include (can be repeated)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob pattern of files to exclude (can be repeated)",
    )

    args = parser.parse_args()
    scan_path = Path(args.scan)
    data = load_scan(scan_path)

    root = Path(data.get("root", ".")).resolve()
    config_path = find_config(root, args.config, args.no_config)
    config = load_json(config_path) if config_path else {}
    skeleton_cfg = config.get("skeleton", {}) if isinstance(config.get("skeleton"), dict) else {}

    max_lines = args.max_lines if args.max_lines is not None else coerce_int(skeleton_cfg.get("max_lines"), 400)
    max_bytes = args.max_bytes if args.max_bytes is not None else coerce_int(skeleton_cfg.get("max_bytes"), 200000)
    keep_head = args.keep_head if args.keep_head is not None else coerce_int(skeleton_cfg.get("keep_head"), 30)
    keep_tail = args.keep_tail if args.keep_tail is not None else coerce_int(skeleton_cfg.get("keep_tail"), 20)
    comment_backtrack = args.comment_backtrack if args.comment_backtrack is not None else coerce_int(skeleton_cfg.get("comment_backtrack"), 2)
    min_keep_lines = args.min_keep_lines if args.min_keep_lines is not None else coerce_int(skeleton_cfg.get("min_keep_lines"), 40)
    min_keep_ratio = args.min_keep_ratio if args.min_keep_ratio is not None else coerce_float(skeleton_cfg.get("min_keep_ratio"), 0.05)
    line_numbers = args.line_numbers if args.line_numbers is not None else coerce_bool(skeleton_cfg.get("line_numbers"), False)
    only_skeleton = args.only_skeleton if args.only_skeleton is not None else coerce_bool(skeleton_cfg.get("only_skeleton"), False)

    include_patterns = args.include if args.include is not None else normalize_patterns(skeleton_cfg.get("include") or config.get("include"))
    exclude_patterns = args.exclude if args.exclude is not None else normalize_patterns(skeleton_cfg.get("exclude") or config.get("exclude"))

    out_value = args.out if args.out is not None else skeleton_cfg.get("out")
    out_path = Path(out_value) if out_value else root / ".claude" / "mapper" / "skeleton.md"
    if not out_path.is_absolute():
        out_path = root / out_path

    files = filter_paths(data.get("files", []), include_patterns, exclude_patterns)
    if not files:
        print("ERROR: Scan file has no files after filtering", file=sys.stderr)
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    full_count = 0
    skeleton_count = 0
    missing = 0

    lines_out = []
    lines_out.append("# Codebase Skeleton")
    lines_out.append("")
    lines_out.append(f"Root: {root}")
    lines_out.append(f"Scan: {scan_path}")
    lines_out.append("")

    for entry in files:
        rel_path = entry.get("path")
        if not rel_path:
            continue
        total += 1
        file_path = root / rel_path
        if not file_path.exists():
            missing += 1
            continue
        text = read_text(file_path)
        try:
            file_bytes = file_path.stat().st_size
        except Exception:
            file_bytes = len(text.encode("utf-8"))
        file_lines = text.splitlines()
        line_count = len(file_lines)

        include_full = (
            not only_skeleton
            and line_count <= max_lines
            and file_bytes <= max_bytes
        )

        if include_full:
            full_count += 1
            keep = None
            mode = "full"
            kept_count = line_count
        else:
            keep = build_keep_mask(
                file_lines,
                keep_head,
                keep_tail,
                comment_backtrack,
            )
            kept_count = sum(1 for value in keep if value)
            too_thin = (
                not only_skeleton
                and (kept_count < min_keep_lines or kept_count < line_count * min_keep_ratio)
            )
            if too_thin:
                full_count += 1
                keep = None
                mode = "full-fallback"
                kept_count = line_count
            else:
                skeleton_count += 1
                mode = "skeleton"

        lines_out.append(f"## {rel_path} ({mode}, lines {line_count}, kept {kept_count})")
        lines_out.append("")
        lines_out.append("```")
        lines_out.extend(render_lines(file_lines, keep, line_numbers))
        lines_out.append("```")
        lines_out.append("")

    lines_out.append("## Summary")
    lines_out.append("")
    lines_out.append(f"Files: {total}")
    lines_out.append(f"Full: {full_count}")
    lines_out.append(f"Skeleton: {skeleton_count}")
    lines_out.append(f"Missing: {missing}")

    out_path.write_text("\n".join(lines_out), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
