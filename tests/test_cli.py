"""Tests for CLI module."""

import os
import sys
from unittest.mock import patch

import pytest

from lambda_coding_agent.cli import _resolve_workspace, main


class TestResolveWorkspace:
    def test_default_is_cwd(self):
        result = _resolve_workspace(None)
        assert result == os.getcwd()

    def test_expands_user(self):
        result = _resolve_workspace("~/tmp")
        assert result.startswith("/")
        assert "tmp" in result

    def test_absolute_path(self):
        result = _resolve_workspace("/tmp")
        assert result == "/tmp"


class TestCLI:
    def test_main_with_invalid_workspace(self, capsys):
        with patch("sys.argv", ["lambda-agent", "--workspace", "/nonexistent/path/12345"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_main_stub_agent_warning(self, tmp_path, capsys):
        # Run with a valid workspace but no provider.json
        with patch("sys.argv", ["lambda-agent", "--workspace", str(tmp_path)]):
            # We can't actually launch the TUI in tests, so we mock launch_tui
            with patch("lambda_coding_agent.cli.launch_tui"):
                with patch("lambda_coding_agent.cli.print"):
                    # main() calls launch_tui which we mock
                    try:
                        main()
                    except SystemExit:
                        pass
