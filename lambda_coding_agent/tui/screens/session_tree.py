"""Branch-aware session tree screen."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets._option_list import Option

from lambda_coding_agent.tui.session import SessionManager


@dataclass(frozen=True)
class SessionTreeRow:
    """One visible row in the compact branch tree."""

    node_id: str
    indent: int
    label: str


def build_session_tree_rows(
    message_nodes: dict[str, dict[str, Any]],
    active_leaf_id: str | None,
) -> list[SessionTreeRow]:
    """Build rows where indentation changes only at branch points.

    The persisted structure is a per-message parent chain, so a naïve Tree widget
    indents every message below the previous message.  For conversation history,
    that is visually wrong: a linear trunk should stay at one column, and only
    sibling alternatives should create an extra branch indentation level.
    """
    children: dict[str | None, list[str]] = {}
    for node_id, node in message_nodes.items():
        children.setdefault(node.get("parent_id"), []).append(node_id)

    def sort_key(node_id: str) -> str:
        node = message_nodes.get(node_id, {})
        return str(node.get("created_at", "")) + node_id

    for ids in children.values():
        ids.sort(key=sort_key)

    active_path = set(SessionManager.active_path_ids(message_nodes, active_leaf_id))
    rows: list[SessionTreeRow] = []

    def walk(parent_id: str | None, indent: int) -> None:
        sibling_ids = children.get(parent_id, [])
        is_branch_point = len(sibling_ids) > 1
        for child_id in sibling_ids:
            row_indent = indent + 1 if is_branch_point else indent
            node = message_nodes.get(child_id, {})
            if _show_message_node(node):
                rows.append(
                    SessionTreeRow(
                        node_id=child_id,
                        indent=row_indent,
                        label=_format_row_label(
                            child_id,
                            node,
                            active_leaf_id,
                            active_path,
                        ),
                    )
                )
            walk(child_id, row_indent)

    walk(None, 0)
    return rows


def _show_message_node(node: dict[str, Any]) -> bool:
    message = node.get("message", {}) if isinstance(node, dict) else {}
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return True
    return bool(_message_preview(message))


def _format_row_label(
    node_id: str,
    node: dict[str, Any],
    active_leaf_id: str | None,
    active_path: set[str],
) -> str:
    message = node.get("message", {}) if isinstance(node, dict) else {}
    role = str(message.get("role", "message"))
    preview = _message_preview(message).replace("[", "\\[")
    marker = "●" if node_id == active_leaf_id else "•"
    active = " [bold]*[/bold]" if node_id in active_path else ""
    color = _role_color(role)
    return f"[{color}]{marker} {role}:[/{color}] {preview}{active}"


def _role_color(role: str) -> str:
    return {
        "user": "blue",
        "assistant": "green",
        "tool": "yellow",
    }.get(role, "white")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if content is None:
        return ""
    if isinstance(content, list):
        return " ".join(str(part) for part in content)
    return str(content)


def _message_preview(message: dict[str, Any]) -> str:
    text = " ".join(_message_text(message).split())
    return text[:120] + ("…" if len(text) > 120 else "")


class SessionTreeScreen(Screen):
    """Full-screen message tree navigator.

    The screen intentionally mirrors the pi-agent style shown in the design note:
    a compact header with key hints, a tree view of message nodes, and a search
    input.  It is branch-aware but keeps the actions small:

    - Enter: switch to the selected node's branch.
    - r: rewind to the selected user node, pre-filling that message for editing.
    - Escape/q: close without changing the active branch.
    """

    BINDINGS = [
        ("escape", "cancel", "Close"),
        ("q", "cancel", "Close"),
        ("r", "rewind", "Rewind"),
        ("slash", "focus_search", "Search"),
    ]

    DEFAULT_CSS = """
    SessionTreeScreen {
        layout: vertical;
        background: $background;
        color: $text;
    }

    SessionTreeScreen Header {
        dock: top;
    }

    #session-tree-help {
        height: auto;
        padding: 0 2;
        border: tall $border;
    }

    #session-tree-help .title {
        text-style: bold;
        color: $foreground;
    }

    #session-tree-help .hint {
        color: $text-muted;
    }

    #session-tree-container {
        height: 1fr;
        padding: 1 2;
    }

    #session-tree-widget {
        height: 1fr;
        background: transparent;
    }

    #session-tree-search {
        dock: bottom;
        display: none;
        margin: 0 1 1 1;
        border: tall $border;
    }
    """

    def __init__(
        self,
        *,
        message_nodes: dict[str, dict[str, Any]],
        active_leaf_id: str | None,
        on_switch: Callable[[str], Any],
        on_rewind: Callable[[str], Any],
        session_name: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.message_nodes = copy.deepcopy(message_nodes)
        self.active_leaf_id = active_leaf_id
        self.on_switch = on_switch
        self.on_rewind = on_rewind
        self.session_name = session_name
        self._rows: list[SessionTreeRow] = []
        self._row_index_by_message_id: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            "[b]Session Tree[/b]\n"
            "↑/↓: move · ←/→: fold/branch · enter: switch branch · r: rewind/edit · /: search · esc/q: close\n"
            "Type to search; enter in search jumps to next match.",
            id="session-tree-help",
        )
        with Container(id="session-tree-container"):
            yield OptionList(id="session-tree-widget")
        yield Input(placeholder="Search messages...", id="session-tree-search")
        yield Footer()

    def on_mount(self) -> None:
        self._build_tree()
        self.query_one("#session-tree-widget", OptionList).focus()

    def _build_tree(self) -> None:
        option_list = self.query_one("#session-tree-widget", OptionList)
        option_list.clear_options()
        self._rows = build_session_tree_rows(self.message_nodes, self.active_leaf_id)
        self._row_index_by_message_id = {
            row.node_id: index for index, row in enumerate(self._rows)
        }
        option_list.add_options(
            Option(f"{'  ' * row.indent}{row.label}", id=row.node_id)
            for row in self._rows
        )
        if self.active_leaf_id in self._row_index_by_message_id:
            option_list.highlighted = self._row_index_by_message_id[self.active_leaf_id]
        elif self._rows:
            option_list.highlighted = 0

    def _selected_message_id(self) -> str | None:
        option_list = self.query_one("#session-tree-widget", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None or highlighted >= len(self._rows):
            return None
        return self._rows[highlighted].node_id

    def _search_input(self) -> Input:
        return self.query_one("#session-tree-search", Input)

    def _hide_search(self) -> None:
        search = self._search_input()
        search.value = ""
        search.display = False
        self.query_one("#session-tree-widget", OptionList).focus()

    def action_cancel(self) -> None:
        search = self._search_input()
        if search.display and search.has_focus:
            self._hide_search()
            return
        self.dismiss(None)

    def action_focus_search(self) -> None:
        search = self._search_input()
        search.display = True
        search.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is not self._search_input():
            return
        event.stop()
        query = event.value.strip().casefold()
        if not query:
            return
        match = self._find_first_match(query)
        if match is not None:
            option_list = self.query_one("#session-tree-widget", OptionList)
            option_list.highlighted = match
            option_list.scroll_to_highlight()

    def action_rewind(self) -> None:
        node_id = self._selected_message_id()
        if node_id and self._node_role(node_id) == "user":
            self.dismiss(("rewind", node_id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is not self.query_one("#session-tree-widget", OptionList):
            return
        node_id = event.option.id
        # Rewind operates only at user-message boundaries. Selecting assistant
        # or tool nodes is intentionally a no-op: those messages are outputs of
        # a turn, not valid restart inputs.
        if isinstance(node_id, str) and self._node_role(node_id) == "user":
            self.dismiss(("rewind", node_id))

    def _node_role(self, node_id: str) -> str:
        node = self.message_nodes.get(node_id, {})
        message = node.get("message", {}) if isinstance(node, dict) else {}
        return str(message.get("role", ""))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self._search_input():
            return
        event.stop()
        query = event.value.strip().casefold()
        if not query:
            self._hide_search()
            return
        match = self._find_next_match(query)
        if match is not None:
            option_list = self.query_one("#session-tree-widget", OptionList)
            option_list.highlighted = match
            option_list.scroll_to_highlight()
            option_list.focus()

    def _find_first_match(self, query: str) -> int | None:
        return self._find_match(query, 0)

    def _find_next_match(self, query: str) -> int | None:
        option_list = self.query_one("#session-tree-widget", OptionList)
        if not self._rows:
            return None
        highlighted = option_list.highlighted
        start = 0 if highlighted is None else highlighted + 1
        return self._find_match(query, start)

    def _find_match(self, query: str, start: int) -> int | None:
        if not self._rows:
            return None
        for offset in range(len(self._rows)):
            index = (start + offset) % len(self._rows)
            row = self._rows[index]
            message = self.message_nodes.get(row.node_id, {}).get("message", {})
            haystack = f"{row.label} {_message_text(message)}".casefold()
            if query in haystack:
                return index
        return None
