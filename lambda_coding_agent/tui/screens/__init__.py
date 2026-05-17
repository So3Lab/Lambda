"""Modal selection screens — shared base class and utilities."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, Static


class SelectModalScreen(Screen):
    """Abstract base for modal selection screens.

    Subclass must implement:
    - _build_items() -> list[tuple[str, Any]] — display text + payload pairs
    - _title -> str — screen title

    The modal shows a centered list. Navigate with up/down arrows,
    press Enter to select (dismisses with payload), Escape to cancel.
    """

    DEFAULT_CSS = """
    SelectModalScreen {
        align: center middle;
    }
    SelectModalScreen .modal-container {
        width: 80%;
        max-height: 70%;
        background: #1a1d24;
        border: tall #2a2f3a;
    }
    SelectModalScreen .modal-title {
        text-style: bold;
        color: #6f87a8;
        padding: 0 2 1 2;
    }
    SelectModalScreen .modal-list {
        padding: 0 1;
    }
    SelectModalScreen .select-item {
        padding: 0 1;
    }
    SelectModalScreen .select-item--focused {
        background: #2a8c8c;
        color: #0f1115;
    }
    SelectModalScreen .modal-hint {
        text-style: dim;
        padding: 1 2 0 2;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._focused_index: int = 0
        self._items: list[tuple[str, Any]] = []

    def compose(self) -> ComposeResult:
        self._items = self._build_items()
        self._focused_index = 0
        with Container(classes="modal-container"):
            yield Label(self._title(), classes="modal-title")
            yield VerticalScroll(classes="modal-list")
            yield Label("↑↓ navigate  ·  enter select  ·  esc cancel  ·  shift+d delete", classes="modal-hint")

    def on_mount(self) -> None:
        self._render_items()

    @abstractmethod
    def _title(self) -> str:
        ...

    @abstractmethod
    def _build_items(self) -> list[tuple[str, Any]]:
        """Return list of (display_text, payload) tuples."""
        ...

    def _render_items(self) -> None:
        """Build Static widgets for each item inside the scroll area."""
        try:
            scroll = self.query_one(".modal-list", VerticalScroll)
        except Exception:
            return
        scroll.remove_children()
        for i, (text, _) in enumerate(self._items):
            classes = "select-item"
            if i == self._focused_index:
                classes += " select-item--focused"
            scroll.mount(Static(text, classes=classes))
        self._scroll_focused_into_view()

    def _scroll_focused_into_view(self) -> None:
        """Ensure the focused item is visible."""
        try:
            scroll = self.query_one(".modal-list", VerticalScroll)
            items = scroll.query(".select-item")
            if items and self._focused_index < len(items):
                items[self._focused_index].scroll_visible(animate=False)
        except Exception:
            pass

    def on_key(self, event) -> None:
        if event.key == "up":
            event.prevent_default()
            event.stop()
            if self._focused_index > 0:
                self._focused_index -= 1
                self._render_items()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            if self._focused_index < len(self._items) - 1:
                self._focused_index += 1
                self._render_items()
        elif event.key == "enter":
            event.prevent_default()
            event.stop()
            if self._items:
                _, payload = self._items[self._focused_index]
                self.dismiss(payload)
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            self.dismiss(None)
