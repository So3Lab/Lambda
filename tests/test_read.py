"""Tests for read_file tool."""

import os

import pytest

from lambda_coding_agent.tools.read import read_file


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with sample files."""
    # Create a sample file
    sample = tmp_path / "sample.py"
    sample.write_text("line1\nline2\nline3\nline4\nline5\n")

    # Create a binary file
    binary = tmp_path / "image.bin"
    binary.write_bytes(b"\x00\x01\x02\xff\xfe")

    # Create a large file
    large = tmp_path / "large.txt"
    large.write_text("\n".join(f"line {i}" for i in range(1, 3001)))

    return str(tmp_path)


class TestReadFile:
    async def test_read_whole_file(self, workspace):
        result = await read_file("sample.py", workspace=workspace)
        assert "1 | line1" in result
        assert "5 | line5" in result

    async def test_read_with_offset(self, workspace):
        result = await read_file("sample.py", workspace=workspace, offset=3)
        assert "1 | line1" not in result
        assert "3 | line3" in result

    async def test_read_with_limit(self, workspace):
        result = await read_file("sample.py", workspace=workspace, limit=2)
        assert "1 | line1" in result
        assert "2 | line2" in result
        assert "3 | line3" not in result

    async def test_read_with_offset_and_limit(self, workspace):
        result = await read_file("sample.py", workspace=workspace, offset=2, limit=2)
        assert "2 | line2" in result
        assert "3 | line3" in result
        assert "4 | line4" not in result

    async def test_file_not_found(self, workspace):
        result = await read_file("nonexistent.py", workspace=workspace)
        assert "error" in result.lower() or "not found" in result.lower()

    async def test_binary_file_detection(self, workspace):
        result = await read_file("image.bin", workspace=workspace)
        assert "binary" in result.lower()

    async def test_path_traversal_rejected(self, workspace):
        result = await read_file("../../etc/passwd", workspace=workspace)
        assert "error" in result.lower() or "denied" in result.lower() or "outside" in result.lower()

    async def test_large_file_default_limit(self, workspace):
        result = await read_file("large.txt", workspace=workspace)
        lines = result.strip().split("\n")
        # Default limit should cap at 2000 lines
        assert len(lines) <= 2000
