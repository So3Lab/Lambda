"""Tests for write_file tool."""

import os

import pytest

from lambda_coding_agent.tools.write import write_file


@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path)


class TestWriteFile:
    async def test_create_new_file(self, workspace):
        result = await write_file(
            file_path="new.py",
            content="print('hello')\n",
            workspace=workspace,
        )
        assert result["success"] is True
        assert os.path.exists(os.path.join(workspace, "new.py"))
        assert open(os.path.join(workspace, "new.py")).read() == "print('hello')\n"

    async def test_create_with_subdirs(self, workspace):
        result = await write_file(
            file_path="src/lib/mod.py",
            content="# module\n",
            workspace=workspace,
        )
        assert result["success"] is True
        assert os.path.exists(os.path.join(workspace, "src/lib/mod.py"))

    async def test_overwrite_existing_requires_flag(self, workspace):
        path = os.path.join(workspace, "exist.py")
        with open(path, "w") as f:
            f.write("old content")

        result = await write_file(
            file_path="exist.py",
            content="new content",
            workspace=workspace,
            overwrite=False,
        )
        assert result["success"] is False
        # File should be unchanged
        assert open(path).read() == "old content"

    async def test_overwrite_existing_with_flag(self, workspace):
        path = os.path.join(workspace, "exist.py")
        with open(path, "w") as f:
            f.write("old content")

        result = await write_file(
            file_path="exist.py",
            content="new content",
            workspace=workspace,
            overwrite=True,
        )
        assert result["success"] is True
        assert open(path).read() == "new content"

    async def test_path_traversal_rejected(self, workspace):
        result = await write_file(
            file_path="../../evil.py",
            content="bad",
            workspace=workspace,
        )
        assert result["success"] is False
