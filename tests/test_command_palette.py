"""Tests for command palette integration."""

import pytest

from lambda_coding_agent.tui.app import LambdaCodingTUIApp
from lambda_coding_agent.agent import create_agent


# Stub agent for testing
_stub_agent = create_agent(provider_path=None, workspace="/tmp", environment_block="")


class TestCommandPalette:
    """Test that command palette has correct commands without duplicates."""

    def test_command_palette_has_expected_commands(self):
        """All expected commands should be registered without our duplicates of builtins."""
        app = LambdaCodingTUIApp(
            agent_func=_stub_agent,
            workspace="/tmp",
            model_name="test",
        )

        async def check():
            async with app.run_test() as pilot:
                # Our custom commands only
                our_commands = [
                    "Switch Model",
                    "Sessions",
                    "Rewind",
                    "Refresh Skills",
                    "Clear Chat",
                ]
                commands = list(app.get_system_commands(app.screen))
                names = [cmd.title for cmd in commands]

                # Expected custom commands are present
                for name in our_commands:
                    assert name in names, f"Missing command: {name}"

                # We should NOT have added these duplicates of builtins
                # (Quit is builtin, Exit was our duplicate of Quit)
                # (Undo was moved to action-only since Rewind covers similar ground)
                our_cmds = [c for c in commands if c.title not in (
                    "Theme", "Quit", "Keys", "Maximize", "Screenshot"
                )]
                our_names = [c.title for c in our_cmds]
                assert "Exit" not in our_names, "Exit duplicates builtin Quit"
                assert "Undo" not in our_names, "Undo removed (Rewind covers similar ground)"
                assert "Session Tree" not in our_names, "Use Rewind/double Esc for branch tree"

        import asyncio
        asyncio.run(check())

    def test_slash_opens_command_palette(self):
        """Typing / in the input should open the command palette."""
        app = LambdaCodingTUIApp(
            agent_func=_stub_agent,
            workspace="/tmp",
            model_name="test",
        )

        async def check():
            async with app.run_test() as pilot:
                await pilot.press("/")
                await pilot.pause()
                # Command palette should be visible (it's a screen)
                # Check that the command palette screen is active
                from textual.command import CommandPalette
                assert isinstance(app.screen, CommandPalette)

        import asyncio
        asyncio.run(check())

    def test_no_unknown_command_hint_for_slash(self):
        """Typing / should NOT produce 'Unknown command' hint."""
        app = LambdaCodingTUIApp(
            agent_func=_stub_agent,
            workspace="/tmp",
            model_name="test",
        )

        async def check():
            async with app.run_test() as pilot:
                await pilot.press("/")
                await pilot.pause()
                # No system-hint should be added (command palette opens instead)
                from textual.containers import VerticalScroll
                chat_log = app.query_one("#main-chat-log", VerticalScroll)
                hints = chat_log.query(".system-hint")
                assert len(list(hints)) == 0

        import asyncio
        asyncio.run(check())



def test_tui_css_uses_textual_theme_variables():
    css = LambdaCodingTUIApp.CSS

    assert "background: $background" in css
    assert "#0f1115" not in css
    assert "#1a1d24" not in css
    assert "#6f87a8" not in css


def test_tui_applies_theme_palette_to_rich_ansi_colors():
    app = LambdaCodingTUIApp(
        agent_func=_stub_agent,
        workspace="/tmp",
        model_name="test",
    )

    async def check():
        async with app.run_test():
            app.theme = "dracula"
            await app._animator.wait_until_complete()
            app_theme = app.ansi_theme
            expected = terminal_theme_from_textual_theme(app.current_theme)
            assert app_theme.background_color == expected.background_color
            assert app_theme.foreground_color == expected.foreground_color

    import asyncio
    from lambda_coding_agent.tui.theme import terminal_theme_from_textual_theme

    asyncio.run(check())
