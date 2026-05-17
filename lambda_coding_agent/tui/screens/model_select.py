"""Model selection modal screen."""

from __future__ import annotations

from typing import Any

from lambda_coding_agent.tui.screens import SelectModalScreen


class ModelSelectModalScreen(SelectModalScreen):
    """Modal for selecting an LLM model from provider.json."""

    def __init__(
        self,
        provider_path: str | None,
        current_model_name: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.provider_path = provider_path
        self.current_model_name = current_model_name

    def _title(self) -> str:
        return "Select Model"

    def _build_items(self) -> list[tuple[str, Any]]:
        import json

        items: list[tuple[str, Any]] = []
        if not self.provider_path:
            return [("No provider.json configured", None)]

        try:
            with open(self.provider_path) as f:
                data = json.load(f)
        except Exception:
            return [("Failed to read provider.json", None)]

        for pid, models in data.items():
            models_list = models if isinstance(models, list) else list(models.values())
            for m in models_list:
                name = m.get("model_name", "unknown")
                ctx = m.get("context_window", 200_000)
                ctx_display = f"{ctx // 1000}K" if ctx >= 1000 else str(ctx)
                label = f"{name} ({pid}) — ctx: {ctx_display}"
                if name == self.current_model_name:
                    label += "  *"
                items.append((label, (pid, name, ctx)))

        return items
