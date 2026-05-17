"""Tests for shell tool (run_command)."""

import asyncio
import os
import tempfile

import pytest

from lambda_coding_agent.tools.shell import run_command


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace."""
    return str(tmp_path)


class TestRunCommand:
    async def test_simple_echo(self, workspace):
        result = await run_command("echo hello", workspace=workspace)
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert result["timed_out"] is False

    async def test_exit_code_nonzero(self, workspace):
        result = await run_command("exit 1", workspace=workspace)
        assert result["exit_code"] == 1

    async def test_stderr_capture(self, workspace):
        result = await run_command("echo err >&2", workspace=workspace)
        assert "err" in result["stderr"]

    async def test_timeout(self, workspace):
        result = await run_command("sleep 10", workspace=workspace, timeout=1)
        assert result["timed_out"] is True

    async def test_cwd(self, workspace):
        subdir = os.path.join(workspace, "sub")
        os.makedirs(subdir)
        result = await run_command("pwd", workspace=workspace, cwd="sub")
        assert "sub" in result["stdout"]

    async def test_cwd_absolute_fallback(self, workspace):
        result = await run_command("pwd", workspace=workspace)
        assert workspace in result["stdout"]

    async def test_output_truncation(self, workspace):
        # Generate large output
        result = await run_command(
            "python3 -c \"print('x' * 100000)\"", workspace=workspace
        )
        assert result["exit_code"] == 0
        # stdout should be truncated
        assert result["truncated"] is True or len(result["stdout"]) <= 80000

    async def test_duration_ms(self, workspace):
        result = await run_command("echo fast", workspace=workspace)
        assert "duration_ms" in result
        assert result["duration_ms"] >= 0

    async def test_env_inherited(self, workspace):
        result = await run_command("echo $HOME", workspace=workspace)
        assert result["stdout"].strip() != ""
        assert result["stdout"].strip() != "$HOME"
