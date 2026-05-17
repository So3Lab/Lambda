"""Edit file tool - exact string match replacement."""

import difflib
import os

# Module-level undo stack
undo_stack: list[dict] = []


async def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    workspace: str,
) -> dict:
    """Replace an exact string in a file.

    Args:
        file_path: Path relative to workspace.
        old_string: Exact text to find (must appear exactly once).
        new_string: Replacement text.
        workspace: Absolute path to workspace root.

    Returns:
        Dict with success, diff, error.
    """
    abs_path = os.path.normpath(os.path.join(workspace, file_path))

    if not abs_path.startswith(os.path.normpath(workspace)):
        return {"success": False, "error": "Path is outside workspace.", "diff": ""}

    if not os.path.exists(abs_path):
        return {"success": False, "error": f"File not found: {file_path}", "diff": ""}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return {"success": False, "error": str(e), "diff": ""}

    count = content.count(old_string)
    if count == 0:
        return {"success": False, "error": "Old string not found in file.", "diff": ""}
    if count > 1:
        return {
            "success": False,
            "error": f"Multiple matches found ({count}). Include more context to make it unique.",
            "diff": "",
        }

    # Store undo
    undo_stack.append(
        {"file_path": file_path, "before_content": content, "abs_path": abs_path}
    )

    # Perform replacement
    new_content = content.replace(old_string, new_string, 1)

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as e:
        return {"success": False, "error": f"Write failed: {e}", "diff": ""}

    # Generate diff
    diff = difflib.unified_diff(
        content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3,
    )
    diff_str = "".join(diff)

    return {"success": True, "diff": diff_str, "error": ""}
