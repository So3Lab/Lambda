"""Text-block-based tool call display for the TUI.

Each tool call is rendered as a compact text block with:
- Status icon + tool name + key argument summary
- Arrow + result summary
- Indented content block (truncated, Ctrl+O to expand/collapse)
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text

from textual.containers import Vertical
from textual.widgets import Static

# Max lines shown in collapsed state
_COLLAPSED_MAX_LINES = 6


def _truncate_lines(text: str, max_lines: int) -> tuple[str, int]:
    """Truncate text to max_lines, return (visible_text, hidden_count)."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, 0
    visible = "\n".join(lines[:max_lines])
    hidden = len(lines) - max_lines
    return visible, hidden


def _indent(text: str, prefix: str = "     ") -> str:
    """Indent each line of text."""
    return "\n".join(prefix + line for line in text.splitlines())


def _format_header(icon: str, tool_name: str, summary: str, color: str = "#d4c9a2") -> str:
    """Format the header line with Rich color markup based on status."""
    if summary:
        text = f"{icon}  {tool_name}({summary})"
    else:
        text = f"{icon}  {tool_name}"
    return f"[{color}]{text}[/{color}]"


def _format_result_line(text: str) -> str:
    """Format the arrow result line."""
    return f"   \u2192 {text}"


# ═══════════════════════════════════════════════════════════════════════
# Summary formatters per tool type
# ═══════════════════════════════════════════════════════════════════════


def _summary_run_command(args: dict) -> str:
    cmd = args.get("command", "")
    if len(cmd) > 60:
        cmd = cmd[:57] + "..."
    return cmd


def _summary_read_file(args: dict) -> str:
    return args.get("file_path", "")


def _summary_edit_file(args: dict) -> str:
    return args.get("file_path", "")


def _summary_write_file(args: dict) -> str:
    return args.get("file_path", "")


def _summary_find_files(args: dict) -> str:
    pattern = args.get("pattern", "")
    path = args.get("path")
    if path:
        return f"{pattern} in {path}"
    return pattern


def _summary_search(args: dict) -> str:
    pattern = args.get("pattern", "")
    path = args.get("path")
    if path:
        return f"{pattern} in {path}"
    return pattern


def _summary_execute_code(args: dict) -> str:
    return ""


def _summary_reset_repl(args: dict) -> str:
    return ""


def _summary_execute_code(args: dict) -> str:
    """Parse execute_code args to show which workspace primitive was called."""
    code = args.get("code", "")
    if not code:
        return ""
    # Look for runtime.workspace.XXX patterns
    import re
    matches = re.findall(r"runtime\.workspace\.(\w+)", code)
    if matches:
        return ", ".join(dict.fromkeys(matches))  # unique, preserve order
    return ""


def _summary_generic(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 30:
            s = s[:27] + "..."
        parts.append(f"{k}={s}")
        if len(parts) >= 2:
            break
    return ", ".join(parts)


_SUMMARY_MAP: dict[str, Any] = {
    "run_command": _summary_run_command,
    "read_file": _summary_read_file,
    "edit_file": _summary_edit_file,
    "write_file": _summary_write_file,
    "find_files": _summary_find_files,
    "search": _summary_search,
    "execute_code": _summary_execute_code,
    "reset_repl": _summary_reset_repl,
}


# ═══════════════════════════════════════════════════════════════════════
# ToolBlock widget
# ═══════════════════════════════════════════════════════════════════════


class ToolBlock(Vertical):
    """A compact text-block widget for displaying a tool call.

    Layout:
        ⟳  ToolName(summary_args)
           → result_text
             indented_content (truncated)
             … +N lines (Ctrl+O to expand)
    """

    DEFAULT_CSS = """
    ToolBlock {
        height: auto;
        margin: 0 0 1 0;
        padding: 0;
    }
    .tool-block-header {
        text-style: bold;
    }
    .tool-block-result {
        color: #8b95a7;
    }
    .tool-block-content {
        color: #9ba3b0;
        margin: 0 0 0 0;
    }
    .tool-block-truncation {
        color: #6f87a8;
        text-style: italic;
    }
    """

    can_focus = True

    def __init__(self, tool_name: str, arguments: dict[str, Any], **kwargs: Any):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.arguments = dict(arguments)
        self._status = "running"
        self._success = True
        self._result_text = ""
        self._output = ""
        self._expanded = False

    @property
    def _icon(self) -> str:
        if self._status == "running":
            return "\u27f3"
        return "\u2713" if self._success else "\u2717"

    @property
    def _status_color(self) -> str:
        if self._status == "running":
            return "#d4a373"  # yellow/amber
        return "#5f8d5a" if self._success else "#c0392b"  # green / red

    def _get_summary(self) -> str:
        formatter = _SUMMARY_MAP.get(self.tool_name, _summary_generic)
        return formatter(self.arguments)

    def compose(self):
        header_text = _format_header(self._icon, self.tool_name, self._get_summary(), self._status_color)
        yield Static(header_text, classes="tool-block-header", markup=True)
        yield Static("", classes="tool-block-result")
        yield Static("", classes="tool-block-content")
        yield Static("", classes="tool-block-truncation")

    def on_mount(self) -> None:
        # Hide result/content/truncation initially
        self.query_one(".tool-block-result", Static).display = False
        self.query_one(".tool-block-content", Static).display = False
        self.query_one(".tool-block-truncation", Static).display = False

    def _refresh_header(self) -> None:
        header_text = _format_header(self._icon, self.tool_name, self._get_summary(), self._status_color)
        try:
            self.query_one(".tool-block-header", Static).update(header_text)
        except Exception:
            pass

    def _get_display_content(self) -> str:
        """Get the full content to display, combining code + output for execute_code."""
        if self.tool_name == "execute_code":
            code = self.arguments.get("code", "")
            output = self._output or self._result_text
            if code and output:
                return f"{code}\n---\n{output}"
            return code or output
        return self._output or self._result_text

    def _build_renderable(self, content: str, expanded: bool):
        """Build the renderable for the content area.

        For execute_code, uses Rich Syntax highlighting on the code portion.
        For other tools, returns indented plain text.
        """
        if expanded:
            display_text = content
        else:
            display_text, _ = _truncate_lines(content, _COLLAPSED_MAX_LINES)

        if self.tool_name == "execute_code":
            code = self.arguments.get("code", "")
            output = self._output or self._result_text
            # Figure out which parts are in the display_text
            if code and output:
                separator = "\n---\n"
                sep_pos = display_text.find(separator)
                if sep_pos >= 0:
                    code_part = display_text[:sep_pos]
                    output_part = display_text[sep_pos + len(separator):]
                    parts = []
                    parts.append(Syntax(code_part, "python", theme="monokai",
                                        line_numbers=False, word_wrap=True, padding=(0, 5)))
                    parts.append(Text(f"     ───", style="#6f87a8"))
                    if output_part:
                        parts.append(Text(_indent(output_part), style="#9ba3b0"))
                    return Group(*parts)
                else:
                    # Only code visible (output truncated away)
                    return Syntax(display_text, "python", theme="monokai",
                                  line_numbers=False, word_wrap=True, padding=(0, 5))
            elif code:
                return Syntax(display_text, "python", theme="monokai",
                              line_numbers=False, word_wrap=True, padding=(0, 5))

        return Text(_indent(display_text), style="#9ba3b0")

    def _refresh_content_display(self) -> None:
        """Re-render the content block based on expanded state."""
        content = self._get_display_content()
        if not content:
            try:
                self.query_one(".tool-block-content", Static).display = False
                self.query_one(".tool-block-truncation", Static).display = False
            except Exception:
                pass
            return

        try:
            content_widget = self.query_one(".tool-block-content", Static)
            trunc_widget = self.query_one(".tool-block-truncation", Static)

            renderable = self._build_renderable(content, self._expanded)
            content_widget.update(renderable)
            content_widget.display = True

            if self._expanded:
                trunc_widget.display = False
            else:
                _, hidden = _truncate_lines(content, _COLLAPSED_MAX_LINES)
                if hidden > 0:
                    trunc_widget.update(f"     \u2026 +{hidden} lines")
                    trunc_widget.display = True
                else:
                    trunc_widget.display = False
        except Exception:
            pass

    def toggle_expand(self) -> None:
        """Toggle between expanded and collapsed content view."""
        self._expanded = not self._expanded
        self._refresh_content_display()

    # ── Public API (matches old BaseToolCard interface) ──────────

    def update_arguments(self, arguments: dict[str, Any]) -> None:
        """Update arguments as they stream in."""
        self.arguments = dict(arguments)
        self._refresh_header()
        # For execute_code, show code as content while streaming
        if self.tool_name == "execute_code" and arguments.get("code"):
            self._refresh_content_display()

    def update_output(self, output: str) -> None:
        """Update streaming output content."""
        self._output = output
        self._refresh_content_display()

    def update_result(self, result: str, success: bool) -> None:
        """Set final result and mark done."""
        self._result_text = result
        self._success = success
        self._status = "done"
        self._refresh_header()

        # Show result line
        try:
            result_widget = self.query_one(".tool-block-result", Static)
            # Compact result summary
            first_line = result.split("\n", 1)[0] if result else ""
            if len(first_line) > 80:
                first_line = first_line[:77] + "..."
            if first_line:
                result_widget.update(_format_result_line(first_line))
                result_widget.display = True
        except Exception:
            pass

        # Update content display with result if no streaming output was shown
        if not self._output and result:
            self._refresh_content_display()

    def update_status(self, status: str) -> None:
        """Update running status."""
        self._status = status
        self._refresh_header()


# ═══════════════════════════════════════════════════════════════════════
# Factory (preserves same API as before)
# ═══════════════════════════════════════════════════════════════════════


def create_tool_card(tool_name: str, arguments: dict[str, Any]) -> ToolBlock:
    """Create a ToolBlock for the given tool call."""
    return ToolBlock(tool_name=tool_name, arguments=arguments)
