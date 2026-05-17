"""Tests for glob tool (find_files)."""

import os

import pytest

from lambda_coding_agent.tools.glob_tool import find_files


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with various files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("# main")
    (tmp_path / "src" / "utils.py").write_text("# utils")
    (tmp_path / "src" / "sub").mkdir()
    (tmp_path / "src" / "sub" / "deep.py").write_text("# deep")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("# test")
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("gitconfig")
    return str(tmp_path)


class TestFindFiles:
    async def test_find_all_python(self, workspace):
        results = await find_files("**/*.py", workspace=workspace)
        assert len(results) >= 3
        assert any("main.py" in r for r in results)
        assert any("deep.py" in r for r in results)

    async def test_find_in_subpath(self, workspace):
        results = await find_files("**/*.py", path="src", workspace=workspace)
        assert any("main.py" in r for r in results)
        assert not any("test_main.py" in r for r in results)

    async def test_find_markdown(self, workspace):
        results = await find_files("*.md", workspace=workspace)
        assert any("README.md" in r for r in results)

    async def test_excludes_git_dir(self, workspace):
        results = await find_files("**/*", workspace=workspace)
        assert not any(".git" in r for r in results)

    async def test_returns_sorted(self, workspace):
        results = await find_files("**/*.py", workspace=workspace)
        assert results == sorted(results)

    async def test_cap_results(self, workspace):
        # Create many files
        many_dir = os.path.join(workspace, "many")
        os.makedirs(many_dir)
        for i in range(250):
            with open(os.path.join(many_dir, f"f{i}.txt"), "w") as f:
                f.write(f"file {i}")

        results = await find_files("**/*.txt", workspace=workspace)
        assert len(results) <= 200
