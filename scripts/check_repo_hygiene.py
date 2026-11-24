#!/usr/bin/env python3
"""Check tracked Markdown links, long source files, and likely private data.

Only tracked files with the suffixes below are read. Markdown checks inline and
reference-definition local links outside fenced code blocks; plain prose and
generated or binary assets are deliberately out of scope. Symlinks resolving
outside the repository are not read. Findings never print the matched value.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


MAX_SOURCE_LINES = 1_500
MARKDOWN_SUFFIXES = {".md", ".markdown"}
SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".java",
    ".js", ".jsx", ".mjs", ".php", ".py", ".rb", ".rs", ".sh", ".swift",
    ".ts", ".tsx",
}
TEXT_SUFFIXES = MARKDOWN_SUFFIXES | SOURCE_SUFFIXES | {
    ".json", ".toml", ".txt", ".yaml", ".yml",
}
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^]]+\]:\s*(?:<([^>]+)>|(\S+))")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
SECRET_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,255}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,255}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,255}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{20,255}\b"),
)
PERSONAL_PATH = re.compile(
    r"(?<![\w/])/(?:" + "Users" + r"|home)/[A-Za-z0-9][A-Za-z0-9._-]*/"
    r"(?:Documents|Workspaces|repos|Library|Downloads|Desktop|\.codex|\.config)(?=$|[^A-Za-z0-9._-])"
)


def tracked_files(root: Path) -> list[Path]:
    """Return the current worktree's tracked paths, preserving unusual names."""
    result = subprocess.run(
        ["git", "-C", os.fspath(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [Path(os.fsdecode(path)) for path in result.stdout.split(b"\0") if path]


def markdown_lines(lines: list[str]):
    fenced = False
    marker = ""
    for number, line in enumerate(lines, start=1):
        fence = FENCE.match(line)
        if fence:
            token = fence.group(1)
            if not fenced:
                fenced, marker = True, token[0]
            elif token[0] == marker:
                fenced = False
            continue
        if not fenced:
            yield number, line


def local_target(raw: str) -> str | None:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith(("#", "/")) or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    return unquote(target.split("#", 1)[0].split("?", 1)[0]) or None


def tracked_directories(tracked: set[Path]) -> set[Path]:
    directories = {Path(".")}
    for path in tracked:
        parent = path.parent
        while parent != Path("."):
            directories.add(parent)
            parent = parent.parent
    return directories


def check_markdown(
    relative: Path,
    lines: list[str],
    tracked: set[Path],
    directories: set[Path],
) -> list[tuple[Path, int, str]]:
    findings = []
    for number, line in markdown_lines(lines):
        targets = [match.group(1) for match in INLINE_LINK.finditer(line)]
        reference = REFERENCE_LINK.match(line)
        if reference:
            targets.append(reference.group(1) or reference.group(2))
        for raw_target in targets:
            target = local_target(raw_target)
            if target is None:
                continue
            resolved = Path(posixpath.normpath(str(relative.parent / target)))
            if resolved not in tracked and resolved not in directories:
                findings.append((relative, number, "missing-markdown-target"))
    return findings


def check_file(
    root: Path,
    relative: Path,
    tracked: set[Path],
    directories: set[Path],
) -> list[tuple[Path, int, str]]:
    path = root / relative
    if path.is_symlink() and root not in path.resolve().parents:
        return []
    if not path.is_file():
        return [(relative, 0, "missing-tracked-file")]
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    findings = []
    if path.suffix.lower() in MARKDOWN_SUFFIXES:
        findings.extend(check_markdown(relative, lines, tracked, directories))
    if path.suffix.lower() in SOURCE_SUFFIXES and len(lines) > MAX_SOURCE_LINES:
        findings.append((relative, MAX_SOURCE_LINES + 1, "source-over-1500-lines"))
    for number, line in enumerate(lines, start=1):
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            findings.append((relative, number, "probable-secret"))
        if PERSONAL_PATH.search(line):
            findings.append((relative, number, "personal-absolute-path"))
    return findings


def check(root: Path, selected: set[Path] | None = None) -> list[tuple[Path, int, str]]:
    tracked = tracked_files(root)
    tracked_set = set(tracked)
    directories = tracked_directories(tracked_set)
    if selected is not None and not selected <= tracked_set:
        raise ValueError("selected paths must be tracked")
    if selected is not None:
        tracked = [path for path in tracked if path in selected]
    findings = []
    for relative in tracked:
        findings.extend(check_file(root, relative, tracked_set, directories))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Checks are read-only and offline. Markdown paths may use anchors, URL escapes, "
        "spaces, inline links, and reference definitions. Tracked repository directories are allowed. "
        "Use --path for a sparse-checkout subset; without it every tracked path is checked and unavailable paths fail distinctly.",
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root (default: this script's repository)")
    parser.add_argument("--path", action="append", type=Path, help="tracked relative path to check; repeat for a sparse-checkout subset")
    args = parser.parse_args(argv)
    try:
        findings = check(args.root.resolve(), set(args.path) if args.path else None)
    except subprocess.CalledProcessError as error:
        print(error.stderr.decode(errors="replace").strip() or "unable to list tracked files", file=sys.stderr)
        return 2
    except ValueError:
        print("invalid --path selection", file=sys.stderr)
        return 2
    for relative, line, category in findings:
        print(f"{relative}:{line}: {category}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
