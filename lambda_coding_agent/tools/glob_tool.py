"""Glob tool - find files by pattern."""

import os
from pathlib import Path

MAX_RESULTS = 200

# Directories to always exclude
EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"}


async def find_files(
    pattern: str,
    workspace: str,
    path: str | None = None,
) -> list[str]:
    """Find files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g., '**/*.py').
        workspace: Absolute path to workspace root.
        path: Subdirectory to search in.

    Returns:
        Sorted list of relative file paths (capped at 200).
    """
    base = Path(workspace)
    if path:
        base = base / path

    if not base.exists():
        return []

    results = []
    for match in base.glob(pattern):
        # Skip excluded directories
        parts = match.relative_to(Path(workspace)).parts
        if any(p in EXCLUDE_DIRS for p in parts):
            continue

        if match.is_file():
            rel = str(match.relative_to(Path(workspace)))
            results.append(rel)

        if len(results) >= MAX_RESULTS:
            break

    results.sort()
    return results[:MAX_RESULTS]
