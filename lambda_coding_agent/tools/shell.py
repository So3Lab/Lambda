"""Shell tool - run commands in the workspace."""

import asyncio
import os
import time


MAX_OUTPUT_CHARS = 80000


async def run_command(
    command: str,
    workspace: str,
    cwd: str | None = None,
    timeout: int = 120,
) -> dict:
    """Run a shell command in the workspace.

    Args:
        command: Shell command to execute.
        workspace: Absolute path to workspace root.
        cwd: Subdirectory relative to workspace. Defaults to workspace root.
        timeout: Max seconds before killing.

    Returns:
        Dict with stdout, stderr, exit_code, timed_out, duration_ms, truncated.
    """
    if cwd:
        work_dir = os.path.join(workspace, cwd)
    else:
        work_dir = workspace

    if not os.path.isdir(work_dir):
        return {
            "stdout": "",
            "stderr": f"Directory not found: {work_dir}",
            "exit_code": 1,
            "timed_out": False,
            "duration_ms": 0,
            "truncated": False,
        }

    start = time.time()
    timed_out = False

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=os.environ.copy(),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            timed_out = True
            stdout_bytes = b""
            stderr_bytes = b"Command timed out"

    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": 1,
            "timed_out": False,
            "duration_ms": int((time.time() - start) * 1000),
            "truncated": False,
        }

    duration_ms = int((time.time() - start) * 1000)

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    truncated = len(stdout) > MAX_OUTPUT_CHARS
    if truncated:
        stdout = stdout[:MAX_OUTPUT_CHARS] + "\n... [output truncated]"

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": proc.returncode if not timed_out else -1,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "truncated": truncated,
    }
