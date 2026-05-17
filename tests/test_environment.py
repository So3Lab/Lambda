"""Tests for context/environment module."""

import os

import pytest

from lambda_coding_agent.context.environment import build_environment_block


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace simulating a Python project."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("import fastapi\n")
    # Init a git repo
    os.system(f"cd {tmp_path} && git init -q && git add . && git commit -q -m 'init' --allow-empty")
    return str(tmp_path)


class TestEnvironmentBlock:
    def test_contains_workspace_path(self, workspace):
        block = build_environment_block(workspace)
        assert workspace in block or "Workspace" in block

    def test_detects_python(self, workspace):
        block = build_environment_block(workspace)
        assert "python" in block.lower() or "Python" in block

    def test_contains_platform(self, workspace):
        block = build_environment_block(workspace)
        assert "Platform" in block or "platform" in block

    def test_detects_git(self, workspace):
        block = build_environment_block(workspace)
        assert "Git" in block or "git" in block
