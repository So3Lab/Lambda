"""Custom Textual TUI for LambdaCodingAgent.

A production-grade TUI built directly on Textual + SimpleLLMFunc's event stream.
Implements TUIStreamAdapter protocol for real-time agent interaction.
Uses TabbedContent for main agent + fork subagent panels.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional

from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import MouseScrollUp
from textual.screen import Screen
from textual.widgets import Input, Markdown, OptionList, Static, TabbedContent, TabPane

from SimpleLLMFunc.hooks.abort import AbortSignal
from SimpleLLMFunc.hooks.stream import is_event_yield
from SimpleLLMFunc.type.message import MessageList
from SimpleLLMFunc.utils.tui.core import TUIStreamAdapter, consume_react_stream
from SimpleLLMFunc.utils.tui.formatters import (
    format_model_stats,
    format_tool_arguments_markdown,
    format_tool_result_markdown,
    format_tool_stats,
)

from lambda_coding_agent.tui.chat_input import ChatInput
from lambda_coding_agent.tui.tool_cards import create_tool_card
from lambda_coding_agent.tui.theme import terminal_theme_from_textual_theme
from lambda_coding_agent.tui.session import SessionManager
from lambda_coding_agent.tui.plan_panel import PlanPanel
from lambda_coding_agent.tools.plan import PlanManager



def _patch_markdown_append() -> None:
    """Monkey-patch Textual's Markdown.append to fix a race condition.

    Original bug: append() reads _last_parsed_line and updated_source in the
    outer (sync) function, but updates _last_parsed_line before the async
    batch_update runs. When append() is called rapidly, multiple calls read
    the same stale _last_parsed_line, causing duplicate content.

    Fix: chain appends sequentially — each new append awaits any pending
    operation, then re-reads _last_parsed_line and recalculates the delta
    inside the async coroutine.
    """
    from textual.await_complete import AwaitComplete
    from textual.widgets import Markdown
    from textual.widgets._markdown import MarkdownBlock, MarkdownHeader
    from markdown_it import MarkdownIt

    def patched_append(self: Markdown, markdown: str) -> AwaitComplete:
        previous = getattr(self, "_pending_append", None)

        def _do_append() -> AwaitComplete:
            parser = (
                MarkdownIt("gfm-like")
                if self._parser_factory is None
                else self._parser_factory()
            )
            self._markdown = self.source + markdown

            async def await_append() -> None:
                async with self.lock:
                    # Recalculate delta AFTER any previous append has finished
                    current_last = self._last_parsed_line
                    updated_source = "".join(
                        self._markdown.splitlines(keepends=True)[current_last:]
                    )
                    tokens = parser.parse(updated_source)
                    existing_blocks = [
                        child for child in self.children if isinstance(child, MarkdownBlock)
                    ]
                    start_line = current_last
                    for token in reversed(tokens):
                        if token.map is not None and token.level == 0:
                            self._last_parsed_line += token.map[0]
                            break

                    new_blocks = list(self._parse_markdown(tokens))
                    any_headers = any(
                        isinstance(block, MarkdownHeader) for block in new_blocks
                    )
                    for block in new_blocks:
                        start, end = block.source_range
                        block.source_range = (
                            start + start_line,
                            end + start_line,
                        )

                    with self.app.batch_update():
                        if existing_blocks and new_blocks:
                            last_block = existing_blocks[-1]
                            last_block.source_range = new_blocks[0].source_range
                            try:
                                await last_block._update_from_block(new_blocks[0])
                            except IndexError:
                                pass
                            else:
                                new_blocks = new_blocks[1:]

                        if new_blocks:
                            await self.mount_all(new_blocks)

                    if any_headers:
                        self._table_of_contents = None
                        self.post_message(
                            Markdown.TableOfContentsUpdated(
                                self, self.table_of_contents
                            ).set_sender(self)
                        )

            return AwaitComplete(await_append())

        async def _chain() -> AwaitComplete:
            if previous is not None:
                await previous
            result = _do_append()
            setattr(self, "_pending_append", result)
            return result

        return AwaitComplete(_chain())

    Markdown.append = patched_append

# Fork model_call_id prefix used by consume_react_stream
_FORK_PREFIX = "fork::"


def _is_fork_model_call_id(model_call_id: str) -> bool:
    return model_call_id.startswith(_FORK_PREFIX)


def _extract_fork_id(model_call_id: str) -> str:
    """Extract fork_id from 'fork::<fork_id>'."""
    return model_call_id[len(_FORK_PREFIX):]


def _is_fork_tool_call_id(tool_call_id: str) -> bool:
    return tool_call_id.startswith(_FORK_PREFIX)


def _extract_fork_id_from_tool(tool_call_id: str) -> str | None:
    """Extract fork_id from 'fork::<fork_id>::tool::<tool_call_id>'."""
    if not tool_call_id.startswith(_FORK_PREFIX):
        return None
    rest = tool_call_id[len(_FORK_PREFIX):]
    parts = rest.split("::tool::", 1)
    return parts[0] if parts else None


class LazyHistoryScroll(VerticalScroll):
    """Scroll container that notifies the app before moving into older history."""

    def _maybe_expand_history(self) -> None:
        app = self.app
        if hasattr(app, "_request_render_window_expansion"):
            app._request_render_window_expansion()

    def action_scroll_up(self) -> None:
        self._maybe_expand_history()
        super().action_scroll_up()

    def scroll_up(self, *args: Any, **kwargs: Any) -> None:
        self._maybe_expand_history()
        return super().scroll_up(*args, **kwargs)

    def scroll_page_up(self, *args: Any, **kwargs: Any) -> None:
        self._maybe_expand_history()
        return super().scroll_page_up(*args, **kwargs)

    def scroll_home(self, *args: Any, **kwargs: Any) -> None:
        self._maybe_expand_history()
        return super().scroll_home(*args, **kwargs)

    def _on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._maybe_expand_history()
        super()._on_mouse_scroll_up(event)

@dataclass
class _ModelState:
    """State for a single model response within a turn.

    Supports interleaved text and tool blocks:
    bubble contains [reasoning, text_block_1, tool_block_1, text_block_2, tool_block_2, ...]
    """

    call_id: str
    content: str = ""
    reasoning: str = ""
    bubble: Vertical | None = None
    content_widget: Markdown | None = None
    reasoning_widget: Static | None = None
    content_dirty: bool = False
    reasoning_dirty: bool = False
    finished: bool = False
    # Track whether we need a new content widget after a tool call
    _needs_new_content_widget: bool = False
    # Track how much of content has been fed to append() for incremental rendering
    _content_appended_len: int = 0


@dataclass
class _ToolState:
    """State for a single tool call card."""

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    result: str = ""
    stats_line: str = ""
    status: str = "running"
    success: bool = True
    card: Any = None
    output_dirty: bool = False
    result_dirty: bool = False
    status_dirty: bool = False


@dataclass
class _ForkPaneState:
    """State for a fork subagent tab pane."""

    fork_id: str
    pane_id: str
    scroll: VerticalScroll | None = None
    bubble: Vertical | None = None
    status: str = "running"  # running, completed, error


class LambdaCodingTUIApp(App[None]):
    """Main TUI application for the coding agent.

    Implements TUIStreamAdapter to consume event streams from llm_chat.
    Uses TabbedContent: main agent in first tab, fork subagents in dynamic tabs.
    Input box stays outside tabs - only main agent receives user input.
    """

    CSS = """
    Screen { layout: vertical; background: $background; color: $text; }

    #agent-tabs {
        height: 1fr;
    }

    /* Hide the tab bar when only main pane is present */
    #agent-tabs.single-pane ContentTabs {
        display: none;
    }

    #agent-tabs ContentTabs {
        background: $panel;
        height: 2;
    }

    #agent-tabs ContentTabs Underline .underline--bar {
        color: $primary;
        background: $primary-background;
    }

    #agent-tabs Tab {
        color: $text-muted;
        padding: 0 2;
    }

    #agent-tabs Tab.-active {
        color: $text;
        text-style: bold;
    }

    #main-chat-log {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }

    #lambda-logo {
        width: 100%;
        height: 1fr;
        align: center middle;
        content-align: center middle;
        color: $primary;
        text-style: bold;
    }

    #lambda-logo-mark {
        width: 100%;
        height: auto;
        content-align: center middle;
        color: $accent;
    }

    #lambda-logo-title {
        width: 100%;
        height: 1;
        content-align: center middle;
        color: $text;
        text-style: bold;
    }

    #lambda-logo-subtitle {
        width: 100%;
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }

    .fork-chat-log {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }

    #status-bar {
        dock: bottom;
        height: auto;
        layout: horizontal;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }

    #status-left { width: 1fr; }
    #status-right { width: auto; }

    #input-area {
        dock: bottom;
        height: auto;
    }

    #path-autocomplete {
        display: none;
        max-height: 8;
        margin: 0 1;
        border: tall $border;
    }

    #chat-input {
        height: auto;
        max-height: 8;
        margin: 0 1 1 1;
        border: tall $border;
    }

    .bubble {
        height: auto;
        margin: 0 0 1 0;
        padding: 1;
        border: round $border-blurred;
        background: $surface;
    }

    .user-bubble {
        border: round $success;
        background: $success-muted;
    }

    .model-bubble {
        border: none;
        background: transparent;
        padding: 0 1;
        margin: 0;
    }

    .model-bubble .body {
        margin: 0;
        padding: 0;
    }

    .model-bubble MarkdownFence {
        margin: 1 0;
        color: $text;
        background: $panel;
    }

    .role {
        text-style: bold;
        color: $foreground;
        margin: 0 0 1 0;
    }

    .reasoning {
        color: $text-muted;
        margin: 0 0 1 0;
    }

    .system-hint {
        color: $text-warning;
        margin: 1 0;
        text-style: italic;
    }

    .fork-header {
        color: $foreground;
        text-style: bold;
        margin: 0 0 1 0;
    }

    .working-indicator {
        color: $text-primary;
        text-style: italic;
        margin: 0 0 1 0;
        padding: 0 1;
    }

    .usage-footer {
        color: $text-primary;
        text-style: italic;
        margin: 1 0 0 0;
    }

    #queued-indicator {
        height: auto;
        background: $accent;
        color: $background;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [
        ("ctrl+c", "clear_input", "Clear input"),
        ("escape", "interrupt", "Interrupt"),
        ("ctrl+o", "toggle_tool_expand", "Expand/Collapse tool"),
        ("ctrl+p", "command_palette", "Commands"),
    ]

    def __init__(
        self,
        agent_func: Any,
        workspace: str,
        model_name: str = "unknown",
        git_info: str = "",
        provider_path: str | None = None,
        provider_id: str | None = None,
        environment_block: str = "",
        context_window: int = 200_000,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.agent_func = agent_func
        self.workspace = os.path.abspath(os.path.expanduser(str(workspace)))
        self.model_name = model_name
        self.git_info = git_info
        self.provider_path = provider_path
        self.provider_id = provider_id
        self.environment_block = environment_block
        self.context_window = context_window
        self._git_info_cache: str | None = None
        self._git_refresh_pending = False
        self.history: MessageList = []
        self._busy = False
        self._active_abort_signal: AbortSignal | None = None
        self._models: dict[str, _ModelState] = {}
        self._tools: dict[str, _ToolState] = {}
        self._current_model_call_id: str | None = None
        self._current_turn_bubble: Vertical | None = None
        self._fork_panes: dict[str, _ForkPaneState] = {}
        self._working_indicator: Static | None = None
        self._auto_scroll: bool = True
        self._last_prompt_tokens: int = 0
        self._skill_count: int = int(getattr(agent_func, "_skill_count", 0) or 0)
        self._queued_text: str | None = None
        self._flushing_models: set[str] = set()
        self._flushing_tools: set[str] = set()
        self._suppress_path_autocomplete = False

        # Session management
        self.session_manager = SessionManager(self.workspace)
        self._current_session_id: str | None = None
        self._current_session_has_user_message: bool = False
        self._message_nodes: dict[str, dict[str, Any]] = {}
        self._active_leaf_id: str | None = None
        self._next_message_seq: int = 1
        self._last_saved_history_len: int = 0
        self._save_timer: asyncio.TimerHandle | None = None

        # Plan management (session-scoped)
        self.plan_manager = PlanManager(self.workspace, self._current_session_id)
        self.plan_panel: PlanPanel | None = None
        self._save_debounce_secs = 30.0
        self._name_generated: bool = False
        self._original_session_name: str = ""  # for fork naming
        self._last_escape_at: datetime | None = None
        self._logo_visible = False
        self._initial_render_message_count = 8
        self._max_render_message_count = 20
        self._render_window_start = 0
        self._render_window_end = 0
        self._rendering_history_window = False
        self._live_turn_user_index: int | None = None
        self._live_model_history_indices: dict[str, int] = {}
        self._live_tool_history_indices: dict[str, int] = {}


    def _apply_theme_palette_to_rich(self) -> None:
        terminal_theme = terminal_theme_from_textual_theme(self.current_theme)
        self.ansi_theme_dark = terminal_theme
        self.ansi_theme_light = terminal_theme

    def _watch_theme(self, theme_name: str) -> None:
        super()._watch_theme(theme_name)
        self._apply_theme_palette_to_rich()

    def _recreate_plan_manager(self) -> None:
        """Recreate PlanManager with the current session_id."""
        self.plan_manager = PlanManager(self.workspace, self._current_session_id)
        if self.plan_panel is not None:
            self.plan_panel.plan_manager = self.plan_manager

    def compose(self) -> ComposeResult:
        self.plan_panel = PlanPanel(self.plan_manager, id="plan-panel")
        yield self.plan_panel
        with TabbedContent(id="agent-tabs", initial="main-pane"):
            with TabPane("Main", id="main-pane"):
                yield LazyHistoryScroll(id="main-chat-log")
        with Container(id="input-area"):
            yield OptionList(id="path-autocomplete")
            yield ChatInput(
                placeholder="Type a message... (Enter send, Shift+Enter newline, @ file)",
                id="chat-input",
            )
        yield Horizontal(
            Static("", id="status-left"),
            Static("", id="status-right"),
            id="status-bar",
        )

    def on_mount(self) -> None:
        _patch_markdown_append()
        self._update_status_bar()
        self._chat_input().focus()
        # Start with single-pane class (hides tab bar when no forks)
        self.query_one("#agent-tabs", TabbedContent).add_class("single-pane")
        # Start a new session
        self._current_session_id = self.session_manager.start_new_session()
        self._current_session_has_user_message = False
        self._original_session_name = ""
        self._reset_branch_state()
        self._recreate_plan_manager()
        self.run_worker(self._show_lambda_logo(), exclusive=False)

    async def on_unmount(self) -> None:
        """Save session on exit."""
        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = None
        if self._current_session_has_user_message or self._has_user_message():
            await self._do_auto_save()

    def _has_user_message(self) -> bool:
        return any(
            isinstance(msg, dict) and msg.get("role") == "user" for msg in self.history
        )

    def _reset_branch_state(self) -> None:
        self._message_nodes = {}
        self._active_leaf_id = None
        self._next_message_seq = 1
        self._last_saved_history_len = 0

    def _load_branch_state(self, data: dict[str, Any]) -> None:
        self._message_nodes = data.get("message_nodes", {})
        self._active_leaf_id = data.get("active_leaf_id")
        self._next_message_seq = data.get("next_message_seq", 1)
        self._last_saved_history_len = len(self.history)

    def _sync_branch_state_from_history(self) -> None:
        history_len = len(self.history)
        if history_len == self._last_saved_history_len:
            return
        if history_len < self._last_saved_history_len:
            (
                self._message_nodes,
                self._active_leaf_id,
                self._next_message_seq,
            ) = self.session_manager.history_to_message_tree(self.history)
        else:
            parent_id = self._active_leaf_id
            for message in self.history[self._last_saved_history_len:]:
                parent_id, self._next_message_seq = self.session_manager.append_message_node(
                    self._message_nodes,
                    parent_id,
                    message,
                    self._next_message_seq,
                )
            self._active_leaf_id = parent_id
        self._last_saved_history_len = history_len

    def _scroll_to_bottom(
        self, scroll_widget: VerticalScroll | None = None, *, force: bool = False
    ) -> None:
        """Scroll to bottom, unless the user scrolled up and force is false."""
        if scroll_widget is None:
            scroll_widget = self.query_one("#main-chat-log", VerticalScroll)
        if not force and not self._is_at_bottom(scroll_widget):
            return
        scroll_widget.scroll_end(animate=False)
        if force:
            self.call_after_refresh(lambda: scroll_widget.scroll_end(animate=False))
        self._auto_scroll = True

    def _is_at_bottom(self, scroll_widget: VerticalScroll) -> bool:
        """Check if scroll is at or near the bottom (within 5 rows)."""
        max_y = scroll_widget.max_scroll_y
        if max_y <= 0:
            return True  # Nothing to scroll, treat as at bottom
        current_y = scroll_widget.scroll_y
        return current_y >= max_y - 5

    def _request_render_window_expansion(self) -> None:
        """Request older history before the scroll action consumes the viewport."""
        self._auto_scroll = False
        if not self._rendering_history_window:
            self.run_worker(self._expand_render_window_for_scroll_up(), exclusive=False)

    def on_vertical_scroll_scroll_up(self, event) -> None:
        """Compatibility hook for any bubbled scroll-up messages."""
        self._request_render_window_expansion()

    def on_vertical_scroll_scroll_down(self, event) -> None:
        """User scrolled down — re-enable if at bottom."""
        try:
            chat_log = self.query_one("#main-chat-log", VerticalScroll)
            if self._is_at_bottom(chat_log):
                self._auto_scroll = True
        except Exception:
            pass

    def _modal_screen_active(self) -> bool:
        return any(screen.is_modal for screen in self.screen_stack[1:])

    def on_click(self, event) -> None:
        if not self._modal_screen_active():
            self._chat_input().focus()

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("Switch Model", "Select a different LLM model", self._open_model_selector)
        yield SystemCommand("Sessions", "Browse and switch sessions", self._open_session_selector)
        yield SystemCommand("Rewind", "Browse this session's branch tree and rewind to a user message", self._open_rewind_selector)
        yield SystemCommand("Refresh Skills", "Reload discovered Agent Skills", self._refresh_skills)
        yield SystemCommand("Clear Chat", "Clear all chat history", self._clear_chat)

    def _do_undo(self) -> None:
        """Undo last file edit (command palette callback)."""
        from lambda_coding_agent.tools.edit import undo_stack

        if undo_stack:
            entry = undo_stack.pop()
            try:
                with open(entry["abs_path"], "w", encoding="utf-8") as f:
                    f.write(entry["before_content"])
                self.notify(f"Undid edit on {entry['file_path']}")
            except Exception as e:
                self.notify(f"Undo failed: {e}", severity="error")
        else:
            self.notify("Nothing to undo.", severity="warning")

    def _update_status_bar(self) -> None:
        left = f"  {self.workspace}"
        git = self._git_info_cache or self.git_info
        if git:
            left += f"  |  {git}"
        right = f"model: {self.model_name}  |  skills: {self._skill_count}"
        if self._last_prompt_tokens > 0 and self.context_window > 0:
            pct = int(self._last_prompt_tokens * 100 / self.context_window)
            right += f"  |  ctx: {pct}%"
        right += "  "
        self.query_one("#status-left", Static).update(left)
        self.query_one("#status-right", Static).update(right)

    def _lambda_logo(self) -> Vertical:
        return Vertical(
            Static(
                "\n".join(
                    [
                        ' __     __   _  _  ____  ____   __  ',
                        '(  )   / _\\ ( \\/ )(  _ \\(    \\ / _\\ ',
                        '/ (_/\\/    \\/ \\/ \\ ) _ ( ) D (/    \\',
                        '\\____/\\_/\\_/\\_)(_/(____/(____/\\_/\\_/',
                    ]
                ),
                id="lambda-logo-mark",
            ),
            Static("LambdaCodingAgent", id="lambda-logo-title"),
            Static("Send a message or choose a session to begin.", id="lambda-logo-subtitle"),
            id="lambda-logo",
        )

    async def _show_lambda_logo(self) -> None:
        chat_log = self.query_one("#main-chat-log", VerticalScroll)
        if chat_log.children:
            return
        await chat_log.mount(self._lambda_logo())
        self._logo_visible = True

    async def _hide_lambda_logo(self) -> None:
        if not self._logo_visible:
            return
        try:
            logo = self.query_one("#lambda-logo", Vertical)
            await logo.remove()
        except Exception:
            pass
        self._logo_visible = False

    def _refresh_git_info(self) -> None:
        """Synchronously query git status for the status bar."""
        import subprocess

        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if branch.returncode != 0:
                self._git_info_cache = ""
                return
            branch_name = branch.stdout.strip()

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=3,
            )
            lines = [l for l in status.stdout.strip().split("\n") if l.strip()]
            modified = len(lines)
            clean = "clean" if modified == 0 else f"{modified} modified"
            self._git_info_cache = f"{branch_name}, {clean}"
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            self._git_info_cache = ""

    def _schedule_git_refresh(self) -> None:
        """Debounce git status refresh — runs once after DOM refresh."""
        if self._git_refresh_pending:
            return
        self._git_refresh_pending = True
        self.call_after_refresh(self._do_git_refresh)

    def _do_git_refresh(self) -> None:
        self._git_refresh_pending = False
        self._refresh_git_info()
        self._update_status_bar()

    def action_interrupt(self) -> None:
        """Handle escape key: abort current turn, or open tree on double Esc."""
        now = datetime.now(timezone.utc)
        if (
            self._last_escape_at is not None
            and now - self._last_escape_at <= timedelta(seconds=0.75)
            and not self._modal_screen_active()
        ):
            self._last_escape_at = None
            self.run_worker(self._open_session_tree(), exclusive=False)
            return
        self._last_escape_at = now
        if self._busy and self._active_abort_signal:
            self._active_abort_signal.abort("user interrupted")

    def action_clear_input(self) -> None:
        """Clear the chat input without exiting the TUI."""
        input_widget = self._chat_input()
        input_widget.value = ""
        input_widget.focus()

    def action_toggle_tool_expand(self) -> None:
        """Toggle expand/collapse on ALL ToolBlock widgets, preserving visual position.

        Strategy: track the widget at the top of the viewport by its unique
        identity (Python id). After expand/collapse, find that same widget
        and scroll so its top edge is at the same screen row.
        """
        from lambda_coding_agent.tui.tool_cards import ToolBlock

        chat_log = self.query_one("#main-chat-log", VerticalScroll)
        scroll_y = chat_log.scroll_y

        # Record the widget ID of the first visible direct child of the scroll
        # container (bubbles, tool blocks, hints, etc.)
        anchor_widget_id = None
        try:
            for child in chat_log.children:
                try:
                    region = child.region
                    # The first child whose top is at or below the scroll offset
                    if region.y >= scroll_y - 1:
                        anchor_widget_id = id(child)
                        break
                except Exception:
                    continue
        except Exception:
            pass

        self._tools_expanded = not getattr(self, "_tools_expanded", False)
        try:
            for block in chat_log.query(ToolBlock):
                if isinstance(block, ToolBlock):
                    block._expanded = self._tools_expanded
                    block._refresh_content_display()
            # Also check fork panes
            for pane in self._fork_panes.values():
                if pane.bubble:
                    for block in pane.bubble.query(ToolBlock):
                        if isinstance(block, ToolBlock):
                            block._expanded = self._tools_expanded
                            block._refresh_content_display()
        except Exception:
            pass

        # Restore visual position by finding the anchor widget and scrolling to it
        if anchor_widget_id is not None:
            try:
                for child in chat_log.children:
                    if id(child) == anchor_widget_id:
                        try:
                            new_region = child.region
                            chat_log.scroll_to(y=new_region.y, animate=False)
                        except Exception:
                            pass
                        break
            except Exception:
                pass
        else:
            # Fallback: preserve absolute scroll_y
            chat_log.scroll_to(y=scroll_y, animate=False)

        self._scroll_to_bottom(chat_log, force=True)

    # ── Working indicator & tab labels ─────────────────────────

    _SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    _SPINNER_INTERVAL_SECONDS = 0.2

    async def _show_working_indicator(self) -> None:
        """Show a spinner indicator at the bottom of the main chat log."""
        import time

        self._working_start_time = time.monotonic()
        self._spinner_frame = 0

        chat_log = self.query_one("#main-chat-log", VerticalScroll)
        indicator = Static(self._format_spinner_text(), classes="working-indicator")
        self._working_indicator = indicator
        await chat_log.mount(indicator)

        # Start a timer to update spinner
        self._spinner_timer = self.set_interval(self._SPINNER_INTERVAL_SECONDS, self._tick_spinner)

        self._scroll_to_bottom(chat_log)
        self._update_main_tab_label(busy=True)

    def _format_spinner_text(self) -> str:
        import time

        frame = self._SPINNER_FRAMES[self._spinner_frame % len(self._SPINNER_FRAMES)]
        elapsed = int(time.monotonic() - self._working_start_time)
        return f"{frame} Thinking (esc to interrupt, {elapsed}s elapsed)"

    def _tick_spinner(self) -> None:
        """Update the spinner frame and elapsed time."""
        self._spinner_frame += 1
        if self._working_indicator is not None:
            self._working_indicator.update(self._format_spinner_text())
            self._scroll_to_bottom()

    async def _hide_working_indicator(self) -> None:
        """Remove the spinner indicator."""
        if hasattr(self, "_spinner_timer") and self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._working_indicator is not None:
            await self._working_indicator.remove()
            self._working_indicator = None
        self._update_main_tab_label(busy=False)

    async def _reposition_working_indicator(self) -> None:
        """Move the working indicator to the very bottom of main-chat-log."""
        if self._working_indicator is None:
            return
        chat_log = self.query_one("#main-chat-log", VerticalScroll)
        # Remove and re-mount at the end
        await self._working_indicator.remove()
        await chat_log.mount(self._working_indicator)

    def _update_main_tab_label(self, busy: bool) -> None:
        """Update Main tab label with working indicator."""
        tabs = self.query_one("#agent-tabs", TabbedContent)
        try:
            tab = tabs.get_tab("main-pane")
            tab.label = "\u27f3 Main" if busy else "Main"
        except Exception:
            pass

    # ── Fork tab pane management ──────────────────────────────

    def _update_tabs_visibility(self) -> None:
        """Show/hide tab bar based on whether fork panes exist."""
        tabs = self.query_one("#agent-tabs", TabbedContent)
        if self._fork_panes:
            tabs.remove_class("single-pane")
        else:
            tabs.add_class("single-pane")

    async def _ensure_fork_pane(self, fork_id: str) -> _ForkPaneState:
        """Create or return an existing fork tab pane."""
        if fork_id in self._fork_panes:
            return self._fork_panes[fork_id]

        pane_id = f"fork-pane-{fork_id}"
        short_id = fork_id[:8]
        label = f"\u27f3 Fork {short_id}"

        tabs = self.query_one("#agent-tabs", TabbedContent)

        # Create the pane with a scrollable chat log
        scroll = VerticalScroll(classes="fork-chat-log")
        pane = TabPane(label, scroll, id=pane_id)
        await tabs.add_pane(pane)

        fork_state = _ForkPaneState(
            fork_id=fork_id,
            pane_id=pane_id,
            scroll=scroll,
        )
        self._fork_panes[fork_id] = fork_state
        self._update_tabs_visibility()
        return fork_state

    async def _finish_fork_pane(self, fork_id: str, status: str) -> None:
        """Mark a fork pane as completed/error and update its tab label."""
        fork_state = self._fork_panes.get(fork_id)
        if fork_state is None:
            return

        fork_state.status = status
        tabs = self.query_one("#agent-tabs", TabbedContent)

        # Update tab label with status indicator
        icon = "\u2713" if status == "completed" else "\u2717"
        short_id = fork_id[:8]
        try:
            tab = tabs.get_tab(fork_state.pane_id)
            tab.label = f"{icon} Fork {short_id}"
        except Exception:
            pass

    async def _cleanup_finished_forks(self) -> None:
        """Remove all finished fork panes."""
        tabs = self.query_one("#agent-tabs", TabbedContent)
        finished = [
            fid for fid, fs in self._fork_panes.items()
            if fs.status in ("completed", "error")
        ]
        for fork_id in finished:
            fork_state = self._fork_panes.pop(fork_id)
            try:
                await tabs.remove_pane(fork_state.pane_id)
            except Exception:
                pass
        self._update_tabs_visibility()

    def _get_fork_scroll(self, fork_id: str) -> VerticalScroll | None:
        """Get the scroll widget for a fork pane."""
        fork_state = self._fork_panes.get(fork_id)
        if fork_state:
            return fork_state.scroll
        return None

    # ── TUIStreamAdapter protocol ──────────────────────────────

    async def start_model_response(self, model_call_id: str) -> None:
        self._current_model_call_id = model_call_id
        state = _ModelState(call_id=model_call_id)
        self._models[model_call_id] = state

        # Determine target: fork pane or main chat log
        if _is_fork_model_call_id(model_call_id):
            fork_id = _extract_fork_id(model_call_id)
            fork_state = await self._ensure_fork_pane(fork_id)
            chat_log = fork_state.scroll

            # Each fork model call gets its own bubble in the fork pane
            bubble = Vertical(classes="bubble model-bubble")
            await chat_log.mount(bubble)
            fork_state.bubble = bubble
        else:
            chat_log = self.query_one("#main-chat-log", VerticalScroll)

            # Reuse existing turn bubble or create new one
            if self._current_turn_bubble is None:
                bubble = Vertical(classes="bubble model-bubble")
                await chat_log.mount(bubble)
                self._current_turn_bubble = bubble
            else:
                bubble = self._current_turn_bubble

        state.bubble = bubble

        # Mount reasoning (hidden initially) and first content widget
        reasoning = Static("", classes="reasoning")
        content = Markdown("", classes="body")

        reasoning.display = False
        content.display = False

        await bubble.mount(reasoning, content)

        state.content_widget = content
        state.reasoning_widget = reasoning

        if _is_fork_model_call_id(model_call_id):
            chat_log.scroll_end(animate=False)
        else:
            self._scroll_to_bottom(chat_log)

        # Keep working indicator at the very bottom of main chat log
        if not _is_fork_model_call_id(model_call_id):
            index = len(self.history)
            self.history.append({"role": "assistant", "content": ""})
            self._live_model_history_indices[model_call_id] = index
            self._render_window_end = len(self.history)
            await self._reposition_working_indicator()
            self._trim_rendered_history_after_live_append()

    async def append_model_content(
        self, model_call_id: str, content_delta: str
    ) -> None:
        state = self._models.get(model_call_id)
        if state is None:
            return

        # If we need a new content widget (after tool call), create one
        if state._needs_new_content_widget and state.bubble:
            new_content = Markdown("", classes="body")
            new_content.display = False
            await state.bubble.mount(new_content)
            state.content_widget = new_content
            state.content = ""
            state._content_appended_len = 0
            state._needs_new_content_widget = False

        state.content += content_delta
        state.content_dirty = True
        self._update_live_model_history(model_call_id)
        if content_delta:
            self._schedule_model_flush(model_call_id)

    async def append_model_reasoning(
        self, model_call_id: str, reasoning_delta: str
    ) -> None:
        state = self._models.get(model_call_id)
        if state is None:
            return
        state.reasoning += reasoning_delta
        state.reasoning_dirty = True
        self._update_live_model_history(model_call_id)
        self._schedule_model_flush(model_call_id)

    async def finish_model_response(
        self, model_call_id: str, stats_line: str
    ) -> None:
        state = self._models.get(model_call_id)
        if state is None:
            return
        state.finished = True
        self._update_live_model_history(model_call_id)
        self._schedule_model_flush(model_call_id)

        # Show usage footer in the bubble (skip for forks)
        if not _is_fork_model_call_id(model_call_id) and stats_line and state.bubble:
            footer = Static(stats_line, classes="usage-footer")
            await state.bubble.mount(footer)

        # Parse prompt_tokens from stats_line and update ctx%
        match = re.search(r"tokens (\d+)/", stats_line)
        if match:
            self._last_prompt_tokens = int(match.group(1))
            self._update_status_bar()

        # Handle fork lifecycle: mark fork tab as done
        if _is_fork_model_call_id(model_call_id):
            fork_id = _extract_fork_id(model_call_id)
            if "error" in stats_line:
                await self._finish_fork_pane(fork_id, "error")
            elif "completed" in stats_line:
                await self._finish_fork_pane(fork_id, "completed")

    async def start_tool_call(
        self,
        model_call_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
    ) -> None:
        state = _ToolState(call_id=tool_call_id, name=tool_name, arguments=arguments)
        self._tools[tool_call_id] = state

        card = create_tool_card(tool_name=tool_name, arguments=arguments)
        state.card = card

        # Apply current expand/collapse state to newly created tool blocks
        if getattr(self, "_tools_expanded", False):
            from lambda_coding_agent.tui.tool_cards import ToolBlock
            if isinstance(card, ToolBlock):
                card._expanded = True
                card._refresh_content_display()

        model_state = self._models.get(model_call_id)
        if model_state and model_state.bubble:
            await model_state.bubble.mount(card)
            # After a tool call, next content should go into a new widget
            model_state._needs_new_content_widget = True

        if not _is_fork_tool_call_id(tool_call_id):
            index = len(self.history)
            self.history.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": "",
            })
            self._live_tool_history_indices[tool_call_id] = index
            self._render_window_end = len(self.history)
            self._update_live_model_history(model_call_id)
            self._trim_rendered_history_after_live_append()

    async def append_tool_output(
        self, tool_call_id: str, output_delta: str
    ) -> None:
        state = self._tools.get(tool_call_id)
        if state is None:
            return
        state.output += output_delta
        state.output_dirty = True
        self._update_live_tool_history(tool_call_id)
        self._schedule_tool_flush(tool_call_id)

    async def append_tool_argument(
        self,
        tool_call_id: str,
        argname: str,
        argcontent_delta: str,
    ) -> None:
        state = self._tools.get(tool_call_id)
        if state is None:
            return
        current = state.arguments.get(argname, "")
        state.arguments[argname] = current + argcontent_delta
        if state.card:
            state.card.update_arguments(state.arguments)

    async def set_tool_status(self, tool_call_id: str, status: str) -> None:
        state = self._tools.get(tool_call_id)
        if state is None:
            return
        state.status = status
        state.status_dirty = True
        self._schedule_tool_flush(tool_call_id)

    async def request_tool_input(
        self,
        tool_call_id: str,
        request_id: str,
        prompt: str,
    ) -> None:
        # Not used by our tools
        pass

    async def clear_tool_input(self, tool_call_id: str) -> None:
        pass

    async def finish_tool_call(
        self,
        tool_call_id: str,
        result_markdown: str,
        stats_line: str,
        success: bool,
    ) -> None:
        state = self._tools.get(tool_call_id)
        if state is None:
            return
        state.result = result_markdown
        state.stats_line = stats_line
        state.success = success
        state.status = "success" if success else "error"
        self._update_live_tool_history(tool_call_id)
        state.result_dirty = True
        state.status_dirty = True
        self._schedule_tool_flush(tool_call_id)

        # Refresh plan panel immediately after plan tool or execute_code completion
        tool_name = state.name
        if tool_name.startswith("plan_") or tool_name == "execute_code":
            if self.plan_panel is not None:
                self.plan_panel.refresh_if_active()

        # Refresh git status after execute_code (may modify files)
        if tool_name == "execute_code":
            self._schedule_git_refresh()

    # ── Rendering helpers ─────────────────────────────────────

    def _update_live_model_history(self, model_call_id: str) -> None:
        index = self._live_model_history_indices.get(model_call_id)
        state = self._models.get(model_call_id)
        if index is None or state is None or index >= len(self.history):
            return
        message = self.history[index]
        if message.get("role") != "assistant":
            return
        message["content"] = state.content
        if state.reasoning:
            message["reasoning"] = state.reasoning

    def _update_live_tool_history(self, tool_call_id: str) -> None:
        index = self._live_tool_history_indices.get(tool_call_id)
        state = self._tools.get(tool_call_id)
        if index is None or state is None or index >= len(self.history):
            return
        message = self.history[index]
        if message.get("role") != "tool":
            return
        message["content"] = state.result or state.output
        message["name"] = state.name
        message["tool_call_id"] = tool_call_id

    def _trim_rendered_history_after_live_append(self) -> None:
        """Keep live streaming widgets visible while enforcing the 20-message cap."""
        try:
            chat_log = self.query_one("#main-chat-log", VerticalScroll)
        except Exception:
            return
        overflow = len(chat_log.children) - self._max_render_message_count
        if overflow <= 0:
            return
        removable = list(chat_log.children)[:overflow]
        self._render_window_start = min(
            len(self.history),
            self._render_window_start + len(removable),
        )
        for widget in removable:
            self.call_later(widget.remove)

    def _schedule_model_flush(self, model_call_id: str) -> None:
        if model_call_id in self._flushing_models:
            return
        self._flushing_models.add(model_call_id)
        self.call_after_refresh(self._flush_model, model_call_id)

    def _flush_model(self, model_call_id: str) -> None:
        getattr(self, "_flushing_models", set()).discard(model_call_id)
        state = self._models.get(model_call_id)
        if state is None:
            return
        if state.content_dirty and state.content_widget:
            delta = state.content[state._content_appended_len:]
            if delta:
                state.content_widget.append(delta)
                state._content_appended_len = len(state.content)
            state.content_widget.display = True
            state.content_dirty = False
        if state.reasoning_dirty and state.reasoning_widget:
            state.reasoning_widget.update(state.reasoning)
            state.reasoning_widget.display = True
            state.reasoning_dirty = False

        # Scroll the appropriate pane
        if _is_fork_model_call_id(model_call_id):
            fork_id = _extract_fork_id(model_call_id)
            scroll = self._get_fork_scroll(fork_id)
            if scroll:
                scroll.scroll_end(animate=False)
        else:
            self._scroll_to_bottom()

    def _schedule_tool_flush(self, tool_call_id: str) -> None:
        if tool_call_id in self._flushing_tools:
            return
        self._flushing_tools.add(tool_call_id)
        self.call_after_refresh(self._flush_tool, tool_call_id)

    def _flush_tool(self, tool_call_id: str) -> None:
        getattr(self, "_flushing_tools", set()).discard(tool_call_id)
        state = self._tools.get(tool_call_id)
        if state is None or state.card is None:
            return
        if state.output_dirty:
            state.card.update_output(state.output)
            state.output_dirty = False
        if state.result_dirty:
            state.card.update_result(state.result, state.success)
            state.result_dirty = False
        if state.status_dirty:
            state.card.update_status(state.status)
            state.status_dirty = False

        # Scroll the appropriate pane
        fork_id = _extract_fork_id_from_tool(tool_call_id)
        if fork_id:
            scroll = self._get_fork_scroll(fork_id)
            if scroll:
                scroll.scroll_end(animate=False)
        else:
            self._scroll_to_bottom()

    # ── User input handling ───────────────────────────────────

    def _chat_input(self) -> ChatInput:
        return self.query_one("#chat-input", ChatInput)

    def _path_autocomplete(self) -> OptionList:
        return self.query_one("#path-autocomplete", OptionList)

    def _active_path_token(self) -> tuple[int, str] | None:
        input_widget = self._chat_input()
        prefix = input_widget.value[:input_widget.cursor_position]
        match = re.search(r"(?:^|\s)@([^\s]*)$", prefix)
        if not match:
            return None
        return match.start(1), match.group(1)

    def _matching_workspace_paths(self, query: str) -> list[str]:
        matches: list[tuple[tuple[int, int, str], str]] = []
        query = query.replace(os.sep, "/").replace("\\", "/").strip("/")
        query_lower = query.casefold()
        query_parts = [part for part in query_lower.split("/") if part]

        def match_score(rel_path: str) -> tuple[int, int, str] | None:
            rel_lower = rel_path.casefold()
            name_lower = rel_lower.rsplit("/", 1)[-1]
            if not query_lower or rel_lower.startswith(query_lower):
                return (0, len(rel_path), rel_path)
            if f"/{query_lower}" in rel_lower:
                return (1, len(rel_path), rel_path)
            if name_lower.startswith(query_lower):
                return (2, len(rel_path), rel_path)
            if query_lower in name_lower:
                return (3, len(rel_path), rel_path)
            if query_lower in rel_lower:
                return (4, len(rel_path), rel_path)
            if len(query_parts) > 1:
                path_parts = rel_lower.split("/")
                index = 0
                for part in query_parts:
                    for index in range(index, len(path_parts)):
                        if part in path_parts[index]:
                            index += 1
                            break
                    else:
                        return None
                return (5, len(rel_path), rel_path)
            return None

        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in {".git", ".lambda", "__pycache__"}]
            rel_root = os.path.relpath(root, self.workspace)
            if rel_root == ".":
                rel_root = ""
            for name in files:
                rel_path = os.path.join(rel_root, name) if rel_root else name
                rel_path = rel_path.replace(os.sep, "/")
                score = match_score(rel_path)
                if score is not None:
                    matches.append((score, rel_path))
        return [path for _, path in sorted(matches)[:8]]

    def _update_path_autocomplete(self) -> None:
        popup = self._path_autocomplete()
        active = self._active_path_token()
        if active is None:
            popup.display = False
            popup.clear_options()
            return
        _, query = active
        matches = self._matching_workspace_paths(query)
        if not matches:
            popup.display = False
            popup.clear_options()
            return
        popup.clear_options().add_options(matches)
        popup.highlighted = 0
        popup.display = True

    def _hide_path_autocomplete(self) -> None:
        popup = self._path_autocomplete()
        popup.display = False
        popup.clear_options()

    def _insert_path_completion(self, path: str) -> None:
        active = self._active_path_token()
        if active is None:
            return
        start, query = active
        input_widget = self._chat_input()
        end = start + len(query)
        self._suppress_path_autocomplete = True
        input_widget.insert_path_completion(start, end, path)
        self._hide_path_autocomplete()
        input_widget.focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self._chat_input():
            return
        popup = self._path_autocomplete()
        if popup.display and popup.highlighted is not None:
            option = popup.get_option_at_index(popup.highlighted)
            self._insert_path_completion(str(option.prompt))
            return

        text = event.value
        if not text.strip():
            return

        input_widget = self._chat_input()
        input_widget.value = ""

        lowered = text.strip().lower()

        # "/" opens command palette
        if lowered == "/":
            self.action_command_palette()
            return
        if lowered in ("/skills refresh", "/refresh skills"):
            await self._refresh_skills()
            return
        if lowered.startswith("/"):
            await self._append_system_hint(
                f"Unknown command: {text}. Press / for the command palette."
            )
            return

        if self._busy:
            # Queue the message - show indicator, don't interrupt yet
            self._queued_text = text
            await self._show_queued_indicator(text)
            return

        await self._append_user_message(text)
        self._busy = True
        self.run_worker(self._run_turn(text), thread=False)

    async def _append_user_message(self, text: str) -> None:
        self._current_session_has_user_message = True
        self.history.append({"role": "user", "content": text})
        self._live_turn_user_index = len(self.history) - 1
        self._render_window_end = len(self.history)
        # Reset turn bubble so next assistant response starts fresh
        self._current_turn_bubble = None
        # Re-enable auto-scroll on user message
        self._auto_scroll = True

        chat_log = self.query_one("#main-chat-log", VerticalScroll)
        await self._hide_lambda_logo()

        bubble = Vertical(classes="bubble user-bubble")
        await chat_log.mount(bubble)

        # Now bubble is in the DOM, safe to mount children
        role = Static("You", classes="role")
        body = Static(text, classes="body")
        await bubble.mount(role, body)

        self._scroll_to_bottom(chat_log, force=True)

        # Refresh git status — previous agent turn may have modified files
        self._schedule_git_refresh()

        # Clean up finished fork panes from previous turn
        await self._cleanup_finished_forks()
        self._trim_rendered_history_after_live_append()

    async def _append_system_hint(self, text: str) -> None:
        hint = Static(text, classes="system-hint")
        chat_log = self.query_one("#main-chat-log", VerticalScroll)
        await chat_log.mount(hint)
        self._scroll_to_bottom(chat_log)

    async def _show_queued_indicator(self, text: str) -> None:
        """Show or update the queued message indicator above the input."""
        label = f"QUEUED (Press \u2191 to interrupt and send immediately)  {text}"
        try:
            indicator = self.query_one("#queued-indicator", Static)
            indicator.update(label)
        except Exception:
            indicator = Static(label, id="queued-indicator")
            await self.mount(indicator, before=self.query_one("#input-area"))

    async def _hide_queued_indicator(self) -> None:
        """Remove the queued message indicator."""
        try:
            indicator = self.query_one("#queued-indicator", Static)
            await indicator.remove()
        except Exception:
            pass

    async def on_key(self, event) -> None:
        """Handle queued-message interrupts and path autocomplete navigation."""
        if event.key == "up" and self._queued_text:
            event.prevent_default()
            event.stop()
            text = self._queued_text
            self._queued_text = None
            await self._hide_queued_indicator()
            if self._active_abort_signal:
                self._active_abort_signal.abort("user sent new message")
            self._pending_user_text = text
            return

        try:
            popup = self._path_autocomplete()
        except Exception:
            return
        if not popup.display:
            return
        if event.key == "up":
            event.prevent_default()
            event.stop()
            popup.action_cursor_up()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            popup.action_cursor_down()
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            self._hide_path_autocomplete()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Detect '/' commands and update @ file path autocomplete."""
        try:
            inp = self._chat_input()
        except Exception:
            return
        if event.input is not inp:
            return
        if event.value == "/" and inp.has_focus:
            inp.value = ""
            self._hide_path_autocomplete()
            self.action_command_palette()
            return
        if self._suppress_path_autocomplete:
            self._suppress_path_autocomplete = False
            self._hide_path_autocomplete()
            return
        self._update_path_autocomplete()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is self._path_autocomplete():
            event.stop()
            self._insert_path_completion(str(event.option.prompt))

    async def _clear_chat(self) -> None:
        chat_log = self.query_one("#main-chat-log", VerticalScroll)
        await chat_log.remove_children()
        self._logo_visible = False
        self.history = []
        self._reset_branch_state()
        self._models.clear()
        self._tools.clear()
        self._current_turn_bubble = None
        self._flushing_models.clear()
        self._flushing_tools.clear()
        # Remove all fork panes
        tabs = self.query_one("#agent-tabs", TabbedContent)
        for fork_state in list(self._fork_panes.values()):
            try:
                await tabs.remove_pane(fork_state.pane_id)
            except Exception:
                pass
        self._fork_panes.clear()
        self._update_tabs_visibility()

    # ── Modal selectors ────────────────────────────────────────

    async def _open_model_selector(self) -> None:
        """Open the model selection modal."""
        from lambda_coding_agent.tui.screens.model_select import ModelSelectModalScreen

        screen = ModelSelectModalScreen(
            provider_path=self.provider_path,
            current_model_name=self.model_name,
        )

        def on_select(result: Any) -> None:
            if result is None:
                return
            pid, model_name, ctx = result
            self._switch_model(pid, model_name, ctx)

        self.push_screen(screen, on_select)

    def _switch_model(self, provider_id: str, model_name: str, context_window: int) -> None:
        """Switch to a new model, recreating the agent."""
        from lambda_coding_agent.agent import create_agent

        try:
            new_agent = create_agent(
                provider_path=self.provider_path,
                workspace=self.workspace,
                environment_block=self.environment_block,
                model_name=model_name,
                provider_id=provider_id,
                session_id=self._current_session_id,
            )
            self.agent_func = new_agent
            self._skill_count = int(getattr(new_agent, "_skill_count", 0) or 0)
            self.model_name = model_name
            self.context_window = context_window
            self.provider_id = provider_id
            self._last_prompt_tokens = 0
            self._update_status_bar()
            self._schedule_auto_save()
        except Exception as e:
            self.notify(f"Failed to switch model: {e}", severity="error")

    async def _refresh_skills(self) -> None:
        """Refresh discovered skills by recreating the agent for future turns."""
        from lambda_coding_agent.agent import create_agent

        try:
            new_agent = create_agent(
                provider_path=self.provider_path,
                workspace=self.workspace,
                environment_block=self.environment_block,
                model_name=self.model_name,
                provider_id=self.provider_id,
                session_id=self._current_session_id,
            )
            self.agent_func = new_agent
            self._skill_count = int(getattr(new_agent, "_skill_count", 0) or 0)
            self._update_status_bar()
            if self.is_mounted:
                await self._append_system_hint(f"Skills refreshed: {self._skill_count} loaded.")
        except Exception as e:
            self.notify(f"Failed to refresh skills: {e}", severity="error")

    async def _open_session_selector(self) -> None:
        """Open the session list modal."""
        from lambda_coding_agent.tui.screens.session_list import SessionListModalScreen

        screen = SessionListModalScreen(session_manager=self.session_manager)

        def on_select(result: Any) -> None:
            if result is None:
                # New Session
                self.run_worker(self._start_new_session(), exclusive=False)
            else:
                # Load existing session
                self.run_worker(self._load_session(result), exclusive=False)

        self.push_screen(screen, on_select)

    async def _open_rewind_selector(self) -> None:
        """Open the same branch-aware session tree used by double Esc."""
        await self._open_session_tree()

    async def _open_session_tree(self) -> None:
        """Open the branch-aware session tree, triggered by double Esc."""
        from lambda_coding_agent.tui.screens.session_tree import SessionTreeScreen

        if self._busy:
            await self._append_system_hint("Wait for the current turn to finish before opening the session tree.")
            return
        self._sync_branch_state_from_history()
        if not self._message_nodes:
            await self._append_system_hint("No messages in the session tree yet.")
            return

        screen = SessionTreeScreen(
            message_nodes=self._message_nodes,
            active_leaf_id=self._active_leaf_id,
            session_name=self._original_session_name,
            on_switch=self._switch_branch,
            on_rewind=self._rewind_to_node,
        )

        def on_select(result: Any) -> None:
            if result is None:
                return
            action, node_id = result
            if action == "switch":
                self.run_worker(self._switch_branch(node_id), exclusive=False)
            elif action == "rewind":
                self.run_worker(self._rewind_to_node(node_id), exclusive=False)

        self.push_screen(screen, on_select)

    # ── Session management ─────────────────────────────────────

    async def _start_new_session(self) -> None:
        """Save current session, create a new one, clear chat."""
        await self._do_auto_save()
        self._current_session_id = self.session_manager.start_new_session()
        self._current_session_has_user_message = False
        self._original_session_name = ""
        self._name_generated = False
        self._reset_branch_state()
        self._recreate_plan_manager()
        await self._clear_chat()
        await self._show_lambda_logo()

    async def _load_session(self, session_id: str) -> None:
        """Auto-save current, then load a session from disk."""
        await self._do_auto_save()

        try:
            data = self.session_manager.load_session(session_id)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            await self._append_system_hint(f"Failed to load session: {e}")
            return

        self._current_session_id = session_id
        self._recreate_plan_manager()
        self.history = data.get("history", [])
        self._load_branch_state(data)
        self._current_session_has_user_message = self._has_user_message()
        self.model_name = data.get("model_name", self.model_name)
        self._last_prompt_tokens = data.get("last_ctx_usage", 0)
        self._name_generated = True  # Name is already set
        self._original_session_name = data.get("name", "")

        # Recreate agent if provider/model changed
        saved_pid = data.get("provider_id", "")
        if saved_pid and saved_pid != self.provider_id:
            self.provider_id = saved_pid
            try:
                from lambda_coding_agent.agent import create_agent

                self.agent_func = create_agent(
                    provider_path=self.provider_path,
                    workspace=self.workspace,
                    environment_block=self.environment_block,
                    model_name=self.model_name,
                    provider_id=saved_pid,
                    session_id=session_id,
                )
                self._skill_count = int(getattr(self.agent_func, "_skill_count", 0) or 0)
            except Exception:
                pass

        await self._rebuild_chat_from_history()
        await self._hide_lambda_logo()
        self._scroll_to_bottom(force=True)
        self._update_status_bar()

    async def _rewind_and_fork(self, history_index: int, user_message_text: str) -> None:
        """Create a branch in the current session before the selected user message."""
        await self._do_auto_save()

        active_path = self.session_manager.active_path_ids(
            self._message_nodes,
            self._active_leaf_id,
        )
        node_id = active_path[history_index] if history_index < len(active_path) else None
        if node_id:
            await self._rewind_to_node(node_id, user_message_text=user_message_text)
            return

        truncate_after = self._find_preceding_turn_end(history_index)
        branch_history = list(self.history[:truncate_after + 1]) if truncate_after >= 0 else []
        self._active_leaf_id = active_path[truncate_after] if truncate_after >= 0 else None
        self._last_saved_history_len = len(branch_history)
        self.history = branch_history
        self._current_session_has_user_message = self._has_user_message()

        await self._finish_rewind(user_message_text)

    async def _rewind_to_node(
        self,
        node_id: str,
        user_message_text: str | None = None,
    ) -> None:
        """Rewind before a selected user node, leaving the old path as a sibling branch."""
        await self._do_auto_save()
        node = self._message_nodes.get(node_id)
        if not isinstance(node, dict):
            return
        message = node.get("message", {})
        if message.get("role") != "user":
            await self._switch_branch(node_id)
            return

        user_message_text = user_message_text if user_message_text is not None else str(message.get("content", ""))
        self._active_leaf_id = self.session_manager.parent_id_for_node(
            self._message_nodes,
            node_id,
        )
        self.history = self.session_manager.project_active_history(
            self._message_nodes,
            self._active_leaf_id,
        )
        self._last_saved_history_len = len(self.history)
        self._current_session_has_user_message = self._has_user_message()

        await self._finish_rewind(user_message_text)

    async def _finish_rewind(self, user_message_text: str) -> None:
        await self._rebuild_chat_from_history()
        self._update_status_bar()

        input_widget = self._chat_input()
        input_widget.value = user_message_text
        input_widget.focus()

        preview = user_message_text[:40].replace("\n", " ")
        self.notify(f"Branched at: {preview}")
        self._schedule_auto_save()

    async def _switch_branch(self, leaf_id: str) -> None:
        """Switch the active branch projection to the selected node."""
        await self._do_auto_save()
        if leaf_id not in self._message_nodes:
            return
        self._active_leaf_id = leaf_id
        self.history = self.session_manager.project_active_history(
            self._message_nodes,
            self._active_leaf_id,
        )
        self._last_saved_history_len = len(self.history)
        self._current_session_has_user_message = self._has_user_message()
        await self._rebuild_chat_from_history()
        self._scroll_to_bottom(force=True)
        self._update_status_bar()
        self._schedule_auto_save()

    def _find_preceding_turn_end(self, user_msg_index: int) -> int:
        """Find the index of the last message before user_msg_index
        that ends an assistant turn. Walk backwards."""
        for i in range(user_msg_index - 1, -1, -1):
            msg = self.history[i]
            role = msg.get("role", "")
            if role == "assistant":
                return i
            if role == "tool":
                continue  # tool messages are part of preceding assistant turn
        return -1  # No preceding assistant (first user message)

    async def _rebuild_chat_from_history(self) -> None:
        """Reconstruct the visual chat UI from the recent history window."""
        end = len(self.history)
        start = max(0, end - self._initial_render_message_count)
        await self._render_history_window(start, end, scroll_to_bottom=True)

    async def _expand_render_window_for_scroll_up(self) -> None:
        """Render older messages when the user scrolls up, capped at 20 messages."""
        if self._render_window_start <= 0:
            return
        end = self._render_window_end or len(self.history)
        current_size = end - self._render_window_start
        if current_size >= self._max_render_message_count:
            return
        target_size = self._max_render_message_count
        start = max(0, end - target_size)
        await self._render_history_window(start, end, preserve_scroll=True)

    async def _render_history_window(
        self,
        start: int,
        end: int,
        *,
        scroll_to_bottom: bool = False,
        preserve_scroll: bool = False,
    ) -> None:
        """Render a sliding message window, not the entire persisted history."""
        self._rendering_history_window = True
        try:
            chat_log = self.query_one("#main-chat-log", VerticalScroll)
            previous_scroll_y = chat_log.scroll_y
            start = max(0, min(start, len(self.history)))
            end = max(start, min(end, len(self.history)))
            if end - start > self._max_render_message_count:
                start = end - self._max_render_message_count

            await chat_log.remove_children()
            self._logo_visible = False
            self._models.clear()
            self._tools.clear()
            self._current_turn_bubble = None
            self._current_model_call_id = None
            self._flushing_models.clear()
            self._flushing_tools.clear()
            self._render_window_start = start
            self._render_window_end = end

            i = start
            while i < end:
                msg = self.history[i]
                role = msg.get("role", "")
                if role == "user":
                    await self._replay_user_bubble(chat_log, msg.get("content", ""))
                    i += 1
                elif role == "assistant":
                    # Collect this turn's messages (assistant + interleaved tools)
                    turn_msgs, next_i = self._collect_turn_messages(i, end=end)
                    await self._replay_assistant_turn(chat_log, turn_msgs)
                    i = next_i
                else:
                    # Skip standalone tool/system messages
                    i += 1

            if scroll_to_bottom:
                self._scroll_to_bottom(chat_log, force=True)
            elif preserve_scroll:
                chat_log.scroll_to(y=previous_scroll_y, animate=False)
        finally:
            self._rendering_history_window = False

    def _collect_turn_messages(
        self,
        start_idx: int,
        *,
        end: int | None = None,
    ) -> tuple[list[dict], int]:
        """Collect all messages belonging to one assistant turn.

        An assistant turn starts with role='assistant' and continues
        through any interleaved tool responses until the next user message.
        """
        limit = len(self.history) if end is None else min(end, len(self.history))
        turn_msgs = [self.history[start_idx]]
        i = start_idx + 1
        while i < limit:
            role = self.history[i].get("role", "")
            if role == "user":
                break
            turn_msgs.append(self.history[i])
            i += 1
        return turn_msgs, i

    async def _replay_user_bubble(self, chat_log: VerticalScroll, text: str) -> None:
        """Mount a user bubble for replay."""
        bubble = Vertical(classes="bubble user-bubble")
        await chat_log.mount(bubble)
        role = Static("You", classes="role")
        body = Static(text, classes="body")
        await bubble.mount(role, body)

    async def _replay_assistant_turn(
        self, chat_log: VerticalScroll, turn_msgs: list[dict]
    ) -> None:
        """Mount an assistant turn with content and tool cards."""
        bubble = Vertical(classes="bubble model-bubble")
        await chat_log.mount(bubble)

        for msg in turn_msgs:
            role = msg.get("role", "")
            if role == "assistant":
                # Reasoning
                reasoning = msg.get("reasoning_details") or msg.get("reasoning")
                if reasoning:
                    if isinstance(reasoning, list):
                        reasoning_text = "\n".join(
                            r.get("text", "") for r in reasoning
                        )
                    else:
                        reasoning_text = str(reasoning)
                    if reasoning_text.strip():
                        rw = Static(reasoning_text, classes="reasoning")
                        await bubble.mount(rw)

                # Content
                content = msg.get("content", "")
                if content and content.strip():
                    cw = Markdown(content, classes="body")
                    await bubble.mount(cw)

                # Tool calls
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        tc_name = tc.get("function", {}).get("name", "unknown") if isinstance(tc, dict) else getattr(tc, "name", "unknown")
                        if isinstance(tc, dict):
                            import json as _json
                            try:
                                tc_args = _json.loads(tc["function"]["arguments"])
                            except (json.JSONDecodeError, KeyError):
                                tc_args = {}
                        else:
                            tc_args = getattr(tc, "arguments", {})
                        card = create_tool_card(tool_name=tc_name, arguments=tc_args)
                        # Apply current expand/collapse state
                        if getattr(self, "_tools_expanded", False):
                            from lambda_coding_agent.tui.tool_cards import ToolBlock
                            if isinstance(card, ToolBlock):
                                card._expanded = True
                                card._refresh_content_display()
                        await bubble.mount(card)

            elif role == "tool":
                # Find the tool card to update
                tool_call_id = msg.get("tool_call_id", "")
                result = msg.get("content", "")
                # Find the last ToolBlock in the bubble and update it
                try:
                    blocks = list(bubble.query("ToolBlock"))
                    if blocks:
                        last_block = blocks[-1]
                        last_block._output = result
                        last_block._result_text = result
                        last_block._refresh_content_display()
                except Exception:
                    pass

        self._scroll_to_bottom(chat_log)

    async def _do_auto_save(self) -> None:
        """Save the current session to disk."""
        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = None

        if not self._current_session_id:
            return
        if not (self._current_session_has_user_message or self._has_user_message()):
            return

        name = self._original_session_name
        active_plan_id = self.plan_manager.get_active_plan_id()
        active_plan_path = None
        if active_plan_id:
            active_plan_path = f".lambda/plans/{active_plan_id}.json"
        self._sync_branch_state_from_history()
        self.session_manager.save_session(
            self._current_session_id,
            history=list(self.history),
            model_name=self.model_name,
            name=name,
            provider_id=self.provider_id or "",
            last_ctx_usage=self._last_prompt_tokens,
            active_plan_id=active_plan_id,
            active_plan_path=active_plan_path,
            message_nodes=self._message_nodes,
            active_leaf_id=self._active_leaf_id,
            next_message_seq=self._next_message_seq,
        )
        self._last_saved_history_len = len(self.history)

    def _schedule_auto_save(self) -> None:
        """Debounce auto-save: save after 30s of inactivity."""
        if self._save_timer:
            self._save_timer.cancel()
        try:
            loop = asyncio.get_event_loop()
            self._save_timer = loop.call_later(
                self._save_debounce_secs,
                lambda: self.call_later(self._do_auto_save),
            )
        except Exception:
            pass

    async def _generate_session_name(self, first_user_message: str) -> None:
        """Generate a session name via a cheap background LLM call."""
        if not self.provider_path:
            self._original_session_name = first_user_message[:60]
            return

        # Auto-detect a cheap model from provider.json
        naming_model = self._resolve_naming_model()

        try:
            from SimpleLLMFunc import OpenAICompatible

            models = OpenAICompatible.load_from_json_file(self.provider_path)
            # Find the LLM interface for the naming model
            llm = None
            for pid, mlist in models.items():
                for m in (mlist if isinstance(mlist, list) else list(mlist.values())):
                    if m.get("model_name") == naming_model:
                        llm = m
                        break
                if llm:
                    break

            if llm is None:
                self._original_session_name = first_user_message[:60]
                return

            prompt = f"Generate a short descriptive title (2-4 words) for this coding task: {first_user_message[:200]}"

            # Use the LLM's chat or completion method
            response = await llm.complete(prompt, max_tokens=30)
            name = response.strip().strip('"\'').strip()
            self._original_session_name = name[:80] if name else first_user_message[:60]
        except Exception:
            self._original_session_name = first_user_message[:60]

        # Save the name to the session file
        await self._do_auto_save()

    def _resolve_naming_model(self) -> str:
        """Auto-detect a cheap/fast model for session naming."""
        if not self.provider_path:
            return "unknown"
        try:
            with open(self.provider_path) as f:
                data = json.load(f)
            keywords = ("flash", "mini", "m2", "turbo", "sonnet")
            for pid, mlist in data.items():
                for m in (mlist if isinstance(mlist, list) else list(mlist.values())):
                    name = m.get("model_name", "").lower()
                    if any(kw in name for kw in keywords):
                        return m.get("model_name", "unknown")
            # Fallback: first model
            for pid, mlist in data.items():
                for m in (mlist if isinstance(mlist, list) else list(mlist.values())):
                    return m.get("model_name", "unknown")
        except Exception:
            pass
        return "unknown"

    # ── Agent turn runner ─────────────────────────────────────

    async def _run_turn(self, user_text: str) -> None:
        abort_signal = AbortSignal()
        self._active_abort_signal = abort_signal
        await self._show_working_indicator()

        # Trigger session name generation on first message
        if not self._name_generated and not self.history:
            self._name_generated = True
            self.run_worker(self._generate_session_name(user_text), exclusive=False)

        try:
            # Build template params for runtime toolkit override
            template_params: dict = {}
            if hasattr(self.agent_func, "_environment_block"):
                template_params["environment_block"] = self.agent_func._environment_block
            if hasattr(self.agent_func, "_build_runtime_toolkit"):
                from SimpleLLMFunc.runtime.selfref import (
                    SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM,
                )
                template_params[SELF_REFERENCE_TOOLKIT_OVERRIDE_TEMPLATE_PARAM] = (
                    self.agent_func._build_runtime_toolkit()
                )

            stream = self.agent_func(
                message=user_text,
                history=self.history,
                _abort_signal=abort_signal,
                _template_params=template_params if template_params else None,
            )
            new_history = await consume_react_stream(
                stream=stream,
                adapter=self,
                abort_signal=abort_signal,
            )
            if new_history:
                self.history = new_history
            self._live_turn_user_index = None
            self._live_model_history_indices.clear()
            self._live_tool_history_indices.clear()
            self._render_window_end = len(self.history)
            self._sync_branch_state_from_history()
        except Exception as exc:
            await self._append_system_hint(f"Agent error: {exc}")
        finally:
            await self._hide_working_indicator()
            self._busy = False
            self._active_abort_signal = None
            input_widget = self._chat_input()
            input_widget.focus()
            # Schedule auto-save after turn completes
            self._schedule_auto_save()
            # Refresh plan panel if active plan exists
            if self.plan_panel is not None:
                self.plan_panel.refresh_if_active()
            # Check for pending message after abort (from up-arrow interrupt)
            pending = getattr(self, "_pending_user_text", None)
            if pending:
                self._pending_user_text = None
                await self._append_user_message(pending)
                self._busy = True
                self.run_worker(self._run_turn(pending), thread=False)
            elif self._queued_text:
                # Turn finished naturally, auto-send queued message
                text = self._queued_text
                self._queued_text = None
                await self._hide_queued_indicator()
                await self._append_user_message(text)
                self._busy = True
                self.run_worker(self._run_turn(text), thread=False)
