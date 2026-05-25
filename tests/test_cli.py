"""Tests for CLI module."""

import io
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

    def test_main_rejects_one_shot_and_headless_together(self, tmp_path):
        with patch("sys.argv", [
            "lambda-agent",
            "--workspace",
            str(tmp_path),
            "--one-shot",
            "hello",
            "--headless",
            "hello",
        ]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 2

    def test_main_headless_creates_session_before_agent(self, tmp_path):
        with patch("sys.argv", [
            "lambda-agent",
            "--workspace",
            str(tmp_path),
            "--headless",
            "hello",
            "--events",
            "-",
        ]):
            with patch("lambda_coding_agent.cli.create_agent") as create_agent:
                create_agent.return_value = object()
                with patch("lambda_coding_agent.headless.run_headless_turn_sync") as run_headless:
                    main()

        kwargs = create_agent.call_args.kwargs
        assert kwargs["session_id"]
        run_kwargs = run_headless.call_args.kwargs
        assert run_kwargs["session"].session_id == kwargs["session_id"]
        assert run_kwargs["prompt"] == "hello"

    def test_main_headless_reads_prompt_from_stdin(self, tmp_path):
        with patch("sys.argv", [
            "lambda-agent",
            "--workspace",
            str(tmp_path),
            "--headless",
            "-",
        ]):
            with patch("sys.stdin", io.StringIO("from stdin")):
                with patch("lambda_coding_agent.cli.create_agent") as create_agent:
                    create_agent.return_value = object()
                    with patch("lambda_coding_agent.headless.run_headless_turn_sync") as run_headless:
                        main()

        assert run_headless.call_args.kwargs["prompt"] == "from stdin"
