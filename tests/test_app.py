"""Tests for app module."""

from lambda_coding_agent.app import launch_tui
from lambda_coding_agent.agent import create_agent


class TestApp:
    def test_launch_tui_is_callable(self):
        """launch_tui should be callable."""
        assert callable(launch_tui)

    def test_create_agent_returns_callable_for_tui(self):
        """Agent function should be wrappable by tui."""
        agent_fn = create_agent(
            provider_path=None,
            workspace="/tmp",
            environment_block="## Environment\n- test",
        )
        assert callable(agent_fn)
        # TUI wrapper should accept the function
        # We don't actually launch it in tests
