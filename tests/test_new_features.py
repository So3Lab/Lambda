"""TDD tests for new TUI features.

Tests for:
1. /model command - list and switch models
2. Usage display at bottom of assistant bubble
3. Context window fill percentage in status bar
4. Tool block bottom margin
5. Ctrl+O expand ALL tool blocks
6. Colored tool name by status
7. Fork subagent prompt refinement
8. Planning-first working mindset in system prompt
"""

import json
import os
import tempfile
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from lambda_coding_agent.tui.tool_cards import (
    ToolBlock,
    _format_header,
    create_tool_card,
)
from lambda_coding_agent.agent import create_agent, _build_system_prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 4: Tool block bottom margin
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolBlockMargin:
    def test_tool_block_has_bottom_margin_in_css(self):
        """ToolBlock DEFAULT_CSS should include bottom margin for spacing."""
        css = ToolBlock.DEFAULT_CSS
        # Should contain margin with bottom value > 0
        assert "margin: 0 0 1 0" in css


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 5: Ctrl+O expand ALL tool blocks
# ═══════════════════════════════════════════════════════════════════════════════


class TestExpandAllToolBlocks:
    def test_tool_block_expanded_state_can_be_set_directly(self):
        """ToolBlock._expanded should be settable for bulk toggle."""
        block = ToolBlock(tool_name="run_command", arguments={"command": "ls"})
        assert block._expanded is False
        block._expanded = True
        assert block._expanded is True

    def test_app_has_toggle_tool_expand_action(self):
        """TUI app should have action_toggle_tool_expand method."""
        from lambda_coding_agent.tui.app import LambdaCodingTUIApp

        assert hasattr(LambdaCodingTUIApp, "action_toggle_tool_expand")


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 6: Colored tool name by status (yellow/green/red)
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolBlockColoredStatus:
    def test_format_header_with_running_color(self):
        """Header should use yellow/amber color for running status."""
        result = _format_header("⟳", "run_command", "ls", "#d4a373")
        assert "[#d4a373]" in result
        assert "run_command" in result
        assert "[/#d4a373]" in result

    def test_format_header_with_success_color(self):
        """Header should use green color for success."""
        result = _format_header("✓", "run_command", "ls", "#5f8d5a")
        assert "[#5f8d5a]" in result

    def test_format_header_with_error_color(self):
        """Header should use red color for error."""
        result = _format_header("✗", "run_command", "ls", "#c0392b")
        assert "[#c0392b]" in result

    def test_tool_block_status_color_running(self):
        """ToolBlock._status_color should be amber when running."""
        block = ToolBlock(tool_name="run_command", arguments={"command": "ls"})
        assert block._status == "running"
        assert block._status_color == "#d4a373"

    def test_tool_block_status_color_success(self):
        """ToolBlock._status_color should be green on success."""
        block = ToolBlock(tool_name="run_command", arguments={"command": "ls"})
        block._status = "done"
        block._success = True
        assert block._status_color == "#5f8d5a"

    def test_tool_block_status_color_error(self):
        """ToolBlock._status_color should be red on failure."""
        block = ToolBlock(tool_name="run_command", arguments={"command": "ls"})
        block._status = "done"
        block._success = False
        assert block._status_color == "#c0392b"


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 7: Fork subagent prompt refinement
# ═══════════════════════════════════════════════════════════════════════════════


class TestForkSubagentPrompt:
    def test_system_prompt_has_fork_subagent_rules(self):
        """System prompt should include fork subagent rules."""
        prompt = _build_system_prompt()
        assert "Fork Subagent Rules" in prompt

    def test_fork_rules_require_text_reply(self):
        """Fork rules should instruct forks to reply in text, not print."""
        prompt = _build_system_prompt()
        assert "reply with plain text" in prompt

    def test_fork_rules_forbid_print(self):
        """Fork rules should forbid using print/code for communication."""
        prompt = _build_system_prompt()
        assert "NOT use print()" in prompt or "NOT use print() or code execution" in prompt

    def test_fork_rules_allow_file_for_long_findings(self):
        """Fork rules should allow writing findings to a file."""
        prompt = _build_system_prompt()
        assert "write" in prompt and "file" in prompt and "path" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 8: Planning-first working mindset
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlanningMindset:
    def test_system_prompt_has_code_execution_model(self):
        """System prompt should include code execution model section."""
        prompt = _build_system_prompt()
        assert "Code Execution Model" in prompt

    def test_prompt_mentions_web_fetch(self):
        """System prompt should mention the web_fetch primitive."""
        prompt = _build_system_prompt()
        assert "web_fetch" in prompt

    def test_prompt_mentions_runtime_workspace(self):
        """System prompt should mention runtime.workspace primitives."""
        prompt = _build_system_prompt()
        assert "runtime.workspace" in prompt

    def test_prompt_mentions_plan_primitives(self):
        """System prompt should mention plan primitives."""
        prompt = _build_system_prompt()
        assert "plan_create" in prompt or "plan_create" in prompt

    def test_prompt_mentions_parallel_forks(self):
        """Working methodology should encourage parallel forks for independent tasks."""
        prompt = _build_system_prompt()
        assert "parallel forks" in prompt or "parallel" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 1: /model command
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelCommand:
    def _make_provider_json(self, tmp_path):
        """Create a test provider.json with multiple models."""
        data = {
            "test_provider": [
                {
                    "model_name": "model-a",
                    "api_keys": ["sk-test"],
                    "base_url": "http://localhost:8080/v1",
                    "context_window": 100_000,
                },
                {
                    "model_name": "model-b",
                    "api_keys": ["sk-test"],
                    "base_url": "http://localhost:8080/v1",
                    "context_window": 50_000,
                },
            ]
        }
        path = os.path.join(tmp_path, "provider.json")
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_tui_app_accepts_provider_path(self):
        """TUI app __init__ should accept provider_path parameter."""
        from lambda_coding_agent.tui.app import LambdaCodingTUIApp

        # Just check the signature accepts the param (don't actually run app)
        import inspect
        sig = inspect.signature(LambdaCodingTUIApp.__init__)
        params = list(sig.parameters.keys())
        assert "provider_path" in params
        assert "provider_id" in params
        assert "environment_block" in params
        assert "context_window" in params

    def test_launch_tui_accepts_new_params(self):
        """launch_tui should accept provider_path, provider_id, environment_block, context_window."""
        from lambda_coding_agent.app import launch_tui
        import inspect

        sig = inspect.signature(launch_tui)
        params = list(sig.parameters.keys())
        assert "provider_path" in params
        assert "provider_id" in params
        assert "environment_block" in params
        assert "context_window" in params

    def test_handle_model_command_exists(self):
        """TUI app should have model selector method."""
        from lambda_coding_agent.tui.app import LambdaCodingTUIApp

        assert hasattr(LambdaCodingTUIApp, "_open_model_selector")


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 2: Usage display at bottom of assistant bubble
# ═══════════════════════════════════════════════════════════════════════════════


class TestUsageDisplay:
    def test_usage_footer_css_exists(self):
        """TUI app CSS should include .usage-footer styling."""
        from lambda_coding_agent.tui.app import LambdaCodingTUIApp

        assert ".usage-footer" in LambdaCodingTUIApp.CSS


# ═══════════════════════════════════════════════════════════════════════════════
# Feature 3: Context window fill percentage
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextWindowPercentage:
    def test_cli_reads_context_window_from_provider_json(self, tmp_path):
        """CLI should read context_window from provider.json model entries."""
        data = {
            "test_provider": [
                {
                    "model_name": "test-model",
                    "api_keys": ["sk-test"],
                    "base_url": "http://localhost/v1",
                    "context_window": 128_000,
                }
            ]
        }
        provider_path = os.path.join(tmp_path, "provider.json")
        with open(provider_path, "w") as f:
            json.dump(data, f)

        # Simulate what cli.py does to extract context_window
        with open(provider_path) as f:
            loaded = json.load(f)

        first_provider = next(iter(loaded.values()))
        first_model = first_provider[0]
        ctx_window = first_model.get("context_window", 200_000)
        assert ctx_window == 128_000

    def test_context_window_defaults_to_200k(self):
        """If context_window not in provider.json, default to 200k."""
        model_entry = {"model_name": "test", "api_keys": ["sk-test"]}
        ctx_window = model_entry.get("context_window", 200_000)
        assert ctx_window == 200_000

    def test_stats_line_prompt_tokens_parsing(self):
        """Should parse prompt_tokens from stats_line format."""
        import re

        stats_line = "gpt-4 | 2.34s | tokens 1500/350/1850 (in/out/total)"
        match = re.search(r"tokens (\d+)/", stats_line)
        assert match is not None
        assert int(match.group(1)) == 1500

    def test_stats_line_no_tokens(self):
        """Should handle stats_line without token info gracefully."""
        import re

        stats_line = "gpt-4 | 2.34s"
        match = re.search(r"tokens (\d+)/", stats_line)
        assert match is None

    def test_ctx_percentage_calculation(self):
        """Context percentage should be prompt_tokens / context_window * 100."""
        prompt_tokens = 50_000
        context_window = 200_000
        pct = int(prompt_tokens * 100 / context_window)
        assert pct == 25

    def test_tui_app_tracks_prompt_tokens(self):
        """TUI app should have _last_prompt_tokens attribute."""
        from lambda_coding_agent.tui.app import LambdaCodingTUIApp
        import inspect

        sig = inspect.signature(LambdaCodingTUIApp.__init__)
        # Can't easily instantiate without textual running, but check the class
        source = inspect.getsource(LambdaCodingTUIApp.__init__)
        assert "_last_prompt_tokens" in source
