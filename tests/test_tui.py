"""Tests for custom TUI components."""

import pytest

from lambda_coding_agent.tui.tool_cards import (
    create_tool_card,
    ToolBlock,
)
from lambda_coding_agent.tui.app import (
    LambdaCodingTUIApp,
    _is_fork_model_call_id,
    _extract_fork_id,
    _is_fork_tool_call_id,
    _extract_fork_id_from_tool,
)


class TestForkIdUtils:
    def test_is_fork_model_call_id_true(self):
        assert _is_fork_model_call_id("fork::abc123") is True

    def test_is_fork_model_call_id_false(self):
        assert _is_fork_model_call_id("regular-call-id") is False

    def test_extract_fork_id(self):
        assert _extract_fork_id("fork::abc123") == "abc123"
        assert _extract_fork_id("fork::my-fork-id-long") == "my-fork-id-long"

    def test_is_fork_tool_call_id_true(self):
        assert _is_fork_tool_call_id("fork::abc::tool::xyz") is True

    def test_is_fork_tool_call_id_false(self):
        assert _is_fork_tool_call_id("call_xyz") is False

    def test_extract_fork_id_from_tool(self):
        assert _extract_fork_id_from_tool("fork::abc123::tool::call_xyz") == "abc123"

    def test_extract_fork_id_from_tool_no_fork(self):
        assert _extract_fork_id_from_tool("call_xyz") is None


class TestToolBlock:
    def test_create_tool_block(self):
        block = create_tool_card(
            tool_name="run_command",
            arguments={"command": "echo hello"},
        )
        assert isinstance(block, ToolBlock)
        assert block.tool_name == "run_command"
        assert block.arguments == {"command": "echo hello"}

    def test_create_different_tools(self):
        block = create_tool_card(
            tool_name="edit_file",
            arguments={"file_path": "test.py", "old_string": "old", "new_string": "new"},
        )
        assert isinstance(block, ToolBlock)
        assert block.arguments["file_path"] == "test.py"

    def test_create_unknown_tool(self):
        block = create_tool_card(
            tool_name="unknown_tool",
            arguments={"config": {"key": "value"}},
        )
        assert isinstance(block, ToolBlock)

    def test_update_status(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        block.update_status("success")
        assert block._status == "success"

    def test_update_result(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        block.update_result("Done", success=True)
        assert block._result_text == "Done"
        assert block._success is True

    def test_update_result_failure(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        block.update_result("Error occurred", success=False)
        assert block._result_text == "Error occurred"
        assert block._success is False

    def test_update_arguments(self):
        block = create_tool_card(tool_name="execute_code", arguments={})
        block.update_arguments({"code": "print('hello')"})
        assert block.arguments["code"] == "print('hello')"

    def test_update_output(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        block.update_output("line1\nline2")
        assert block._output == "line1\nline2"

    def test_toggle_expand(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        assert block._expanded is False
        block.toggle_expand()
        assert block._expanded is True
        block.toggle_expand()
        assert block._expanded is False

    def test_icon_running(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        assert block._icon == "\u27f3"

    def test_icon_success(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        block.update_result("ok", success=True)
        assert block._icon == "\u2713"

    def test_icon_error(self):
        block = create_tool_card(tool_name="run_command", arguments={})
        block.update_result("fail", success=False)
        assert block._icon == "\u2717"


class TestLambdaCodingTUIApp:
    def test_app_init(self):
        def dummy_agent(message, history=None):
            pass

        app = LambdaCodingTUIApp(
            agent_func=dummy_agent,
            workspace="/tmp/test",
            model_name="gpt-4",
            git_info="main, clean",
        )
        assert app.workspace == "/tmp/test"
        assert app.model_name == "gpt-4"
        assert app.git_info == "main, clean"
        assert app.history == []
        assert not app._busy

    def test_app_models_and_tools_storage(self):
        def dummy_agent(message, history=None):
            pass

        app = LambdaCodingTUIApp(
            agent_func=dummy_agent,
            workspace="/tmp/test",
        )
        assert app._models == {}
        assert app._tools == {}
        assert app._fork_panes == {}
