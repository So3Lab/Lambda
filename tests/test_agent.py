"""Tests for agent definition."""

import pytest

from lambda_coding_agent.agent import create_agent


class TestAgent:
    def test_create_agent_returns_callable(self):
        agent_fn = create_agent(
            provider_path=None,
            workspace="/tmp",
            environment_block="## Environment\n- test",
        )
        # Should be a callable (the decorated llm_chat function)
        assert callable(agent_fn)

    def test_create_agent_with_custom_model(self):
        agent_fn = create_agent(
            provider_path=None,
            workspace="/tmp",
            environment_block="## Environment\n- test",
            model_name="test-model",
            provider_id="test",
        )
        assert callable(agent_fn)
