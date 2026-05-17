"""Integration test for TUI mount fix."""

import pytest

from lambda_coding_agent.tui.app import LambdaCodingTUIApp, ChatInput


async def stub_agent(message, history=None, **kwargs):
    """Stub agent that yields nothing."""
    return
    yield  # make it a generator


@pytest.mark.asyncio
async def test_user_message_mounts():
    """Verify that typing a message and submitting doesn't cause MountError."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        # Type a message and press enter
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("enter")
        # If we get here without MountError, the fix works
        await pilot.pause()


@pytest.mark.asyncio
async def test_slash_exit_command():
    """Verify Exit via command palette works."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press("e", "x", "i", "t")
        await pilot.press("enter")
        # App should exit


@pytest.mark.asyncio
async def test_slash_clear_command():
    """Verify Clear Chat via command palette works."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        # First add a message
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        # Open command palette and run Clear Chat
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press("c", "l", "e", "a", "r")
        await pilot.press("enter")
        await pilot.pause()
        # Chat log should be empty now
        from textual.containers import VerticalScroll
        chat_log = app.query_one("#main-chat-log", VerticalScroll)
        assert len(chat_log.children) == 0


@pytest.mark.asyncio
async def test_ctrl_c_clears_input_without_exiting():
    """Verify Ctrl+C clears the input and keeps the TUI running."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert input_widget.value == ""
        assert app.is_running


@pytest.mark.asyncio
async def test_shift_enter_adds_newline_and_enter_submits():
    """Shift+Enter should create a newline; Enter should submit the full text."""
    calls = []

    async def capturing_agent(message, history=None, **kwargs):
        calls.append(message)
        return
        yield

    app = LambdaCodingTUIApp(
        agent_func=capturing_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("shift+enter")
        await pilot.press("t", "h", "e", "r", "e")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert input_widget.value == "hi\nthere"
        assert calls == []

        await pilot.press("enter")
        await pilot.pause()

        assert calls == ["hi\nthere"]
        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_at_path_autocomplete_inserts_selected_path(tmp_path):
    """Typing @ should show matching workspace files and Enter should insert one."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")

    calls = []

    async def capturing_agent(message, history=None, **kwargs):
        calls.append(message)
        return
        yield

    app = LambdaCodingTUIApp(
        agent_func=capturing_agent,
        workspace=str(tmp_path),
        model_name="test",
    )

    async with app.run_test() as pilot:
        await pilot.press("@", "s")
        await pilot.pause()

        from textual.widgets import OptionList

        popup = app.query_one("#path-autocomplete", OptionList)
        assert popup.display is True
        assert [option.prompt for option in popup.options] == ["src/main.py"]

        await pilot.press("enter")
        await pilot.pause()

        input_widget = app.query_one("#chat-input", ChatInput)
        assert input_widget.value == "@src/main.py"
        assert popup.display is False
        assert calls == []


def test_at_path_autocomplete_matches_nested_and_partial_paths(tmp_path, monkeypatch):
    """Workspace path matching should find nested paths from the configured workspace."""
    workspace = tmp_path / "workspace"
    launch_dir = tmp_path / "launch-dir"
    (workspace / "src" / "ui" / "components").mkdir(parents=True)
    launch_dir.mkdir()
    (workspace / "src" / "ui" / "components" / "Button.py").write_text("")
    (launch_dir / "Button.py").write_text("")
    monkeypatch.chdir(launch_dir)

    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace=str(workspace),
        model_name="test",
    )

    expected = ["src/ui/components/Button.py"]
    assert app._matching_workspace_paths("Button") == expected
    assert app._matching_workspace_paths("utt") == expected
    assert app._matching_workspace_paths("ui/utt") == expected


def test_at_path_autocomplete_resolves_relative_workspace_from_launch_dir(tmp_path, monkeypatch):
    """Relative workspace paths should resolve once, then search that workspace."""
    launch_dir = tmp_path / "launch-dir"
    workspace = launch_dir / "repo"
    workspace.mkdir(parents=True)
    (workspace / "target.py").write_text("")
    (tmp_path / "target.py").write_text("")
    monkeypatch.chdir(launch_dir)

    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="repo",
        model_name="test",
    )

    assert app.workspace == str(workspace)
    assert app._matching_workspace_paths("target") == ["target.py"]


@pytest.mark.asyncio
async def test_click_focuses_input_unless_modal_is_active():
    """Background clicks focus the chat input, but not while a modal screen is active."""
    app = LambdaCodingTUIApp(
        agent_func=stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async with app.run_test() as pilot:
        input_widget = app.query_one("#chat-input", ChatInput)
        app.screen.set_focus(None)
        await pilot.pause()
        assert not input_widget.has_focus

        await pilot.click("#main-chat-log")
        await pilot.pause()
        assert input_widget.has_focus

        await pilot.press("ctrl+p")
        await pilot.pause()
        active_screen = app.screen
        assert app._modal_screen_active()
        input_widget.blur()
        await pilot.pause()
        app.on_click(None)
        await pilot.pause()

        assert app.screen is active_screen
        assert not input_widget.has_focus

