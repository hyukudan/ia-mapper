#!/usr/bin/env python3
"""
Generate risk signals from a scan JSON.
"""

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_FILES = [".mapper.json", ".claude/mapper/config.json"]
DEFAULT_PATTERNS = ["TODO", "FIXME", "HACK", "XXX"]
DEFAULT_TEST_PATTERNS = [
    "tests/**",
    "test/**",
    "__tests__/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/test_*.js",
    "**/*_test.js",
    "**/*.spec.js",
    "**/*.test.js",
    "**/*.spec.ts",
    "**/*.test.ts",
    "**/*_spec.rb",
    "**/spec/**",
]


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


def filter_paths(files: list[dict], include: list[str], exclude: list[str]) -> list[dict]:
    result = []
    for entry in files:
        path = entry.get("path", "")
        if include and not any(fnmatch.fnmatch(path, pattern) for pattern in include):
            continue
        if exclude and any(fnmatch.fnmatch(path, pattern) for pattern in exclude):
            continue
        result.append(entry)
    return result


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def read_text(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def build_pattern_regex(patterns: list[str]) -> re.Pattern:
    escaped = [re.escape(pattern) for pattern in patterns if pattern]
    if not escaped:
        escaped = [re.escape(p) for p in DEFAULT_PATTERNS]
    joined = "|".join(escaped)
    return re.compile(r"\b(?:" + joined + r")\b", re.IGNORECASE)


def format_markdown(report: dict) -> str:
    lines = []
    lines.append("# Risk Signals")
    lines.append("")
    lines.append(f"Root: {report.get('root')}")
    lines.append(f"Scan: {report.get('scan')}")
    lines.append("")

    summary = report.get("summary", {})
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- TODO markers: {summary.get('todo_total', 0)} across {summary.get('todo_files', 0)} files")
    lines.append(f"- Large files by tokens: {summary.get('large_tokens', 0)}")
    lines.append(f"- Large files by size: {summary.get('large_size', 0)}")
    lines.append(f"- Tests detected: {summary.get('test_files', 0)}")
    lines.append(f"- No tests detected: {summary.get('no_tests_detected', False)}")
    lines.append(f"- Churn hotspots: {summary.get('churn_files', 0)}")
    lines.append("")

    if report.get("todo_markers"):
        lines.append("## TODO/FIXME/HACK/XXX")
        lines.append("")
        for item in report["todo_markers"]:
            lines.append(f"- {item['path']} ({item['count']})")
        lines.append("")

    if report.get("large_files_tokens"):
        lines.append("## Large Files (tokens)")
        lines.append("")
        for item in report["large_files_tokens"]:
            lines.append(f"- {item['path']} ({item['tokens']:,} tokens)")
        lines.append("")

    if report.get("large_files_size"):
        lines.append("## Large Files (size)")
        lines.append("")
        for item in report["large_files_size"]:
            lines.append(f"- {item['path']} ({item['size_bytes']:,} bytes)")
        lines.append("")

    if report.get("test_files"):
        lines.append("## Test Files")
        lines.append("")
        for path in report["test_files"]:
            lines.append(f"- {path}")
        lines.append("")

    if report.get("churn_hotspots"):
        lines.append("## Churn Hotspots")
        lines.append("")
        for item in report["churn_hotspots"]:
            lines.append(f"- {item['path']} ({item['commits']} commits)")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate risk signals from a scan JSON"
    )
    parser.add_argument("scan", help="Path to scan JSON")
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: docs/RISK_SIGNALS.md)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format (default: markdown)",
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
        "--pattern",
        action="append",
        default=None,
        help="Marker pattern to count (can be repeated)",
    )
    parser.add_argument(
        "--tokens-threshold",
        type=int,
        default=None,
        help="Large file threshold by tokens (default: 5000)",
    )
    parser.add_argument(
        "--size-threshold",
        type=int,
        default=None,
        help="Large file threshold by bytes (default: 200000)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Max items per section (default: 50)",
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
    parser.add_argument(
        "--test-pattern",
        action="append",
        default=None,
        help="Glob pattern to detect tests (can be repeated)",
    )

    args = parser.parse_args()
    scan_path = Path(args.scan)
    scan = load_json(scan_path)
    if not scan:
        print("ERROR: Failed to read scan JSON", file=sys.stderr)
        sys.exit(1)

    root = Path(scan.get("root", ".")).resolve()
    config_path = find_config(root, args.config, args.no_config)
    config = load_json(config_path) if config_path else {}
    risk_cfg = config.get("risk", {}) if isinstance(config.get("risk"), dict) else {}

    patterns = args.pattern if args.pattern is not None else normalize_patterns(risk_cfg.get("patterns"))
    if not patterns:
        patterns = DEFAULT_PATTERNS

    test_patterns = args.test_pattern if args.test_pattern is not None else normalize_patterns(risk_cfg.get("test_patterns"))
    if not test_patterns:
        test_patterns = DEFAULT_TEST_PATTERNS

    tokens_threshold = args.tokens_threshold if args.tokens_threshold is not None else coerce_int(risk_cfg.get("tokens_threshold"), 5000)
    size_threshold = args.size_threshold if args.size_threshold is not None else coerce_int(risk_cfg.get("size_threshold"), 200000)
    max_items = args.max_items if args.max_items is not None else coerce_int(risk_cfg.get("max_items"), 50)

    include_patterns = args.include if args.include is not None else normalize_patterns(risk_cfg.get("include") or config.get("include"))
    exclude_patterns = args.exclude if args.exclude is not None else normalize_patterns(risk_cfg.get("exclude") or config.get("exclude"))

    files = filter_paths(scan.get("files", []), include_patterns, exclude_patterns)
    if not files:
        print("ERROR: No files after filtering", file=sys.stderr)
        sys.exit(1)

    pattern_regex = build_pattern_regex(patterns)
    todo_markers = []
    todo_total = 0

    for entry in files:
        rel_path = entry.get("path")
        if not rel_path:
            continue
        file_path = root / rel_path
        if not file_path.exists():
            continue
        text = read_text(file_path)
        count = len(pattern_regex.findall(text))
        if count:
            todo_markers.append({"path": rel_path, "count": count})
            todo_total += count

    todo_markers.sort(key=lambda x: x["count"], reverse=True)
    if max_items > 0:
        todo_markers = todo_markers[:max_items]

    large_files_tokens = [
        {"path": f["path"], "tokens": f.get("tokens", 0)}
        for f in files
        if f.get("tokens", 0) >= tokens_threshold
    ]
    large_files_tokens.sort(key=lambda x: x["tokens"], reverse=True)
    if max_items > 0:
        large_files_tokens = large_files_tokens[:max_items]

    large_files_size = [
        {"path": f["path"], "size_bytes": f.get("size_bytes", 0)}
        for f in files
        if f.get("size_bytes", 0) >= size_threshold
    ]
    large_files_size.sort(key=lambda x: x["size_bytes"], reverse=True)
    if max_items > 0:
        large_files_size = large_files_size[:max_items]

    test_files = [f["path"] for f in files if matches_any(f.get("path", ""), test_patterns)]
    test_files.sort()
    if max_items > 0:
        test_files = test_files[:max_items]

    churn_hotspots = scan.get("churn", [])
    if isinstance(churn_hotspots, list) and max_items > 0:
        churn_hotspots = churn_hotspots[:max_items]

    summary = {
        "todo_total": todo_total,
        "todo_files": len(todo_markers),
        "large_tokens": len(large_files_tokens),
        "large_size": len(large_files_size),
        "test_files": len(test_files),
        "no_tests_detected": len(test_files) == 0,
        "churn_files": len(churn_hotspots) if isinstance(churn_hotspots, list) else 0,
    }

    report = {
        "root": str(root),
        "scan": str(scan_path),
        "summary": summary,
        "todo_markers": todo_markers,
        "large_files_tokens": large_files_tokens,
        "large_files_size": large_files_size,
        "test_files": test_files,
        "churn_hotspots": churn_hotspots if isinstance(churn_hotspots, list) else [],
    }

    out_path = Path(args.out) if args.out else root / "docs" / "RISK_SIGNALS.md"
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "json":
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        out_path.write_text(format_markdown(report), encoding="utf-8")

    print(str(out_path))


if __name__ == "__main__":
    main()
