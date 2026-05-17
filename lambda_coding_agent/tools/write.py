"""Write file tool."""

import os


async def write_file(
    file_path: str,
    content: str,
    workspace: str,
    overwrite: bool = False,
) -> dict:
    """Create or overwrite a file.

    Args:
        file_path: Path relative to workspace.
        content: File content to write.
        workspace: Absolute path to workspace root.
        overwrite: Must be True to overwrite existing files.

    Returns:
        Dict with success and error.
    """
    abs_path = os.path.normpath(os.path.join(workspace, file_path))

    if not abs_path.startswith(os.path.normpath(workspace)):
        return {"success": False, "error": "Path is outside workspace."}

    if os.path.exists(abs_path) and not overwrite:
        return {
            "success": False,
            "error": f"File already exists: {file_path}. Set overwrite=True to replace.",
        }

    # Create parent dirs
    parent = os.path.dirname(abs_path)
    os.makedirs(parent, exist_ok=True)

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return {"success": False, "error": str(e)}

    return {"success": True, "error": ""}
