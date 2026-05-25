"""Configuration loading."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Agent configuration."""

    workspace: str
    provider_path: str | None = None
    provider_id: str | None = None
    model_name: str | None = None


def load_config(
    workspace: str,
    model: str | None = None,
    provider_id: str | None = None,
    provider_path: str | None = None,
) -> Config:
    """Load configuration, searching for provider.json.

    Search order for provider.json:
    1. Explicit provider_path argument
    2. workspace/provider.json
    3. ~/.lambda/provider.json

    Args:
        workspace: Workspace directory path.
        model: Model name override.
        provider_id: Provider ID override.
        provider_path: Explicit path to provider.json.

    Returns:
        Config dataclass.
    """
    # Find provider.json
    found_provider = None

    if provider_path and os.path.exists(provider_path):
        found_provider = provider_path
    else:
        candidates = [
            os.path.join(workspace, "provider.json"),
            os.path.expanduser("~/.lambda/provider.json"),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                found_provider = candidate
                break

    return Config(
        workspace=workspace,
        provider_path=found_provider,
        provider_id=provider_id,
        model_name=model,
    )
