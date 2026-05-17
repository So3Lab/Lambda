"""Tests for edit_file tool."""

import os

import pytest

from lambda_coding_agent.tools.edit import edit_file, undo_stack


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with a sample file."""
    sample = tmp_path / "hello.py"
    sample.write_text('def greet():\n    return "hello"\n')
    return str(tmp_path)


@pytest.fixture(autouse=True)
def clear_undo():
    """Clear undo stack before each test."""
    undo_stack.clear()
    yield
    undo_stack.clear()


class TestEditFile:
    async def test_simple_replace(self, workspace):
        result = await edit_file(
            file_path="hello.py",
            old_string='    return "hello"',
            new_string='    return "world"',
            workspace=workspace,
        )
        assert result["success"] is True
        assert "diff" in result

        # Verify file content changed
        content = open(os.path.join(workspace, "hello.py")).read()
        assert '"world"' in content

    async def test_old_string_not_found(self, workspace):
        result = await edit_file(
            file_path="hello.py",
            old_string="this does not exist",
            new_string="replacement",
            workspace=workspace,
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    async def test_multiple_matches_fails(self, workspace):
        # Create file with duplicate content
        path = os.path.join(workspace, "dup.py")
        with open(path, "w") as f:
            f.write("x = 1\nx = 1\n")

        result = await edit_file(
            file_path="dup.py",
            old_string="x = 1",
            new_string="x = 2",
            workspace=workspace,
        )
        assert result["success"] is False
        assert "multiple" in result["error"].lower()

    async def test_file_not_found(self, workspace):
        result = await edit_file(
            file_path="nonexistent.py",
            old_string="x",
            new_string="y",
            workspace=workspace,
        )
        assert result["success"] is False

    async def test_undo_stack_populated(self, workspace):
        await edit_file(
            file_path="hello.py",
            old_string='    return "hello"',
            new_string='    return "world"',
            workspace=workspace,
        )
        assert len(undo_stack) == 1
        assert undo_stack[0]["file_path"] == "hello.py"
        assert '    return "hello"' in undo_stack[0]["before_content"]

    async def test_diff_in_result(self, workspace):
        result = await edit_file(
            file_path="hello.py",
            old_string='    return "hello"',
            new_string='    return "world"',
            workspace=workspace,
        )
        assert "-" in result["diff"] or "+" in result["diff"]
