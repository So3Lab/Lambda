"""Configuration loading."""

from __future__ import annotations

import atexit
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Config:
    """Agent configuration."""

    workspace: str
    provider_path: str | None = None
    provider_id: str | None = None
    model_name: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


def _home_lambda_path(*parts: str) -> Path:
    return Path(os.path.expanduser(os.path.join("~", ".lambda", *parts)))


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_config_aliases(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    if "bypassPaths" in normalized:
        normalized["bypass_paths"] = normalized.pop("bypassPaths")
    return normalized


def load_lambda_config(workspace: str) -> dict[str, Any]:
    """Load merged ~/.lambda and workspace .lambda config.json."""
    merged: dict[str, Any] = {}
    for config_path in (
        _home_lambda_path("config.json"),
        Path(workspace) / ".lambda" / "config.json",
    ):
        merged = _merge_dicts(merged, _normalize_config_aliases(_read_json_object(config_path)))
    return merged


def _write_merged_provider(data: dict[str, Any]) -> str:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="lambda_provider_",
        suffix=".json",
        delete=False,
    ) as f:
        json.dump(data, f)
        f.write("\n")
        path = f.name
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    atexit.register(lambda: os.path.exists(path) and os.unlink(path))
    return path


def _load_provider_path(workspace: str, provider_path: str | None) -> str | None:
    if provider_path:
        explicit = Path(os.path.expanduser(os.path.expandvars(provider_path)))
        if explicit.exists():
            return str(explicit)

    provider_candidates = (
        _home_lambda_path("provider.json"),
        Path(workspace) / "provider.json",
        Path(workspace) / ".lambda" / "provider.json",
    )
    provider_sources: list[Path] = []
    merged_provider: dict[str, Any] = {}
    for candidate in provider_candidates:
        data = _read_json_object(candidate)
        if not data:
            continue
        provider_sources.append(candidate)
        merged_provider = _merge_dicts(merged_provider, data)

    if not provider_sources:
        return None
    if len(provider_sources) == 1:
        return str(provider_sources[0])
    return _write_merged_provider(merged_provider)


def _first_string(settings: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = settings.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def load_config(
    workspace: str,
    model: str | None = None,
    provider_id: str | None = None,
    provider_path: str | None = None,
) -> Config:
    """Load configuration.

    ~/.lambda/config.json and <workspace>/.lambda/config.json are merged with
    workspace values taking precedence. Provider config is loaded from
    ~/.lambda/provider.json, legacy <workspace>/provider.json, and
    <workspace>/.lambda/provider.json with the same precedence.
    """
    settings = load_lambda_config(workspace)

    return Config(
        workspace=workspace,
        provider_path=_load_provider_path(workspace, provider_path),
        provider_id=provider_id or _first_string(settings, ("provider_id", "provider")),
        model_name=model or _first_string(settings, ("model_name", "model")),
        settings=settings,
    )
