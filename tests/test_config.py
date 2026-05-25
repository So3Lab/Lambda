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
        # Ensure global ~/.lambda/provider.json doesn't interfere
        monkeypatch.setattr(os.path, "expanduser", lambda p: "/nonexistent" + p)
        config = load_config(workspace=str(tmp_path))
        assert config.provider_path is None

    def test_load_config_merges_home_and_workspace_config(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        workspace = tmp_path / "workspace"
        home_lambda = home / ".lambda"
        workspace_lambda = workspace / ".lambda"
        home_lambda.mkdir(parents=True)
        workspace_lambda.mkdir(parents=True)
        (home_lambda / "config.json").write_text(
            json.dumps({
                "provider": "home-provider",
                "model": "home-model",
                "bypass_paths": ["~/shared"],
                "nested": {"home": True, "same": "home"},
            }),
            encoding="utf-8",
        )
        (workspace_lambda / "config.json").write_text(
            json.dumps({
                "model": "workspace-model",
                "bypass_paths": ["local-shared"],
                "nested": {"workspace": True, "same": "workspace"},
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) + p[1:] if p.startswith("~") else p)

        config = load_config(workspace=str(workspace))

        assert config.provider_id == "home-provider"
        assert config.model_name == "workspace-model"
        assert config.settings["bypass_paths"] == ["local-shared"]
        assert config.settings["nested"] == {
            "home": True,
            "workspace": True,
            "same": "workspace",
        }

    def test_cli_overrides_merged_config(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        workspace = tmp_path / "workspace"
        (home / ".lambda").mkdir(parents=True)
        workspace.mkdir()
        (home / ".lambda" / "config.json").write_text(
            json.dumps({"provider": "home-provider", "model": "home-model"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) + p[1:] if p.startswith("~") else p)

        config = load_config(
            workspace=str(workspace),
            model="cli-model",
            provider_id="cli-provider",
        )

        assert config.provider_id == "cli-provider"
        assert config.model_name == "cli-model"

    def test_load_config_merges_provider_json_with_workspace_precedence(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        workspace = tmp_path / "workspace"
        home_lambda = home / ".lambda"
        workspace_lambda = workspace / ".lambda"
        home_lambda.mkdir(parents=True)
        workspace_lambda.mkdir(parents=True)
        (home_lambda / "provider.json").write_text(
            json.dumps({
                "home_provider": [
                    {"model_name": "home-only", "api_keys": ["home"], "base_url": "http://home"}
                ],
                "shared_provider": [
                    {"model_name": "home-shared", "api_keys": ["home"], "base_url": "http://home"}
                ],
            }),
            encoding="utf-8",
        )
        (workspace_lambda / "provider.json").write_text(
            json.dumps({
                "shared_provider": [
                    {"model_name": "workspace-shared", "api_keys": ["workspace"], "base_url": "http://workspace"}
                ]
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) + p[1:] if p.startswith("~") else p)

        config = load_config(workspace=str(workspace))

        assert config.provider_path is not None
        with open(config.provider_path, encoding="utf-8") as f:
            merged = json.load(f)
        assert [m["model_name"] for m in merged["home_provider"]] == ["home-only"]
        assert [m["model_name"] for m in merged["shared_provider"]] == ["workspace-shared"]

    def test_load_config_uses_home_provider_when_workspace_has_none(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        workspace = tmp_path / "workspace"
        home_lambda = home / ".lambda"
        home_lambda.mkdir(parents=True)
        workspace.mkdir()
        (home_lambda / "provider.json").write_text(
            json.dumps({
                "home_provider": [
                    {"model_name": "home-model", "api_keys": ["home"], "base_url": "http://home"}
                ]
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) + p[1:] if p.startswith("~") else p)

        config = load_config(workspace=str(workspace))

        assert config.provider_path == str(home_lambda / "provider.json")
