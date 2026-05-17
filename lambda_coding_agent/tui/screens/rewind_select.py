"""Rewind/fork selection modal screen."""

from __future__ import annotations

from typing import Any

from lambda_coding_agent.tui.screens import SelectModalScreen


class RewindSelectModalScreen(SelectModalScreen):
    """Modal for selecting a user message to rewind to.

    Payload on selection: (history_index, user_message_text)
    """

    def __init__(
        self,
        history: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.history = history

    def _title(self) -> str:
        return "Rewind to Message"

    def _build_items(self) -> list[tuple[str, Any]]:
        items: list[tuple[str, Any]] = []
        turn = 1
        for i, msg in enumerate(self.history):
            if msg.get("role") == "user":
                text = msg.get("content", "")
                preview = text[:80] + ("..." if len(text) > 80 else "")
                # Escape newlines for display
                preview = preview.replace("\n", " ")
                label = f"Turn {turn}: {preview}"
                items.append((label, (i, text)))
                turn += 1

        if not items:
            items.append(("No messages to rewind to", None))

        return items
