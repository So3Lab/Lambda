"""Grep tool - search file contents."""

import os
import re
from pathlib import Path

MAX_MATCHES = 100

EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


async def search(
    pattern: str,
    workspace: str,
    path: str | None = None,
    glob: str | None = None,
    context: int = 2,
) -> str:
    """Search file contents using regex.

    Args:
        pattern: Regex pattern.
        workspace: Absolute path to workspace root.
        path: Subdirectory scope.
        glob: File pattern filter (e.g., '*.py').
        context: Lines of context around matches.

    Returns:
        Formatted match results or empty string.
    """
    base = Path(workspace)
    if path:
        base = base / path

    if not base.exists():
        return ""

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: Invalid regex: {e}"

    # Collect files to search
    file_pattern = glob if glob else "**/*"
    files = []
    for f in base.glob(file_pattern):
        if not f.is_file():
            continue
        parts = f.relative_to(Path(workspace)).parts
        if any(p in EXCLUDE_DIRS for p in parts):
            continue
        files.append(f)

    results = []
    match_count = 0

    for file_path in sorted(files):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = content.splitlines()
        file_matches = []

        for i, line in enumerate(lines):
            if regex.search(line):
                # Gather context
                start = max(0, i - context)
                end = min(len(lines), i + context + 1)
                for j in range(start, end):
                    prefix = ">" if j == i else " "
                    file_matches.append(f"{prefix} {j + 1}: {lines[j]}")
                file_matches.append("")  # separator
                match_count += 1

                if match_count >= MAX_MATCHES:
                    break

        if file_matches:
            rel = str(file_path.relative_to(Path(workspace)))
            results.append(f"--- {rel} ---")
            results.extend(file_matches)

        if match_count >= MAX_MATCHES:
            results.append(f"... (capped at {MAX_MATCHES} matches)")
            break

    return "\n".join(results)
