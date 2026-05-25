"""Session list modal screen."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lambda_coding_agent.tui.screens import SelectModalScreen


class SessionListModalScreen(SelectModalScreen):
    """Modal for listing and selecting sessions."""

    def __init__(
        self,
        session_manager: Any,  # SessionManager
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.session_manager = session_manager

    def _title(self) -> str:
        return "Select Session"

    def _build_items(self) -> list[tuple[str, Any]]:
        items: list[tuple[str, Any]] = []
        # First item: New Session
        items.append(("New Session", None))

        sessions = self.session_manager.list_sessions()
        for s in sessions:
            name = s.get("name", "") or "(untitled)"
            model = s.get("model_name", "unknown")
            last_active = s.get("last_active_at", "")
            rel_time = _relative_time(last_active)
            label = f"{name}  |  {model}  |  {rel_time}"
            items.append((label, s["id"]))

        return items

    def on_key(self, event) -> None:
        if event.key in ("shift+d", "D") and self._items:
            # Cannot delete "New Session" (index 0)
            idx = self._focused_index
            if idx > 0:
                _, payload = self._items[idx]
                if isinstance(payload, str):
                    self.session_manager.delete_session(payload)
                    # Rebuild items and keep focus in bounds
                    self._items = self._build_items()
                    if self._focused_index >= len(self._items):
                        self._focused_index = max(0, len(self._items) - 1)
                    self._render_items()
                    return
        super().on_key(event)


def _relative_time(iso_str: str) -> str:
    """Convert ISO datetime to human-readable relative time."""
    if not iso_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (now - dt).total_seconds()
    except (ValueError, TypeError):
        return "unknown"

    if diff < 60:
        return "just now"
    if diff < 3600:
        m = int(diff / 60)
        return f"{m}m ago"
    if diff < 86400:
        h = int(diff / 3600)
        return f"{h}h ago"
    if diff < 604800:
        d = int(diff / 86400)
        return f"{d}d ago"
    return dt.strftime("%Y-%m-%d")
