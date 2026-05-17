"""Tests for config module."""

import json
import os

import pytest

from lambda_coding_agent.config import load_config, Config


@pytest.fixture
def workspace(tmp_path):
    provider = {
        "test_provider": [
            {
                "model_name": "test-model",
                "api_keys": ["sk-test-key"],
                "base_url": "http://localhost:8080/v1",
            }
        ]
    }
    (tmp_path / "provider.json").write_text(json.dumps(provider))
    return str(tmp_path)


class TestConfig:
    def test_load_config_finds_provider(self, workspace):
        config = load_config(workspace=workspace)
        assert config.provider_path is not None
        assert os.path.exists(config.provider_path)

    def test_load_config_workspace(self, workspace):
        config = load_config(workspace=workspace)
        assert config.workspace == workspace

    def test_load_config_model_override(self, workspace):
        config = load_config(
            workspace=workspace, model="custom-model", provider_id="custom_provider"
        )
        assert config.model_name == "custom-model"
        assert config.provider_id == "custom_provider"

    def test_load_config_no_provider(self, tmp_path, monkeypatch):
        # Ensure global ~/.lambda-agent/provider.json doesn't interfere
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/nonexistent" + p)
        config = load_config(workspace=str(tmp_path))
        assert config.provider_path is None
