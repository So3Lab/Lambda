"""Integration test for TUI mount fix."""

import pytest

from lambda_coding_agent.tui.app import LambdaCodingTUIApp


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

        from textual.widgets import Input
        input_widget = app.query_one("#chat-input", Input)
        assert input_widget.value == ""
        assert app.is_running
