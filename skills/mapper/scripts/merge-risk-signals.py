#!/usr/bin/env python3
"""
Merge docs/RISK_SIGNALS.md into docs/CODEBASE_MAP.md under a Risk Signals section.
"""

import argparse
import re
import sys
from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"ERROR: Failed to read {path}: {e}", file=sys.stderr)
        sys.exit(1)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"ERROR: Failed to write {path}: {e}", file=sys.stderr)
        sys.exit(1)


def normalize_heading(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().lower())


def find_heading(lines: list[str], title: str) -> int:
    needle = normalize_heading(f"## {title}")
    for idx, line in enumerate(lines):
        if normalize_heading(line) == needle:
            return idx
    return -1


def find_next_section(lines: list[str], start: int) -> int:
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## "):
            return idx
    return len(lines)


def adjust_headings(lines: list[str], increment: int) -> list[str]:
    adjusted = []
    for line in lines:
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if level > 0:
                new_level = min(6, level + increment)
                adjusted.append("#" * new_level + line[level:])
                continue
        adjusted.append(line)
    return adjusted


def build_risk_section(risk_text: str, source: str) -> list[str]:
    lines = risk_text.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    lines = adjust_headings(lines, 1)

    section = [
        "## Risk Signals",
        "",
        f"Generated from `{source}`.",
        "",
    ]
    section.extend(lines)
    if section and section[-1].strip():
        section.append("")
    return section


def merge_sections(map_text: str, risk_section: list[str]) -> str:
    lines = map_text.splitlines()
    if not lines:
        return "\n".join(risk_section).rstrip() + "\n"

    risk_idx = find_heading(lines, "Risk Signals")
    if risk_idx != -1:
        end = find_next_section(lines, risk_idx)
        merged = lines[:risk_idx] + risk_section + lines[end:]
        return "\n".join(merged).rstrip() + "\n"

    risks_idx = find_heading(lines, "Risks and Hotspots")
    if risks_idx != -1:
        end = find_next_section(lines, risks_idx)
        merged = lines[:end] + [""] + risk_section + lines[end:]
        return "\n".join(merged).rstrip() + "\n"

    nav_idx = find_heading(lines, "Navigation Guide")
    if nav_idx != -1:
        merged = lines[:nav_idx] + risk_section + lines[nav_idx:]
        return "\n".join(merged).rstrip() + "\n"

    return "\n".join(lines + [""] + risk_section).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge docs/RISK_SIGNALS.md into docs/CODEBASE_MAP.md"
    )
    parser.add_argument(
        "--map",
        default="docs/CODEBASE_MAP.md",
        help="Path to CODEBASE_MAP.md",
    )
    parser.add_argument(
        "--risk",
        default="docs/RISK_SIGNALS.md",
        help="Path to RISK_SIGNALS.md",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (default: overwrite map)",
    )

    args = parser.parse_args()
    map_path = Path(args.map)
    risk_path = Path(args.risk)

    if not map_path.exists():
        print(f"ERROR: Map file not found: {map_path}", file=sys.stderr)
        sys.exit(1)
    if not risk_path.exists():
        print(f"ERROR: Risk signals file not found: {risk_path}", file=sys.stderr)
        sys.exit(1)

    map_text = read_text(map_path)
    risk_text = read_text(risk_path)

    risk_section = build_risk_section(risk_text, str(risk_path))
    merged = merge_sections(map_text, risk_section)

    out_path = Path(args.out) if args.out else map_path
    write_text(out_path, merged)
    print(str(out_path))


if __name__ == "__main__":
    main()
