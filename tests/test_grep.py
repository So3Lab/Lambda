"""Tests for grep tool (search)."""

import os

import pytest

from lambda_coding_agent.tools.grep import search


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with searchable files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "import os\n\ndef main():\n    print('hello world')\n    return 0\n"
    )
    (tmp_path / "src" / "utils.py").write_text(
        "def helper():\n    return 'utility'\n\ndef another():\n    pass\n"
    )
    (tmp_path / "README.md").write_text("# Project\n\nThis is a hello world project.\n")
    return str(tmp_path)


class TestSearch:
    async def test_simple_pattern(self, workspace):
        result = await search("hello", workspace=workspace)
        assert "main.py" in result
        assert "hello" in result

    async def test_regex_pattern(self, workspace):
        result = await search(r"def \w+\(\)", workspace=workspace)
        assert "main" in result
        assert "helper" in result

    async def test_path_scope(self, workspace):
        result = await search("hello", path="src", workspace=workspace)
        assert "main.py" in result
        assert "README" not in result

    async def test_glob_filter(self, workspace):
        result = await search("hello", glob="*.md", workspace=workspace)
        assert "README.md" in result
        assert "main.py" not in result

    async def test_no_matches(self, workspace):
        result = await search("nonexistent_string_xyz", workspace=workspace)
        assert result == "" or "no matches" in result.lower()

    async def test_context_lines(self, workspace):
        result = await search("hello", workspace=workspace, context=1)
        # Should include lines around the match
        assert len(result.split("\n")) > 1

    async def test_result_cap(self, workspace):
        # Create file with many matches
        many = os.path.join(workspace, "many.txt")
        with open(many, "w") as f:
            for i in range(200):
                f.write(f"match_target line {i}\n")

        result = await search("match_target", workspace=workspace)
        # Should be capped
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) <= 600  # 100 matches * ~5 context lines each
