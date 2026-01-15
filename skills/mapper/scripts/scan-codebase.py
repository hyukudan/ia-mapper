#!/usr/bin/env python3
"""
Mapper codebase scanner.
Scans a directory tree, respects gitignore (when available), and outputs file paths
with token counts. Includes caching and git-aware file listing for faster runs.
"""

import argparse
import concurrent.futures
import fnmatch
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    tiktoken = None
    TIKTOKEN_AVAILABLE = False

try:
    import pathspec
except ImportError:
    pathspec = None

DEFAULT_IGNORE_PATTERNS = [
    "**/.git/",
    "**/.svn/",
    "**/.hg/",
    "**/.idea/",
    "**/.vscode/",
    "**/.cache/",
    "**/node_modules/",
    "**/__pycache__/",
    "**/.pytest_cache/",
    "**/.mypy_cache/",
    "**/.ruff_cache/",
    "**/venv/",
    "**/.venv/",
    "**/env/",
    "**/.env/",
    "**/dist/",
    "**/build/",
    "**/.next/",
    "**/.nuxt/",
    "**/.output/",
    "**/coverage/",
    "**/.nyc_output/",
    "**/target/",
    "**/vendor/",
    "**/.bundle/",
    "**/.cargo/",
    "**/.gradle/",
    "**/.turbo/",
    "**/.parcel-cache/",
    "**/.vercel/",
    "**/.svelte-kit/",
    "**/.serverless/",
    "**/.terraform/",
    "**/Pods/",
    "**/*.pyc",
    "**/*.pyo",
    "**/*.so",
    "**/*.dylib",
    "**/*.dll",
    "**/*.exe",
    "**/*.o",
    "**/*.a",
    "**/*.lib",
    "**/*.class",
    "**/*.jar",
    "**/*.war",
    "**/*.egg",
    "**/*.whl",
    "**/*.lock",
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/bun.lockb",
    "**/Cargo.lock",
    "**/poetry.lock",
    "**/Gemfile.lock",
    "**/composer.lock",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.map",
    "**/*.chunk.js",
    "**/*.bundle.js",
    "**/*.bundle.css",
    "**/*.log",
    "**/*.tmp",
    "**/*.temp",
    "**/*.bak",
    "**/*.swp",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.ico",
    "**/*.svg",
    "**/*.webp",
    "**/*.mp3",
    "**/*.mp4",
    "**/*.wav",
    "**/*.avi",
    "**/*.mov",
    "**/*.pdf",
    "**/*.zip",
    "**/*.tar",
    "**/*.gz",
    "**/*.rar",
    "**/*.7z",
    "**/*.woff",
    "**/*.woff2",
    "**/*.ttf",
    "**/*.eot",
    "**/*.otf",
]

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".vue",
    ".svelte",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".md",
    ".mdx",
    ".txt",
    ".rst",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".graphql",
    ".gql",
    ".proto",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".clj",
    ".cljs",
    ".edn",
    ".ex",
    ".exs",
    ".erl",
    ".hrl",
    ".hs",
    ".lhs",
    ".ml",
    ".mli",
    ".fs",
    ".fsx",
    ".fsi",
    ".cs",
    ".vb",
    ".swift",
    ".m",
    ".mm",
    ".h",
    ".hpp",
    ".c",
    ".cpp",
    ".cc",
    ".cxx",
    ".r",
    ".jl",
    ".lua",
    ".vim",
    ".el",
    ".lisp",
    ".scm",
    ".rkt",
    ".zig",
    ".nim",
    ".d",
    ".dart",
    ".v",
    ".sv",
    ".vhd",
    ".vhdl",
    ".tf",
    ".hcl",
    ".dockerfile",
    ".containerfile",
    ".makefile",
    ".cmake",
    ".gradle",
    ".groovy",
    ".rake",
    ".gemspec",
    ".podspec",
    ".cabal",
    ".nix",
    ".dhall",
    ".jsonc",
    ".json5",
    ".cson",
    ".ini",
    ".cfg",
    ".conf",
    ".config",
    ".env",
    ".env.example",
    ".env.local",
    ".env.development",
    ".env.production",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".prettierrc",
    ".eslintrc",
    ".stylelintrc",
    ".babelrc",
    ".nvmrc",
    ".ruby-version",
    ".python-version",
    ".node-version",
    ".tool-versions",
}

TEXT_NAMES = {
    "readme",
    "license",
    "licence",
    "changelog",
    "authors",
    "contributors",
    "copying",
    "dockerfile",
    "containerfile",
    "makefile",
    "rakefile",
    "gemfile",
    "procfile",
    "brewfile",
    "vagrantfile",
    "justfile",
    "taskfile",
}

CACHE_VERSION = 1
DEFAULT_CONFIG_FILES = [".mapper.json", ".claude/mapper/config.json"]
ENCODING_CACHE: dict[str, "tiktoken.Encoding"] = {}

ENTRYPOINT_PATTERNS = [
    "main.*",
    "index.*",
    "app.*",
    "server.*",
    "cli.*",
    "cmd/*/main.go",
    "cmd/*/main.rs",
    "cmd/*/main.py",
    "bin/*",
    "src/main.*",
    "src/index.*",
    "src/app.*",
    "src/server.*",
]


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


def git_head(root: Path) -> Optional[str]:
    if not git_available():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def git_branch(root: Path) -> Optional[str]:
    if not git_available():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def git_dirty(root: Path) -> Optional[bool]:
    if not git_available():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return bool(result.stdout.strip())
    except Exception:
        return None


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


def group_label(path: str, depth: int) -> str:
    parts = Path(path).parts
    if len(parts) <= depth:
        key = "/".join(parts)
    else:
        key = "/".join(parts[:depth])
    if not key:
        key = "."
    return key


def load_changed_list(path: Optional[str]) -> set[str]:
    if not path:
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
    except Exception:
        return set()
    return {line.lstrip("./") for line in lines if line}


def coerce_int(value, default: int) -> int:
    try:
        return int(value)
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


def read_ignore_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    patterns = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except Exception:
        return []
    return patterns


def build_ignore_spec(root: Path, include_gitignore: bool) -> tuple[Optional["pathspec.PathSpec"], list[str]]:
    patterns = list(DEFAULT_IGNORE_PATTERNS)
    if include_gitignore:
        patterns.extend(read_ignore_file(root / ".gitignore"))
        patterns.extend(read_ignore_file(root / ".git" / "info" / "exclude"))
    if pathspec is None:
        return None, patterns
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns), patterns


def matches_simple_pattern(rel_path: str, pattern: str) -> bool:
    if pattern.startswith("!"):
        return False
    if pattern.endswith("/"):
        if not rel_path.endswith("/"):
            return False
        pattern = pattern[:-1]
    if pattern.startswith("/"):
        pattern = pattern[1:]
    return fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(rel_path, f"**/{pattern}")


def should_ignore(rel_path: str, spec: Optional["pathspec.PathSpec"], extra_patterns: list[str]) -> bool:
    if spec is not None:
        if spec.match_file(rel_path):
            return True
    else:
        for pattern in DEFAULT_IGNORE_PATTERNS + extra_patterns:
            if matches_simple_pattern(rel_path, pattern):
                return True
    return False


def is_text_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True

    name = path.name.lower()
    if name in TEXT_NAMES:
        return True

    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return False
            try:
                chunk.decode("utf-8")
                return True
            except UnicodeDecodeError:
                return False
    except Exception:
        return False


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="ignore")


def heuristic_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def count_tokens(text: str, tokenizer: str, encoding_name: str) -> int:
    if tokenizer != "tiktoken":
        return heuristic_tokens(text)
    try:
        encoding = get_encoding(encoding_name)
        return len(encoding.encode(text))
    except Exception:
        return heuristic_tokens(text)


def get_encoding(name: str) -> "tiktoken.Encoding":
    if not TIKTOKEN_AVAILABLE:
        raise RuntimeError("tiktoken not available")
    encoding = ENCODING_CACHE.get(name)
    if encoding is None:
        encoding = tiktoken.get_encoding(name)
        ENCODING_CACHE[name] = encoding
    return encoding


def process_file_task(args: tuple[str, str, str, str, str, int]) -> dict:
    rel_path, abs_path, encoding_name, tokenizer, hash_mode, max_file_tokens = args
    try:
        data = read_bytes(Path(abs_path))
    except Exception as e:
        return {"path": rel_path, "error": f"read_error: {e}"}

    if b"\x00" in data:
        return {"path": rel_path, "skip": "binary"}

    text = decode_text(data)
    try:
        tokens = count_tokens(text, tokenizer, encoding_name)
    except Exception as e:
        return {"path": rel_path, "error": f"token_error: {e}"}

    if tokens > max_file_tokens:
        return {"path": rel_path, "skip": "too_many_tokens", "tokens": tokens}

    content_hash = None
    if hash_mode == "full":
        content_hash = hash_bytes(data)
    elif hash_mode == "fast":
        content_hash = hash_data_fast(data)

    return {"path": rel_path, "tokens": tokens, "content_hash": content_hash}


def hash_bytes(data: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(data)
    return hasher.hexdigest()


def hash_data_fast(data: bytes) -> str:
    size = len(data)
    head = data[:4096]
    tail = data[-4096:] if size > 8192 else b""
    hasher = hashlib.sha256()
    hasher.update(str(size).encode("utf-8"))
    hasher.update(head)
    hasher.update(tail)
    return hasher.hexdigest()


def default_cache_path(root: Path) -> Path:
    return root / ".claude" / "mapper" / "scan-cache.json"


def open_cache_file(path: Path, mode: str, compress: bool):
    if compress or path.suffix == ".gz":
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def load_cache(
    path: Path,
    encoding_name: str,
    tokenizer: str,
    max_file_tokens: int,
    max_file_size: int,
    hash_mode: str,
    cache_compress: bool,
) -> dict:
    if not path.exists():
        return {"version": CACHE_VERSION, "files": {}}
    try:
        with open_cache_file(path, "rt", cache_compress) as f:
            data = json.load(f)
        if data.get("version") != CACHE_VERSION:
            return {"version": CACHE_VERSION, "files": {}}
        if data.get("encoding") != encoding_name:
            return {"version": CACHE_VERSION, "files": {}}
        if data.get("tokenizer") != tokenizer:
            return {"version": CACHE_VERSION, "files": {}}
        if data.get("max_file_tokens") != max_file_tokens:
            return {"version": CACHE_VERSION, "files": {}}
        if data.get("max_file_size") != max_file_size:
            return {"version": CACHE_VERSION, "files": {}}
        if data.get("hash_mode") != hash_mode:
            return {"version": CACHE_VERSION, "files": {}}
        if data.get("cache_compress") != cache_compress:
            return {"version": CACHE_VERSION, "files": {}}
        if "files" not in data:
            return {"version": CACHE_VERSION, "files": {}}
        return data
    except Exception:
        return {"version": CACHE_VERSION, "files": {}}


def save_cache(path: Path, data: dict, cache_compress: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_cache_file(path, "wt", cache_compress) as f:
        json.dump(data, f, indent=2)


def normalize_git_pattern(pattern: str, exclude: bool) -> str:
    pattern = pattern.strip()
    if not pattern:
        return ""
    if pattern.startswith("!"):
        pattern = pattern[1:]
    if pattern.startswith(":"):
        return pattern
    if exclude:
        return f":(exclude,glob){pattern}"
    return f":(glob){pattern}"


def build_git_pathspec(include_patterns: list[str], exclude_patterns: list[str]) -> list[str]:
    specs: list[str] = []
    for pattern in include_patterns:
        spec = normalize_git_pattern(pattern, exclude=False)
        if spec:
            specs.append(spec)
    for pattern in exclude_patterns:
        spec = normalize_git_pattern(pattern, exclude=True)
        if spec:
            specs.append(spec)
    return specs


def iter_files_git(root: Path, pathspecs: Optional[list[str]] = None) -> Iterable[Path]:
    cmd = ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"]
    if pathspecs:
        cmd.append("--")
        cmd.extend(pathspecs)
    output = subprocess.check_output(cmd)
    for rel_path in output.decode("utf-8", errors="ignore").split("\0"):
        if rel_path:
            yield root / rel_path


def iter_files_fs(root: Path, spec: Optional["pathspec.PathSpec"], ignore_patterns: list[str], follow_symlinks: bool) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        else:
            rel_dir = rel_dir.replace(os.sep, "/")

        filtered = []
        for d in dirnames:
            rel_path = f"{rel_dir}/{d}" if rel_dir else d
            rel_path = rel_path.replace(os.sep, "/") + "/"
            if should_ignore(rel_path, spec, ignore_patterns):
                continue
            filtered.append(d)
        dirnames[:] = filtered

        for filename in filenames:
            rel_path = f"{rel_dir}/{filename}" if rel_dir else filename
            rel_path = rel_path.replace(os.sep, "/")
            if should_ignore(rel_path, spec, ignore_patterns):
                continue
            yield Path(dirpath) / filename


def compute_scan_hash(files: list[dict]) -> str:
    hasher = hashlib.sha256()
    for entry in sorted(files, key=lambda x: x["path"]):
        hasher.update(entry["path"].encode("utf-8"))
        hasher.update(str(entry.get("size_bytes", 0)).encode("utf-8"))
        hasher.update(str(entry.get("mtime", 0)).encode("utf-8"))
        hasher.update(str(entry.get("tokens", 0)).encode("utf-8"))
        if entry.get("content_hash"):
            hasher.update(str(entry.get("content_hash", "")).encode("utf-8"))
    return hasher.hexdigest()


def compute_module_hashes(files: list[dict], depth: int) -> dict[str, str]:
    groups: dict[str, list[dict]] = {}
    for entry in files:
        label = group_label(entry["path"], depth)
        groups.setdefault(label, []).append(entry)

    hashes: dict[str, str] = {}
    for label, entries in groups.items():
        hasher = hashlib.sha256()
        for entry in sorted(entries, key=lambda x: x["path"]):
            hasher.update(entry["path"].encode("utf-8"))
            if entry.get("content_hash"):
                hasher.update(entry["content_hash"].encode("utf-8"))
            else:
                hasher.update(str(entry.get("size_bytes", 0)).encode("utf-8"))
                hasher.update(str(entry.get("mtime", 0)).encode("utf-8"))
                hasher.update(str(entry.get("tokens", 0)).encode("utf-8"))
        hashes[label] = hasher.hexdigest()
    return hashes


def detect_entrypoints(files: list[dict], limit: int) -> list[dict]:
    entrypoints = []
    for f in files:
        path = f["path"]
        for pattern in ENTRYPOINT_PATTERNS:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"**/{pattern}"):
                entrypoints.append({
                    "path": path,
                    "tokens": f.get("tokens", 0),
                    "reason": f"pattern:{pattern}",
                })
                break
    entrypoints = sorted(entrypoints, key=lambda x: x.get("tokens", 0), reverse=True)
    if limit > 0:
        entrypoints = entrypoints[:limit]
    return entrypoints


def top_files_by_tokens(files: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return []
    return sorted(files, key=lambda x: x.get("tokens", 0), reverse=True)[:limit]


def git_churn(root: Path, commits: int, file_set: set[str]) -> list[dict]:
    if commits <= 0 or not is_git_repo(root) or not git_available():
        return []
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "-n",
                str(commits),
                "--name-only",
                "--pretty=format:",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return []

    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        path = line.strip()
        if not path:
            continue
        if file_set and path not in file_set:
            continue
        counts[path] = counts.get(path, 0) + 1

    churn = [{"path": path, "commits": count} for path, count in counts.items()]
    churn.sort(key=lambda x: x["commits"], reverse=True)
    return churn


def git_changed_files(
    root: Path,
    range_spec: Optional[str],
    since_commit: Optional[str],
    since_date: Optional[str],
    include_untracked: bool,
) -> list[str]:
    if not is_git_repo(root):
        return []

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
        return []

    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        files = []

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

    deduped = []
    seen = set()
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def scan_directory(
    root: Path,
    encoding_name: str,
    tokenizer: str,
    max_file_tokens: int,
    max_file_size: int,
    use_git: bool,
    follow_symlinks: bool,
    include_patterns: list[str],
    exclude_patterns: list[str],
    changed_paths: set[str],
    changed_scope: str,
    changed_labels: set[str],
    changed_depth: int,
    git_pathspec: bool,
    workers: int,
    hash_mode: str,
    churn_commits: int,
    entrypoints_limit: int,
    top_files_limit: int,
    module_depth: int,
    prev_scan_path: Optional[Path],
    cache_path: Optional[Path],
    cache_enabled: bool,
    cache_compress: bool,
) -> dict:
    start = time.time()
    root = root.resolve()

    spec, ignore_patterns = build_ignore_spec(root, include_gitignore=not use_git)
    extra_patterns = exclude_patterns

    cache = {"version": CACHE_VERSION, "files": {}}
    if cache_enabled and cache_path is not None:
        cache = load_cache(
            cache_path,
            encoding_name,
            tokenizer,
            max_file_tokens,
            max_file_size,
            hash_mode,
            cache_compress,
        )
    cache_files = cache.get("files", {})
    new_cache_files = {}

    files = []
    skipped = []
    total_tokens = 0
    cache_hits = 0
    cache_misses = 0
    tasks: list[tuple[str, str, str, str, str, int]] = []
    task_meta: dict[str, dict] = {}

    git_used = use_git
    git_specs = None
    if git_used and git_pathspec:
        git_specs = build_git_pathspec(include_patterns, exclude_patterns)

    if git_used and changed_paths and changed_scope == "files":
        file_iter = (root / path for path in sorted(changed_paths))
    elif git_used:
        try:
            file_iter = iter_files_git(root, git_specs)
        except Exception:
            git_used = False
            file_iter = iter_files_fs(root, spec, ignore_patterns + extra_patterns, follow_symlinks)
    else:
        file_iter = iter_files_fs(root, spec, ignore_patterns + extra_patterns, follow_symlinks)

    for path in file_iter:
        try:
            rel_path = path.relative_to(root).as_posix()
        except Exception:
            continue

        if should_ignore(rel_path, spec, ignore_patterns):
            continue

        if changed_paths:
            if changed_scope == "files":
                if rel_path not in changed_paths:
                    continue
            else:
                if group_label(rel_path, changed_depth) not in changed_labels:
                    continue

        if include_patterns:
            if not any(fnmatch.fnmatch(rel_path, pattern) for pattern in include_patterns):
                continue
        if exclude_patterns:
            if any(fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_patterns):
                continue

        try:
            stat = path.stat()
        except Exception:
            skipped.append({"path": rel_path, "reason": "stat_error"})
            continue

        if not follow_symlinks and path.is_symlink():
            skipped.append({"path": rel_path, "reason": "symlink"})
            continue

        if stat.st_size > max_file_size:
            skipped.append({"path": rel_path, "reason": "too_large", "size_bytes": stat.st_size})
            continue

        if not is_text_file(path):
            skipped.append({"path": rel_path, "reason": "binary"})
            continue

        cache_entry = cache_files.get(rel_path)
        cache_hit = (
            cache_entry
            and cache_entry.get("mtime") == stat.st_mtime
            and cache_entry.get("size") == stat.st_size
        )
        needs_read = (
            not cache_hit
            or hash_mode == "full"
            or (hash_mode != "mtime" and cache_entry and not cache_entry.get("content_hash"))
        )

        if not needs_read and cache_entry:
            tokens = cache_entry.get("tokens", 0)
            content_hash = cache_entry.get("content_hash") if hash_mode != "mtime" else None
            cache_hits += 1

            entry = {
                "path": rel_path,
                "tokens": tokens,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
            }
            if content_hash:
                entry["content_hash"] = content_hash
            files.append(entry)
            total_tokens += tokens

            if cache_enabled and cache_path is not None:
                new_cache_files[rel_path] = {
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                    "tokens": tokens,
                }
                if content_hash:
                    new_cache_files[rel_path]["content_hash"] = content_hash
        else:
            task_meta[rel_path] = {
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
            tasks.append((rel_path, str(path), encoding_name, tokenizer, hash_mode, max_file_tokens))

    if tasks:
        if workers and workers > 1:
            chunksize = max(1, len(tasks) // (workers * 4))
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
                results = executor.map(process_file_task, tasks, chunksize=chunksize)
                for result in results:
                    cache_misses += 1
                    rel_path = result.get("path")
                    if not rel_path:
                        continue
                    meta = task_meta.get(rel_path, {})
                    if result.get("error"):
                        skipped.append({"path": rel_path, "reason": result["error"]})
                        continue
                    if result.get("skip"):
                        reason = result["skip"]
                        record = {"path": rel_path, "reason": reason}
                        if reason == "too_many_tokens":
                            record["tokens"] = result.get("tokens", 0)
                        skipped.append(record)
                        continue

                    tokens = result.get("tokens", 0)
                    entry = {
                        "path": rel_path,
                        "tokens": tokens,
                        "size_bytes": meta.get("size", 0),
                        "mtime": meta.get("mtime", 0),
                    }
                    content_hash = result.get("content_hash")
                    if content_hash:
                        entry["content_hash"] = content_hash
                    files.append(entry)
                    total_tokens += tokens

                    if cache_enabled and cache_path is not None:
                        new_cache_files[rel_path] = {
                            "mtime": meta.get("mtime", 0),
                            "size": meta.get("size", 0),
                            "tokens": tokens,
                        }
                        if content_hash:
                            new_cache_files[rel_path]["content_hash"] = content_hash
        else:
            for task in tasks:
                result = process_file_task(task)
                cache_misses += 1
                rel_path = result.get("path")
                if not rel_path:
                    continue
                meta = task_meta.get(rel_path, {})
                if result.get("error"):
                    skipped.append({"path": rel_path, "reason": result["error"]})
                    continue
                if result.get("skip"):
                    reason = result["skip"]
                    record = {"path": rel_path, "reason": reason}
                    if reason == "too_many_tokens":
                        record["tokens"] = result.get("tokens", 0)
                    skipped.append(record)
                    continue

                tokens = result.get("tokens", 0)
                entry = {
                    "path": rel_path,
                    "tokens": tokens,
                    "size_bytes": meta.get("size", 0),
                    "mtime": meta.get("mtime", 0),
                }
                content_hash = result.get("content_hash")
                if content_hash:
                    entry["content_hash"] = content_hash
                files.append(entry)
                total_tokens += tokens

                if cache_enabled and cache_path is not None:
                    new_cache_files[rel_path] = {
                        "mtime": meta.get("mtime", 0),
                        "size": meta.get("size", 0),
                        "tokens": tokens,
                    }
                    if content_hash:
                        new_cache_files[rel_path]["content_hash"] = content_hash

    scan_hash = compute_scan_hash(files)
    elapsed_ms = int((time.time() - start) * 1000)

    directories = sorted(
        {
            str(Path(f["path"]).parent)
            for f in files
            if str(Path(f["path"]).parent) != "."
        }
    )

    file_set = {f["path"] for f in files}
    entrypoints = detect_entrypoints(files, entrypoints_limit)
    top_files = top_files_by_tokens(files, top_files_limit)
    churn = git_churn(root, churn_commits, file_set)
    module_hashes = compute_module_hashes(files, module_depth)
    changed_modules = []
    removed_modules = []
    if prev_scan_path:
        prev_data = load_json(prev_scan_path)
        prev_hashes = prev_data.get("module_hashes", {})
        if isinstance(prev_hashes, dict):
            for label, digest in module_hashes.items():
                if prev_hashes.get(label) != digest:
                    changed_modules.append(label)
            for label in prev_hashes.keys():
                if label not in module_hashes:
                    removed_modules.append(label)

    result = {
        "root": str(root),
        "files": files,
        "directories": directories,
        "total_tokens": total_tokens,
        "total_files": len(files),
        "skipped": skipped,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_enabled": cache_enabled,
        "cache_path": str(cache_path) if cache_enabled and cache_path is not None else None,
        "cache_compress": cache_compress,
        "scan_hash": scan_hash,
        "scan_created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_used": git_used,
        "git_head": git_head(root) if git_used else None,
        "git_branch": git_branch(root) if git_used else None,
        "git_dirty": git_dirty(root) if git_used else None,
        "git_pathspec": git_pathspec if git_used else None,
        "git_available": git_available(),
        "git_repo": is_git_repo(root),
        "workers": workers,
        "tokenizer": tokenizer,
        "hash_mode": hash_mode,
        "churn_commits": churn_commits,
        "changed_scope": changed_scope if changed_paths else None,
        "changed_depth": changed_depth if changed_paths else None,
        "changed_count": len(changed_paths),
        "entrypoints": entrypoints,
        "entrypoints_limit": entrypoints_limit,
        "top_files": top_files,
        "top_files_limit": top_files_limit,
        "churn": churn,
        "module_depth": module_depth,
        "module_hashes": module_hashes,
        "changed_modules": changed_modules if prev_scan_path else None,
        "removed_modules": removed_modules if prev_scan_path else None,
        "prev_scan": str(prev_scan_path) if prev_scan_path else None,
        "elapsed_ms": elapsed_ms,
    }

    if cache_enabled and cache_path is not None:
        cache.update({
            "encoding": encoding_name,
            "tokenizer": tokenizer,
            "max_file_tokens": max_file_tokens,
            "max_file_size": max_file_size,
            "hash_mode": hash_mode,
            "cache_compress": cache_compress,
            "files": new_cache_files,
        })
        save_cache(cache_path, cache, cache_compress)

    return result


def format_tree(scan_result: dict, show_tokens: bool = True) -> str:
    lines = []
    root_name = Path(scan_result["root"]).name
    lines.append(f"{root_name}/")
    lines.append(
        f"Total: {scan_result['total_files']} files, {scan_result['total_tokens']:,} tokens"
    )
    lines.append("")

    tree: dict = {}
    for f in scan_result["files"]:
        parts = Path(f["path"]).parts
        current = tree
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = f

    def print_tree(node: dict, prefix: str = ""):
        items = sorted(node.items(), key=lambda x: (not isinstance(x[1], dict) or "tokens" in x[1], x[0].lower()))

        for i, (name, value) in enumerate(items):
            is_last_item = i == len(items) - 1
            connector = "└── " if is_last_item else "├── "

            if isinstance(value, dict) and "tokens" not in value:
                lines.append(f"{prefix}{connector}{name}/")
                extension = "    " if is_last_item else "│   "
                print_tree(value, prefix + extension)
            else:
                if show_tokens:
                    tokens = value.get("tokens", 0)
                    lines.append(f"{prefix}{connector}{name} ({tokens:,} tokens)")
                else:
                    lines.append(f"{prefix}{connector}{name}")

    print_tree(tree)
    return "\n".join(lines)


def format_summary(scan_result: dict) -> str:
    lines = []
    lines.append(f"Root: {scan_result['root']}")
    lines.append(f"Files: {scan_result['total_files']}")
    lines.append(f"Tokens: {scan_result['total_tokens']:,}")
    lines.append(f"Scan hash: {scan_result['scan_hash']}")
    lines.append(f"Git used: {scan_result['git_used']}")
    if scan_result.get("git_head"):
        lines.append(f"Git head: {scan_result['git_head']}")
    if scan_result.get("git_branch"):
        lines.append(f"Git branch: {scan_result['git_branch']}")
    if scan_result.get("git_dirty") is not None:
        lines.append(f"Git dirty: {scan_result['git_dirty']}")
    lines.append(f"Cache hits: {scan_result['cache_hits']}")
    lines.append(f"Cache misses: {scan_result['cache_misses']}")
    lines.append(f"Cache compress: {scan_result.get('cache_compress')}")
    lines.append(f"Tokenizer: {scan_result.get('tokenizer')}")
    lines.append(f"Hash mode: {scan_result.get('hash_mode')}")
    lines.append(f"Workers: {scan_result.get('workers')}")
    lines.append(f"Modules: {len(scan_result.get('module_hashes', {}))}")
    lines.append(f"Entrypoints: {len(scan_result.get('entrypoints', []))}")
    lines.append(f"Top files: {len(scan_result.get('top_files', []))}")
    if scan_result.get("changed_modules") is not None:
        lines.append(f"Changed modules: {len(scan_result.get('changed_modules', []))}")
    lines.append(f"Elapsed ms: {scan_result['elapsed_ms']}")
    return "\n".join(lines)


def write_output(output: str, out_path: Optional[Path]) -> None:
    if out_path is None:
        print(output)
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan a codebase and output file paths with token counts"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to scan (default: current directory)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "tree", "compact", "summary"],
        default="json",
        help="Output format (default: json)",
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
        "--max-tokens",
        type=int,
        default=None,
        help="Skip files with more than this many tokens",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=None,
        help="Skip files larger than this many bytes",
    )
    parser.add_argument(
        "--encoding",
        default=None,
        help="Tiktoken encoding to use",
    )
    parser.add_argument(
        "--tokenizer",
        choices=["tiktoken", "heuristic"],
        default=None,
        help="Token counting mode (tiktoken|heuristic)",
    )
    parser.add_argument(
        "--use-git",
        dest="use_git",
        action="store_true",
        help="Use git to list files (respects gitignore)",
    )
    parser.add_argument(
        "--no-git",
        dest="use_git",
        action="store_false",
        help="Do not use git to list files",
    )
    parser.set_defaults(use_git=None)
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks when walking the filesystem",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Glob pattern of files to include (can be repeated)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob pattern of files to exclude (can be repeated)",
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
        "--changed-range",
        default=None,
        help="Git diff range (e.g. abc123..HEAD) to derive changed files",
    )
    parser.add_argument(
        "--changed-since-commit",
        default=None,
        help="Base commit to derive changed files",
    )
    parser.add_argument(
        "--changed-since-date",
        default=None,
        help="Since date to derive changed files (e.g. '2024-01-01')",
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Include untracked files when using git-changed flags",
    )
    parser.add_argument(
        "--changed-scope",
        choices=["files", "modules"],
        default=None,
        help="Limit scan to changed files or their modules",
    )
    parser.add_argument(
        "--changed-depth",
        type=int,
        default=None,
        help="Module depth when using --changed-scope modules (default: 1)",
    )
    parser.add_argument(
        "--module-depth",
        type=int,
        default=None,
        help="Module depth for module hashes (default: 1)",
    )
    parser.add_argument(
        "--prev-scan",
        default=None,
        help="Previous scan JSON path to compute changed modules",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel workers for tokenization (default: 0)",
    )
    parser.add_argument(
        "--git-pathspec",
        action="store_true",
        default=None,
        help="Use git pathspec to prefilter include/exclude patterns",
    )
    parser.add_argument(
        "--no-git-pathspec",
        action="store_true",
        default=None,
        help="Disable git pathspec prefiltering",
    )
    parser.add_argument(
        "--hash-mode",
        choices=["mtime", "fast", "full"],
        default=None,
        help="Content hash mode for scan stability (mtime|fast|full)",
    )
    parser.add_argument(
        "--cache-compress",
        action="store_true",
        default=None,
        help="Compress cache file with gzip",
    )
    parser.add_argument(
        "--churn-commits",
        type=int,
        default=None,
        help="Compute churn hotspots from last N commits",
    )
    parser.add_argument(
        "--entrypoints-limit",
        type=int,
        default=None,
        help="Limit entrypoint candidates (0 disables)",
    )
    parser.add_argument(
        "--top-files",
        type=int,
        default=None,
        help="Limit top files by tokens (0 disables)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable scan cache",
    )
    parser.add_argument(
        "--cache-path",
        default=None,
        help="Path to cache file (default: .claude/mapper/scan-cache.json)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write output to a file instead of stdout",
    )

    args = parser.parse_args()
    path = Path(args.path).resolve()

    if not path.exists():
        print(f"ERROR: Path does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    if not path.is_dir():
        print(f"ERROR: Path is not a directory: {path}", file=sys.stderr)
        sys.exit(1)

    config_path = find_config(path, args.config, args.no_config)
    config = load_json(config_path) if config_path else {}

    max_file_tokens = args.max_tokens
    if max_file_tokens is None:
        max_file_tokens = coerce_int(config.get("max_tokens"), 50000)

    max_file_size = args.max_size
    if max_file_size is None:
        max_file_size = coerce_int(config.get("max_size"), 1_000_000)

    encoding_name = args.encoding or config.get("encoding") or "cl100k_base"
    tokenizer = args.tokenizer or config.get("tokenizer") or "tiktoken"

    if tokenizer == "tiktoken":
        if not TIKTOKEN_AVAILABLE:
            print(
                "ERROR: tiktoken not installed. Install it or use --tokenizer heuristic.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            get_encoding(encoding_name)
        except Exception as e:
            print(f"ERROR: Failed to load encoding '{encoding_name}': {e}", file=sys.stderr)
            sys.exit(1)

    hash_mode = args.hash_mode or config.get("hash_mode") or "mtime"
    churn_commits = args.churn_commits
    if churn_commits is None:
        churn_commits = coerce_int(config.get("churn_commits"), 0)

    entrypoints_limit = args.entrypoints_limit
    if entrypoints_limit is None:
        entrypoints_limit = coerce_int(config.get("entrypoints_limit"), 20)

    top_files_limit = args.top_files
    if top_files_limit is None:
        top_files_limit = coerce_int(config.get("top_files"), 20)

    include_patterns = normalize_patterns(config.get("include")) + args.include
    exclude_patterns = normalize_patterns(config.get("exclude")) + args.exclude

    changed_paths = load_changed_list(args.changed_list)
    changed_paths.update(path.lstrip("./") for path in args.changed if path)

    changed_range = args.changed_range or config.get("changed_range")
    changed_since_commit = args.changed_since_commit or config.get("changed_since_commit")
    changed_since_date = args.changed_since_date or config.get("changed_since_date")
    include_untracked = args.include_untracked or coerce_bool(config.get("include_untracked"), False)

    if changed_range or changed_since_commit or changed_since_date:
        changed_paths.update(
            git_changed_files(
                path,
                changed_range,
                changed_since_commit,
                changed_since_date,
                include_untracked,
            )
        )

    changed_scope = args.changed_scope or config.get("changed_scope") or "files"
    changed_depth = args.changed_depth if args.changed_depth is not None else coerce_int(config.get("changed_depth"), 1)
    changed_labels = set()
    if changed_paths and changed_scope == "modules":
        changed_labels = {group_label(path, changed_depth) for path in changed_paths}

    follow_symlinks = args.follow_symlinks or coerce_bool(config.get("follow_symlinks"), False)

    use_git = args.use_git
    if use_git is None:
        if "use_git" in config:
            use_git = coerce_bool(config.get("use_git"), False)
        else:
            use_git = is_git_repo(path) and git_available()
    if use_git and not is_git_repo(path):
        use_git = False

    git_pathspec = True
    if args.no_git_pathspec:
        git_pathspec = False
    elif args.git_pathspec:
        git_pathspec = True
    else:
        git_pathspec = coerce_bool(config.get("git_pathspec"), True)
    if not use_git:
        git_pathspec = False

    workers = args.workers if args.workers is not None else coerce_int(config.get("workers"), 0)
    if workers < 0:
        workers = 0
    cpu_count = os.cpu_count() or 1
    if workers > cpu_count:
        workers = cpu_count

    module_depth = args.module_depth if args.module_depth is not None else coerce_int(config.get("module_depth"), 1)
    if module_depth < 1:
        module_depth = 1
    prev_scan_value = args.prev_scan or config.get("prev_scan")
    prev_scan_path = None
    if prev_scan_value:
        prev_scan_path = Path(prev_scan_value)
        if not prev_scan_path.is_absolute():
            prev_scan_path = path / prev_scan_path

    cache_enabled = not args.no_cache and coerce_bool(config.get("cache"), True)
    cache_path_value = args.cache_path or config.get("cache_path")
    if cache_path_value:
        cache_path = Path(cache_path_value)
        if not cache_path.is_absolute():
            cache_path = path / cache_path
    else:
        cache_path = default_cache_path(path)
    cache_compress = args.cache_compress if args.cache_compress is not None else coerce_bool(config.get("cache_compress"), False)
    if cache_path.suffix == ".gz":
        cache_compress = True

    result = scan_directory(
        root=path,
        encoding_name=encoding_name,
        tokenizer=tokenizer,
        max_file_tokens=max_file_tokens,
        max_file_size=max_file_size,
        use_git=use_git,
        follow_symlinks=follow_symlinks,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        changed_paths=changed_paths,
        changed_scope=changed_scope,
        changed_labels=changed_labels,
        changed_depth=changed_depth,
        git_pathspec=git_pathspec,
        workers=workers,
        hash_mode=hash_mode,
        churn_commits=churn_commits,
        entrypoints_limit=entrypoints_limit,
        top_files_limit=top_files_limit,
        module_depth=module_depth,
        prev_scan_path=prev_scan_path,
        cache_path=cache_path,
        cache_enabled=cache_enabled,
        cache_compress=cache_compress,
    )
    result["config_path"] = str(config_path) if config_path else None

    if args.format == "json":
        output = json.dumps(result, indent=2)
    elif args.format == "tree":
        output = format_tree(result, show_tokens=True)
    elif args.format == "summary":
        output = format_summary(result)
    else:
        files_sorted = sorted(result["files"], key=lambda x: x["tokens"], reverse=True)
        lines = []
        lines.append(f"# {result['root']}")
        lines.append(f"# Total: {result['total_files']} files, {result['total_tokens']:,} tokens")
        lines.append("")
        for f in files_sorted:
            lines.append(f"{f['tokens']:>8} {f['path']}")
        output = "\n".join(lines)

    out_path = Path(args.out) if args.out else None
    write_output(output, out_path)


if __name__ == "__main__":
    main()
