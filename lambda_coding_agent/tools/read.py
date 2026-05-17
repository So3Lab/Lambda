"""Read file tool."""

import os

DEFAULT_LIMIT = 2000


async def read_file(
    file_path: str,
    workspace: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """Read file contents with line numbers.

    Args:
        file_path: Path relative to workspace.
        workspace: Absolute path to workspace root.
        offset: Start from this line (1-indexed).
        limit: Max lines to read.

    Returns:
        File content with line numbers, or error message.
    """
    abs_path = os.path.normpath(os.path.join(workspace, file_path))

    # Path traversal check
    if not abs_path.startswith(os.path.normpath(workspace)):
        return "Error: Path is outside workspace."

    if not os.path.exists(abs_path):
        return f"Error: File not found: {file_path}"

    if not os.path.isfile(abs_path):
        return f"Error: Not a file: {file_path}"

    # Binary detection
    try:
        with open(abs_path, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return f"Error: Binary file detected: {file_path}"
    except OSError as e:
        return f"Error: Cannot read file: {e}"

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"Error: Cannot read file: {e}"

    if limit is None:
        limit = DEFAULT_LIMIT

    start = (offset - 1) if offset and offset > 0 else 0
    end = start + limit

    selected = lines[start:end]

    result_lines = []
    for i, line in enumerate(selected, start=start + 1):
        result_lines.append(f"{i} | {line.rstrip()}")

    return "\n".join(result_lines)
